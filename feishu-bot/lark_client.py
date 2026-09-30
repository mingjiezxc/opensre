# feishu-bot/lark_client.py
"""Thin async wrapper over the parts of the ``lark-oapi`` SDK that we use.

Why a wrapper:

* the generated client methods have synchronous **and** asynchronous twins
  (``create``/``acreate``, ``patch``/``apatch``, ``reply``/``areply``,
  ``get``/``aget``, ``internal``/``ainternal``); this service is asyncio-native,
  so only the ``a*`` forms are called and callers never block the event loop;
* every failure is reduced to "log it and return None/False", because the shared
  runner calls these from inside its stream loop and must not be interrupted by a
  chat-API hiccup;
* ``GET /open-apis/bot/v3/info`` is not modelled by the SDK (it only ships
  ``bot/v4/search``), so the startup identity lookup is done over HTTP here.

The request builders are module-level functions rather than inline calls: they
are the seam where this service touches the generated SDK, and exposing them lets
the test suite assert the exact request that would be sent without a live tenant.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp
import lark_oapi as lark
from lark_oapi.api.contact.v3 import GetUserRequest
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.core.const import FEISHU_DOMAIN
from lark_oapi.core.enum import LogLevel

logger = logging.getLogger(__name__)

MSG_TYPE_TEXT = "text"
MSG_TYPE_INTERACTIVE = "interactive"

RECEIVE_ID_TYPE_CHAT = "chat_id"
USER_ID_TYPE_OPEN_ID = "open_id"

BOT_INFO_PATH = "/open-apis/bot/v3/info"
TENANT_TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
MAX_DISPLAY_NAME_LENGTH = 128
REQUEST_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class BotInfo:
    """The bot's own identity, resolved at startup."""

    open_id: str
    name: str = ""
    activate_status: Optional[int] = None


