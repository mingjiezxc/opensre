# feishu-bot/webhook_server.py
"""aiohttp endpoints for ``FEISHU_MODE=webhook``.

Two checks are mandatory whenever the matching secret is configured, because an
open webhook would let anyone on the internet inject messages into sre-agent:

* ``FEISHU_VERIFICATION_TOKEN`` — compared against the payload's token on every
  request;
* ``FEISHU_ENCRYPT_KEY`` — when Feishu sends an ``encrypt`` envelope the payload
  must decrypt (via the SDK's own ``AESCipher``, the same implementation its
  dispatcher uses).

The ``url_verification`` challenge is echoed verbatim after those checks.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from aiohttp import web
from lark_oapi.core import JSON as LarkJSON
from lark_oapi.core.utils import AESCipher

logger = logging.getLogger(__name__)

EVENT_PATH = "/webhook/event"
CARD_PATH = "/webhook/card"
HEALTH_PATH = "/health"

CHALLENGE_FIELD = "challenge"
ENCRYPT_FIELD = "encrypt"
TOKEN_FIELD = "token"
HEADER_FIELD = "header"


class WebhookAuthError(Exception):
    """The request failed an authentication check (token or key)."""


class WebhookPayloadError(Exception):
    """The request body could not be decoded into a JSON object."""


# aiohttp's typed application keys (avoids the NotAppKeyWarning on string keys).
DISPATCH_KEY = web.AppKey("dispatch", object)
VERIFICATION_TOKEN_KEY = web.AppKey("verification_token", str)
ENCRYPT_KEY = web.AppKey("encrypt_key", str)


def _token_from(payload: dict) -> str:
    """The verification token, from the v2 ``header.token`` or the v1 top level."""
    top_level = payload.get(TOKEN_FIELD)
    if isinstance(top_level, str) and top_level:
        return top_level
    header = payload.get(HEADER_FIELD)
    if isinstance(header, dict):
        header_token = header.get(TOKEN_FIELD)
        if isinstance(header_token, str):
            return header_token
    return ""


def decrypt_payload(raw: bytes, encrypt_key: str = "") -> dict:
    """Decode a webhook body, decrypting the ``encrypt`` envelope when present."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebhookPayloadError(f"body is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise WebhookPayloadError("body is not a JSON object")

    encrypted = body.get(ENCRYPT_FIELD)
    if not encrypted:
        return body
    if not encrypt_key:
        raise WebhookAuthError("payload is encrypted but FEISHU_ENCRYPT_KEY is not set")
    if not isinstance(encrypted, str):
        raise WebhookPayloadError("encrypt field is not a string")
    try:
        plaintext = AESCipher(encrypt_key).decrypt_str(encrypted)
    except Exception as exc:  # wrong key, tampered ciphertext, bad padding
        raise WebhookPayloadError(f"could not decrypt payload: {exc}") from exc
    try:
        decrypted = json.loads(plaintext)
    except ValueError as exc:
        raise WebhookPayloadError(f"decrypted payload is not JSON: {exc}") from exc
    if not isinstance(decrypted, dict):
        raise WebhookPayloadError("decrypted payload is not a JSON object")
    return decrypted


def decode_payload(
    raw: bytes, *, verification_token: str = "", encrypt_key: str = ""
) -> dict:
    """Decrypt (if needed) and authenticate one webhook request body."""
    payload = decrypt_payload(raw, encrypt_key)
    expected = (verification_token or "").strip()
    if expected and _token_from(payload) != expected:
        raise WebhookAuthError("verification token mismatch")
    return payload


def make_dispatcher(handler: Any) -> Callable[[dict], Optional[Any]]:
    """Adapt an SDK ``EventDispatcherHandler`` to ``dispatch(payload) -> result``.

    ``EventDispatcherHandler.do()`` takes a signed ``RawRequest`` and repeats the
    signature check and decryption that this module has already done explicitly.
    Routing the verified payload through the SDK's parse-and-dispatch entry point
    (the same one the long-connection client uses) keeps one event-type table and
    avoids re-implementing the v1/v2 envelope handling. It is an internal method,
    which is why the adaptation is isolated to this one function.
    """

    def dispatch(payload: dict) -> Optional[Any]:
        return handler._do_without_validation(json.dumps(payload).encode("utf-8"))

    return dispatch


async def _read_payload(request: web.Request) -> dict:
    raw = await request.read()
    try:
        return decode_payload(
            raw,
            verification_token=request.app.get(VERIFICATION_TOKEN_KEY, ""),
            encrypt_key=request.app.get(ENCRYPT_KEY, ""),
        )
    except WebhookAuthError as exc:
        logger.warning("feishu webhook rejected (%s): %s", request.path, exc)
        raise web.HTTPUnauthorized(text=str(exc)) from exc
    except WebhookPayloadError as exc:
        logger.warning("feishu webhook bad payload (%s): %s", request.path, exc)
        raise web.HTTPBadRequest(text=str(exc)) from exc


async def handle_event(request: web.Request) -> web.Response:
    """``POST /webhook/event`` — message events (and the URL challenge)."""
    payload = await _read_payload(request)
    challenge = payload.get(CHALLENGE_FIELD)
    if isinstance(challenge, str) and challenge:
        return web.json_response({CHALLENGE_FIELD: challenge})
    request.app[DISPATCH_KEY](payload)
    return web.json_response({"code": 0})


async def handle_card(request: web.Request) -> web.Response:
    """``POST /webhook/card`` — card action callbacks (toast response)."""
    payload = await _read_payload(request)
    challenge = payload.get(CHALLENGE_FIELD)
    if isinstance(challenge, str) and challenge:
        return web.json_response({CHALLENGE_FIELD: challenge})
    result = request.app[DISPATCH_KEY](payload)
    if result is None:
        return web.json_response({"code": 0})
    # marshal() already returns a JSON string, so it must not be re-encoded by
    # json_response (which would wrap it in a JSON string literal).
    return web.Response(
        text=LarkJSON.marshal(result), content_type="application/json"
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def build_app(
    *,
    dispatcher: Callable[[dict], Optional[Any]],
    verification_token: str = "",
    encrypt_key: str = "",
) -> web.Application:
    """Build the aiohttp app for webhook mode."""
    app = web.Application()
    app[DISPATCH_KEY] = dispatcher
    app[VERIFICATION_TOKEN_KEY] = verification_token or ""
    app[ENCRYPT_KEY] = encrypt_key or ""
    app.router.add_post(EVENT_PATH, handle_event)
    app.router.add_post(CARD_PATH, handle_card)
    app.router.add_get(HEALTH_PATH, handle_health)
    return app
