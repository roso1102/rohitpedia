"""Verify Task 4.6: article + backlinks in one transaction (manual run)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from workers.absorb import _sync_backlinks_for_article, _upsert_article


async def main() -> None:
    import asyncpg

    dsn = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgresql://")
    if "+asyncpg" in dsn:
        dsn = dsn.split("+asyncpg", 1)[0] + dsn.split("+asyncpg", 1)[1]
    user_id = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT set_config('app.current_tenant', $1, true)", user_id)
        async with conn.transaction():
            await _upsert_article(
                conn,
                user_id,
                "backlink-test",
                "Backlink test",
                "Links to [[turmeric]] only.\n\n`[[ignored]]`",
                {"test": True},
                json.dumps({"verify": "4.6"}),
            )
            n = await _sync_backlinks_for_article(
                conn, user_id, "backlink-test", "Links to [[turmeric]] only.\n\n`[[ignored]]`"
            )
        row = await conn.fetchrow(
            """
            SELECT from_slug, to_slug FROM backlinks
            WHERE user_id = $1::uuid AND from_slug = 'backlink-test'
            """,
            user_id,
        )
        assert row and row["to_slug"] == "turmeric", row
        assert n == 1, n
        print("ok: backlinks row", dict(row))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
