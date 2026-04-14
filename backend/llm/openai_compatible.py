from __future__ import annotations

import os
from typing import Any

import httpx

from llm.provider import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    """Generic OpenAI-compatible chat/completions provider."""

    def __init__(self, model: str | None = None) -> None:
        self.base_url = os.getenv("OPENAI_COMPAT_BASE_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("OPENAI_COMPAT_API_KEY", "").strip()
        self.model = model or os.getenv("OPENAI_COMPAT_MODEL", "llama-3.1-8b-instruct").strip()
        self.timeout_seconds = float(os.getenv("OPENAI_COMPAT_TIMEOUT_SECONDS", "180"))

    async def complete(
        self,
        prompt: str,
        max_tokens: int = 2048,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            return {"ok": False, "error": "missing_openai_compat_base_url"}
        if not self.api_key:
            return {"ok": False, "error": "missing_openai_compat_api_key"}

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "facets_schema", "schema": schema},
            }

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except Exception:
            return {"ok": False, "error": "openai_compat_request_error", "model": self.model}

        if resp.status_code != 200:
            return {
                "ok": False,
                "error": "openai_compat_request_failed",
                "status_code": resp.status_code,
                "model": self.model,
            }

        data = resp.json() if resp.content else {}
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices or not isinstance(choices[0], dict):
            return {"ok": False, "error": "openai_compat_empty_response", "model": self.model}
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "").strip()
        if not text:
            return {"ok": False, "error": "openai_compat_empty_text", "model": self.model}
        return {"ok": True, "model": self.model, "text": text}
