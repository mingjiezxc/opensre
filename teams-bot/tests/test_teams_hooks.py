"""Teams-only coverage: the PlatformHooks adapter and sre-agent client wiring.

The SSE runner, progress text, tool display, run links and markdown parsing moved
into opensre-im-core and are tested there; this module covers the Teams layer
that stays in this package (final-reply shape, question bookkeeping, ctx-callable
delegation, AgentClient construction).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opensre_im_core.agent_client import AgentClient

from bot_handlers import (
    PROGRESS_UPDATE_INTERVAL_SECONDS,
    TeamsHooks,
    _agent_client,
    _build_question_card,
    queue_message,
    register_handlers,
    submit_answers,
)
from state import pending_questions


def test_teams_progress_throttle_stays_at_the_tuned_half_second():
    """The shared core default is 1.0s; Teams was tuned at 0.5s.

    Migration must be behaviour-neutral for Teams users, so the platform pins
    its own interval (Feishu pins a slower one) rather than inheriting 1.0s.
    """
    assert PROGRESS_UPDATE_INTERVAL_SECONDS == 0.5


def _hooks(*, plain_text_final: bool, thread_id: str = "teams-test"):
    """Build TeamsHooks plus the ctx-facing mocks it wraps."""
    mocks = SimpleNamespace(
        stream_update=AsyncMock(),
        stream_close=AsyncMock(),
        send_card=AsyncMock(return_value="act-1"),
        send_text=AsyncMock(),
        update_card=AsyncMock(),
    )
    hooks = TeamsHooks(
        thread_id=thread_id,
        stream_update=mocks.stream_update,
        stream_close=mocks.stream_close,
        send_card=mocks.send_card,
        send_text=mocks.send_text,
        update_card=mocks.update_card,
        plain_text_final=plain_text_final,
    )
    return hooks, mocks


@pytest.mark.asyncio
async def test_hooks_delegate_progress_and_cards_to_ctx_callables():
    hooks, mocks = _hooks(plain_text_final=False)

    await hooks.stream_update("Working…")
    await hooks.stream_close()
    assert await hooks.send_card({"type": "AdaptiveCard"}) == "act-1"
    await hooks.send_text("plain")
    await hooks.update_card("act-9", {"type": "AdaptiveCard"})

    mocks.stream_update.assert_awaited_once_with("Working…")
    mocks.stream_close.assert_awaited_once()
    mocks.send_card.assert_awaited_once_with({"type": "AdaptiveCard"})
    mocks.send_text.assert_awaited_once_with("plain")
    mocks.update_card.assert_awaited_once_with("act-9", {"type": "AdaptiveCard"})


@pytest.mark.asyncio
async def test_channel_final_reply_is_plain_text_with_run_link():
    hooks, mocks = _hooks(plain_text_final=True)

    await hooks.send_final_reply(
        result_text="All clear",
        error=None,
        run_url="https://opensre.example.com/team/agent-runs/run-1",
    )

    mocks.send_card.assert_not_awaited()
    mocks.send_text.assert_awaited_once()
    body = mocks.send_text.await_args.args[0]
    assert "All clear" in body
    assert "[View in OpenSRE](https://opensre.example.com/team/agent-runs/run-1)" in body


@pytest.mark.asyncio
async def test_direct_chat_final_reply_is_adaptive_card():
    hooks, mocks = _hooks(plain_text_final=False)

    await hooks.send_final_reply(result_text="Root cause: OOM", error=None, run_url=None)

    mocks.send_text.assert_not_awaited()
    mocks.send_card.assert_awaited_once()
    card = mocks.send_card.await_args.args[0]
    assert card["type"] == "AdaptiveCard"
    assert "OOM" in str(card)


@pytest.mark.asyncio
async def test_direct_chat_error_is_single_text_block_card():
    hooks, mocks = _hooks(plain_text_final=False)

    await hooks.send_final_reply(result_text=None, error="Agent timed out", run_url=None)

    card = mocks.send_card.await_args.args[0]
    assert card["body"] == [
        {"type": "TextBlock", "text": "Agent timed out", "wrap": True}
    ]


def test_question_card_override_records_questions_for_card_action():
    questions = [
        {"header": "Env", "question": "Which env?", "options": [{"label": "prod"}]}
    ]
    try:
        card = _build_question_card(thread_id="teams-abc", questions=questions)

        assert pending_questions["teams-abc"] == questions
        assert card["type"] == "AdaptiveCard"
        assert any(a.get("type") == "Action.Execute" for a in card["actions"])
    finally:
        pending_questions.pop("teams-abc", None)


def test_timeout_override_drops_the_timed_out_questions():
    hooks, _ = _hooks(plain_text_final=False, thread_id="teams-abc")
    pending_questions["teams-abc"] = [{"question": "Which env?"}]

    card = hooks.timeout_card_override()

    assert "teams-abc" not in pending_questions
    assert "Question timed out" in str(card)


def test_agent_client_is_built_from_config_url_and_tokens():
    cfg = MagicMock()
    cfg.SRE_AGENT_URL = "http://agent:8001/"
    cfg.INVESTIGATE_AUTH_TOKEN = "tok"
    cfg.OPENSRE_TEAM_TOKEN = "team-tok"

    client = _agent_client(cfg)

    assert isinstance(client, AgentClient)
    assert client.base_url == "http://agent:8001"
    assert client.headers(accept_sse=True) == {
        "Accept": "text/event-stream",
        "Authorization": "Bearer tok",
        "X-OpenSRE-Team-Token": "team-tok",
    }


@pytest.mark.asyncio
async def test_queue_message_delegates_to_agent_client(monkeypatch):
    fake = MagicMock()
    fake.queue_message = AsyncMock(return_value={"queued": True, "pending_count": 1})
    monkeypatch.setattr("bot_handlers._agent_client", lambda cfg=None: fake)

    result = await queue_message(thread_id="teams-abc", text="also check redis")

    assert result == {"queued": True, "pending_count": 1}
    fake.queue_message.assert_awaited_once_with(
        thread_id="teams-abc", text="also check redis"
    )


@pytest.mark.asyncio
async def test_submit_answers_delegates_to_agent_client(monkeypatch):
    fake = MagicMock()
    fake.submit_answers = AsyncMock()
    monkeypatch.setattr("bot_handlers._agent_client", lambda cfg=None: fake)

    await submit_answers(thread_id="teams-abc", answers={"question_0": "prod"})

    fake.submit_answers.assert_awaited_once_with(
        thread_id="teams-abc", answers={"question_0": "prod"}
    )


@pytest.mark.asyncio
async def test_submit_card_action_shapes_answers_from_pending_questions():
    on_submit_handler = None

    class FakeApp:
        def on_message(self, fn):
            return fn

        def on_card_action_execute(self, _verb):
            def decorator(fn):
                nonlocal on_submit_handler
                on_submit_handler = fn
                return fn

            return decorator

    register_handlers(FakeApp())
    assert on_submit_handler is not None

    pending_questions["teams-abc"] = [{"question": "Which env?"}]
    ctx = MagicMock()
    ctx.activity = MagicMock()
    ctx.activity.value = MagicMock(
        action=MagicMock(
            data={
                "answer_thread_id": "teams-abc",
                "answer_0": "prod",
                "comment_0": "prod west",
            }
        )
    )

    try:
        with patch("bot_handlers.submit_answers", new_callable=AsyncMock) as mock_submit:
            response = await on_submit_handler(ctx)
    finally:
        pending_questions.pop("teams-abc", None)

    mock_submit.assert_awaited_once_with(
        thread_id="teams-abc", answers={"question_0": "prod // prod west"}
    )
    assert response.status_code == 200
