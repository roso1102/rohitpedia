from __future__ import annotations

import os
from typing import Any

import httpx

from llm.provider import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model: str | None = None) -> None:
        self.host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip() or "http://localhost:11434"
        self.model = model or os.getenv("LLM_FACETS_MODEL", "phi3:mini").strip() or "phi3:mini"
        self.timeout_seconds = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))

    async def complete(self, prompt: str, max_tokens: int = 2048, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if schema:
            payload["format"] = schema

        url = f"{self.host.rstrip('/')}/api/generate"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, json=payload)
        except Exception:
            return {"ok": False, "error": "ollama_request_error", "model": self.model}

        if resp.status_code != 200:
            return {"ok": False, "error": "ollama_request_failed", "status_code": resp.status_code, "model": self.model}

        data = resp.json() if resp.content else {}
        text = str(data.get("response") or "").strip() if isinstance(data, dict) else ""
        if not text:
            return {"ok": False, "error": "ollama_empty_response", "model": self.model}

        return {"ok": True, "model": self.model, "text": text}
