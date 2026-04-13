import os
import re
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db_session

router = APIRouter(prefix="/webhook", tags=["webhook"])


URL_REGEX = re.compile(r"https?://\S+")


def _extract_message_payload(update: dict[str, Any]) -> tuple[int | None, str, str, str | None, str | None]:
    message = update.get("message") or {}
    caption = (message.get("caption") or "").strip()
    text_body = message.get("text", "").strip()
    telegram_id = message.get("from", {}).get("id")

    source_type = "text"
    body = text_body or caption or "[non-text message captured]"
    source_url = None
    media_path = None

    if text_body:
        url_match = URL_REGEX.search(text_body)
        if url_match:
            source_type = "url"
            source_url = url_match.group(0)
    elif message.get("voice"):
        source_type = "voice"
        media_path = message["voice"].get("file_id")
    elif message.get("photo"):
        source_type = "image"
        # Telegram sends multiple sizes; keep the largest.
        photos = message["photo"]
        if photos:
            media_path = photos[-1].get("file_id")
    elif message.get("document"):
        mime_type = (message["document"].get("mime_type") or "").lower()
        media_path = message["document"].get("file_id")
        source_type = "pdf" if mime_type == "application/pdf" else "document"
    elif message.get("video"):
        source_type = "video"
        media_path = message["video"].get("file_id")
    elif message.get("audio"):
        source_type = "audio"
        media_path = message["audio"].get("file_id")
    else:
        source_type = "unknown"

    return telegram_id, body, source_type, source_url, media_path


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
    telegram_id, body, source_type, source_url, media_path = _extract_message_payload(update)
    if not telegram_id:
        return {"ok": True, "skipped": "no_telegram_id"}

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
    entry_id = (
        await db.execute(
            text(
                """
                INSERT INTO raw_entries (user_id, body, source_type, source_url, media_path)
                VALUES (:user_id, :body, :source_type, :source_url, :media_path)
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "body": body,
                "source_type": source_type,
                "source_url": source_url,
                "media_path": media_path,
            },
        )
    ).scalar_one()

    await db.execute(
        text(
            """
            INSERT INTO ingest_jobs (user_id, entry_id, status, payload)
            VALUES (:user_id, :entry_id, 'pending', CAST(:payload AS jsonb))
            """
        ),
        {
            "user_id": user_id,
            "entry_id": entry_id,
            "payload": json.dumps(
                {
                    "source_type": source_type,
                    "source_url": source_url,
                    "media_path": media_path,
                }
            ),
        },
    )
    await db.commit()
    return {"ok": True}
