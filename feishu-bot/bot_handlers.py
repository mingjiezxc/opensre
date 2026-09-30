# feishu-bot/bot_handlers.py
"""Feishu event routing + the platform hooks the shared runner drives.

The split mirrors ``teams-bot``: routing decisions and the
:class:`~opensre_im_core.investigation_runner.PlatformHooks` implementation live
here, while everything about SSE interpretation, throttling and timeout policy
stays in ``im-core``.

Two Feishu-specific facts shape this module:

* the SDK invokes registered handlers **synchronously** (its event processor
  calls ``self.f(data)`` and discards the result), so an ``async def`` handler
  would never run. The registered callables are therefore thin sync adapters
  that schedule the real coroutine on the running loop — and the card callback
  returns its toast immediately, exactly as Feishu requires;
* the agent's clarifying questions must be remembered per thread by the
  *platform*, because the shared runner only tracks the question card's message
  id. That bookkeeping lives in :meth:`FeishuRuntime.question_card_builder`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Optional

import aiohttp
from opensre_im_core.agent_client import AgentClient
from opensre_im_core.investigation_runner import (
    InvestigationCallbacks,
    PlatformHooks,
    run_investigation,
    sanitize_thread_id,
)

from card_actions import (
    CardActionSubmission,
    card_action_response,
    parse_card_action,
    submit_card_action,
)
from card_builder import (
    build_final_card,
    build_progress_card,
    build_question_card,
    build_timeout_card,
    build_welcome_card,
)
from config import DEFAULT_BOT_NAME
from lark_client import LarkClient
from message_parser import (
    HELP_COMMANDS,
    chat_id_of,
    chat_type_of,
    content_of,
    extract_text,
    is_bot_mentioned,
    is_p2p,
    mentions_of,
    sender_open_id,
    should_handle_message,
    strip_bot_name,
    strip_mentions,
)

logger = logging.getLogger(__name__)

# sre-agent attributes a run's origin from this value.
TRIGGER_SOURCE = "feishu"
# thread_id namespace for this platform (keeps ids unique across bots).
THREAD_PREFIX = "feishu"

# Feishu rate-limits interactive-card PATCHes, so the shared 1.0s default is too
# aggressive for a live progress card.
UPDATE_INTERVAL_SECONDS = 1.5

PROGRESS_STARTED = "_Starting investigation…_"
QUEUED_TEXT = "Message queued — I'll use it after the current step."
QUEUE_FAILED_TEXT = "Couldn't queue your message — please try again."
# Only transport-level failures reach this (the core already turns SSE "error"
# events and timeouts into a final reply), and without it the user would be left
# staring at a progress card that never resolves.
INVESTIGATION_FAILED_TEXT = (
    "Couldn't reach the investigation service — please try again."
)


def schedule(coro: Awaitable[Any]) -> Optional[asyncio.Task]:
    """Run ``coro`` on the currently running loop.

    Called from the SDK's synchronous handler context. When there is no loop
    (a misconfigured embed), the coroutine is closed and dropped with a warning
    instead of leaking a "never awaited" coroutine.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("no running event loop — dropping scheduled feishu handler")
        if hasattr(coro, "close"):
            coro.close()
        return None
    return loop.create_task(coro)


