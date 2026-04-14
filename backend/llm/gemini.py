from __future__ import annotations

import os
from typing import Any

import httpx

from llm.provider import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self, model: str | None = None) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model = model or os.getenv("LLM_ABSORB_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

    async def complete(self, prompt: str, max_tokens: int = 2048, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "error": "missing_gemini_api_key"}

        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if schema:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            payload["generationConfig"]["responseSchema"] = schema

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=payload)
        except Exception:
            return {"ok": False, "error": "gemini_request_error", "model": self.model}

        if resp.status_code != 200:
            return {"ok": False, "error": "gemini_request_failed", "status_code": resp.status_code, "model": self.model}

        data = resp.json() if resp.content else {}
        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not candidates:
            return {"ok": False, "error": "gemini_empty_response", "model": self.model}

        parts = (((candidates[0] or {}).get("content") or {}).get("parts")) if isinstance(candidates[0], dict) else None
        if not parts:
            return {"ok": False, "error": "gemini_empty_parts", "model": self.model}

        text_parts = [p.get("text", "").strip() for p in parts if isinstance(p, dict) and p.get("text")]
        text = "\n".join(tp for tp in text_parts if tp).strip()
        if not text:
            return {"ok": False, "error": "gemini_no_text", "model": self.model}

        return {"ok": True, "model": self.model, "text": text}
