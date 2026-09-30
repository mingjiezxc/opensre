"""Card shape tests, including the im-core answer contract round-trip."""

import json

from opensre_im_core.investigation_runner import shape_answers_from_card_data

from card_builder import (
    build_final_card,
    build_progress_card,
    build_question_card,
    build_timeout_card,
    build_welcome_card,
)

THREAD_ID = "feishu-oc-synthetic-chat"

QUESTIONS = [
    {
        "header": "Which service?",
        "question": "Which service is failing?",
        "options": [
            {"label": "api", "description": "the public API"},
            {"label": "worker", "description": "the queue worker"},
        ],
        "multi_select": False,
    },
    {
        "header": "Which regions?",
        "question": "Which regions are affected?",
        "options": [{"label": "cn"}, {"label": "sg"}],
        "multi_select": True,
    },
]


def _form(card: dict) -> dict:
    forms = [element for element in card["elements"] if element["tag"] == "form"]
    assert len(forms) == 1
    return forms[0]


def _named(form: dict) -> dict:
    return {element["name"]: element for element in form["elements"] if "name" in element}


def test_welcome_card_uses_bot_name_as_title():
    card = build_welcome_card("OpenSRE 测试机器人")
    assert card["header"]["title"]["content"] == "OpenSRE 测试机器人"
    body = json.dumps(card, ensure_ascii=False)
    assert "help" in body and "status" in body


def test_progress_card_renders_content_and_has_no_boilerplate_header():
    card = build_progress_card("**OpenSRE — Investigating…**\n\n- one")
    assert "header" not in card
    assert card["elements"][0]["text"]["tag"] == "lark_md"
    assert "Investigating" in card["elements"][0]["text"]["content"]


def test_progress_card_defaults_when_content_missing():
    card = build_progress_card(None)
    assert card["elements"][0]["text"]["content"] == "_Starting investigation…_"


def test_question_card_exposes_core_field_names():
    form = _form(build_question_card(thread_id=THREAD_ID, questions=QUESTIONS))
    named = _named(form)
    assert {"answer_0", "comment_0", "answer_1", "comment_1"} <= set(named)
    assert named["comment_0"]["tag"] == "input"
    assert named["comment_0"]["placeholder"]["content"] == "Optional comment"


def test_question_card_picks_multi_select_component_per_question():
    form = _form(build_question_card(thread_id=THREAD_ID, questions=QUESTIONS))
    named = _named(form)
    assert named["answer_0"]["tag"] == "select_static"
    assert named["answer_1"]["tag"] == "multi_select_static"
    assert [option["value"] for option in named["answer_1"]["options"]] == ["cn", "sg"]


def test_question_card_submit_button_carries_thread_id():
    form = _form(build_question_card(thread_id=THREAD_ID, questions=QUESTIONS))
    buttons = [e for e in form["elements"] if e["tag"] == "button"]
    assert len(buttons) == 1
    assert buttons[0]["action_type"] == "form_submit"
    assert buttons[0]["value"]["answer_thread_id"] == THREAD_ID


def test_question_card_keeps_option_descriptions():
    """The Teams card drops descriptions; Feishu must still show them."""
    body = json.dumps(build_question_card(thread_id=THREAD_ID, questions=QUESTIONS))
    assert "the public API" in body
    assert "the queue worker" in body


def test_question_card_option_values_stay_clean_labels():
    form = _form(build_question_card(thread_id=THREAD_ID, questions=QUESTIONS))
    options = _named(form)["answer_0"]["options"]
    assert [option["value"] for option in options] == ["api", "worker"]
    assert options[0]["text"]["content"] == "api"


def test_question_card_handles_question_without_options():
    form = _form(
        build_question_card(
            thread_id=THREAD_ID,
            questions=[{"header": "Anything else?", "question": "Notes?"}],
        )
    )
    named = _named(form)
    assert "answer_0" not in named
    assert "comment_0" in named


def test_question_form_data_round_trips_through_core_shape_answers():
    """The field names must satisfy im-core's frozen answer contract."""
    form = _form(build_question_card(thread_id=THREAD_ID, questions=QUESTIONS))
    names = {element["name"] for element in form["elements"] if "name" in element}
    # A Feishu form submit delivers exactly these keys, plus the button value.
    data = {
        "answer_thread_id": THREAD_ID,
        "answer_0": "api",
        "comment_0": "started at 09:00",
        "answer_1": ["cn", "sg"],
    }
    thread_id, answers = shape_answers_from_card_data(data, QUESTIONS)
    assert thread_id == THREAD_ID
    assert answers["question_0"] == "api // started at 09:00"
    # A raw multi-select list is not the str contract the POST expects; the
    # adapter normalises it (see test_card_actions).
    assert answers["question_1"]
    assert names >= {"answer_0", "comment_0", "answer_1", "comment_1"}


def test_timeout_card_announces_the_timeout():
    card = build_timeout_card()
    assert card["header"]["title"]["content"] == "Question timed out"
    assert "continued without an answer" in card["elements"][0]["text"]["content"]


def test_final_card_renders_markdown_result():
    card = build_final_card(result_text="## Root cause\n\n- OOM in `api`", error=None)
    body = json.dumps(card)
    assert "Root cause" in body
    assert "OOM" in body


def test_final_card_uses_placeholder_for_empty_result():
    card = build_final_card(result_text=None, error=None)
    assert card["elements"][0]["text"]["content"] == "_No summary returned._"


def test_final_card_prefers_error_and_appends_run_link():
    card = build_final_card(
        result_text="ignored",
        error="Agent timed out",
        run_url="https://console.example.com/team/agent-runs/run-1",
    )
    body = json.dumps(card)
    assert "Agent timed out" in body
    assert "ignored" not in body
    assert "https://console.example.com/team/agent-runs/run-1" in body
    assert "View in OpenSRE" in body


def test_final_card_without_run_url_has_no_link():
    body = json.dumps(build_final_card(result_text="done", error=None, run_url=None))
    assert "View in OpenSRE" not in body
