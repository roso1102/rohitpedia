from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx


def get_media_metadata(local_path: str) -> tuple[str, int]:
    path = Path(local_path)
    mime_type, _ = mimetypes.guess_type(path.name)
    resolved_mime = mime_type or "application/octet-stream"
    size_bytes = path.stat().st_size if path.exists() else 0
    return resolved_mime, size_bytes


async def download_telegram_file(file_id: str, user_id: str) -> str | None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    media_root = os.getenv("MEDIA_DIR", "./media").strip()
    if not bot_token or not file_id:
        return None

    api_base = f"https://api.telegram.org/bot{bot_token}"
    async with httpx.AsyncClient(timeout=30) as client:
        file_resp = await client.get(f"{api_base}/getFile", params={"file_id": file_id})
        if file_resp.status_code != 200:
            return None
        payload = file_resp.json()
        file_path = (((payload or {}).get("result") or {}).get("file_path")) if isinstance(payload, dict) else None
        if not file_path:
            return None

        file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        content_resp = await client.get(file_url)
        if content_resp.status_code != 200:
            return None

    parsed_name = Path(urlparse(file_path).path).name
    now = datetime.now(timezone.utc)
    target_dir = Path(media_root) / str(user_id) / f"{now.year:04d}" / f"{now.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / parsed_name
    target.write_bytes(content_resp.content)
    return str(target)
