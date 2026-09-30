"""Tests for the shared markdown → IMBlock parser.

Priority case: nested list items used to be silently dropped by the Teams-only
converter, which took only the first ``block_text`` child of each item.
"""

from opensre_im_core.markdown_parser import (
    CODE,
    HEADING,
    LIST,
    PARAGRAPH,
    RULE,
    TABLE,
    parse_markdown,
)


def _kinds(blocks):
    return [b.kind for b in blocks]


def test_empty_input_yields_no_blocks():
    assert parse_markdown("") == []
    assert parse_markdown(None) == []


def test_paragraph_inline_markdown_is_preserved():
    blocks = parse_markdown("Intro **bold** and `code` and [link](https://x).")
    assert _kinds(blocks) == [PARAGRAPH]
    assert blocks[0].text == "Intro **bold** and `code` and [link](https://x)."


def test_headings_carry_level():
    blocks = parse_markdown("# One\n\n### Three")
    assert _kinds(blocks) == [HEADING, HEADING]
    assert blocks[0].level == 1
    assert blocks[0].text == "One"
    assert blocks[1].level == 3


def test_blank_lines_are_dropped():
    blocks = parse_markdown("a\n\n\n\nb")
    assert _kinds(blocks) == [PARAGRAPH, PARAGRAPH]


def test_code_block_keeps_language_and_strips_trailing_newline():
    blocks = parse_markdown("```python\nprint(1)\n```")
    assert _kinds(blocks) == [CODE]
    assert blocks[0].language == "python"
    assert blocks[0].text == "print(1)"


def test_code_block_without_language():
    blocks = parse_markdown("```\nraw\n```")
    assert blocks[0].kind == CODE
    assert blocks[0].language is None


def test_unordered_list_items_and_prefix():
    blocks = parse_markdown("- alpha\n- beta")
    assert _kinds(blocks) == [LIST]
    items = blocks[0].items
    assert [i.text for i in items] == ["alpha", "beta"]
    assert items[0].ordered is False
    assert items[0].prefix() == "• "


def test_ordered_list_items_numbering_and_prefix():
    blocks = parse_markdown("1. first\n2. second")
    items = blocks[0].items
    assert [i.text for i in items] == ["first", "second"]
    assert items[0].ordered is True
    assert items[0].prefix() == "1. "
    assert items[1].prefix() == "2. "


def test_nested_list_items_are_kept_not_dropped():
    """Regression: the old converter returned only the first block_text."""
    blocks = parse_markdown("- top one\n  - nested a\n  - nested b\n- top two")
    items = blocks[0].items
    assert [i.text for i in items] == ["top one", "top two"]
    nested = items[0].children
    assert [c.text for c in nested] == ["nested a", "nested b"]
    assert items[1].children == []


def test_ordered_list_nested_inside_unordered_keeps_its_own_ordering():
    blocks = parse_markdown("- outer\n  1. one\n  2. two")
    nested = blocks[0].items[0].children
    assert [c.text for c in nested] == ["one", "two"]
    assert nested[0].ordered is True


def test_multi_paragraph_list_item_keeps_all_text():
    """A second paragraph in an item used to be discarded with the sub-list."""
    blocks = parse_markdown("- first para\n\n  second para\n- next")
    assert blocks[0].items[0].text == "first para second para"


def test_table_header_and_rows():
    blocks = parse_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
    assert _kinds(blocks) == [TABLE]
    assert blocks[0].header == ["a", "b"]
    assert blocks[0].rows == [["1", "2"], ["3", "4"]]


def test_blockquote_flattens_to_paragraphs():
    blocks = parse_markdown("> quoted text")
    assert _kinds(blocks) == [PARAGRAPH]
    assert blocks[0].text == "quoted text"


def test_thematic_break_becomes_rule():
    blocks = parse_markdown("a\n\n---\n\nb")
    assert _kinds(blocks) == [PARAGRAPH, RULE, PARAGRAPH]


def test_mixed_document_order_is_preserved():
    src = "# T\n\nintro\n\n- a\n- b\n\n```sh\nls\n```\n\n> q"
    assert _kinds(parse_markdown(src)) == [
        HEADING,
        PARAGRAPH,
        LIST,
        CODE,
        PARAGRAPH,
    ]


def test_long_realistic_report_renders_every_section():
    src = """## Root cause

The **checkout** service exhausted its connection pool.

1. Pool size 10
2. Requests 400/s

- Evidence
  - Grafana spike at 14:02
  - Deploy d-1234

```text
pool_exhausted=true
```

| metric | value |
|---|---|
| p99 | 4200ms |
"""
    blocks = parse_markdown(src)
    assert _kinds(blocks) == [HEADING, PARAGRAPH, LIST, LIST, CODE, TABLE]
    assert blocks[2].items[0].text == "Pool size 10"
    assert blocks[3].items[0].children[0].text == "Grafana spike at 14:02"
    assert blocks[4].text == "pool_exhausted=true"
    assert blocks[5].header == ["metric", "value"]