class FeishuHooks(PlatformHooks):
    """One conversation's progress surface, driven by the shared runner."""

    def __init__(
        self,
        *,
        lark: LarkClient,
        chat_id: str,
        thread_id: str = "",
        progress_message_id: Optional[str] = None,
        pending_questions: Optional[dict[str, list[dict]]] = None,
    ) -> None:
        self._lark = lark
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._progress_message_id = progress_message_id
        # Shared with the card-action handler; the run owns the lifecycle of its
        # own pending questions.
        self._pending_questions = (
            pending_questions if pending_questions is not None else {}
        )

    @property
    def progress_message_id(self) -> Optional[str]:
        return self._progress_message_id

    def _forget_questions(self) -> None:
        if self._thread_id:
            self._pending_questions.pop(self._thread_id, None)

    async def stream_update(self, content: str) -> None:
        """Edit the progress card in place; never raises into the stream loop."""
        if not self._progress_message_id:
            return
        try:
            await self._lark.patch_card(
                self._progress_message_id, build_progress_card(content)
            )
        except Exception:
            logger.exception(
                "failed to patch progress card %s", self._progress_message_id
            )

    async def stream_close(self) -> None:
        """Nothing to tear down: the progress card is replaced by the final one."""

    async def send_card(self, card: dict) -> Optional[str]:
        return await self._lark.send_card(receive_id=self._chat_id, card=card)

    async def send_text(self, content: str) -> None:
        await self._lark.send_text(receive_id=self._chat_id, text=content)

    async def update_card(self, card_id: str, card: dict) -> None:
        """Replace a card in place (the question-timeout path)."""
        # The run's questions are answered or gone once its card is replaced.
        self._forget_questions()
        try:
            await self._lark.patch_card(card_id, card)
        except Exception:
            logger.exception("failed to update card %s", card_id)

    async def send_final_reply(
        self,
        *,
        result_text: Optional[str],
        error: Optional[str],
        run_url: Optional[str],
    ) -> None:
        self._forget_questions()
        card = build_final_card(result_text=result_text, error=error, run_url=run_url)
        await self._lark.send_card(receive_id=self._chat_id, card=card)


@dataclass
class FeishuRuntime:
    """Everything a handler needs, created once at startup.

    The mutable containers are caller-owned (and injectable) so tests get
    isolation and a restart does not leak state between bots.
    """

    lark: LarkClient
    agent_client: AgentClient
    bot_open_id: str = ""
    bot_name: str = DEFAULT_BOT_NAME
    web_ui_base_url: str = ""
    update_interval: float = UPDATE_INTERVAL_SECONDS
    active_investigations: set[str] = field(default_factory=set)
    question_card_ids: dict[str, str] = field(default_factory=dict)
    pending_questions: dict[str, list[dict]] = field(default_factory=dict)

    def thread_id_for(self, chat_id: str) -> str:
        return sanitize_thread_id(chat_id, THREAD_PREFIX)

    def question_card_builder(self):
        """Card factory that also remembers the questions for this thread.

        The shared runner tracks only the card's message id; the platform must
        keep the questions so the card callback can map ``answer_{i}`` fields
        back onto them.
        """

        def build(*, thread_id: str, questions: list[dict]) -> dict:
            self.pending_questions[thread_id] = list(questions or [])
            return build_question_card(thread_id=thread_id, questions=questions)

        return build


async def queue_mid_run_message(
    *,
    runtime: FeishuRuntime,
    chat_id: str,
    thread_id: str,
    text: str,
) -> bool:
    """Hand a mid-run message to the live session.

    Returns True when the message was consumed here (queued, or a failure the
    user was told about) and False when the caller should start a fresh run:
    404 means no in-process session (orphaned run after a restart) and 409 that
    the run finished between the check and the call. In both cases the same
    thread_id is reused, so the agent keeps its conversation context.
    """
    try:
        await runtime.agent_client.queue_message(thread_id=thread_id, text=text)
    except aiohttp.ClientResponseError as exc:
        if exc.status in (404, 409):
            logger.info(
                "queue-message %s for thread %s — starting a follow-up run",
                exc.status,
                thread_id,
            )
            return False
        logger.exception("queue_message failed for thread %s", thread_id)
        await runtime.lark.send_text(receive_id=chat_id, text=QUEUE_FAILED_TEXT)
        return True
    except Exception:
        logger.exception("queue_message failed for thread %s", thread_id)
        await runtime.lark.send_text(receive_id=chat_id, text=QUEUE_FAILED_TEXT)
        return True
    await runtime.lark.send_text(receive_id=chat_id, text=QUEUED_TEXT)
    return True


