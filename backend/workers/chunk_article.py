"""Split article markdown into chunks for embedding (Task 5.1).

Covers Action Plan 5.1:
- chunk_article(body_md, *, doc_id, ...) -> list[Chunk]
- Split on ## and ### only (not inside fenced code blocks)
- Long sections: recursive split when > ~512 tokens, ~100-token overlap
- Minimum chunk size ~80 chars (merge small fragments)
- Chunk: index, header, text; meta: doc_id, section_id, parent_section_id, page_range, heading_path
- PDF section articles: optional page_range from JSON context; body is already per-section after absorb
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

# Approximate tokens without a tokenizer (English prose; conservative).
_CHARS_PER_TOKEN = 4
_SECTION_MAX_TOKENS = 512
_OVERLAP_TOKENS = 100
_SECTION_MAX_CHARS = _SECTION_MAX_TOKENS * _CHARS_PER_TOKEN
_OVERLAP_CHARS = _OVERLAP_TOKENS * _CHARS_PER_TOKEN
_MIN_CHUNK_CHARS = 80

_HEADER_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
_INTRO_PATH = ("__intro__",)


def _approx_tokens(s: str) -> int:
    return max(1, (len(s) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def _needs_split(s: str) -> bool:
    t = s.strip()
    if not t:
        return False
    return len(t) > _SECTION_MAX_CHARS or _approx_tokens(t) > _SECTION_MAX_TOKENS


def _stable_section_id(doc_id: str, path: tuple[str, ...]) -> str:
    raw = f"{doc_id}|{'|'.join(path)}".encode("utf-8")
    return "s" + hashlib.sha1(raw).hexdigest()[:16]


def _take_first_window(text: str) -> tuple[str, str]:
    """
    Take first chunk from text (recursive split step) with ~100-token overlap to remainder.
    Returns (first_piece, remainder) remainder may be empty.
    """
    t = text.strip()
    n = len(t)
    if not t:
        return "", ""

    end = min(_SECTION_MAX_CHARS, n)
    if end >= n:
        return t, ""

    window = t[:end]
    break_at = window.rfind("\n\n")
    if break_at < _MIN_CHUNK_CHARS:
        break_at = window.rfind("\n")
    if break_at < _MIN_CHUNK_CHARS:
        break_at = window.rfind(". ")
    if break_at < _MIN_CHUNK_CHARS:
        break_at = window.rfind(" ")
    if break_at < _MIN_CHUNK_CHARS:
        break_at = end

    first = t[:break_at].strip()
    if not first:
        first = t[:end].strip()
        break_at = end

    rest_start = break_at
    if rest_start < n:
        rest_start = max(0, break_at - _OVERLAP_CHARS)
    rest = t[rest_start:].strip()

    if rest == t.strip():
        # No progress (e.g. huge token); hard slice
        first = t[:end].strip()
        rest = t[max(0, end - _OVERLAP_CHARS) :].strip()

    return first, rest


def _split_long_text_recursive(text: str) -> list[str]:
    """Recursively split until each piece is under the token/char budget."""
    t = text.strip()
    if not t:
        return []
    if not _needs_split(t):
        return [t]

    first, rest = _take_first_window(t)
    out: list[str] = []
    if first:
        if _needs_split(first):
            out.extend(_split_long_text_recursive(first))
        else:
            out.append(first)
    if rest:
        if rest == t:
            # guarantee progress
            hard = t[:_SECTION_MAX_CHARS].strip()
            tail = t[max(0, _SECTION_MAX_CHARS - _OVERLAP_CHARS) :].strip()
            if hard:
                out.append(hard)
            if tail and tail != hard:
                out.extend(_split_long_text_recursive(tail))
        else:
            out.extend(_split_long_text_recursive(rest))
    return out


@dataclass
class Chunk:
    index: int
    header: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _merge_short_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Merge undersized tail pieces only within the same logical section (same section_id)."""
    out: list[Chunk] = []
    for c in chunks:
        if (
            out
            and len(c.text) < _MIN_CHUNK_CHARS
            and out[-1].meta.get("section_id") == c.meta.get("section_id")
        ):
            prev = out[-1]
            prev.text = (prev.text + "\n\n" + c.text).strip()
        else:
            out.append(c)
    for i, c in enumerate(out):
        c.index = i
    return out


