"""Split article markdown into chunks for embedding (Task 5.1)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# ~4 chars per token for English prose (no tokenizer dependency).
_CHARS_PER_TOKEN = 4
_SECTION_MAX_TOKENS = 512
_OVERLAP_TOKENS = 100
_SECTION_MAX_CHARS = _SECTION_MAX_TOKENS * _CHARS_PER_TOKEN
_OVERLAP_CHARS = _OVERLAP_TOKENS * _CHARS_PER_TOKEN
_MIN_CHUNK_CHARS = 80

_HEADER_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
_INTRO_PATH = ("__intro__",)


def _stable_section_id(doc_id: str, path: tuple[str, ...]) -> str:
    raw = f"{doc_id}|{'|'.join(path)}".encode("utf-8")
    return "s" + hashlib.sha1(raw).hexdigest()[:16]


def _split_long_text(text: str) -> list[str]:
    """Split long section body with overlap; prefers paragraph boundaries."""
    t = text.strip()
    if not t:
        return []
    if len(t) <= _SECTION_MAX_CHARS:
        return [t]

    out: list[str] = []
    start = 0
    n = len(t)
    while start < n:
        end = min(start + _SECTION_MAX_CHARS, n)
        if end < n:
            window = t[start:end]
            break_at = window.rfind("\n\n")
            if break_at > _MIN_CHUNK_CHARS:
                end = start + break_at
            else:
                break_at = window.rfind("\n")
                if break_at > _MIN_CHUNK_CHARS:
                    end = start + break_at
        piece = t[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        start = max(start + 1, end - _OVERLAP_CHARS)
    return out


@dataclass
class Chunk:
    index: int
    header: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _merge_short_chunks(chunks: list[Chunk]) -> list[Chunk]:
    out: list[Chunk] = []
    for c in chunks:
        if out and len(c.text) < _MIN_CHUNK_CHARS:
            prev = out[-1]
            prev.text = (prev.text + "\n\n" + c.text).strip()
        else:
            out.append(c)
    for i, c in enumerate(out):
        c.index = i
    return out


def chunk_article(body_md: str, *, doc_id: str) -> list[Chunk]:
    """
    Split body_md on ## and ### headers; split oversized sections recursively.
    Each chunk includes hierarchical metadata for retrieval and provenance.
    """
    raw = body_md or ""
    lines = raw.splitlines()

    sections: list[tuple[tuple[str, ...], str, str, str | None]] = []

    h2: str | None = None
    h3: str | None = None
    buf: list[str] = []

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
        pieces = _split_long_text(body)
        if not pieces:
            continue
        for piece in pieces:
            meta = {
                "doc_id": doc_id,
                "section_id": section_id,
                "parent_section_id": parent_section_id,
                "page_range": None,
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
