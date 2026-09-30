"""Card-action parsing and the /answer submit path."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from helpers import SYNTHETIC_USER_OPEN_ID, make_card_action_event

from card_actions import (
    INACTIVE_MESSAGE,
    SUBMITTED_MESSAGE,
    TOAST_ERROR,
    TOAST_SUCCESS,
    card_action_response,
    card_message_id_of,
    flatten_card_action_data,
    operator_open_id,
    parse_card_action,
    submit_card_action,
    toast,
)

THREAD_ID = "feishu-oc-synthetic-chat"

QUESTIONS = [
    {
        "header": "Which service?",
        "question": "Which service?",
        "options": [{"label": "api"}, {"label": "worker"}],
        "multi_select": False,
    },
    {
        "header": "Which regions?",
        "question": "Which regions?",
        "options": [{"label": "cn"}, {"label": "sg"}],
        "multi_select": True,
    },
]


def _pending() -> dict:
    return {THREAD_ID: [dict(question) for question in QUESTIONS]}


def test_flatten_merges_button_value_with_form_fields():
    event = make_card_action_event(
        thread_id=THREAD_ID, form_value={"answer_0": "api", "comment_0": "note"}
    )
    data = flatten_card_action_data(event)
    assert data["answer_thread_id"] == THREAD_ID
    assert data["answer_0"] == "api"
    assert data["comment_0"] == "note"


def test_flatten_lets_the_button_value_win_over_a_form_field():
    """answer_thread_id is static card data, never user input."""
    event = make_card_action_event(thread_id=THREAD_ID, form_value={})
    event["event"]["action"]["form_value"]["answer_thread_id"] = "spoofed"
    assert flatten_card_action_data(event)["answer_thread_id"] == THREAD_ID


def test_flatten_normalises_multi_select_lists_to_strings():
    event = make_card_action_event(
        thread_id=THREAD_ID, form_value={"answer_1": ["cn", "sg"]}
    )
    assert flatten_card_action_data(event)["answer_1"] == "cn, sg"


def test_flatten_tolerates_missing_action():
    assert flatten_card_action_data({"event": {}}) == {}
    assert flatten_card_action_data({}) == {}


def test_card_context_and_operator():
    event = make_card_action_event(message_id="om_42")
    assert card_message_id_of(event) == "om_42"
    assert operator_open_id(event) == SYNTHETIC_USER_OPEN_ID


def test_parse_card_action_shapes_answers_with_pending_questions():
    event = make_card_action_event(
        thread_id=THREAD_ID,
        form_value={"answer_0": "api", "comment_0": "since 09:00", "answer_1": ["cn", "sg"]},
    )
    submission = parse_card_action(event, _pending())
    assert submission is not None
    assert submission.thread_id == THREAD_ID
    assert submission.answers == {
        "question_0": "api // since 09:00",
        "question_1": "cn, sg",
    }


def test_parse_card_action_uses_only_the_submitted_answer():
    event = make_card_action_event(
        thread_id=THREAD_ID, form_value={"comment_1": "just a comment"}
    )
    submission = parse_card_action(event, _pending())
    assert submission is not None
    assert submission.answers["question_1"] == "just a comment"


def test_parse_card_action_rejects_missing_thread_id():
    event = make_card_action_event(thread_id=None, form_value={"answer_0": "api"})
    assert parse_card_action(event, _pending()) is None


def test_parse_card_action_rejects_a_stale_card():
    """A card whose questions are already answered or timed out is not live."""
    event = make_card_action_event(thread_id=THREAD_ID, form_value={"answer_0": "api"})
    assert parse_card_action(event, {}) is None
    assert parse_card_action(event, {"other-thread": []}) is None


def test_parse_card_action_accepts_a_thread_with_zero_questions():
    """A card with only a comment box is still a live submission."""
    event = make_card_action_event(thread_id=THREAD_ID, form_value={"comment_0": "hi"})
    submission = parse_card_action(event, {THREAD_ID: []})
    assert submission is not None
    assert submission.thread_id == THREAD_ID


@pytest.mark.asyncio
async def test_submit_card_action_posts_answers():
    submission = parse_card_action(
        make_card_action_event(thread_id=THREAD_ID, form_value={"answer_0": "api"}),
        _pending(),
    )
    assert submission is not None
    client = MagicMock()
    client.submit_answers = AsyncMock()
    await submit_card_action(submission, agent_client=client)
    client.submit_answers.assert_awaited_once_with(
        thread_id=THREAD_ID, answers={"question_0": "api", "question_1": ""}
    )


def test_toast_and_card_action_response():
    success = card_action_response(ok=True)
    assert success.toast.content == SUBMITTED_MESSAGE
    assert success.toast.type == TOAST_SUCCESS

    failure = card_action_response(ok=False)
    assert failure.toast.content == INACTIVE_MESSAGE
    assert failure.toast.type == TOAST_ERROR

    assert toast("hi").toast.content == "hi"
