from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


def slugify(text: str, max_len: int = 80) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return (s or "note")[:max_len]


def parse_long_pdf_sections(body: str) -> list[dict[str, Any]] | None:
    raw = (body or "").strip()
    if not raw.startswith("["):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    if "heading" not in first and "content" not in first:
        return None
    return [x for x in data if isinstance(x, dict)]


def validate_absorb_payload(data: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(data, dict):
        return None, "payload_not_object"
    slug = slugify(str(data.get("slug") or "").strip(), max_len=96)
    title = str(data.get("title") or "").strip()
    body_md = str(data.get("body_md") or "").strip()
    facets_raw = data.get("facets")
    if not slug:
        return None, "missing_slug"
    if not title:
        return None, "missing_title"
    if not body_md:
        return None, "missing_body_md"
    facets: dict[str, Any]
    if facets_raw is None or facets_raw == "":
        facets = {}
    elif isinstance(facets_raw, dict):
        facets = facets_raw
    elif isinstance(facets_raw, str):
        try:
            parsed = json.loads(facets_raw) if facets_raw.strip() else {}
        except json.JSONDecodeError:
            return None, "facets_parse_error"
        if not isinstance(parsed, dict):
            return None, "facets_not_object"
        facets = parsed
    else:
        return None, "facets_invalid_type"
    return {
        "slug": slug,
        "title": title,
        "body_md": body_md,
        "facets": facets,
    }, None


def parse_llm_json(text: str) -> tuple[Any | None, str | None]:
    t = (text or "").strip()
    if not t:
        return None, "empty_json_text"
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        return json.loads(t), None
    except json.JSONDecodeError:
        return None, "json_decode_error"
