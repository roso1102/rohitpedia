from __future__ import annotations

import hashlib

ABSORB_SYSTEM_PROMPT = """You are Rohitpedia Absorb Writer.
Your job is to convert incoming captured content into clean wiki-ready markdown.

Rules:
1) Preserve factual meaning and avoid hallucinations.
2) Produce concise, structured markdown with short sections and bullet points when useful.
3) Include facets as JSON in a fenced block only when explicitly requested.
4) Wikilink policy: Only create [[wikilinks]] that exactly match candidate slugs provided.
5) Never invent new wikilink slugs. If unsure, use plain text instead of wikilink.
6) Keep output deterministic and implementation-friendly.
"""

_PREFIX_CACHE: dict[str, str] = {}


def build_absorb_prompt(source_type: str, entry_text: str, candidate_slugs: list[str]) -> str:
    # Lightweight context caching for Gemini path: reuse static prompt prefix for identical slug sets.
    slug_key = hashlib.sha1(("|".join(candidate_slugs)).encode("utf-8")).hexdigest()
    if slug_key not in _PREFIX_CACHE:
        slug_lines = "\n".join(f"- {slug}" for slug in candidate_slugs[:300]) or "- (none)"
        _PREFIX_CACHE[slug_key] = (
            f"{ABSORB_SYSTEM_PROMPT}\n\n"
            "Allowed wikilink candidate slugs:\n"
            f"{slug_lines}\n\n"
            "Reminder: Only use [[wikilinks]] from the list above. Never invent slug names.\n"
        )

    prefix = _PREFIX_CACHE[slug_key]
    return (
        f"{prefix}\n"
        f"Source type: {source_type}\n\n"
        "Entry content:\n"
        f"{entry_text[:12000]}"
    )


def build_structured_absorb_task() -> str:
    return (
        "Task: Return ONE JSON object (no markdown outside JSON) with keys: "
        "slug, title, body_md, facets. "
        "body_md must be wiki-ready markdown. "
        "facets must be a JSON object serialized as a string (e.g. \"{}\" or "
        "'{\"health\":[\"anti-inflammatory\"]}'). "
        "Respect wikilink rules from the system prompt."
    )


def build_provenance_block(entry_id: str, source_type: str, section_index: int | None, heading: str | None) -> str:
    lines = [
        "Provenance (for traceability; do not repeat verbatim in body unless relevant):",
        f"- entry_id: {entry_id}",
        f"- source_type: {source_type}",
    ]
    if section_index is not None:
        lines.append(f"- section_index: {section_index}")
    if heading:
        lines.append(f"- section_heading: {heading}")
    return "\n".join(lines) + "\n\n"


def build_meta_overview_prompt(
    entry_id: str,
    source_type: str,
    section_articles: list[tuple[str, str]],
) -> str:
    lines = [
        "You are creating a hub/overview wiki article for a long document that was split into sections.",
        "Each section already has its own article. Produce ONE JSON object (slug, title, body_md, facets).",
        "The overview should briefly summarize themes and link to section articles using ONLY [[slug]] wikilinks from the list below.",
        build_provenance_block(entry_id, source_type, None, None),
        "Section articles (slug — title):",
    ]
    for slug, title in section_articles:
        lines.append(f"- {slug} — {title}")
    lines.append(
        "\nWrite body_md as a short overview with bullet list of links to each section where appropriate."
    )
    return "\n".join(lines)
