"""Create a test raw entry, enqueue absorb, run once, and verify outputs."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llm.wikilinks import extract_wikilinks_ast


USER_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
TEST_BODY = (
    "Test absorb entry for 4.6.\n\n"
    "This note links to [[Turmeric Benefits]] and [[Golden Milk]].\n"
    "Inline code `[[ignored-inline]]` should be ignored.\n"
)


async def _create_entry_and_enqueue(conn: asyncpg.Connection) -> str:
    await conn.execute("SELECT set_config('app.current_tenant', $1, true)", USER_ID)
    entry_id = await conn.fetchval(
        """
        INSERT INTO raw_entries (user_id, body, source_type, status)
        VALUES ($1::uuid, $2, 'text', 'processing')
        RETURNING id
        """,
        USER_ID,
        TEST_BODY,
    )
    await conn.execute(
        """
        INSERT INTO pgboss.job (name, data)
        VALUES (
          'absorb',
          jsonb_build_object(
            'user_id', $1::text,
            'entry_id', $2::text,
            'source_type', 'text'
          )
        )
        """,
        USER_ID,
        str(entry_id),
    )
    return str(entry_id)


async def _verify(conn: asyncpg.Connection, entry_id: str) -> dict[str, object]:
    await conn.execute("SELECT set_config('app.current_tenant', $1, true)", USER_ID)
    raw = await conn.fetchrow(
        """
        SELECT id, status, absorbed_into
        FROM raw_entries
        WHERE id = $1::uuid AND user_id = $2::uuid
        """,
        entry_id,
        USER_ID,
    )
    if not raw:
        return {"ok": False, "error": "raw_entry_missing", "entry_id": entry_id}
    slug = str(raw["absorbed_into"] or "")
    if not slug:
        return {"ok": False, "error": "absorb_not_completed", "raw": dict(raw)}

    article = await conn.fetchrow(
        """
        SELECT slug, title, facets, body_md
        FROM articles
        WHERE user_id = $1::uuid AND slug = $2
        """,
        USER_ID,
        slug,
    )
    backlinks = await conn.fetch(
        """
        SELECT to_slug
        FROM backlinks
        WHERE user_id = $1::uuid AND from_slug = $2
        ORDER BY to_slug
        """,
        USER_ID,
        slug,
    )
    body_md = str(article["body_md"] or "") if article else ""
    extracted_links = extract_wikilinks_ast(body_md) if body_md else []
    return {
        "ok": article is not None and raw["status"] == "absorbed",
        "entry_id": entry_id,
        "raw_status": str(raw["status"]),
        "slug": slug,
        "article_exists": article is not None,
        "facets": article["facets"] if article else None,
        "backlink_count": len(backlinks),
        "backlinks": [str(r["to_slug"]) for r in backlinks],
        "article_wikilinks_found": extracted_links,
        "body_preview": body_md[:220],
    }


async def main() -> None:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")

    conn = await asyncpg.connect(dsn)
    try:
        entry_id = await _create_entry_and_enqueue(conn)
        print(f"queued_entry_id={entry_id}")
    finally:
        await conn.close()

    # Run absorb worker once against queued test job.
    completed = subprocess.run(
        [sys.executable, "workers/runner.py", "--queue", "absorb", "--once"],
        cwd=str(root / "backend"),
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        print(json.dumps({"ok": False, "error": "worker_failed", "code": completed.returncode}))
        return

    conn = await asyncpg.connect(dsn)
    try:
        # Give DB a moment for transaction visibility in some environments.
        await asyncio.sleep(0.5)
        result = await _verify(conn, entry_id)
        print(json.dumps(result, ensure_ascii=True))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
