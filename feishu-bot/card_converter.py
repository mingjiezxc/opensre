# feishu-bot/card_converter.py
"""Render ``opensre_im_core.markdown_parser`` blocks into Feishu card elements.

The core owns Markdown parsing (and the nested-list fix that came with it); this
module only maps :class:`~opensre_im_core.markdown_parser.IMBlock` values onto
Feishu's JSON card vocabulary. Feishu's ``lark_md`` renders inline Markdown
natively, so inline markup is passed through verbatim — only block structure
needs translating.

Two deliberate differences from the Teams adapter:

* the empty-input placeholder is added here, because the shared parser returns
  an empty list for empty input (presentation is the adapter's job);
* a table keeps a separate row per element instead of one TextBlock per row, so
  Feishu can wrap wide cells without reflowing the whole table.
"""

from __future__ import annotations

from typing import Any, Optional

from opensre_im_core.markdown_parser import (
    CODE,
    HEADING,
    LIST,
    PARAGRAPH,
    RULE,
    TABLE,
    IMBlock,
    IMListItem,
    parse_markdown,
)

# Feishu renders ``{"tag": "lark_md"}`` content as rich text (bold, italic,
# strikethrough, links, inline code, code fences) inside a ``div``.
TEXT_TAG = "lark_md"

NO_SUMMARY_PLACEHOLDER = "_No summary returned._"

# Nested list items are indented two spaces per level inside the markdown body.
_INDENT = "  "


def _div(content: str, **extra: Any) -> dict:
    element: dict[str, Any] = {
        "tag": "div",
        "text": {"tag": TEXT_TAG, "content": content},
    }
    element.update(extra)
    return element


def _render_items(items: list[IMListItem], depth: int = 0) -> list[dict]:
    """List items as one div each; sub-lists are indented, never dropped."""
    elements: list[dict] = []
    indent = _INDENT * depth
    for item in items:
        text = item.text or ""
        elements.append(_div(f"{indent}{item.prefix()}{text}"))
        if item.children:
            elements.extend(_render_items(item.children, depth + 1))
    return elements


def _render_table(block: IMBlock) -> list[dict]:
    """Flatten a table to one ``col1 | col2`` line per row.

    Feishu cards have no table element that renders consistently across clients,
    so rows become plain text (the same flattening the Teams adapter uses). The
    header keeps its emphasis by being bolded.
    """
    elements: list[dict] = []
    if block.header:
        elements.append(_div(f"**{' | '.join(block.header)}**"))
    for row in block.rows:
        elements.append(_div(" | ".join(row)))
    return elements


def _render_block(block: IMBlock) -> list[dict]:
    if block.kind == PARAGRAPH:
        return [_div(block.text)] if block.text else []

    if block.kind == HEADING:
        # lark_md has no heading syntax; bold stands in for <h1>/<h2>.
        return [_div(f"**{block.text}**")] if block.text else []

    if block.kind == CODE:
        body = block.text or ""
        language = block.language or ""
        # A fenced block inside lark_md is what Feishu renders as monospaced
        # code; an unsupported fence degrades to literal text, it never makes
        # the card invalid.
        return [_div(f"```{language}\n{body}\n```")]

    if block.kind == LIST:
        return _render_items(block.items)

    if block.kind == TABLE:
        return _render_table(block)

    if block.kind == RULE:
        return [{"tag": "hr"}]

    return [_div(block.text)] if block.text else []


def markdown_to_card_elements(text: Optional[str]) -> list[dict]:
    """Convert Markdown to a non-empty list of Feishu card elements.

    ``None``/empty input yields the "no summary" placeholder — the parser
    returns ``[]`` for empty input by design, so the adapter supplies it.
    """
    if not text:
        return [_div(NO_SUMMARY_PLACEHOLDER)]
    try:
        blocks = parse_markdown(text)
        elements: list[dict] = []
        for block in blocks:
            elements.extend(_render_block(block))
    except Exception:
        # Never return an empty body: a card with no elements is rejected.
        return [_div(text)]
    return elements or [_div(text)]
