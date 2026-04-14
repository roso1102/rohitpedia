"""Manual verification for Task 4.5 — run: python scripts/verify_wikilinks_ast.py (from backend/)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.wikilinks import extract_wikilinks_ast

SAMPLE = """# Title [[h-ignored]]

Body [[p-kept]] and `[[code-inline-ignored]]`.

```text
[[fence-ignored]]
```

> Quote [[quote-kept]]

- List [[list-kept]]
"""

EXPECTED = {"p-kept", "quote-kept", "list-kept"}


def main() -> None:
    got = extract_wikilinks_ast(SAMPLE)
    assert got == ["p-kept", "quote-kept", "list-kept"], got
    assert set(got) == EXPECTED
    print("ok:", got)


if __name__ == "__main__":
    main()
