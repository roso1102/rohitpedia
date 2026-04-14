from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
from workers.image_extract import extract_image_text_or_description
from workers.pdf_extract import extract_pdf_content
from workers.telegram_media import download_telegram_file, get_media_metadata
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


def _write_pdf_sidecar(local_pdf_path: str, extracted_body: str, pdf_status: str | None) -> str | None:
    if not extracted_body:
        return None
    src = Path(local_pdf_path)
    if not src.exists():
        return None

    status = (pdf_status or "").lower()
    if "long_pdf_json" in status:
        target = src.with_suffix(".sections.json")
        try:
            parsed = json.loads(extracted_body)
            target.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(target)
        except Exception:
            target.write_text(extracted_body, encoding="utf-8")
            return str(target)

    target = src.with_suffix(".extracted.md")
    target.write_text(extracted_body, encoding="utf-8")
    return str(target)


async def _upsert_media_file(
    conn: asyncpg.Connection,
    user_id: str,
    entry_id: str,
    local_path: str,
) -> None:
    mime_type, size_bytes = get_media_metadata(local_path)
    await conn.execute(
        """
        INSERT INTO media_files (user_id, entry_id, file_path, mime_type, size_bytes)
        SELECT $1::uuid, $2::uuid, $3::text, $4::text, $5::bigint
        WHERE NOT EXISTS (
          SELECT 1
          FROM media_files
          WHERE entry_id = $2::uuid
            AND file_path = $3::text
        )
        """,
        str(user_id),
        str(entry_id),
        local_path,
        mime_type,
        int(size_bytes),
    )


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
    pdf_status = None
    pdf_output_path = None
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
            await _upsert_media_file(conn, str(user_id), str(entry["id"]), local_path)
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
            await _upsert_media_file(conn, str(user_id), str(entry["id"]), local_path)
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
    elif source_type == "pdf":
        telegram_file_id = (entry["media_path"] or "").strip() if entry["media_path"] else ""
        local_path = _resolve_local_media_path(telegram_file_id)
        if not local_path and telegram_file_id:
            local_path = await download_telegram_file(telegram_file_id, str(user_id))
        if local_path:
            pdf_text, pdf_status = extract_pdf_content(local_path)
            if pdf_text:
                body = pdf_text
                pdf_output_path = _write_pdf_sidecar(local_path, pdf_text, pdf_status)
            await _upsert_media_file(conn, str(user_id), str(entry["id"]), local_path)
            if pdf_output_path:
                await _upsert_media_file(conn, str(user_id), str(entry["id"]), pdf_output_path)
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
            pdf_status = "download_failed"

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
        "pdf_status": pdf_status,
        "pdf_output_path": pdf_output_path,
    }
