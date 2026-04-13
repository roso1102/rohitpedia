from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID

import asyncpg
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1]))
from workers import absorb, embed, ingest

load_dotenv("d:/rohitpedia/.env")

JobHandler = Callable[[dict[str, Any], asyncpg.Connection], Awaitable[dict[str, Any]]]
HANDLERS: dict[str, JobHandler] = {
    "ingest": ingest.handle,
    "absorb": absorb.handle,
    "embed": embed.handle,
}


def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is required.")
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _claim_job(conn: asyncpg.Connection, queue_name: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        WITH picked AS (
          SELECT name, id
          FROM pgboss.job
          WHERE name = $1
            AND state IN ('created', 'retry')
            AND start_after <= now()
          ORDER BY created_on
          LIMIT 1
          FOR UPDATE SKIP LOCKED
        )
        UPDATE pgboss.job j
        SET state = 'active',
            started_on = now(),
            retry_count = j.retry_count + 1
        FROM picked
        WHERE j.name = picked.name
          AND j.id = picked.id
        RETURNING j.name, j.id, j.data;
        """,
        queue_name,
    )


async def _complete_job(conn: asyncpg.Connection, queue_name: str, job_id: UUID, output: dict[str, Any]) -> None:
    await conn.execute(
        """
        UPDATE pgboss.job
        SET state = 'completed',
            completed_on = now(),
            output = $3::jsonb
        WHERE name = $1
          AND id = $2;
        """,
        queue_name,
        job_id,
        json.dumps(output),
    )


async def _fail_job(conn: asyncpg.Connection, queue_name: str, job_id: UUID, error_text: str) -> None:
    await conn.execute(
        """
        UPDATE pgboss.job
        SET state = 'failed',
            completed_on = now(),
            output = jsonb_build_object('error', $3::text)
        WHERE name = $1
          AND id = $2;
        """,
        queue_name,
        job_id,
        error_text[:1000],
    )


async def run_worker(queue_name: str, once: bool = False, poll_seconds: float = 1.0) -> None:
    if queue_name not in HANDLERS:
        raise RuntimeError(f"Unknown queue: {queue_name}")

    conn = await asyncpg.connect(_db_url())
    try:
        print(f"[{datetime.now(timezone.utc).isoformat()}] worker started queue={queue_name}")
        while True:
            async with conn.transaction():
                job = await _claim_job(conn, queue_name)

            if not job:
                if once:
                    print("No pending jobs.")
                    return
                await asyncio.sleep(poll_seconds)
                continue

            job_id = job["id"]
            payload_raw = job["data"]
            if isinstance(payload_raw, dict):
                payload = payload_raw
            elif isinstance(payload_raw, str):
                try:
                    parsed = json.loads(payload_raw)
                    payload = parsed if isinstance(parsed, dict) else {}
                except ValueError:
                    payload = {}
            else:
                payload = {}
            try:
                result = await HANDLERS[queue_name](payload, conn)
                await _complete_job(conn, queue_name, job_id, result)
                print(f"Completed {queue_name} job {job_id}")
            except Exception as exc:  # noqa: BLE001
                await _fail_job(conn, queue_name, job_id, str(exc))
                print(f"Failed {queue_name} job {job_id}: {exc}")

            if once:
                return
    finally:
        await conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pg-boss worker loop.")
    parser.add_argument("--queue", default="ingest", choices=["ingest", "absorb", "embed"])
    parser.add_argument("--once", action="store_true", help="Process at most one job then exit.")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_worker(queue_name=args.queue, once=args.once, poll_seconds=args.poll_seconds))
