"""Runner tests for the shared core (platform-neutral hooks + AgentClient)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opensre_im_core.agent_client import SSE_CLIENT_TIMEOUT, AgentClient
from opensre_im_core.investigation_runner import (
    THREAD_ID_MAX_LENGTH,
    InvestigationCallbacks,
    run_investigation,
    sanitize_thread_id,
    shape_answers_from_card_data,
)


class FakeHooks:
    """Minimal PlatformHooks implementation capturing calls."""

    def __init__(self, *, cards=None, texts=None, updates=None, final=None):
        self.stream_update = AsyncMock()
        self.stream_close = AsyncMock()
        self.cards = cards if cards is not None else []
        self.texts = texts if texts is not None else []
        self.card_updates = updates if updates is not None else []
        self.finals = final if final is not None else []
        self._next_id = 0

    async def send_card(self, card):
        self.cards.append(card)
        self._next_id += 1
        return f"card-{self._next_id}"

    async def send_text(self, content):
        self.texts.append(content)

    async def update_card(self, card_id, card):
        self.card_updates.append((card_id, card))

    async def send_final_reply(self, *, result_text, error, run_url):
        # Presentation is the platform's job; record the raw inputs.
        self.finals.append(
            {"result_text": result_text, "error": error, "run_url": run_url}
        )


def _client(url="http://agent:8001", token=""):
    return AgentClient(url, auth_token=token)


def _patch_stream(sse_lines, *, fail_with=None):
    """Patch aiohttp.ClientSession so POST /investigate streams sse_lines."""
    sse_bytes = ("\n".join(sse_lines) + "\n").encode()

    async def _iter_any():
        if fail_with is not None:
            raise fail_with
        yield sse_bytes

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content.iter_any = _iter_any

    mock_post_ctx = AsyncMock()
    mock_post_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_post_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_post_ctx)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    return mock_session_ctx, mock_session


async def _run(
    sse_lines,
    *,
    hooks=None,
    build_question_card=None,
    build_timeout_card=None,
    web_ui_base_url="",
    trigger_actor=None,
    trigger_source="teams",
    update_interval=0,
    question_card_ids=None,
    fail_with=None,
    client=None,
):
    hooks = hooks or FakeHooks()
    callbacks = InvestigationCallbacks(
        hooks,
        build_question_card=build_question_card,
        build_timeout_card=build_timeout_card,
        update_interval=update_interval,
    )
    session_ctx, session = _patch_stream(sse_lines, fail_with=fail_with)
    with patch("aiohttp.ClientSession", return_value=session_ctx):
        await run_investigation(
            thread_id="teams-test",
            prompt="investigate latency",
            callbacks=callbacks,
            client=client or _client(),
            web_ui_base_url=web_ui_base_url,
            trigger_source=trigger_source,
            trigger_actor=trigger_actor,
            question_card_ids=question_card_ids
            if question_card_ids is not None
            else {},
            update_interval=update_interval,
        )
    return hooks, session


# --- sanitize_thread_id ------------------------------------------------------


def test_sanitize_thread_id_prefixes_and_scrubs():
    tid = sanitize_thread_id("19:abc@thread.tacv2", "teams")
    assert tid.startswith("teams-")
    assert "@" not in tid
    assert " " not in tid


def test_sanitize_thread_id_keeps_channel_messageid():
    raw = "19:00000000000000000000000000000000@thread.tacv2;messageid=1700000000000"
    tid = sanitize_thread_id(raw, "teams")
    assert tid.startswith("teams-")
    assert "1700000000000" in tid


def test_sanitize_thread_id_respects_server_limit():
    """sre-agent caps thread_id at 128 chars; the old 240-char body blew past it."""
    raw = "19:" + ("a" * 400) + "@thread.tacv2;messageid=1700000000000"
    for prefix in ("teams", "feishu"):
        tid = sanitize_thread_id(raw, prefix)
        assert len(tid) <= THREAD_ID_MAX_LENGTH, prefix
        assert tid.startswith(f"{prefix}-")


def test_sanitize_thread_id_handles_empty_and_separator_only_input():
    assert sanitize_thread_id("", "feishu") == "feishu-unknown"
    assert sanitize_thread_id("@:@", "feishu") == "feishu-unknown"


def test_sanitize_thread_id_is_stable_for_feishu_chat_ids():
    tid = sanitize_thread_id("oc_9f8e7d6c5b4a3210", "feishu")
    assert tid == "feishu-oc-9f8e7d6c5b4a3210"


# --- shape_answers ----------------------------------------------------------


def test_shape_answers_from_card_data():
    questions = [{"question": "Which env?", "options": [{"label": "prod"}]}]
    data = {
        "answer_thread_id": "teams-abc",
        "answer_0": "prod",
        "comment_0": "prod west",
    }
    thread_id, answers = shape_answers_from_card_data(data, questions)
    assert thread_id == "teams-abc"
    assert answers["question_0"] == "prod // prod west"


def test_shape_answers_prefers_choice_or_comment_alone():
    questions = [{"question": "a"}, {"question": "b"}]
    _, answers = shape_answers_from_card_data(
        {"answer_0": "prod", "comment_1": "just text"}, questions
    )
    assert answers["question_0"] == "prod"
    assert answers["question_1"] == "just text"


# --- timeout constant -------------------------------------------------------


def test_sse_timeout_outlives_aiohttp_default_and_agent_cap():
    # Default aiohttp total is 300s; agent wall-clock is 600s.
    assert SSE_CLIENT_TIMEOUT.total is not None
    assert SSE_CLIENT_TIMEOUT.total > 300
    assert SSE_CLIENT_TIMEOUT.total >= 900


# --- run_investigation ------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_carries_platform_trigger_source_and_actor():
    hooks, session = await _run(
        ['data: {"type": "result", "data": {"text": "done"}}'],
        trigger_actor="Jane Doe",
        trigger_source="feishu",
    )
    payload = session.post.call_args.kwargs["json"]
    assert payload["trigger_source"] == "feishu"
    assert payload["trigger_actor"] == "Jane Doe"
    assert payload["thread_id"] == "teams-test"
    assert payload["prompt"] == "investigate latency"


@pytest.mark.asyncio
async def test_trigger_actor_omitted_when_blank_and_capped_when_present():
    _, session = await _run(
        ['data: {"type": "result", "data": {"text": "done"}}'],
        trigger_actor="   ",
    )
    assert "trigger_actor" not in session.post.call_args.kwargs["json"]

    _, session = await _run(
        ['data: {"type": "result", "data": {"text": "done"}}'],
        trigger_actor="x" * 300,
    )
    assert len(session.post.call_args.kwargs["json"]["trigger_actor"]) == 128


@pytest.mark.asyncio
async def test_queued_message_continuation_delivers_only_last_result():
    """Backend emits result then continues for a queued message."""
    hooks, _ = await _run(
        [
            'data: {"type": "thought", "data": {"text": "Checking..."}}',
            'data: {"type": "result", "data": {"text": "First finding"}}',
            'data: {"type": "thought", "data": {"text": "Checking redis too..."}}',
            'data: {"type": "result", "data": {"text": "Final answer after queued"}}',
        ]
    )
    assert len(hooks.finals) == 1
    assert hooks.finals[0]["result_text"] == "Final answer after queued"
    assert hooks.finals[0]["error"] is None


@pytest.mark.asyncio
async def test_error_event_finishes_immediately_and_skips_ghost_events():
    hooks, _ = await _run(
        [
            'data: {"type": "error", "data": {"message": "agent timed out"}}',
            'data: {"type": "thought", "data": {"text": "ghost thought"}}',
        ]
    )
    assert hooks.stream_close.await_count >= 1
    assert len(hooks.finals) == 1
    assert hooks.finals[0]["error"] == "agent timed out"
    # The ghost thought after the terminal error must not be processed.
    assert all("ghost thought" not in u for u in hooks.stream_update.call_args_list)


@pytest.mark.asyncio
async def test_sse_read_timeout_sends_final_error_instead_of_going_silent():
    hooks, _ = await _run([], fail_with=TimeoutError("read timeout"))
    assert len(hooks.finals) == 1
    assert "Timed out waiting for the agent" in (hooks.finals[0]["error"] or "")


@pytest.mark.asyncio
async def test_progress_updates_include_run_link():
    hooks, _ = await _run(
        [
            'data: {"type": "run_started", "data": {"run_id": "run-abc"}}',
            'data: {"type": "thought", "data": {"text": "Checking pods"}}',
            'data: {"type": "result", "data": {"text": "done"}}',
        ],
        web_ui_base_url="https://opensre.example.com",
    )
    run_link = "[View in OpenSRE](https://opensre.example.com/team/agent-runs/run-abc)"
    sent = [c.args[0] for c in hooks.stream_update.call_args_list]
    assert any("Checking pods" in u and run_link in u for u in sent)


@pytest.mark.asyncio
async def test_run_started_immediate_progress_does_not_swallow_next_thought():
    hooks, _ = await _run(
        [
            'data: {"type": "run_started", "data": {"run_id": "run-abc"}}',
            'data: {"type": "thought", "data": {"text": "Checking pods"}}',
            'data: {"type": "result", "data": {"text": "done"}}',
        ],
        web_ui_base_url="https://opensre.example.com",
    )
    sent = [c.args[0] for c in hooks.stream_update.call_args_list]
    assert any("Starting investigation" in u for u in sent)
    assert any("Checking pods" in u for u in sent)


@pytest.mark.asyncio
async def test_final_reply_receives_run_url():
    hooks, _ = await _run(
        [
            'data: {"type": "run_started", "data": {"run_id": "run-xyz"}}',
            'data: {"type": "result", "data": {"text": "Root cause found"}}',
        ],
        web_ui_base_url="https://opensre.example.com",
    )
    assert hooks.finals[0]["run_url"] == (
        "https://opensre.example.com/team/agent-runs/run-xyz"
    )


@pytest.mark.asyncio
async def test_question_card_sent_and_its_id_used_for_timeout_update():
    ids: dict[str, str] = {}
    hooks, _ = await _run(
        [
            'data: {"type": "question", "data": {"questions": [{"question": "env?"}]}}',
            'data: {"type": "question_timeout", "data": {}}',
            'data: {"type": "result", "data": {"text": "continued"}}',
        ],
        build_question_card=lambda *, thread_id, questions: {"q": questions},
        build_timeout_card=lambda: {"timeout": True},
        question_card_ids=ids,
    )
    assert hooks.cards == [{"q": [{"question": "env?"}]}]
    assert hooks.card_updates == [("card-1", {"timeout": True})]
    # Consumed on timeout, so a later timeout cannot reuse a stale id.
    assert "teams-test" not in ids


@pytest.mark.asyncio
async def test_question_card_needs_platform_builder_and_id():
    """Without a builder the runner must not call send_card; ids stay untouched."""
    hooks, _ = await _run(
        ['data: {"type": "question", "data": {"questions": [{"question": "x"}]}}']
    )
    assert hooks.cards == []


@pytest.mark.asyncio
async def test_timeout_without_previous_card_id_is_a_noop():
    ids: dict[str, str] = {}
    hooks, _ = await _run(
        [
            'data: {"type": "question_timeout", "data": {}}',
            'data: {"type": "result", "data": {"text": "done"}}',
        ],
        build_question_card=lambda *, thread_id, questions: {"q": questions},
        build_timeout_card=lambda: {"timeout": True},
        question_card_ids=ids,
    )
    assert hooks.card_updates == []
    assert ids == {}


@pytest.mark.asyncio
async def test_stream_closed_drained_stream_without_result():
    hooks, _ = await _run(['data: {"type": "thought", "data": {"text": "hi"}}'])
    assert hooks.finals == []
    assert hooks.stream_close.await_count == 1


@pytest.mark.asyncio
async def test_active_investigations_tracked_and_released():
    tracker: set[str] = set()
    hooks = FakeHooks()
    callbacks = InvestigationCallbacks(hooks, update_interval=0)
    session_ctx, _ = _patch_stream(
        ['data: {"type": "result", "data": {"text": "done"}}']
    )
    with patch("aiohttp.ClientSession", return_value=session_ctx):
        await run_investigation(
            thread_id="t-track",
            prompt="p",
            callbacks=callbacks,
            client=_client(),
            trigger_source="teams",
            active_investigations=tracker,
            question_card_ids={},
        )
    assert tracker == set()
