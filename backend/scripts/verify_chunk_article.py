"""Verify Action Plan 5.1 chunking behavior (run: py -3 scripts/verify_chunk_article.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workers.chunk_article import (  # noqa: E402
    Chunk,
    _approx_tokens,
    chunk_article,
    contextual_embed_text,
)

_DOC = "doc-test-uuid"


def _meta_keys(c: Chunk) -> set[str]:
    return set(c.meta.keys())


def main() -> None:
    required_meta = {"doc_id", "section_id", "parent_section_id", "page_range", "heading_path"}

    # 5.1: ## / ### splits
    md_headers = """Intro line.

## Alpha
Body alpha.

### Beta
Body beta.

## Gamma
Body gamma.
"""
    ch = chunk_article(md_headers, doc_id=_DOC)
    assert len(ch) >= 3, ch
    headers = {c.header for c in ch}
    assert "Alpha" in headers and "Alpha > Beta" in headers and "Gamma" in headers, headers

    # 5.1: ## inside fenced code must NOT start a new section
    md_fence = """## Real

Before fence.

```
## Fake Header
not a section
```

After.
"""
    ch2 = chunk_article(md_fence, doc_id=_DOC)
    assert all("Fake Header" not in (c.header or "") for c in ch2), ch2
    texts = "\n".join(c.text for c in ch2)
    assert "## Fake Header" in texts

    # 5.1: long section recursive split (~512 tokens max per piece)
    long_body = "## Sec\n" + ("word " * 2500)
    ch3 = chunk_article(long_body, doc_id=_DOC)
    assert len(ch3) >= 3, len(ch3)
    for c in ch3:
        assert _approx_tokens(c.text) <= 520, _approx_tokens(c.text)
        assert len(c.text) >= 80 or len(ch3) == 1

    # 5.1: Chunk shape + hierarchical meta
    for c in ch3:
        assert isinstance(c.index, int)
        assert isinstance(c.header, str)
        assert isinstance(c.text, str)
        assert required_meta <= _meta_keys(c)

    # parent_section_id on ### chunk from earlier test
    beta_chunks = [c for c in ch if c.header == "Alpha > Beta"]
    assert beta_chunks
    assert beta_chunks[0].meta.get("parent_section_id") is not None

    # 5.1: page_range from context_json (PDF section article pattern)
    ctx = '{"page_start": 3, "page_end": 5}'
    ch4 = chunk_article("## P\nHello.", doc_id=_DOC, context_json=ctx)
    assert ch4[0].meta.get("page_range") == [3, 5]

    # contextual embed prepends path
    c0 = chunk_article("## T\nHi.", doc_id=_DOC)[0]
    assert "T" in contextual_embed_text(c0)

    print("ok: verify_chunk_article 5.1 checks passed")


if __name__ == "__main__":
    main()
