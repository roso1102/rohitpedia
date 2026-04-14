"""Spot-check latest absorbed article for facets and backlinks."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.getenv("DATABASE_URL", "")
    user_id = os.getenv("SPOTCHECK_USER_ID", "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb")
    target_slug = os.getenv("SPOTCHECK_SLUG", "").strip()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT set_config('app.current_tenant', $1, true)", user_id)
        if target_slug:
            row = await conn.fetchrow(
                """
                SELECT a.id, a.slug, a.title, a.facets, a.updated_at
                FROM articles a
                WHERE a.user_id = $1::uuid AND a.slug = $2
                LIMIT 1
                """,
                user_id,
                target_slug,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT a.id, a.slug, a.title, a.facets, a.updated_at
                FROM articles a
                WHERE a.user_id = $1::uuid
                ORDER BY a.updated_at DESC
                LIMIT 1
                """,
                user_id,
            )
        if not row:
            print("no articles found for user")
            return

        slug = str(row["slug"])
        backlinks = await conn.fetch(
            """
            SELECT to_slug
            FROM backlinks
            WHERE user_id = $1::uuid AND from_slug = $2
            ORDER BY to_slug
            """,
            user_id,
            slug,
        )
        print(
            {
                "slug": slug,
                "title": str(row["title"]),
                "facets": row["facets"],
                "backlink_count": len(backlinks),
                "backlinks": [str(b["to_slug"]) for b in backlinks],
                "updated_at": str(row["updated_at"]),
            }
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
