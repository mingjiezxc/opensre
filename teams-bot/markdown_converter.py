"""Render Markdown as Adaptive Card body elements (Teams adapter).

The parse/structuring half moved to ``opensre_im_core.markdown_parser``, which
returns platform-agnostic ``IMBlock`` values. This module keeps the Teams
presentation layer: it turns those blocks into the exact Adaptive Card
TextBlocks the bot sent before the extraction.

Adaptive Card TextBlocks already render ``**bold**``/``*italic*``/links natively,
so inline markdown is left as-is inside each block's ``text`` field; only
block-level structure (headings, lists, tables, code fences) needs conversion.

On any parse/render failure, falls back to a single TextBlock with the raw,
unconverted text — never an empty or partial block list. Empty input is the
Teams-specific ``_No summary returned._`` placeholder (the shared parser returns
an empty list instead, because the placeholder is a presentation choice).
"""

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

NO_SUMMARY_PLACEHOLDER = "_No summary returned._"


def _text_block(text: str, **kwargs) -> dict:
    block = {"type": "TextBlock", "text": text, "wrap": True}
    block.update(kwargs)
    return block


def _flatten_table(header: list[str], rows: list[list[str]]) -> list:
    """Flatten a table into one TextBlock per row — Adaptive Cards have no
    Table element supported consistently across Teams clients, so rows render as
    plain 'col1 | col2' text (mirrors slack-bot/table_converter.py's flattening
    approach). The header row is bolded."""
    blocks = []
    if header:
        blocks.append(_text_block(f"**{' | '.join(header)}**"))
    for row in rows:
        blocks.append(_text_block(" | ".join(row)))
    return blocks


def _render_items(items: list[IMListItem], depth: int = 0) -> list:
    """One TextBlock per item; nested items are kept and indented.

    The pre-extraction converter only looked at an item's first ``block_text``
    child, so nested sub-lists (and extra paragraphs) were silently dropped;
    the shared parser now returns them as ``children`` and we render them.
    """
    indent = "  " * depth
    blocks = []
    for item in items:
        blocks.append(_text_block(f"{indent}{item.prefix()}{item.text}", spacing="None"))
        if item.children:
            blocks.extend(_render_items(item.children, depth + 1))
    return blocks


def _render_block(block: IMBlock) -> list:
    if block.kind == PARAGRAPH:
        return [_text_block(block.text)] if block.text else []

    if block.kind == HEADING:
        size = "Large" if block.level == 1 else "Medium" if block.level <= 3 else "Default"
        return [_text_block(block.text, weight="Bolder", size=size)]

    if block.kind == CODE:
        return [_text_block(block.text, fontType="Monospace")]

    if block.kind == LIST:
        return _render_items(block.items)

    if block.kind == TABLE:
        return _flatten_table(block.header, block.rows)

    if block.kind == RULE:
        return [_text_block("---")]

    # Unknown block kind (future parser addition): render its text if any.
    return [_text_block(block.text)] if block.text else []


def markdown_to_adaptive_blocks(text: str) -> list:
    """Convert Markdown text to a list of Adaptive Card body elements.

    On any parse/render failure, or if the input produces no blocks at
    all, falls back to a single TextBlock containing the original raw
    text — never an empty list, never a partial block list.
    """
    if not text:
        return [_text_block(NO_SUMMARY_PLACEHOLDER)]
    try:
        blocks: list = []
        for block in parse_markdown(text):
            blocks.extend(_render_block(block))
        return blocks if blocks else [_text_block(text)]
    except Exception:
        return [_text_block(text)]
