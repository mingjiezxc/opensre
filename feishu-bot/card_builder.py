# feishu-bot/card_builder.py
"""Feishu interactive-card JSON for welcome, progress, question, timeout, final.

Cards are plain dicts (the Feishu card schema is JSON, not modelled by
``lark-oapi``) and are sent with ``msg_type="interactive"``. Only ``lark_md``
rich text and the documented form components are used, so a card is never
rejected for an unknown tag.
"""

from __future__ import annotations

from typing import Any, Optional

from opensre_im_core.run_links import format_view_link_markdown

from card_converter import markdown_to_card_elements

# Card header colours (Feishu templates).
TEMPLATE_BLUE = "blue"
TEMPLATE_ORANGE = "orange"

# Form field names — frozen contract with im-core's shape_answers_from_card_data,
# which reads ``answer_{i}`` / ``comment_{i}`` and ``answer_thread_id``.
ANSWER_FIELD_TEMPLATE = "answer_{index}"
COMMENT_FIELD_TEMPLATE = "comment_{index}"
THREAD_FIELD = "answer_thread_id"

FORM_NAME = "opensre_question_form"
SUBMIT_BUTTON_NAME = "opensre_submit"

MULTI_SELECT_TAG = "multi_select_static"
SELECT_TAG = "select_static"
FORM_TAG = "form"
BUTTON_TAG = "button"
FORM_SUBMIT_ACTION = "form_submit"

QUESTION_CARD_TITLE = "OpenSRE needs your input"
TIMEOUT_CARD_TITLE = "Question timed out"
TIMEOUT_CARD_BODY = "The agent continued without an answer."


def _markdown(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _card(
    elements: list[dict],
    *,
    title: Optional[str] = None,
    template: str = TEMPLATE_BLUE,
) -> dict:
    card: dict[str, Any] = {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "elements": elements,
    }
    if title:
        card["header"] = {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        }
    return card


def build_welcome_card(bot_name: str = "OpenSRE") -> dict:
    """The `help` / `status` reply."""
    return _card(
        [
            _markdown("**AI incident investigation for your team.**"),
            _markdown(
                "@mention me in a group, or message me directly, and I will "
                "investigate. Reply in the same conversation to continue."
            ),
            _markdown("Commands: `help`, `status`"),
        ],
        title=bot_name,
    )


def build_progress_card(content: Optional[str]) -> dict:
    """The live progress card; edited in place via PATCH on every tick."""
    body = content or "_Starting investigation…_"
    return _card([_markdown(body)])


def answer_field(index: int) -> str:
    return ANSWER_FIELD_TEMPLATE.format(index=index)


def comment_field(index: int) -> str:
    return COMMENT_FIELD_TEMPLATE.format(index=index)


def _option(option: Any) -> dict:
    if isinstance(option, dict):
        label = option.get("label") or option.get("value") or str(option)
    else:
        label = str(option)
    return {"text": {"tag": "plain_text", "content": label}, "value": label}


def _option_hints(options: list[Any]) -> Optional[str]:
    """Option descriptions as markdown, so they survive the dropdown.

    The Teams card drops descriptions entirely; a Feishu select renders only the
    option label, so descriptions are listed above the picker. This keeps the
    submitted value a clean label.
    """
    lines: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        description = (option.get("description") or "").strip()
        if not description:
            continue
        label = option.get("label") or str(option)
        lines.append(f"- **{label}**: {description}")
    return "\n".join(lines) if lines else None


def _select_element(index: int, options: list[Any], *, multi: bool) -> dict:
    return {
        "tag": MULTI_SELECT_TAG if multi else SELECT_TAG,
        "name": answer_field(index),
        "placeholder": {"tag": "plain_text", "content": "Choose an option"},
        "options": [_option(option) for option in options],
    }


def _comment_element(index: int) -> dict:
    return {
        "tag": "input",
        "name": comment_field(index),
        "placeholder": {"tag": "plain_text", "content": "Optional comment"},
    }


def _submit_button(thread_id: str) -> dict:
    return {
        "tag": BUTTON_TAG,
        "name": SUBMIT_BUTTON_NAME,
        "action_type": FORM_SUBMIT_ACTION,
        "type": "primary",
        "text": {"tag": "plain_text", "content": "Submit"},
        # The button's value is delivered as action.value on the callback; it
        # carries the thread id the answers belong to. Field name matches
        # im-core's shape_answers_from_card_data contract.
        "value": {THREAD_FIELD: thread_id},
    }


def build_question_card(*, thread_id: str, questions: list[dict]) -> dict:
    """The clarifying-questions form.

    Maps the SDK's AskUserQuestion shape
    ``{header, question, options: [{label, description}], multi_select}`` onto a
    Feishu form: one select per question (``multi_select_static`` when
    ``multi_select``), one optional comment input, and a form-submit button.
    """
    form_elements: list[dict] = []
    for index, question in enumerate(questions or []):
        header = question.get("header") or f"Question {index + 1}"
        form_elements.append(_markdown(f"**{header}**"))
        body = question.get("question") or ""
        if body:
            form_elements.append(_markdown(body))

        options = question.get("options") or []
        if options:
            hints = _option_hints(options)
            if hints:
                form_elements.append(_markdown(hints))
            form_elements.append(
                _select_element(index, options, multi=bool(question.get("multi_select")))
            )
        form_elements.append(_comment_element(index))

    form_elements.append(_submit_button(thread_id))
    return _card(
        [{"tag": FORM_TAG, "name": FORM_NAME, "elements": form_elements}],
        title=QUESTION_CARD_TITLE,
    )


def build_timeout_card() -> dict:
    """Replaces the question card in place when the agent stops waiting."""
    return _card(
        [_markdown(TIMEOUT_CARD_BODY)],
        title=TIMEOUT_CARD_TITLE,
        template=TEMPLATE_ORANGE,
    )


def build_final_card(
    *,
    result_text: Optional[str],
    error: Optional[str],
    run_url: Optional[str] = None,
) -> dict:
    """The final report card — agent result or error, plus the run link.

    No boilerplate title, matching the Teams final card.
    """
    if error:
        elements = [_markdown(error)]
    else:
        # markdown_to_card_elements supplies NO_SUMMARY_PLACEHOLDER for empty input.
        elements = markdown_to_card_elements(result_text)
    if run_url:
        elements = [*elements, {"tag": "hr"}, _markdown(format_view_link_markdown(run_url))]
    return _card(elements)
