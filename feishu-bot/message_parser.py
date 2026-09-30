# feishu-bot/message_parser.py
"""Pure parsing helpers for Feishu/Lark ``im.message.receive_v1`` events.

Every accessor accepts either the SDK model object or the raw JSON dict, because
the webhook delivery path and the tests both feed plain dicts. Routing logic
therefore never depends on the SDK's generated model classes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

# chat_type values that denote a direct (1:1) conversation.
P2P_CHAT_TYPES = frozenset({"p2p", "private", "1:1"})

# Feishu replaces an @mention in the message body with a placeholder token; the
# real identity travels in message.mentions[].key / .id.open_id.
_MENTION_PLACEHOLDER = re.compile(r"@_user_\d+ ?")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

COMMAND_HELP = "help"
COMMAND_STATUS = "status"
HELP_COMMANDS = frozenset({COMMAND_HELP, COMMAND_STATUS})


def _as_mapping(value: Any) -> dict:
    """Coerce a JSON string / dict / SDK model into a plain dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if hasattr(value, "__dict__"):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    return {}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Field access that works for both dicts and SDK model objects."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        value = obj.get(key, default)
    else:
        value = getattr(obj, key, default)
    return default if value is None else value


def event_message(event: Any) -> Any:
    """The ``event.event.message`` payload of a receive event."""
    return _get(_get(event, "event"), "message")


def event_sender(event: Any) -> Any:
    """The ``event.event.sender`` payload of a receive event."""
    return _get(_get(event, "event"), "sender")


def content_of(event: Any) -> Any:
    """``message.content`` of a receive event (still a JSON string)."""
    return _get(event_message(event), "content")


def extract_text(content: Any) -> str:
    """Message text from ``message.content``.

    ``content`` arrives as a JSON *string* (``{"text": "…"}``). Non-text
    messages (image, file, sticker, …) carry a different key and yield ``""``,
    which callers treat as "nothing to investigate".
    """
    data = _as_mapping(content)
    text = data.get("text")
    return text if isinstance(text, str) else ""


def mentions_of(event: Any) -> list:
    mentions = _get(event_message(event), "mentions", [])
    if isinstance(mentions, (list, tuple)):
        return list(mentions)
    return []


def mention_open_id(mention: Any) -> Optional[str]:
    """``mentions[].id.open_id`` for one mention entry."""
    open_id = _get(_get(mention, "id"), "open_id")
    return open_id if isinstance(open_id, str) and open_id else None


def is_bot_mentioned(event: Any, bot_open_id: Optional[str]) -> bool:
    """True when the bot's own ``open_id`` appears in ``message.mentions``.

    ``bot_open_id`` is resolved at startup (GET /open-apis/bot/v3/info) and is
    never hardcoded here; an unresolved id means "cannot tell", so group
    messages are ignored rather than answered unmentioned.
    """
    target = (bot_open_id or "").strip()
    if not target:
        return False
    return any(mention_open_id(mention) == target for mention in mentions_of(event))


def strip_mentions(text: str, mentions: Iterable[Any]) -> str:
    """Remove ``@_user_N`` placeholders (and known mention keys) from ``text``."""
    original = text or ""
    cleaned = original
    for mention in mentions or []:
        key = _get(mention, "key")
        if isinstance(key, str) and key:
            cleaned = cleaned.replace(key, "")
    cleaned = _MENTION_PLACEHOLDER.sub("", cleaned)
    if cleaned != original:
        # Removing "@_user_1 " leaves a double space behind; collapse the gap.
        cleaned = _MULTI_SPACE.sub(" ", cleaned)
    return cleaned.strip()


def strip_bot_name(text: str, bot_name: str) -> str:
    """Drop a literal (non-mention) occurrence of the bot's display name.

    Users often type the bot's name by hand; Feishu only rewrites real mentions
    into placeholders, so both forms need removing.
    """
    name = (bot_name or "").strip()
    if not name:
        return text or ""
    return _MULTI_SPACE.sub(" ", (text or "").replace(name, "")).strip()


def is_p2p(chat_type: Any) -> bool:
    """True for a direct (1:1) chat, which needs no @mention."""
    return str(chat_type or "").strip().lower() in P2P_CHAT_TYPES


def chat_id_of(event: Any) -> str:
    return str(_get(event_message(event), "chat_id", "") or "")


def chat_type_of(event: Any) -> str:
    return str(_get(event_message(event), "chat_type", "") or "")


def message_id_of(event: Any) -> str:
    return str(_get(event_message(event), "message_id", "") or "")


def sender_open_id(event: Any) -> str:
    """The sender's ``open_id``; "" when the event omits it."""
    return str(_get(_get(event_sender(event), "sender_id"), "open_id", "") or "")


def should_handle_message(*, text: str, mentioned: bool, chat_type: Any) -> bool:
    """Direct messages always qualify; group messages require an @mention."""
    if not (text or "").strip():
        return False
    return True if is_p2p(chat_type) else bool(mentioned)
