"""Show latest failed embed job output (pg-boss)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.getenv("DATABASE_URL", "")
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT id::text, state, output
            FROM pgboss.job
            WHERE name = 'embed' AND state = 'failed'
            ORDER BY completed_on DESC
            LIMIT 1
            """
        )
        print(dict(row) if row else None)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

