"""Activity routing helpers and handler registration."""

import logging
import re
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from opensre_im_core.agent_client import AgentClient
from opensre_im_core.investigation_runner import (
    InvestigationCallbacks,
    PlatformHooks,
    run_investigation,
    sanitize_thread_id,
    shape_answers_from_card_data,
)

from card_builder import (
    build_final_card,
    build_final_text,
    build_question_card,
    build_timeout_card,
    build_welcome_card,
)
from config import Config
from state import active_investigations, pending_questions, question_activity_ids

logger = logging.getLogger(__name__)

SUBMIT_VERB = "opensre.submit_answers"

# sre-agent attributes every run to a trigger_source; Teams is the platform tag.
TRIGGER_SOURCE = "teams"
# sanitize_thread_id prefixes the thread id with this, so `teams-…` ids stay
# distinguishable from other platforms running against the same sre-agent.
THREAD_ID_PREFIX = "teams"
# Live-progress edit cadence. The shared core defaults to 1.0s; Teams cards were
# tuned at 0.5s, and this migration must stay behaviour-neutral for Teams users
# (Feishu pins its own, slower interval because its card PATCH is rate-limited).
PROGRESS_UPDATE_INTERVAL_SECONDS = 0.5

StreamUpdate = Callable[[str], Awaitable[None]]
StreamClose = Callable[[], Awaitable[None]]
SendCard = Callable[[dict], Awaitable[Optional[str]]]  # returns activity id
SendText = Callable[[str], Awaitable[None]]
UpdateCard = Callable[[str, dict], Awaitable[None]]  # activity_id, card


def is_personal_chat(conversation_type: str | None) -> bool:
    return (conversation_type or "").lower() in {"personal", "1:1"}


def is_azure_webchat(channel_id: str | None) -> bool:
    # Azure Bot "Test in Web Chat" uses channelId=webchat (not Teams personal scope).
    return (channel_id or "").lower() == "webchat"


def is_direct_bot_chat(
    conversation_type: str | None, channel_id: str | None = None
) -> bool:
    return is_personal_chat(conversation_type) or is_azure_webchat(channel_id)


def should_handle_message(
    *,
    text: str,
    mentioned: bool,
    conversation_type: str | None,
    channel_id: str | None = None,
) -> bool:
    if is_direct_bot_chat(conversation_type, channel_id):
        return bool((text or "").strip())
    return mentioned and bool((text or "").strip())


def activity_sender_name(activity: Any) -> Optional[str]:
    """Teams display name from the activity sender. Never return the user id."""
    # microsoft-teams-apps 2.x exposes JSON "from" as from_ on MessageActivity.
    sender = getattr(activity, "from_", None)
    if sender is None:
        sender = getattr(activity, "from_property", None)
    if sender is None:
        sender = getattr(activity, "from", None)
    name = getattr(sender, "name", None) if sender is not None else None
    if isinstance(sender, dict):
        name = sender.get("name")
    if not isinstance(name, str):
        return None
    stripped = name.strip()
    if not stripped:
        return None
    return stripped[:128]


