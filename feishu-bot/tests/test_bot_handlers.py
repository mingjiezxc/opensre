"""Routing, PlatformHooks and card-callback behaviour.

The bot is not a member of any live Feishu group, so every group path is driven
with synthetic events. The timeout path runs the *real* shared runner against a
faked aiohttp session, which is what proves the platform hook contract is wired
correctly.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from helpers import make_card_action_event, make_mention, make_receive_event
from opensre_im_core.agent_client import AgentClient
from opensre_im_core.investigation_runner import (
    InvestigationCallbacks,
    run_investigation,
    sanitize_thread_id,
)

from bot_handlers import (
    INVESTIGATION_FAILED_TEXT,
    QUEUE_FAILED_TEXT,
    QUEUED_TEXT,
    TRIGGER_SOURCE,
    UPDATE_INTERVAL_SECONDS,
    FeishuHooks,
    FeishuRuntime,
    handle_message,
    make_card_action_handler,
    make_message_handler,
    register_handlers,
    schedule,
    submit_submission,
)
from card_builder import build_timeout_card

BOT_OPEN_ID = "ou_synthetic_bot"
CHAT_ID = "oc_synthetic_chat"
THREAD_ID = sanitize_thread_id(CHAT_ID, "feishu")

QUESTION = {
    "header": "Which service?",
    "question": "Which service is failing?",
    "options": [{"label": "api", "description": "the public API"}],
    "multi_select": False,
}


def make_runtime(**overrides) -> FeishuRuntime:
    lark = MagicMock()
    lark.send_card = AsyncMock(return_value="om_progress")
    lark.send_text = AsyncMock()
    lark.patch_card = AsyncMock(return_value=True)
    lark.get_user_display_name = AsyncMock(return_value="Jane Doe")
    agent_client = MagicMock()
    agent_client.queue_message = AsyncMock()
    agent_client.submit_answers = AsyncMock()
    runtime = FeishuRuntime(
        lark=lark,
        agent_client=agent_client,
        bot_open_id=BOT_OPEN_ID,
        bot_name="OpenSRE",
    )
    for key, value in overrides.items():
        setattr(runtime, key, value)
    return runtime


# --------------------------------------------------------------------- routing


@pytest.mark.asyncio
async def test_group_message_without_mention_is_ignored():
    runtime = make_runtime()
    event = make_receive_event(text="investigate oc-9", chat_type="group", mentions=[])
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)
    run.assert_not_awaited()
    runtime.lark.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_mention_runs_investigation_with_stripped_prompt():
    runtime = make_runtime()
    event = make_receive_event(
        text="@_user_1 investigate oc-9",
        chat_type="group",
        mentions=[make_mention(BOT_OPEN_ID)],
    )
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)

    run.assert_awaited_once()
    kwargs = run.await_args.kwargs
    assert kwargs["prompt"] == "investigate oc-9"
    assert kwargs["thread_id"] == THREAD_ID
    assert kwargs["trigger_source"] == TRIGGER_SOURCE == "feishu"
    assert kwargs["trigger_actor"] == "Jane Doe"
    assert kwargs["client"] is runtime.agent_client
    # The progress card is the live surface and is sent before the run starts.
    runtime.lark.send_card.assert_awaited_once()
    assert runtime.lark.send_card.await_args.kwargs["receive_id"] == CHAT_ID


@pytest.mark.asyncio
async def test_p2p_message_needs_no_mention():
    runtime = make_runtime()
    event = make_receive_event(text="check redis", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)
    run.assert_awaited_once()
    assert run.await_args.kwargs["prompt"] == "check redis"


@pytest.mark.asyncio
async def test_actor_is_omitted_when_display_name_is_unavailable():
    """Never fall back to the open_id as a person's name."""
    runtime = make_runtime()
    runtime.lark.get_user_display_name = AsyncMock(return_value=None)
    event = make_receive_event(text="check redis", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)
    assert run.await_args.kwargs["trigger_actor"] is None


@pytest.mark.asyncio
async def test_help_returns_welcome_card_without_investigating():
    runtime = make_runtime()
    event = make_receive_event(text="help", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)
    run.assert_not_awaited()
    card = runtime.lark.send_card.await_args.kwargs["card"]
    assert card["header"]["title"]["content"] == "OpenSRE"


@pytest.mark.asyncio
async def test_message_without_chat_id_is_ignored():
    runtime = make_runtime()
    event = make_receive_event(text="hi", chat_type="p2p", chat_id="")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_mention_only_message_is_ignored():
    runtime = make_runtime()
    event = make_receive_event(
        text="@_user_1", chat_type="group", mentions=[make_mention(BOT_OPEN_ID)]
    )
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)
    run.assert_not_awaited()


# ----------------------------------------------------------------- mid-run queue


