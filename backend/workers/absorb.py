from __future__ import annotations

from typing import Any

import asyncpg

async def handle(job_data: dict[str, Any], conn: asyncpg.Connection) -> dict[str, Any]:
    return {"handled_by": "absorb", "entry_id": job_data.get("entry_id")}