async def handle_message(event: Any, *, runtime: FeishuRuntime) -> None:
    """Route one ``im.message.receive_v1`` event."""
    text = extract_text(content_of(event))
    chat_type = chat_type_of(event)
    mentioned = is_bot_mentioned(event, runtime.bot_open_id)
    if not should_handle_message(text=text, mentioned=mentioned, chat_type=chat_type):
        if text.strip() and not is_p2p(chat_type) and not runtime.bot_open_id:
            logger.warning(
                "ignoring group message: the bot's open_id is unresolved, so "
                "@mentions cannot be detected"
            )
        return

    cleaned = strip_bot_name(strip_mentions(text, mentions_of(event)), runtime.bot_name)
    if not cleaned:
        return

    chat_id = chat_id_of(event)
    if not chat_id:
        logger.warning("ignoring feishu message without a chat_id")
        return

    if cleaned.lower() in HELP_COMMANDS:
        await runtime.lark.send_card(
            receive_id=chat_id, card=build_welcome_card(runtime.bot_name)
        )
        return

    thread_id = runtime.thread_id_for(chat_id)

    if thread_id in runtime.active_investigations and await queue_mid_run_message(
        runtime=runtime, chat_id=chat_id, thread_id=thread_id, text=cleaned
    ):
        return

    # The actor is best-effort: get_user_display_name returns None (never the
    # open_id) when the contact scope is missing.
    actor = await runtime.lark.get_user_display_name(sender_open_id(event))

    # A card is the progress surface, so the user sees the run immediately; the
    # runner PATCHes it on every throttled tick.
    progress_message_id = await runtime.lark.send_card(
        receive_id=chat_id, card=build_progress_card(PROGRESS_STARTED)
    )
    hooks = FeishuHooks(
        lark=runtime.lark,
        chat_id=chat_id,
        thread_id=thread_id,
        progress_message_id=progress_message_id,
        pending_questions=runtime.pending_questions,
    )
    callbacks = InvestigationCallbacks(
        hooks,
        build_question_card=runtime.question_card_builder(),
        build_timeout_card=build_timeout_card,
        update_interval=runtime.update_interval,
    )
    try:
        await run_investigation(
            thread_id=thread_id,
            prompt=cleaned,
            callbacks=callbacks,
            client=runtime.agent_client,
            web_ui_base_url=runtime.web_ui_base_url,
            trigger_source=TRIGGER_SOURCE,
            trigger_actor=actor,
            active_investigations=runtime.active_investigations,
            question_card_ids=runtime.question_card_ids,
        )
    except Exception:
        # Transport-level failure (sre-agent down, /investigate 4xx/5xx). The
        # core handles SSE errors and timeouts itself, so reaching here means the
        # run never started and the progress card would hang forever.
        logger.exception("investigation failed for thread %s", thread_id)
        await runtime.lark.send_text(
            receive_id=chat_id, text=INVESTIGATION_FAILED_TEXT
        )


async def submit_submission(
    submission: CardActionSubmission, *, runtime: FeishuRuntime
) -> None:
    """POST one parsed card action, then drop the thread's pending questions."""
    try:
        await submit_card_action(submission, agent_client=runtime.agent_client)
    except Exception:
        logger.exception("failed to submit answers for thread %s", submission.thread_id)
        return
    runtime.pending_questions.pop(submission.thread_id, None)


def make_message_handler(*, runtime: FeishuRuntime):
    """Sync adapter registered with the SDK dispatcher."""

    def on_message(event: Any) -> None:
        schedule(handle_message(event, runtime=runtime))

    return on_message


def make_card_action_handler(*, runtime: FeishuRuntime):
    """Sync adapter that answers Feishu immediately with a toast.

    The toast must be returned synchronously, so the ``/answer`` POST is
    scheduled and the user gets their acknowledgement without waiting on
    sre-agent.
    """

    def on_card_action(event: Any):
        submission = parse_card_action(event, runtime.pending_questions)
        if submission is None:
            return card_action_response(ok=False)
        schedule(submit_submission(submission, runtime=runtime))
        return card_action_response(ok=True)

    return on_card_action


def register_handlers(builder: Any, *, runtime: FeishuRuntime) -> Any:
    """Register both Feishu event handlers on an SDK dispatcher builder."""
    builder.register_p2_im_message_receive_v1(make_message_handler(runtime=runtime))
    builder.register_p2_card_action_trigger(make_card_action_handler(runtime=runtime))
    return builder
