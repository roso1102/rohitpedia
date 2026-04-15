"""Check pg-boss embed queue state (pending/failed/completed counts)."""

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
        pending = await conn.fetchval(
            "SELECT count(*)::bigint FROM pgboss.job WHERE name='embed' AND state IN ('created','retry')"
        )
        failed = await conn.fetchval(
            "SELECT count(*)::bigint FROM pgboss.job WHERE name='embed' AND state='failed'"
        )
        completed = await conn.fetchval(
            "SELECT count(*)::bigint FROM pgboss.job WHERE name='embed' AND state='completed'"
        )
        print(
            {
                "pending": int(pending or 0),
                "failed": int(failed or 0),
                "completed": int(completed or 0),
            }
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