@pytest.mark.asyncio
async def test_mid_run_message_is_queued_not_investigated():
    runtime = make_runtime()
    runtime.active_investigations.add(THREAD_ID)
    event = make_receive_event(text="also check redis", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)

    runtime.agent_client.queue_message.assert_awaited_once_with(
        thread_id=THREAD_ID, text="also check redis"
    )
    run.assert_not_awaited()
    assert runtime.lark.send_text.await_args.kwargs["text"] == QUEUED_TEXT


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 409])
async def test_queue_404_and_409_start_a_follow_up_run(status):
    """No in-process session / just finished — reuse the thread, tell the user nothing."""
    runtime = make_runtime()
    runtime.active_investigations.add(THREAD_ID)
    runtime.agent_client.queue_message = AsyncMock(
        side_effect=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=status, message="x"
        )
    )
    event = make_receive_event(text="follow up", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)

    run.assert_awaited_once()
    assert run.await_args.kwargs["thread_id"] == THREAD_ID
    runtime.lark.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_hard_failure_tells_the_user_and_stops():
    runtime = make_runtime()
    runtime.active_investigations.add(THREAD_ID)
    runtime.agent_client.queue_message = AsyncMock(
        side_effect=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=500, message="boom"
        )
    )
    event = make_receive_event(text="follow up", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)

    run.assert_not_awaited()
    assert runtime.lark.send_text.await_args.kwargs["text"] == QUEUE_FAILED_TEXT


