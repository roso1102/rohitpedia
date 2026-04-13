from __future__ import annotations

import base64
import mimetypes
import os
import asyncio
from pathlib import Path

import httpx


def _guess_mime_type(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "image/jpeg"


async def extract_image_text_or_description(local_path: str, caption: str = "") -> tuple[str | None, str]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    preferred_model = os.getenv("IMAGE_VISION_MODEL", "").strip()
    fallback_models = [
        m
        for m in [preferred_model, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
        if m
    ]
    if not api_key:
        return None, "missing_gemini_api_key"

    image_file = Path(local_path)
    if not image_file.exists():
        return None, "image_missing"

    mime_type = _guess_mime_type(local_path)
    prompt = (
        "You are processing a Telegram image for a personal knowledge base. "
        "If the image contains readable text, extract the important text faithfully. "
        "If text is limited, provide a concise factual description of the image. "
        "Return plain text only."
    )
    if caption.strip():
        prompt = f"{prompt}\n\nUser caption/context: {caption.strip()}"

    encoded = base64.b64encode(image_file.read_bytes()).decode("utf-8")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": encoded}},
                ]
            }
        ]
    }
    async with httpx.AsyncClient(timeout=45) as client:
        for model in fallback_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            try:
                resp = await client.post(url, json=payload)
            except Exception:
                continue

            if resp.status_code != 200:
                if resp.status_code in (429, 503):
                    await asyncio.sleep(1.0)
                continue

            data = resp.json() if resp.content else {}
            candidates = data.get("candidates") if isinstance(data, dict) else None
            if not candidates:
                continue

            parts = (((candidates[0] or {}).get("content") or {}).get("parts")) if isinstance(candidates[0], dict) else None
            if not parts:
                continue

            text_parts = [p.get("text", "").strip() for p in parts if isinstance(p, dict) and p.get("text")]
            text = "\n".join(tp for tp in text_parts if tp).strip()
            if text:
                return text, f"ok:{model}"

    return None, "gemini_request_failed_all_models"
