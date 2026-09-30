"""Drive the sre-agent SSE stream and map events to an IM platform.

Migrated from ``teams-bot/investigation_runner.py`` and made platform-neutral:
every platform-specific action (rendering a card, sending text, updating a
message in place, deciding the final-reply shape) goes through
:class:`PlatformHooks`. The core owns only the state machine, the throttle, and
the timeout policy.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Awaitable, Callable, MutableMapping, Optional, Protocol

import aiohttp

from opensre_im_core.agent_client import AgentClient
from opensre_im_core.models import (
    InvestigationState,
)
from opensre_im_core.models import (
    question_card_ids as default_question_card_ids,
)
from opensre_im_core.progress_text import build_progress_text
from opensre_im_core.run_links import build_agent_run_url
from opensre_im_core.stream_handler import handle_stream_event, parse_sse_event

logger = logging.getLogger(__name__)

# Platform write throttles (cards/messages are rate-limited more aggressively
# than a raw stream). The shared default is deliberately slower than the old
# Teams-only 0.5s so chat APIs are not hammered during long investigations;
# platforms may override per-instance.
UPDATE_INTERVAL_SECONDS = 1.0

# thread_id must fit sre-agent's InvestigateRequest.thread_id (max_length=128).
THREAD_ID_MAX_LENGTH = 128

StreamUpdate = Callable[[str], Awaitable[None]]
StreamClose = Callable[[], Awaitable[None]]


class PlatformHooks(Protocol):
    """Everything the runner needs from a concrete chat platform.

    ``card``/``card_id`` are intentionally ``Any``: each platform passes its own
    native payload (Teams an Adaptive Card dict + activity id, Feishu a card JSON
    + message_id) and the core only passes it back unchanged.
    """

    async def stream_update(self, content: str) -> None: ...

    async def stream_close(self) -> None: ...

    async def send_card(self, card: Any) -> Optional[str]: ...

    async def send_text(self, content: str) -> None: ...

    async def update_card(self, card_id: str, card: Any) -> None: ...

    async def send_final_reply(
        self,
        *,
        result_text: Optional[str],
        error: Optional[str],
        run_url: Optional[str],
    ) -> None: ...


class InvestigationCallbacks:
    """Bundles the per-platform callables the runner drives.

    Passing one object keeps the runner signature short and makes the contract
    obvious: a platform implements these and gets the whole pipeline.
    """

    def __init__(self, hooks: PlatformHooks, **overrides: Any) -> None:
        self.hooks = hooks
        # Optional per-run overrides (used by platforms with different limits).
        for slot in ("build_question_card", "build_timeout_card"):
            setattr(self, slot, overrides.get(slot))
        self.update_interval = float(
            overrides.get("update_interval", UPDATE_INTERVAL_SECONDS)
        )


def sanitize_thread_id(conversation_id: str, prefix: str) -> str:
    """Stable, length-safe thread id derived from a platform conversation id.

    The old Teams-only version produced ``"teams-" + 240`` = 246 characters,
    which exceeded sre-agent's 128-char ``thread_id`` cap and would 422 on long
    channel ids (they embed ``;messageid=…``). The id is now truncated against
    the real limit, prefix included.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", (conversation_id or "unknown").lower())
    cleaned = cleaned.strip("-")
    if not cleaned:
        # Inputs made entirely of separators ("@:@") scrub down to nothing;
        # fall back to the placeholder rather than emitting a bare "feishu-".
        cleaned = "unknown"
    head = f"{prefix}-"
    budget = THREAD_ID_MAX_LENGTH - len(head)
    if budget <= 0:  # pathological prefix; never happens with our literals
        return head[:THREAD_ID_MAX_LENGTH]
    return f"{head}{cleaned[:budget]}"


def shape_answers_from_card_data(
    data: dict, questions: list[dict]
) -> tuple[str, dict[str, str]]:
    """Map platform form fields (``answer_{i}`` / ``comment_{i}``) to answers."""
    thread_id = data.get("answer_thread_id") or ""
    answers: dict[str, str] = {}
    for idx, _ in enumerate(questions):
        choice = data.get(f"answer_{idx}") or ""
        comment = (data.get(f"comment_{idx}") or "").strip()
        if comment and choice:
            value = f"{choice} // {comment}"
        else:
            value = choice or comment
        answers[f"question_{idx}"] = value
    return thread_id, answers


def _run_url(cfg_base: str, state: InvestigationState) -> Optional[str]:
    if not state.run_id:
        return None
    return build_agent_run_url(cfg_base, state.run_id)


