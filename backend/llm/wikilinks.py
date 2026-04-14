from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt

# MediaWiki-style: [[slug]] or [[slug|display]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]")


def _collect_from_text(text: str, out: list[str], seen: set[str]) -> None:
    for m in _WIKILINK_RE.finditer(text):
        slug = m.group(1).strip()
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)


def _walk_inline_tree(token: Any, out: list[str], seen: set[str]) -> None:
    if getattr(token, "type", None) == "code_inline":
        return
    if getattr(token, "type", None) == "text" and token.content:
        _collect_from_text(str(token.content), out, seen)
    for child in getattr(token, "children", None) or ():
        _walk_inline_tree(child, out, seen)


def extract_wikilinks_ast(markdown: str) -> list[str]:
    """
    Return unique wikilink targets in first-seen order.
    Skips: fenced / indented code blocks, inline code, heading lines (titles).
    Includes: paragraph, list, blockquote, etc.
    """
    out: list[str] = []
    seen: set[str] = set()
    tokens = MarkdownIt().parse(markdown or "")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        ttype = tok.type

        if ttype == "heading_open":
            i += 1
            while i < len(tokens) and tokens[i].type != "heading_close":
                i += 1
            i += 1
            continue

        if ttype in ("fence", "code_block"):
            i += 1
            continue

        if ttype == "inline":
            for child in tok.children or ():
                _walk_inline_tree(child, out, seen)

        i += 1

    return out
