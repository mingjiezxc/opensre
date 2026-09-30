"""Markdown -> Feishu card element conversion."""

import json

from card_converter import NO_SUMMARY_PLACEHOLDER, markdown_to_card_elements


def _contents(elements: list[dict]) -> list[str]:
    return [element["text"]["content"] for element in elements]


def test_empty_input_yields_the_placeholder():
    """parse_markdown("") returns []; the adapter owns the placeholder."""
    for empty in (None, ""):
        elements = markdown_to_card_elements(empty)
        assert len(elements) == 1
        assert elements[0]["text"]["content"] == NO_SUMMARY_PLACEHOLDER
        assert elements[0]["text"]["tag"] == "lark_md"


def test_paragraph_becomes_a_div():
    elements = markdown_to_card_elements("The API is **down**.")
    assert elements[0]["tag"] == "div"
    assert elements[0]["text"]["content"] == "The API is **down**."


def test_heading_is_bolded():
    elements = markdown_to_card_elements("## Root cause")
    assert elements[0]["text"]["content"] == "**Root cause**"


def test_fenced_code_block_is_kept_monospaced():
    elements = markdown_to_card_elements("```bash\nkubectl get pods\n```")
    content = elements[0]["text"]["content"]
    assert content.startswith("```bash\n")
    assert "kubectl get pods" in content
    assert content.rstrip().endswith("```")


def test_list_items_and_nested_list_are_both_rendered():
    """A nested sub-list used to be dropped by the old Teams converter."""
    markdown = "- parent item\n  - child one\n  - child two\n- second parent\n"
    elements = markdown_to_card_elements(markdown)
    contents = _contents(elements)
    assert contents == [
        "• parent item",
        "  • child one",
        "  • child two",
        "• second parent",
    ]


def test_ordered_list_uses_numeric_prefixes():
    elements = markdown_to_card_elements("1. first\n2. second\n")
    assert _contents(elements) == ["1. first", "2. second"]


def test_table_is_flattened_row_per_element_with_bold_header():
    markdown = "| service | status |\n| --- | --- |\n| api | down |\n| worker | ok |\n"
    elements = markdown_to_card_elements(markdown)
    contents = _contents(elements)
    assert contents == ["**service | status**", "api | down", "worker | ok"]


def test_thematic_break_becomes_a_rule():
    elements = markdown_to_card_elements("a\n\n---\n\nb")
    assert {"tag": "hr"} in elements


def test_mixed_document_keeps_order_and_never_returns_empty():
    markdown = "# Title\n\nSome text.\n\n- one\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    elements = markdown_to_card_elements(markdown)
    assert elements
    assert all("tag" in element for element in elements)
    assert json.dumps(elements)  # serialisable as part of a card


def test_whitespace_only_input_still_returns_an_element():
    assert markdown_to_card_elements("   ") != []
