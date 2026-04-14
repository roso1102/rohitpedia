from __future__ import annotations

import os
from typing import Any

import httpx


async def embed_with_ollama(text: str) -> tuple[list[float] | None, str | None]:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip() or "http://localhost:11434"
    model = os.getenv("EMBED_MODEL", "nomic-embed-text").strip() or "nomic-embed-text"
    payload: dict[str, Any] = {"model": model, "prompt": text}

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(f"{host.rstrip('/')}/api/embeddings", json=payload)
    except Exception:
        return None, "ollama_embed_request_error"

    if resp.status_code != 200:
        return None, "ollama_embed_request_failed"

    data = resp.json() if resp.content else {}
    embedding = data.get("embedding") if isinstance(data, dict) else None
    if not isinstance(embedding, list) or not embedding:
        return None, "ollama_embed_empty"

    try:
        vector = [float(v) for v in embedding]
    except Exception:
        return None, "ollama_embed_parse_error"
    return vector, None