def _normalize_page_range(
    raw: Any,
) -> list[int] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            a, b = int(raw[0]), int(raw[1])
            return [min(a, b), max(a, b)]
        except (TypeError, ValueError):
            return None
    if isinstance(raw, dict):
        try:
            a = int(raw.get("start", raw.get("page_start", 0)))
            b = int(raw.get("end", raw.get("page_end", 0)))
            if a <= 0 and b <= 0:
                return None
            return [min(a, b), max(a, b)]
        except (TypeError, ValueError):
            return None
    return None


def _page_range_from_context(context_json: str | None) -> list[int] | None:
    if not context_json or not str(context_json).strip():
        return None
    try:
        data = json.loads(context_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    pr = _normalize_page_range(data.get("page_range"))
    if pr:
        return pr
    if "page_start" in data or "page_end" in data:
        return _normalize_page_range(
            {"page_start": data.get("page_start"), "page_end": data.get("page_end")}
        )
    return None


def chunk_article(
    body_md: str,
    *,
    doc_id: str,
    page_range: list[int] | tuple[int, int] | None = None,
    context_json: str | None = None,
) -> list[Chunk]:
    """
    Split body_md on ## and ### (outside fenced code); split oversized sections recursively.

    ``doc_id`` should be the article UUID string for stable section_id hashes.

    ``page_range`` or ``context_json`` (with page_range / page_start+page_end) sets meta page_range
    for all chunks from this article (typical for a single PDF section article).
    """
    effective_pr = _normalize_page_range(page_range) or _page_range_from_context(context_json)

    raw = body_md or ""
    lines = raw.splitlines()

    sections: list[tuple[tuple[str, ...], str, str, str | None]] = []

    h2: str | None = None
    h3: str | None = None
    buf: list[str] = []
    in_fence = False

    def path_tuple() -> tuple[str, ...]:
        if h2 and h3:
            return (h2, h3)
        if h2:
            return (h2,)
        return tuple()

    def parent_for_current_section() -> str | None:
        if h3 and h2:
            return _stable_section_id(doc_id, (h2,))
        return None

    def flush() -> None:
        nonlocal buf
        body = "\n".join(buf).strip()
        buf = []
        if not body:
            return
        pt = path_tuple()
        sid_path = pt if pt else _INTRO_PATH
        sid = _stable_section_id(doc_id, sid_path)
        par = parent_for_current_section() if pt else None
        sections.append((pt, body, sid, par))

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            continue
        if not in_fence:
            m = _HEADER_RE.match(line)
            if m:
                flush()
                level = len(m.group(1))
                title = m.group(2).strip()
                if level == 2:
                    h2 = title
                    h3 = None
                else:
                    if not h2:
                        h2 = title
                        h3 = None
                    else:
                        h3 = title
                continue
        buf.append(line)

    flush()

    if not sections and raw.strip():
        sid = _stable_section_id(doc_id, _INTRO_PATH)
        sections.append((tuple(), raw.strip(), sid, None))

    chunks: list[Chunk] = []
    idx = 0

    for path_tuple_, body, section_id, parent_section_id in sections:
        if not body:
            continue
        header = " > ".join(path_tuple_) if path_tuple_ else ""
        pieces = _split_long_text_recursive(body)
        if not pieces:
            continue
        for piece in pieces:
            meta = {
                "doc_id": doc_id,
                "section_id": section_id,
                "parent_section_id": parent_section_id,
                "page_range": effective_pr,
                "heading_path": list(path_tuple_),
            }
            chunks.append(
                Chunk(
                    index=idx,
                    header=header,
                    text=piece,
                    meta=meta,
                )
            )
            idx += 1

    return _merge_short_chunks(chunks)


def contextual_embed_text(chunk: Chunk) -> str:
    """Prepend heading path for embedding (contextual retrieval)."""
    path = chunk.meta.get("heading_path") or []
    if not path:
        return chunk.text
    prefix = " > ".join(str(p) for p in path if p)
    return f"{prefix}\n\n{chunk.text}" if prefix else chunk.text