async def run_investigation(
    *,
    thread_id: str,
    prompt: str,
    callbacks: InvestigationCallbacks,
    client: AgentClient,
    web_ui_base_url: str = "",
    trigger_source: str,
    trigger_actor: Optional[str] = None,
    active_investigations: Optional[set[str]] = None,
    question_card_ids: Optional[MutableMapping[str, str]] = None,
    update_interval: Optional[float] = None,
) -> None:
    """Run one investigation to completion, streaming progress to the platform.

    ``trigger_source`` is required and platform-supplied (e.g. "teams",
    "feishu"); sre-agent silently rewrites unknown values to "web_ui", so a typo
    here degrades attribution rather than erroring.

    ``active_investigations`` and ``question_card_ids`` are caller-owned mutable
    containers so platforms (and tests) can isolate their state instead of
    sharing the module-level defaults.
    """
    tracker = (
        active_investigations if active_investigations is not None else set()
    )
    cards = question_card_ids if question_card_ids is not None else default_question_card_ids
    tracker.add(thread_id)
    try:
        await _run_investigation_body(
            thread_id=thread_id,
            prompt=prompt,
            callbacks=callbacks,
            client=client,
            web_ui_base_url=web_ui_base_url,
            trigger_source=trigger_source,
            trigger_actor=trigger_actor,
            question_card_ids=cards,
            update_interval=(
                update_interval
                if update_interval is not None
                else callbacks.update_interval
            ),
        )
    finally:
        tracker.discard(thread_id)


async def _run_investigation_body(
    *,
    thread_id: str,
    prompt: str,
    callbacks: InvestigationCallbacks,
    client: AgentClient,
    web_ui_base_url: str,
    trigger_source: str,
    trigger_actor: Optional[str],
    question_card_ids: MutableMapping[str, str],
    update_interval: float,
) -> None:
    hooks = callbacks.hooks
    state = InvestigationState(thread_id=thread_id)
    payload = client.investigate_payload(
        prompt=prompt,
        thread_id=thread_id,
        trigger_source=trigger_source,
        trigger_actor=trigger_actor,
    )
    last_update = 0.0
    stream_closed = False

    async def close_stream() -> None:
        """Close the live progress surface exactly once."""
        nonlocal stream_closed
        if stream_closed:
            return
        stream_closed = True
        try:
            await hooks.stream_close()
        except Exception:
            logger.exception("stream_close failed for thread %s", thread_id)

    async def send_final() -> None:
        await close_stream()
        try:
            await hooks.send_final_reply(
                result_text=state.final_result,
                error=state.error,
                run_url=_run_url(web_ui_base_url, state),
            )
        except Exception:
            logger.exception("send_final_reply failed for thread %s", thread_id)

    async def process_sse_line(line: str) -> bool:
        nonlocal last_update
        line = line.strip()
        event = parse_sse_event(line)
        if not event:
            return False
        step = handle_stream_event(state, event)

        if step.update_progress:
            now = time.monotonic()
            if step.immediate_progress or now - last_update >= update_interval:
                await hooks.stream_update(
                    build_progress_text(state, run_url=_run_url(web_ui_base_url, state))
                )
                last_update = now

        if step.post_question is not None:
            await close_stream()
            if callbacks.build_question_card is not None:
                card_id = await hooks.send_card(
                    callbacks.build_question_card(
                        thread_id=thread_id, questions=step.post_question
                    )
                )
                if card_id:
                    question_card_ids[thread_id] = card_id

        if step.question_timed_out:
            card_id = question_card_ids.pop(thread_id, None)
            if card_id and callbacks.build_timeout_card is not None:
                await hooks.update_card(card_id, callbacks.build_timeout_card())

        if step.finished:
            if state.error:
                # error is terminal — backend sends nothing after this.
                await send_final()
                return True
            # result: backend may continue if a message was queued mid-run.
            # Let the stream drain; the bottom handler sends the final reply.
            return False
        return False

    try:
        async with aiohttp.ClientSession(timeout=client.stream_timeout) as session:
            async with session.post(
                f"{client.base_url}/investigate",
                json=payload,
                headers=client.headers(accept_sse=True),
            ) as resp:
                resp.raise_for_status()
                buffer = ""
                async for raw in resp.content.iter_any():
                    buffer += raw.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if await process_sse_line(line):
                            return
                if buffer.strip() and await process_sse_line(buffer):
                    return
    except TimeoutError:
        logger.exception("SSE timed out for thread %s", thread_id)
        if not state.final_result:
            state.error = (
                "Timed out waiting for the agent. Reply in this thread to continue."
            )
        await send_final()
        return

    # Stream drained — send the final reply with whatever result we accumulated.
    if state.final_result or state.error:
        await send_final()
    else:
        await close_stream()
