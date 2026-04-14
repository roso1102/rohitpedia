from __future__ import annotations

from typing import Any

FACET_KEYS = ("category", "themes", "colors", "health", "cuisine", "style", "sentiment")

FACETS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "array", "items": {"type": "string"}},
        "themes": {"type": "array", "items": {"type": "string"}},
        "colors": {"type": "array", "items": {"type": "string"}},
        "health": {"type": "array", "items": {"type": "string"}},
        "cuisine": {"type": "array", "items": {"type": "string"}},
        "style": {"type": "array", "items": {"type": "string"}},
        "sentiment": {"type": "array", "items": {"type": "string"}},
    },
    "required": list(FACET_KEYS),
}


def build_facets_prompt(body_md: str) -> str:
    return (
        "Extract concise semantic facets from the article body.\n"
        "Return ONLY JSON object with keys: "
        "category, themes, colors, health, cuisine, style, sentiment.\n"
        "Each key value must be an array of short lowercase strings.\n"
        "Use empty arrays when unknown.\n\n"
        f"Article body:\n{body_md[:10000]}"
    )


def validate_facets_payload(payload: Any) -> tuple[dict[str, list[str]] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "facets_payload_not_object"
    out: dict[str, list[str]] = {}
    for key in FACET_KEYS:
        raw = payload.get(key, [])
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            return None, f"facets_{key}_not_array"
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            v = item.strip().lower()
            if not v or v in seen:
                continue
            seen.add(v)
            cleaned.append(v)
        out[key] = cleaned
    return out, None
