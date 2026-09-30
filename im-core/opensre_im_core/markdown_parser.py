"""Markdown → platform-agnostic block list.

Extracted from ``teams-bot/markdown_converter.py``. The old converter walked the
mistune AST and emitted Adaptive Card elements directly, which made the same
markdown impossible to reuse for any other platform. This module keeps only the
parse/structuring half and hands back :class:`IMBlock` values; each platform
renders those into its own card/rich-text shape.

Two behaviours were fixed while extracting:

* **Nested list items are no longer dropped.** The old ``list`` branch took only
  the ``block_text`` child and silently discarded nested sub-lists (and any
  second paragraph in an item). Items now collect all their own text and recurse
  into sub-lists.
* **Empty input is no longer defaulted here.** The ``_No summary returned._``
  placeholder is a presentation choice, so it belongs to the platform adapters;
  ``parse_markdown("")`` returns an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import mistune

_md_ast = mistune.create_markdown(renderer=None, plugins=["table", "strikethrough"])

# Block kinds emitted by the parser.
PARAGRAPH = "paragraph"
HEADING = "heading"
CODE = "code"
LIST = "list"
TABLE = "table"
RULE = "rule"


@dataclass
class IMListItem:
    text: str
    ordered: bool = False
    index: int = 1
    children: list["IMListItem"] = field(default_factory=list)

    def prefix(self) -> str:
        return f"{self.index}. " if self.ordered else "• "


@dataclass
class IMBlock:
    kind: str
    text: str = ""
    level: int = 0
    language: Optional[str] = None
    ordered: bool = False
    items: list[IMListItem] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def inline_markdown(children) -> str:
    """Re-serialize inline children back to Markdown text.

    Both Adaptive Cards and Lark rich-text elements render
    ``**bold**``/``*italic*``/`` `code` ``/``[text](url)`` natively, so these are
    reassembled rather than stripped or converted.
    """
    parts = []
    for token in children or []:
        ttype = token.get("type")
        if ttype == "text":
            parts.append(token.get("raw", ""))
        elif ttype == "strong":
            parts.append(f"**{inline_markdown(token.get('children'))}**")
        elif ttype == "emphasis":
            parts.append(f"*{inline_markdown(token.get('children'))}*")
        elif ttype == "codespan":
            parts.append(f"`{token.get('raw', '')}`")
        elif ttype == "link":
            url = token.get("attrs", {}).get("url", "")
            parts.append(f"[{inline_markdown(token.get('children'))}]({url})")
        elif ttype in ("linebreak", "softbreak"):
            parts.append(" ")
        elif token.get("children"):
            parts.append(inline_markdown(token.get("children")))
        else:
            parts.append(token.get("raw", ""))
    return "".join(parts)


def _item_text(item_children) -> str:
    """All direct text of one list item, in order (fixes dropped paragraphs)."""
    texts = []
    for child in item_children or []:
        if child.get("type") in ("block_text", "paragraph"):
            text = inline_markdown(child.get("children"))
            if text:
                texts.append(text)
        elif child.get("type") == "block_code":
            code = child.get("raw", "").rstrip("\n")
            if code:
                texts.append(code)
    return " ".join(texts).strip()


def _parse_items(token) -> list[IMListItem]:
    ordered = bool(token.get("attrs", {}).get("ordered", False))
    items: list[IMListItem] = []
    for index, item in enumerate(token.get("children", []), start=1):
        children = item.get("children", [])
        nested: list[IMListItem] = []
        for child in children:
            if child.get("type") == "list":
                nested.extend(_parse_items(child))
        items.append(
            IMListItem(
                text=_item_text(children),
                ordered=ordered,
                index=index,
                children=nested,
            )
        )
    return items


def _table_parts(token) -> tuple[list[str], list[list[str]]]:
    header: list[str] = []
    rows: list[list[str]] = []
    for section in token.get("children", []):
        stype = section.get("type")
        if stype == "table_head":
            header = [
                inline_markdown(c.get("children"))
                for c in section.get("children", [])
            ]
        elif stype == "table_body":
            for row in section.get("children", []):
                rows.append(
                    [inline_markdown(c.get("children")) for c in row.get("children", [])]
                )
    return header, rows


def _parse_block(token) -> list[IMBlock]:
    ttype = token.get("type")

    if ttype == "paragraph":
        text = inline_markdown(token.get("children"))
        return [IMBlock(kind=PARAGRAPH, text=text)] if text else []

    if ttype == "heading":
        level = token.get("attrs", {}).get("level", 1)
        text = inline_markdown(token.get("children"))
        return [IMBlock(kind=HEADING, text=text, level=level)] if text else []

    if ttype == "block_code":
        raw = token.get("raw", "").rstrip("\n")
        info = token.get("attrs", {}).get("info") or None
        return [IMBlock(kind=CODE, text=raw, language=info)]

    if ttype == "list":
        return [IMBlock(kind=LIST, items=_parse_items(token))]

    if ttype == "table":
        header, rows = _table_parts(token)
        return [IMBlock(kind=TABLE, header=header, rows=rows)]

    if ttype == "thematic_break":
        return [IMBlock(kind=RULE)]

    if ttype == "blank_line":
        return []

    if ttype == "block_quote":
        # Quote nesting is flattened — neither platform renders it distinctly
        # inside the cards these bots send.
        blocks: list[IMBlock] = []
        for child in token.get("children", []):
            if child.get("type") == "paragraph":
                text = inline_markdown(child.get("children"))
                if text:
                    blocks.append(IMBlock(kind=PARAGRAPH, text=text))
            else:
                blocks.extend(_parse_block(child))
        return blocks

    if token.get("children"):
        blocks = []
        for child in token.get("children", []):
            blocks.extend(_parse_block(child))
        return blocks

    raw = token.get("raw", "")
    return [IMBlock(kind=PARAGRAPH, text=raw)] if raw else []


def parse_markdown(text: str) -> list[IMBlock]:
    """Parse Markdown into platform-agnostic blocks.

    Never raises: a parser failure degrades to a single paragraph block holding
    the raw input so callers always have something to render.
    """
    if not text:
        return []
    try:
        blocks: list[IMBlock] = []
        for token in _md_ast(text):
            blocks.extend(_parse_block(token))
        return blocks
    except Exception:
        return [IMBlock(kind=PARAGRAPH, text=text)]
