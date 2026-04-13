import asyncio
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from workers.url_extract import extract_url_content


URLS = [
    ("normal", "https://example.com"),
    ("medium", "https://medium.com/@towardsdatascience/what-is-rag-3f6b5f4f4d58"),
    ("twitter_x", "https://x.com/elonmusk/status/1853967082405283846"),
    ("pdf", "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"),
    ("not_found", "https://example.com/this-page-should-404"),
]


async def main() -> None:
    rows = []
    for case_name, url in URLS:
        try:
            text, layer = await extract_url_content(url)
            rows.append(
                {
                    "case": case_name,
                    "url": url,
                    "layer": layer,
                    "length": len(text or ""),
                    "preview": (text or "")[:120].replace("\n", " "),
                }
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {
                    "case": case_name,
                    "url": url,
                    "layer": "error",
                    "length": 0,
                    "preview": str(exc)[:120],
                }
            )

    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
