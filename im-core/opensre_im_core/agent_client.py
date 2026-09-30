"""HTTP/SSE client for the sre-agent investigation API.

Split out of ``teams-bot/investigation_runner.py`` so the transport layer no
longer depends on a platform ``Config`` class — the base URL and auth headers
are passed in explicitly, which is what makes it shareable across platforms.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

import aiohttp

logger = logging.getLogger(__name__)

# aiohttp ClientSession defaults to total=300s. Live investigations are awaited
# in-handler, and a 5-minute cap used to cancel the handler (TimeoutError) after
# the ack and before the final card. The agent wall-clock cap is
# AGENT_TIMEOUT_SECONDS (600s) — keep SSE total above that plus buffer.
SSE_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=900, sock_connect=30)
DEFAULT_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=30, sock_connect=15)


class AgentClientError(RuntimeError):
    """Raised when sre-agent returns a non-2xx response."""


class AgentClient:
    """Thin async client over sre-agent's /investigate, /answer, queue-message."""

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: str = "",
        team_token: str = "",
        connect_timeout: Optional[aiohttp.ClientTimeout] = None,
        stream_timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.auth_token = auth_token or ""
        self.team_token = team_token or ""
        self.connect_timeout = connect_timeout or DEFAULT_CLIENT_TIMEOUT
        self.stream_timeout = stream_timeout or SSE_CLIENT_TIMEOUT

    def headers(self, *, accept_sse: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if accept_sse:
            headers["Accept"] = "text/event-stream"
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if self.team_token:
            headers["X-OpenSRE-Team-Token"] = self.team_token
        return headers

    def investigate_payload(
        self,
        *,
        prompt: str,
        thread_id: str,
        trigger_source: str,
        trigger_actor: Optional[str] = None,
    ) -> dict:
        payload: dict = {
            "prompt": prompt,
            "thread_id": thread_id,
            "trigger_source": trigger_source,
        }
        actor = (trigger_actor or "").strip()
        if actor:
            payload["trigger_actor"] = actor[:128]
        return payload

    async def queue_message(self, *, thread_id: str, text: str) -> dict:
        url = f"{self.base_url}/threads/{thread_id}/queue-message"
        async with aiohttp.ClientSession(timeout=self.connect_timeout) as session:
            async with session.post(
                url, json={"text": text}, headers=self.headers()
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def submit_answers(self, *, thread_id: str, answers: dict[str, str]) -> None:
        url = f"{self.base_url}/answer"
        payload = {"thread_id": thread_id, "answers": answers}
        async with aiohttp.ClientSession(timeout=self.connect_timeout) as session:
            async with session.post(
                url, json=payload, headers=self.headers()
            ) as resp:
                resp.raise_for_status()

    async def stream_investigate(
        self,
        *,
        payload: dict,
        on_line,
    ) -> bool:
        """POST /investigate and feed each SSE line to ``on_line``.

        ``on_line`` is an async callable returning True to stop consuming (used
        when an error event is terminal). Returns True if the caller asked to
        stop, False if the stream drained naturally.
        """
        url = f"{self.base_url}/investigate"
        async with aiohttp.ClientSession(timeout=self.stream_timeout) as session:
            async with session.post(
                url, json=payload, headers=self.headers(accept_sse=True)
            ) as resp:
                resp.raise_for_status()
                buffer = ""
                async for raw in resp.content.iter_any():
                    buffer += raw.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if await on_line(line):
                            return True
                if buffer.strip() and await on_line(buffer):
                    return True
        return False

    async def iter_investigate(self, *, payload: dict) -> AsyncIterator[str]:
        """Yield decoded SSE lines from /investigate (straight-through streaming)."""
        url = f"{self.base_url}/investigate"
        async with aiohttp.ClientSession(timeout=self.stream_timeout) as session:
            async with session.post(
                url, json=payload, headers=self.headers(accept_sse=True)
            ) as resp:
                resp.raise_for_status()
                buffer = ""
                async for raw in resp.content.iter_any():
                    buffer += raw.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        yield line
                if buffer.strip():
                    yield buffer
