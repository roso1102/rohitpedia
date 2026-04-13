import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db_session

router = APIRouter(prefix="/webhook", tags=["webhook"])


def _extract_message_payload(update: dict[str, Any]) -> tuple[int | None, str, str]:
    message = update.get("message") or {}
    text_body = message.get("text", "").strip()
    source_type = "text" if text_body else "unknown"
    telegram_id = message.get("from", {}).get("id")
    return telegram_id, text_body, source_type


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if expected_secret and x_telegram_bot_api_secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret token")

    update = await request.json()
    telegram_id, body, source_type = _extract_message_payload(update)
    if not telegram_id:
        return {"ok": True, "skipped": "no_telegram_id"}

    if not body:
        body = "[non-text message captured]"

    async with db.begin():
        user_row = await db.execute(
            text("SELECT id FROM users WHERE telegram_id = :telegram_id LIMIT 1"),
            {"telegram_id": telegram_id},
        )
        user_id = user_row.scalar_one_or_none()

        if not user_id:
            return {"ok": True, "skipped": "user_not_linked"}

        await db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(user_id)},
        )
        await db.execute(
            text(
                """
                INSERT INTO raw_entries (user_id, body, source_type)
                VALUES (:user_id, :body, :source_type)
                """
            ),
            {"user_id": user_id, "body": body, "source_type": source_type},
        )

    # TODO: enqueue ingest job with pg-boss after worker runner is added.
    return {"ok": True}