def strip_bot_mention(text: str, bot_name: str = "OpenSRE") -> str:
    cleaned = re.sub(r"<at>[^<]*</at>", "", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(re.escape(bot_name), "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _dict_to_adaptive_card(card: dict):
    # SDK MessageActivityInput.add_card() requires typed AdaptiveCard, not raw dict.
    from microsoft_teams.cards.core import AdaptiveCard

    return AdaptiveCard.model_validate(card)


def _agent_client(cfg: Optional[Config] = None) -> AgentClient:
    """sre-agent client built from the Teams bot's configuration."""
    cfg = cfg or Config()
    return AgentClient(
        cfg.SRE_AGENT_URL,
        auth_token=cfg.INVESTIGATE_AUTH_TOKEN,
        team_token=cfg.OPENSRE_TEAM_TOKEN,
    )


async def queue_message(*, thread_id: str, text: str) -> dict:
    """Queue a mid-run message onto the live investigation."""
    return await _agent_client().queue_message(thread_id=thread_id, text=text)


async def submit_answers(*, thread_id: str, answers: dict[str, str]) -> None:
    """Submit question-card answers for a waiting investigation."""
    await _agent_client().submit_answers(thread_id=thread_id, answers=answers)


def _build_question_card(*, thread_id: str, questions: list[dict]) -> dict:
    """Question-card builder passed to the shared runner.

    Posting the card is the core's job, but the card-action handler still needs
    the questions to shape the submitted answers — remember them here, keyed by
    the same thread id the action carries.
    """
    pending_questions[thread_id] = questions
    return build_question_card(thread_id=thread_id, questions=questions)


class TeamsHooks(PlatformHooks):
    """Microsoft Teams implementation of the shared ``PlatformHooks`` contract.

    Wraps the ``ctx``-driven callables the handler builds (stream for 1:1/Web
    Chat, PUT-ed text bubble for channels) and owns the Teams-specific final
    reply: 1:1 and Azure Web Chat get an Adaptive Card, channel/group threads
    get plain text (Bot Framework streaming and cards are unavailable there).
    """

    def __init__(
        self,
        *,
        thread_id: str,
        stream_update: StreamUpdate,
        stream_close: StreamClose,
        send_card: SendCard,
        send_text: SendText,
        update_card: UpdateCard,
        plain_text_final: bool,
    ) -> None:
        self.thread_id = thread_id
        self.plain_text_final = plain_text_final
        self._stream_update = stream_update
        self._stream_close = stream_close
        self._send_card = send_card
        self._send_text = send_text
        self._update_card = update_card

    async def stream_update(self, content: str) -> None:
        await self._stream_update(content)

    async def stream_close(self) -> None:
        await self._stream_close()

    async def send_card(self, card: Any) -> Optional[str]:
        return await self._send_card(card)

    async def send_text(self, content: str) -> None:
        await self._send_text(content)

    async def update_card(self, card_id: str, card: Any) -> None:
        await self._update_card(card_id, card)

    async def send_final_reply(
        self,
        *,
        result_text: Optional[str],
        error: Optional[str],
        run_url: Optional[str],
    ) -> None:
        if self.plain_text_final:
            await self._send_text(
                build_final_text(result_text=result_text, error=error, run_url=run_url)
            )
            return
        await self._send_card(
            build_final_card(result_text=result_text, error=error, run_url=run_url)
        )

    def timeout_card_override(self) -> dict:
        """``build_timeout_card`` override: drop the timed-out question set.

        The shared runner replaces the card itself; the Teams card-action
        handler reads ``pending_questions``, so the stale set is cleared here —
        exactly what the pre-extraction runner did on ``question_timeout``.
        """
        pending_questions.pop(self.thread_id, None)
        return build_timeout_card()


def register_handlers(app) -> None:
    from microsoft_teams.api import MessageActivity, MessageActivityInput
    from microsoft_teams.apps import ActivityContext

    @app.on_message
    async def on_message(ctx: ActivityContext[MessageActivity]):
        activity = ctx.activity
        conversation = activity.conversation
        conv_type = getattr(conversation, "conversation_type", None) or getattr(
            conversation, "conversationType", None
        )
        text = activity.text or ""
        mentioned = _has_mention(activity)
        channel_id = getattr(activity, "channel_id", None) or getattr(
            activity, "channelId", None
        )
        if not should_handle_message(
            text=text,
            mentioned=mentioned,
            conversation_type=conv_type,
            channel_id=channel_id,
        ):
            return

        cleaned = strip_bot_mention(text)
        if not cleaned:
            return

        if cleaned.lower() in {"help", "status"}:
            await ctx.send(
                MessageActivityInput().add_card(
                    _dict_to_adaptive_card(build_welcome_card())
                )
            )
            return

        thread_id = sanitize_thread_id(conversation.id, THREAD_ID_PREFIX)

        if thread_id in active_investigations:
            try:
                await queue_message(thread_id=thread_id, text=cleaned)
            except aiohttp.ClientResponseError as exc:
                if exc.status in (404, 409):
                    # 409: race — investigation finished between our check and queue call.
                    # 404: no in-process session (orphaned run after restart/crash).
                    # Fall through so the message starts a follow-up run with the same
                    # thread_id (agent retains conversation context).
                    pass
                else:
                    logger.exception("queue_message failed for thread %s", thread_id)
                    await ctx.send(
                        MessageActivityInput().add_text(
                            "Couldn't queue your message — please try again."
                        )
                    )
                    return
            else:
                await ctx.send(
                    MessageActivityInput().add_text(
                        "Message queued — I'll use it after the current step."
                    )
                )
                return

        # Channel/group threads reject Bot Framework streaming (HTTP 405).
        # 1:1 and Azure Web Chat still use ctx.stream for live progress.
        # Channels edit one text bubble via PUT (MessageActivityInput.with_id).
        use_stream = is_direct_bot_chat(conv_type, channel_id)
        progress_activity_id: str | None = None

        async def stream_update(content: str) -> None:
            nonlocal progress_activity_id
            if use_stream:
                ctx.stream.update(content)
                return
            if not progress_activity_id:
                return
            try:
                # ActivitySender.send() PUTs when activity.id is set — no new toast.
                await ctx.send(
                    MessageActivityInput(text=content).with_id(progress_activity_id)
                )
            except Exception:
                logger.exception(
                    "failed to update channel progress %s", progress_activity_id
                )

        async def stream_close() -> None:
            # HttpStream.close() is synchronous; must run before sending cards.
            if use_stream:
                ctx.stream.close()

        async def send_card(card: dict):
            sent = await ctx.send(
                MessageActivityInput().add_card(_dict_to_adaptive_card(card))
            )
            return getattr(sent, "id", None)

        async def send_text(content: str) -> None:
            await ctx.send(MessageActivityInput().add_text(content))

        async def update_card(activity_id: str, card: dict) -> None:
            # PUT the existing activity so question-timeout does not spawn a new card.
            try:
                await ctx.send(
                    MessageActivityInput()
                    .with_id(activity_id)
                    .add_card(_dict_to_adaptive_card(card))
                )
            except Exception:
                logger.exception("failed to update/replace card %s", activity_id)

        if not use_stream:
            sent = await ctx.send(
                MessageActivityInput().add_text(
                    "Working on it — I'll reply in this thread."
                )
            )
            progress_activity_id = getattr(sent, "id", None)

        cfg = Config()
        hooks = TeamsHooks(
            thread_id=thread_id,
            stream_update=stream_update,
            stream_close=stream_close,
            send_card=send_card,
            send_text=send_text,
            update_card=update_card,
            plain_text_final=not use_stream,
        )
        callbacks = InvestigationCallbacks(
            hooks,
            build_question_card=_build_question_card,
            build_timeout_card=hooks.timeout_card_override,
            update_interval=PROGRESS_UPDATE_INTERVAL_SECONDS,
        )

        # Await runner in-handler so ctx.stream stays valid for 1:1 / Web Chat.
        await run_investigation(
            thread_id=thread_id,
            prompt=cleaned,
            callbacks=callbacks,
            client=_agent_client(cfg),
            web_ui_base_url=cfg.WEB_UI_PUBLIC_BASE_URL,
            trigger_source=TRIGGER_SOURCE,
            trigger_actor=activity_sender_name(activity),
            active_investigations=active_investigations,
            question_card_ids=question_activity_ids,
        )

    @app.on_card_action_execute(SUBMIT_VERB)
    async def on_submit(ctx):
        flat = _flatten_card_action_data(ctx.activity)
        thread_id = flat.get("answer_thread_id")
        questions = pending_questions.get(thread_id or "", [])
        thread_id, answers = shape_answers_from_card_data(flat, questions)
        if not thread_id:
            return _ok_response("Missing thread id")
        await submit_answers(thread_id=thread_id, answers=answers)
        pending_questions.pop(thread_id, None)
        return _ok_response("Answer submitted")


def _has_mention(activity: Any) -> bool:
    entities = getattr(activity, "entities", None) or []
    for ent in entities:
        ent_type = getattr(ent, "type", None)
        if ent_type == "mention":
            return True
    return False


def _flatten_card_action_data(activity: Any) -> dict:
    value = getattr(activity, "value", None)
    action = getattr(value, "action", None) if value else None
    data = getattr(action, "data", None) if action else None
    if not isinstance(data, dict):
        return {}
    # Action.Execute merges Input.* field values into action.data alongside verb data.
    return dict(data)


def _ok_response(message: str):
    from microsoft_teams.api import AdaptiveCardActionMessageResponse

    return AdaptiveCardActionMessageResponse(
        status_code=200,
        type="application/vnd.microsoft.activity.message",
        value=message,
    )
