"""Apply a .sql file to DATABASE_URL using asyncpg (psql-free)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: py -3 scripts/apply_sql_file.py path/to/file.sql")

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("DATABASE_URL missing")

    sql_path = Path(sys.argv[1]).resolve()
    sql_text = sql_path.read_text(encoding="utf-8")
    if not sql_text.strip():
        raise SystemExit("sql file empty")

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql_text)
        print("ok: applied", str(sql_path))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

