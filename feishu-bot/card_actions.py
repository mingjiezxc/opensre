# feishu-bot/card_actions.py
"""Handle Feishu ``card.action.trigger`` callbacks (question-form submits).

The callback payload carries the form fields in ``event.action.form_value`` and
the button's static ``value`` in ``event.action.value``. Both are merged into one
flat mapping and handed to im-core's
:func:`~opensre_im_core.investigation_runner.shape_answers_from_card_data`, which
is the same code path the Teams bot uses — so the field-name contract lives in
exactly one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTriggerResponse,
)
from opensre_im_core.agent_client import AgentClient
from opensre_im_core.investigation_runner import shape_answers_from_card_data

logger = logging.getLogger(__name__)

TOAST_SUCCESS = "success"
TOAST_ERROR = "error"

SUBMITTED_MESSAGE = "Answer submitted"
INACTIVE_MESSAGE = "This question is no longer active"


@dataclass(frozen=True)
class CardActionSubmission:
    """Answers parsed from one card submit, ready to POST to /answer."""

    thread_id: str
    answers: dict[str, str]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        value = obj.get(key, default)
    else:
        value = getattr(obj, key, default)
    return default if value is None else value


def action_of(event: Any) -> Any:
    """``event.event.action`` of a card-action callback."""
    return _get(_get(event, "event"), "action")


def card_message_id_of(event: Any) -> str:
    """The card message the user interacted with (``event.event.context``)."""
    value = _get(_get(_get(event, "event"), "context"), "open_message_id")
    return str(value or "")


def operator_open_id(event: Any) -> str:
    value = _get(_get(_get(event, "event"), "operator"), "open_id")
    return str(value or "")


def _normalize(value: Any) -> str:
    """Coerce one form field to the ``str`` the answer contract expects.

    A Feishu ``multi_select_static`` submits a list; the shared
    ``shape_answers_from_card_data`` is typed for strings, and a raw list would
    otherwise end up in the POSTed answers as a Python repr.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def flatten_card_action_data(event: Any) -> dict:
    """Merge ``action.form_value`` with the submit button's ``action.value``.

    Form fields (``answer_{i}`` / ``comment_{i}``) arrive in ``form_value``; the
    button's ``value`` carries ``answer_thread_id``. The button value wins,
    because it is static card data rather than user-supplied input.
    """
    action = action_of(event)
    data: dict[str, Any] = {}
    form_value = _get(action, "form_value")
    if isinstance(form_value, Mapping):
        data.update(form_value)
    value = _get(action, "value")
    if isinstance(value, Mapping):
        data.update(value)
    return {key: _normalize(item) for key, item in data.items()}


def parse_card_action(
    event: Any, pending_questions: Mapping[str, list[dict]]
) -> Optional[CardActionSubmission]:
    """Map a callback onto answers for the thread's pending questions.

    Returns ``None`` when the callback carries no thread id (a stale or foreign
    card), which the caller turns into an error toast.
    """
    data = flatten_card_action_data(event)
    thread_id = str(data.get("answer_thread_id") or "")
    if not thread_id or thread_id not in pending_questions:
        # No thread id: a foreign/stale card. Not in pending_questions: the run
        # already answered or timed out, so the card is no longer live.
        return None
    questions = list(pending_questions.get(thread_id) or [])
    resolved_thread_id, answers = shape_answers_from_card_data(data, questions)
    if not resolved_thread_id:
        return None
    return CardActionSubmission(thread_id=resolved_thread_id, answers=answers)


async def submit_card_action(
    submission: CardActionSubmission, *, agent_client: AgentClient
) -> None:
    """POST the parsed answers to sre-agent's /answer."""
    await agent_client.submit_answers(
        thread_id=submission.thread_id, answers=submission.answers
    )


def toast(content: str, *, kind: str = TOAST_SUCCESS) -> P2CardActionTriggerResponse:
    """The immediate toast the callback must return."""
    return P2CardActionTriggerResponse({"toast": {"type": kind, "content": content}})


def card_action_response(*, ok: bool) -> P2CardActionTriggerResponse:
    return toast(
        SUBMITTED_MESSAGE if ok else INACTIVE_MESSAGE,
        kind=TOAST_SUCCESS if ok else TOAST_ERROR,
    )
