import asyncio
import os
import sys
import uuid
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv("d:/rohitpedia/.env")
sys.path.append(str(Path(__file__).resolve().parents[1]))
from api.main import app  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")


async def main() -> None:
    if not DATABASE_URL or not WEBHOOK_SECRET:
        raise RuntimeError("DATABASE_URL and TELEGRAM_WEBHOOK_SECRET are required in .env")

    db_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(db_url)
    try:
        tenant_id = str(uuid.uuid4())
        telegram_id = 909000123

        await conn.execute(
            """
            INSERT INTO users (id, telegram_id)
            VALUES ($1::uuid, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET id = EXCLUDED.id
            """,
            tenant_id,
            telegram_id,
        )

        with TestClient(app) as client:
            tenant_resp = client.get("/debug/tenant", cookies={"rp_user_id": tenant_id})
            tenant_resp.raise_for_status()
            resolved_tenant = tenant_resp.json().get("tenant")
            if resolved_tenant != tenant_id:
                raise RuntimeError(f"Tenant mismatch: expected {tenant_id}, got {resolved_tenant}")

            webhook_payload = {
                "message": {
                    "from": {"id": telegram_id},
                    "text": "task2 stability check https://example.com",
                }
            }
            webhook_resp = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
                json=webhook_payload,
            )
            webhook_resp.raise_for_status()

        raw_entry = await conn.fetchrow(
            """
            SELECT id::text AS id
            FROM raw_entries
            WHERE user_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT 1
            """,
            tenant_id,
        )
        if not raw_entry:
            raise RuntimeError("No raw_entries row created by webhook")

        queued_job = await conn.fetchrow(
            """
            SELECT id::text AS id
            FROM pgboss.job
            WHERE name = 'ingest'
              AND data->>'entry_id' = $1
            ORDER BY created_on DESC
            LIMIT 1
            """,
            raw_entry["id"],
        )
        if not queued_job:
            raise RuntimeError("No pg-boss ingest job found for created raw entry")

        print("Task 2 verification passed.")
        print(f"tenant={tenant_id}")
        print(f"entry_id={raw_entry['id']}")
        print(f"job_id={queued_job['id']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