def encode_content(payload: dict) -> str:
    """Compact JSON for a Feishu message body (UTF-8 safe)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def create_message_request(
    *,
    receive_id: str,
    receive_id_type: str,
    msg_type: str,
    content: str,
) -> CreateMessageRequest:
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(receive_id)
        .msg_type(msg_type)
        .content(content)
        .build()
    )
    return (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(body)
        .build()
    )


def patch_message_request(*, message_id: str, content: str) -> PatchMessageRequest:
    body = PatchMessageRequestBody.builder().content(content).build()
    return (
        PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
    )


def reply_message_request(
    *, message_id: str, msg_type: str, content: str
) -> ReplyMessageRequest:
    body = ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).build()
    return (
        ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
    )


def get_user_request(*, open_id: str) -> GetUserRequest:
    return (
        GetUserRequest.builder()
        .user_id(open_id)
        .user_id_type(USER_ID_TYPE_OPEN_ID)
        .build()
    )



class LarkClient:
    """Send/edit Feishu messages and resolve identities."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str = FEISHU_DOMAIN,
        log_level: LogLevel = LogLevel.INFO,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = (domain or FEISHU_DOMAIN).rstrip("/")
        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .domain(self._domain)
            .log_level(log_level)
            .build()
        )

    # ------------------------------------------------------------------ messages

    async def send_text(
        self, *, receive_id: str, text: str, receive_id_type: str = RECEIVE_ID_TYPE_CHAT
    ) -> Optional[str]:
        """Send a plain-text message; returns the message id (None on failure)."""
        return await self._create_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type=MSG_TYPE_TEXT,
            content=encode_content({"text": text}),
        )

    async def send_card(
        self, *, receive_id: str, card: dict, receive_id_type: str = RECEIVE_ID_TYPE_CHAT
    ) -> Optional[str]:
        """Send an interactive card; returns the message id (None on failure)."""
        return await self._create_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type=MSG_TYPE_INTERACTIVE,
            content=encode_content(card),
        )

    async def _create_message(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        msg_type: str,
        content: str,
    ) -> Optional[str]:
        request = create_message_request(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type=msg_type,
            content=content,
        )
        try:
            response = await self._client.im.v1.message.acreate(request)
        except Exception:
            logger.exception("feishu send failed (receive_id=%s)", receive_id)
            return None
        if not response.success():
            logger.warning(
                "feishu send rejected code=%s msg=%s log_id=%s",
                response.code,
                response.msg,
                response.get_log_id(),
            )
            return None
        message_id = getattr(response.data, "message_id", None)
        return str(message_id) if message_id else None

    async def patch_card(self, message_id: str, card: dict) -> bool:
        """Replace an interactive card in place (progress / question timeout)."""
        if not message_id:
            return False
        request = patch_message_request(
            message_id=message_id, content=encode_content(card)
        )
        try:
            response = await self._client.im.v1.message.apatch(request)
        except Exception:
            logger.exception("feishu patch failed (message_id=%s)", message_id)
            return False
        if not response.success():
            logger.warning(
                "feishu patch rejected code=%s msg=%s message_id=%s",
                response.code,
                response.msg,
                message_id,
            )
            return False
        return True

    async def reply(
        self, *, message_id: str, card: Optional[dict] = None, text: Optional[str] = None
    ) -> Optional[str]:
        """Reply to an existing message with a card or plain text."""
        if card is not None:
            msg_type, content = MSG_TYPE_INTERACTIVE, encode_content(card)
        else:
            msg_type, content = MSG_TYPE_TEXT, encode_content({"text": text or ""})
        request = reply_message_request(
            message_id=message_id, msg_type=msg_type, content=content
        )
        try:
            response = await self._client.im.v1.message.areply(request)
        except Exception:
            logger.exception("feishu reply failed (message_id=%s)", message_id)
            return None
        if not response.success():
            logger.warning(
                "feishu reply rejected code=%s msg=%s message_id=%s",
                response.code,
                response.msg,
                message_id,
            )
            return None
        reply_id = getattr(response.data, "message_id", None)
        return str(reply_id) if reply_id else None

    # ---------------------------------------------------------------- identities

    async def get_user_display_name(self, open_id: str) -> Optional[str]:
        """A human display name for ``open_id``, or None.

        Never falls back to returning the open_id: an id is not a name, and the
        Teams bot's ``activity_sender_name`` set that precedent. Callers omit
        ``trigger_actor`` when this returns None.
        """
        if not open_id:
            return None
        request = get_user_request(open_id=open_id)
        try:
            response = await self._client.contact.v3.user.aget(request)
        except Exception:
            logger.exception("feishu user lookup failed (open_id=%s)", open_id)
            return None
        if not response.success():
            logger.info(
                "feishu user lookup rejected code=%s msg=%s open_id=%s",
                response.code,
                response.msg,
                open_id,
            )
            return None
        user = getattr(response.data, "user", None)
        name = getattr(user, "name", None)
        if not isinstance(name, str) or not name.strip():
            return None
        return name.strip()[:MAX_DISPLAY_NAME_LENGTH]

    async def get_bot_info(self) -> Optional[BotInfo]:
        """Resolve the bot's own open_id via ``GET /open-apis/bot/v3/info``.

        The SDK has no generated binding for this endpoint, so the tenant token
        comes from the SDK's auth resource and the call is a plain aiohttp GET.
        """
        token = await self._tenant_access_token()
        if not token:
            return None
        url = f"{self._domain}{BOT_INFO_PATH}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    payload: Any = await resp.json()
                    status = resp.status
        except Exception:
            logger.exception("feishu bot info request failed")
            return None
        if status != 200:
            logger.warning("feishu bot info returned HTTP %s", status)
            return None
        info = self.parse_bot_info(payload)
        if info is None:
            logger.warning(
                "feishu bot info unusable code=%s msg=%s",
                payload.get("code"),
                payload.get("msg"),
            )
        return info

    @staticmethod
    def parse_bot_info(payload: Any) -> Optional[BotInfo]:
        """Interpret a ``bot/v3/info`` response body (pure, unit-testable).

        The endpoint's documented shape is
        ``{"code": 0, "bot": {"open_id", "app_name", "activate_status"}}``.
        """
        if not isinstance(payload, dict) or payload.get("code") not in (None, 0):
            return None
        bot = payload.get("bot") or {}
        open_id = bot.get("open_id")
        if not isinstance(open_id, str) or not open_id:
            return None
        return BotInfo(
            open_id=open_id,
            name=str(bot.get("app_name") or ""),
            activate_status=bot.get("activate_status"),
        )

    async def _tenant_access_token(self) -> Optional[str]:
        """Exchange app credentials for a tenant access token.

        Deliberately a plain HTTP POST rather than
        ``client.auth.v3.tenant_access_token.ainternal()``: the generated SDK
        call *does* build and send the right request (the server answers
        ``{"code":0,"msg":"ok","tenant_access_token":…}``), but it never
        populates ``response.data`` — the body is only reachable as an opaque
        ``RawResponse`` — so the token cannot be read from the SDK model at all.
        Verified against lark-oapi in the container: both ``internal()`` and
        ``ainternal()`` return ``code=0`` with ``data is None``.

        Same policy as the ``bot/v3/info`` lookup above: endpoints the SDK does
        not model properly are called over HTTP here.
        """
        url = f"{self._domain}{TENANT_TOKEN_PATH}"
        body = {"app_id": self._app_id, "app_secret": self._app_secret}
        try:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=body) as resp:
                    payload: Any = await resp.json()
                    status = resp.status
        except Exception:
            logger.exception("feishu tenant_access_token request failed")
            return None
        if status != 200:
            logger.warning("feishu tenant_access_token returned HTTP %s", status)
            return None
        if not isinstance(payload, dict) or payload.get("code") not in (None, 0):
            logger.warning(
                "feishu tenant_access_token rejected code=%s msg=%s",
                (payload or {}).get("code") if isinstance(payload, dict) else None,
                (payload or {}).get("msg") if isinstance(payload, dict) else None,
            )
            return None
        token = payload.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            logger.warning("feishu tenant_access_token response carried no token")
            return None
        return token