@pytest.mark.asyncio
async def test_queue_unexpected_error_tells_the_user_and_stops():
    runtime = make_runtime()
    runtime.active_investigations.add(THREAD_ID)
    runtime.agent_client.queue_message = AsyncMock(side_effect=RuntimeError("nope"))
    event = make_receive_event(text="follow up", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        await handle_message(event, runtime=runtime)
    run.assert_not_awaited()
    assert runtime.lark.send_text.await_args.kwargs["text"] == QUEUE_FAILED_TEXT


@pytest.mark.asyncio
async def test_investigation_transport_failure_tells_the_user():
    """sre-agent unreachable: the progress card must not hang unanswered."""
    runtime = make_runtime()
    event = make_receive_event(text="check redis", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        run.side_effect = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=503, message="unavailable"
        )
        await handle_message(event, runtime=runtime)
    assert runtime.lark.send_text.await_args.kwargs["text"] == INVESTIGATION_FAILED_TEXT


# -------------------------------------------------------------------- hooks


@pytest.mark.asyncio
async def test_hooks_stream_update_patches_the_progress_card():
    runtime = make_runtime()
    hooks = FeishuHooks(
        lark=runtime.lark, chat_id=CHAT_ID, progress_message_id="om_progress"
    )
    await hooks.stream_update("**OpenSRE — Investigating…**")
    message_id, card = runtime.lark.patch_card.await_args.args
    assert message_id == "om_progress"
    assert "Investigating" in card["elements"][0]["text"]["content"]


@pytest.mark.asyncio
async def test_hooks_stream_update_without_a_card_is_a_noop():
    runtime = make_runtime()
    hooks = FeishuHooks(lark=runtime.lark, chat_id=CHAT_ID, progress_message_id=None)
    await hooks.stream_update("progress")
    runtime.lark.patch_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_hooks_stream_update_swallows_send_errors():
    """A chat-API hiccup must never abort the investigation stream."""
    runtime = make_runtime()
    runtime.lark.patch_card = AsyncMock(side_effect=RuntimeError("rate limited"))
    hooks = FeishuHooks(
        lark=runtime.lark, chat_id=CHAT_ID, progress_message_id="om_progress"
    )
    await hooks.stream_update("progress")  # must not raise


@pytest.mark.asyncio
async def test_hooks_update_card_swallows_errors():
    runtime = make_runtime()
    runtime.lark.patch_card = AsyncMock(side_effect=RuntimeError("gone"))
    hooks = FeishuHooks(lark=runtime.lark, chat_id=CHAT_ID)
    await hooks.update_card("om_q", {"elements": []})  # must not raise


@pytest.mark.asyncio
async def test_hooks_send_final_reply_posts_a_card():
    runtime = make_runtime()
    runtime.lark.send_card = AsyncMock(return_value="om_final")
    pending = {THREAD_ID: [QUESTION]}
    hooks = FeishuHooks(
        lark=runtime.lark,
        chat_id=CHAT_ID,
        thread_id=THREAD_ID,
        pending_questions=pending,
    )
    await hooks.send_final_reply(result_text="done", error=None, run_url=None)
    card = runtime.lark.send_card.await_args.kwargs["card"]
    assert "done" in json.dumps(card)
    # The run is over: stale question cards can no longer be submitted.
    assert THREAD_ID not in pending


# ------------------------------------------------------- question bookkeeping


def test_question_card_builder_records_pending_questions():
    runtime = make_runtime()
    build = runtime.question_card_builder()
    card = build(thread_id=THREAD_ID, questions=[QUESTION])
    assert runtime.pending_questions[THREAD_ID] == [QUESTION]
    assert card["elements"][0]["tag"] == "form"


# ------------------------------------------- real runner: timeout patches card


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    def iter_any(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()


class _FakeResponse:
    def __init__(self, body: str):
        self.content = _FakeContent([body.encode()])

    def raise_for_status(self) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, body: str):
        self._body = body

    def post(self, url, json=None, headers=None):
        return _FakeResponse(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


@pytest.mark.asyncio
async def test_question_timeout_patches_the_question_card_in_place(monkeypatch):
    """The core pops the question card id and asks the platform to update it."""
    body = _sse(
        {"type": "question", "data": {"questions": [QUESTION]}},
        {"type": "question_timeout", "data": {}},
        {"type": "result", "data": {"text": "Final report."}},
    )
    runtime = make_runtime()
    runtime.agent_client = AgentClient("http://agent")
    runtime.lark.send_card = AsyncMock(side_effect=["om_question", "om_final"])
    runtime.pending_questions[THREAD_ID] = [QUESTION]

    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kwargs: _FakeSession(body))

    hooks = FeishuHooks(
        lark=runtime.lark,
        chat_id=CHAT_ID,
        thread_id=THREAD_ID,
        progress_message_id="om_progress",
        pending_questions=runtime.pending_questions,
    )
    callbacks = InvestigationCallbacks(
        hooks,
        build_question_card=runtime.question_card_builder(),
        build_timeout_card=build_timeout_card,
        update_interval=UPDATE_INTERVAL_SECONDS,
    )
    await run_investigation(
        thread_id=THREAD_ID,
        prompt="investigate",
        callbacks=callbacks,
        client=runtime.agent_client,
        trigger_source=TRIGGER_SOURCE,
        active_investigations=runtime.active_investigations,
        question_card_ids=runtime.question_card_ids,
    )

    # The timeout replaces the question card instead of spawning a second card.
    assert runtime.lark.patch_card.await_count == 1
    patched_id, patched_card = runtime.lark.patch_card.await_args.args
    assert patched_id == "om_question"
    assert patched_card["header"]["title"]["content"] == "Question timed out"
    # The final reply is a separate card, and the run is no longer active.
    assert runtime.lark.send_card.await_count == 2
    assert THREAD_ID not in runtime.active_investigations
    assert THREAD_ID not in runtime.pending_questions
    assert runtime.question_card_ids == {}


# ------------------------------------------------------- SDK sync adapters


def test_schedule_without_a_running_loop_is_a_noop():
    async def noop():
        return None

    assert schedule(noop()) is None


@pytest.mark.asyncio
async def test_message_adapter_schedules_the_async_handler():
    runtime = make_runtime()
    handler = make_message_handler(runtime=runtime)
    event = make_receive_event(text="check redis", chat_type="p2p")
    with patch("bot_handlers.run_investigation", new_callable=AsyncMock) as run:
        assert handler(event) is None  # sync return value, as the SDK expects
        for _ in range(3):
            await asyncio.sleep(0)
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_card_adapter_returns_a_toast_and_submits_in_the_background():
    runtime = make_runtime()
    runtime.pending_questions[THREAD_ID] = [dict(QUESTION)]
    handler = make_card_action_handler(runtime=runtime)
    event = make_card_action_event(
        thread_id=THREAD_ID, form_value={"answer_0": "api", "comment_0": "since 09:00"}
    )

    response = handler(event)
    assert response.toast.type == "success"
    assert response.toast.content == "Answer submitted"

    for _ in range(3):
        await asyncio.sleep(0)
    runtime.agent_client.submit_answers.assert_awaited_once_with(
        thread_id=THREAD_ID, answers={"question_0": "api // since 09:00"}
    )
    assert THREAD_ID not in runtime.pending_questions


@pytest.mark.asyncio
async def test_card_adapter_rejects_a_stale_card():
    runtime = make_runtime()
    handler = make_card_action_handler(runtime=runtime)
    response = handler(make_card_action_event(thread_id="unknown-thread", form_value={}))
    assert response.toast.type == "error"
    await asyncio.sleep(0)
    runtime.agent_client.submit_answers.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_submission_keeps_questions_when_the_post_fails():
    runtime = make_runtime()
    runtime.pending_questions[THREAD_ID] = [dict(QUESTION)]
    runtime.agent_client.submit_answers = AsyncMock(side_effect=RuntimeError("500"))
    from card_actions import CardActionSubmission

    await submit_submission(
        CardActionSubmission(thread_id=THREAD_ID, answers={"question_0": "api"}),
        runtime=runtime,
    )
    # A failed POST must not consume the questions: the user can retry.
    assert THREAD_ID in runtime.pending_questions


def test_register_handlers_registers_both_event_types():
    runtime = make_runtime()
    builder = MagicMock()
    builder.register_p2_im_message_receive_v1.return_value = builder
    builder.register_p2_card_action_trigger.return_value = builder
    assert register_handlers(builder, runtime=runtime) is builder
    builder.register_p2_im_message_receive_v1.assert_called_once()
    builder.register_p2_card_action_trigger.assert_called_once()
