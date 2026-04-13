from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
from workers.image_extract import extract_image_text_or_description
from workers.telegram_media import download_telegram_file
from workers.transcribe import transcribe_audio
from workers.url_extract import extract_url_content


def _resolve_local_media_path(raw_media_path: str | None) -> str | None:
    if not raw_media_path:
        return None
    candidate = raw_media_path.strip()
    if not candidate:
        return None
    p = Path(candidate)
    if p.exists():
        return str(p)
    return None


async def handle(job_data: dict[str, Any], conn: asyncpg.Connection) -> dict[str, Any]:
    entry_id = job_data.get("entry_id")
    user_id = job_data.get("user_id")
    if not entry_id or not user_id:
        return {"handled_by": "ingest", "skipped": "missing_identifiers"}

    await conn.execute("SELECT set_config('app.current_tenant', $1, true)", str(user_id))

    entry = await conn.fetchrow(
        """
        SELECT id, source_type, body, media_path, status
        FROM raw_entries
        WHERE id = $1::uuid
          AND user_id = $2::uuid
        LIMIT 1
        """,
        str(entry_id),
        str(user_id),
    )
    if not entry:
        return {"handled_by": "ingest", "skipped": "entry_not_found"}
    if entry["status"] == "absorbed":
        return {"handled_by": "ingest", "entry_id": str(entry["id"]), "skipped": "already_absorbed"}

    source_type = str(entry["source_type"] or "unknown")
    body = str(entry["body"] or "")

    # Task 3.3 URL extraction (layered): Firecrawl -> Jina -> trafilatura -> Playwright.
    extraction_layer = None
    transcription_status = None
    image_status = None
    if source_type == "url":
        url_to_extract = body.strip()
        extracted_text, extraction_layer = await extract_url_content(url_to_extract)
        body = extracted_text or body
    elif source_type == "voice":
        telegram_file_id = (entry["media_path"] or "").strip() if entry["media_path"] else ""
        local_path = _resolve_local_media_path(telegram_file_id)
        if not local_path and telegram_file_id:
            local_path = await download_telegram_file(telegram_file_id, str(user_id))
        if local_path:
            transcript, transcription_status = transcribe_audio(local_path)
            if transcript:
                body = transcript
            await conn.execute(
                """
                UPDATE raw_entries
                SET media_path = $2
                WHERE id = $1::uuid
                """,
                str(entry["id"]),
                local_path,
            )
        else:
            transcription_status = "download_failed"
    elif source_type == "image":
        telegram_file_id = (entry["media_path"] or "").strip() if entry["media_path"] else ""
        local_path = _resolve_local_media_path(telegram_file_id)
        if not local_path and telegram_file_id:
            local_path = await download_telegram_file(telegram_file_id, str(user_id))
        if local_path:
            image_text, image_status = await extract_image_text_or_description(local_path, body)
            if image_text:
                body = image_text
            await conn.execute(
                """
                UPDATE raw_entries
                SET media_path = $2
                WHERE id = $1::uuid
                """,
                str(entry["id"]),
                local_path,
            )
        else:
            image_status = "download_failed"

    await conn.execute(
        """
        UPDATE raw_entries
        SET status = 'processing',
            body = $2
        WHERE id = $1::uuid
        """,
        str(entry["id"]),
        body,
    )

    await conn.execute(
        """
        INSERT INTO pgboss.job (name, data)
        VALUES (
          'absorb',
          jsonb_build_object(
            'user_id', $1::text,
            'entry_id', $2::text,
            'source_type', $3::text
          )
        )
        """,
        str(user_id),
        str(entry["id"]),
        source_type,
    )

    return {
        "handled_by": "ingest",
        "entry_id": str(entry["id"]),
        "source_type": source_type,
        "status": "processing",
        "extraction_layer": extraction_layer,
        "transcription_status": transcription_status,
        "image_status": image_status,
    }
