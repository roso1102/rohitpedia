"""Enqueue an embed job for the latest article for a user."""

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

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT set_config('app.current_tenant', $1, true)", user_id)
        article_id = await conn.fetchval(
            """
            SELECT id::text
            FROM articles
            WHERE user_id = $1::uuid
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            user_id,
        )
        if not article_id:
            print("no articles found")
            return
        await conn.execute(
            """
            INSERT INTO pgboss.job (name, data)
            VALUES (
              'embed',
              jsonb_build_object(
                'user_id', $1::text,
                'article_id', $2::text
              )
            )
            """,
            user_id,
            str(article_id),
        )
        print("enqueued_embed_article_id:", str(article_id))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

