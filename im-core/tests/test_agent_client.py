from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opensre_im_core.agent_client import AgentClient


def _client(**kw):
    return AgentClient("http://agent:8001/", **kw)


def test_base_url_is_normalized():
    assert _client().base_url == "http://agent:8001"


def test_headers_omitted_when_no_tokens():
    assert _client().headers() == {}
    assert _client().headers(accept_sse=True) == {"Accept": "text/event-stream"}


def test_headers_include_bearer_and_team_token():
    client = _client(auth_token="tok", team_token="team-abc")
    assert client.headers(accept_sse=True) == {
        "Accept": "text/event-stream",
        "Authorization": "Bearer tok",
        "X-OpenSRE-Team-Token": "team-abc",
    }


def test_payload_includes_trigger_source_and_caps_actor():
    client = _client()
    payload = client.investigate_payload(
        prompt="why is it slow",
        thread_id="feishu-abc",
        trigger_source="feishu",
        trigger_actor="  " + "x" * 200,
    )
    assert payload["prompt"] == "why is it slow"
    assert payload["thread_id"] == "feishu-abc"
    assert payload["trigger_source"] == "feishu"
    assert len(payload["trigger_actor"]) == 128


def test_payload_omits_blank_actor():
    payload = _client().investigate_payload(
        prompt="p", thread_id="t", trigger_source="teams", trigger_actor="   "
    )
    assert "trigger_actor" not in payload


@pytest.mark.asyncio
async def test_queue_message_posts_url_payload_and_headers():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value={"queued": True, "pending_count": 1})

    mock_post_ctx = AsyncMock()
    mock_post_ctx.__aenter__.return_value = mock_resp
    mock_post_ctx.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_post_ctx)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        result = await _client(auth_token="tok").queue_message(
            thread_id="teams-abc", text="also check redis"
        )

    url, kwargs = mock_session.post.call_args
    assert url[0] == "http://agent:8001/threads/teams-abc/queue-message"
    assert kwargs["json"] == {"text": "also check redis"}
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert result == {"queued": True, "pending_count": 1}


@pytest.mark.asyncio
async def test_submit_answers_posts_thread_and_answers():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()

    mock_post_ctx = AsyncMock()
    mock_post_ctx.__aenter__.return_value = mock_resp
    mock_post_ctx.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_post_ctx)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        await _client().submit_answers(
            thread_id="feishu-abc", answers={"question_0": "prod"}
        )

    url, kwargs = mock_session.post.call_args
    assert url[0] == "http://agent:8001/answer"
    assert kwargs["json"] == {
        "thread_id": "feishu-abc",
        "answers": {"question_0": "prod"},
    }


@pytest.mark.asyncio
async def test_stream_investigate_feeds_every_line_and_stops_on_request():
    lines_seen: list[str] = []

    async def _iter_any():
        yield b'data: {"type": "thought", "data": {"text": "a"}}\n'
        yield b'data: {"type": "result", "data": {"text": "b"}}\n'

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

    async def on_line(line: str) -> bool:
        lines_seen.append(line)
        return "result" in line  # stop on the result event

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        stopped = await _client().stream_investigate(payload={"prompt": "p"}, on_line=on_line)

    assert stopped is True
    assert any("thought" in line for line in lines_seen)
    assert any("result" in line for line in lines_seen)


@pytest.mark.asyncio
async def test_stream_investigate_returns_false_when_drained_without_stop():
    async def _iter_any():
        yield b'data: {"type": "thought", "data": {"text": "a"}}\n'

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

    async def on_line(line: str) -> bool:
        return False

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        stopped = await _client().stream_investigate(payload={}, on_line=on_line)

    assert stopped is False
