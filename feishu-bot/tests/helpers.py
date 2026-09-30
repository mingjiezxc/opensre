"""Synthetic Feishu payload factories.

The bot is not a member of any live group, so every group-chat path is exercised
over hand-built events that match the v2 payload shape the SDK and the webhook
deliver.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

SYNTHETIC_CHAT_ID = "oc_synthetic_chat"
SYNTHETIC_USER_OPEN_ID = "ou_synthetic_user"


def make_mention(open_id: str, key: str = "@_user_1", name: str = "OpenSRE") -> dict:
    """One ``message.mentions[]`` entry, as the API sends it."""
    return {"key": key, "id": {"open_id": open_id}, "name": name, "tenant_key": "t"}


def make_receive_event(
    *,
    text: Optional[str] = "hello",
    chat_type: str = "p2p",
    chat_id: str = SYNTHETIC_CHAT_ID,
    mentions: Iterable[dict] = (),
    sender_open_id: str = SYNTHETIC_USER_OPEN_ID,
    message_id: str = "om_synthetic_message",
    content: Any = None,
) -> dict:
    """A v2 ``im.message.receive_v1`` payload."""
    if content is None:
        content = json.dumps({"text": text if text is not None else ""})
    return {
        "schema": "2.0",
        "header": {
            "event_id": "ev_synthetic",
            "event_type": "im.message.receive_v1",
            "token": "verification-token",
            "app_id": "cli_synthetic",
            "tenant_key": "t",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": sender_open_id},
                "sender_type": "user",
                "tenant_key": "t",
            },
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "message_type": "text",
                "content": content,
                "mentions": list(mentions),
            },
        },
    }


def make_card_action_event(
    *,
    thread_id: Optional[str] = "feishu-oc-synthetic-chat",
    form_value: Optional[dict] = None,
    message_id: str = "om_question_card",
    operator_open_id: str = SYNTHETIC_USER_OPEN_ID,
) -> dict:
    """A ``card.action.trigger`` callback payload (form submit)."""
    value: dict = {}
    if thread_id is not None:
        value["answer_thread_id"] = thread_id
    return {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "token": "verification-token"},
        "event": {
            "operator": {"open_id": operator_open_id},
            "action": {"tag": "button", "value": value, "form_value": form_value or {}},
            "context": {"open_message_id": message_id, "open_chat_id": SYNTHETIC_CHAT_ID},
        },
    }
