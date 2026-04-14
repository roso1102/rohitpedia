from __future__ import annotations

from typing import Any

# Gemini `responseSchema` subset (JSON Schema style).
ABSORB_ARTICLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slug": {
            "type": "string",
            "description": "URL-safe wiki slug; lowercase, hyphens, no spaces.",
        },
        "title": {"type": "string"},
        "body_md": {"type": "string", "description": "Full article body in markdown."},
        "facets": {
            "type": "string",
            "description": "JSON object serialized as a string (e.g. '{}' or '{\"health\":[\"x\"]}').",
        },
    },
    "required": ["slug", "title", "body_md", "facets"],
}
