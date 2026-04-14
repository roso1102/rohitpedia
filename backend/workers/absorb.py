from __future__ import annotations

import json
import os
from typing import Any

import asyncpg

from llm.absorb_prompt import (
    build_absorb_prompt,
    build_meta_overview_prompt,
    build_provenance_block,
    build_structured_absorb_task,
)
from llm.absorb_schema import ABSORB_ARTICLE_SCHEMA
from llm.embeddings import embed_with_ollama
from llm.facets import FACETS_SCHEMA, build_facets_prompt, validate_facets_payload
from llm.openai_compatible import OpenAICompatibleProvider
from llm import GeminiProvider, LLMProvider, OllamaProvider
from llm.wikilinks import extract_wikilinks_ast
from workers.absorb_util import parse_llm_json, parse_long_pdf_sections, slugify, validate_absorb_payload

LONG_PDF_MAX_SECTIONS = 40
SECTION_BODY_MAX = 8000
SINGLE_ENTRY_MAX = 12000


def _get_provider() -> LLMProvider:
    provider_name = os.getenv("LLM_ABSORB_PROVIDER", "gemini").strip().lower()
    if provider_name in {"ollama", "local"}:
        return OllamaProvider(model=os.getenv("LLM_ABSORB_MODEL", "").strip() or None)
    return GeminiProvider(model=os.getenv("LLM_ABSORB_MODEL", "").strip() or None)


def _get_facets_provider() -> LLMProvider:
    provider_name = os.getenv("LLM_FACETS_PROVIDER", "ollama").strip().lower()
    if provider_name in {"gemini"}:
        return GeminiProvider(model=os.getenv("LLM_FACETS_MODEL", "").strip() or None)
    if provider_name in {"openai", "openai_compat", "llama_api", "llama"}:
        return OpenAICompatibleProvider(model=os.getenv("LLM_FACETS_MODEL", "").strip() or None)
    return OllamaProvider(model=os.getenv("LLM_FACETS_MODEL", "").strip() or None)


def _get_facets_fallback_provider() -> LLMProvider | None:
    provider_name = os.getenv("LLM_FACETS_FALLBACK_PROVIDER", "").strip().lower()
    model_name = os.getenv("LLM_FACETS_FALLBACK_MODEL", "").strip() or None
    if not provider_name:
        return None
    if provider_name in {"gemini"}:
        return GeminiProvider(model=model_name)
    if provider_name in {"openai", "openai_compat", "llama_api", "llama"}:
        return OpenAICompatibleProvider(model=model_name)
    if provider_name in {"ollama", "local"}:
        return OllamaProvider(model=model_name)
    return None


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


async def _fetch_related_context(
    conn: asyncpg.Connection,
    user_id: str,
    entry_text: str,
) -> tuple[list[dict[str, str]], str | None]:
    embedding, embed_error = await embed_with_ollama(entry_text[:12000])
    if not embedding:
        return [], embed_error or "embed_failed"

    rows = await conn.fetch(
        """
        SELECT
          a.slug,
          a.title,
          dc.chunk_text,
          (1 - (dc.embedding <=> $2::vector)) AS cosine_similarity
        FROM document_chunks dc
        JOIN articles a ON a.id = dc.article_id
        WHERE dc.user_id = $1::uuid
          AND dc.embedding IS NOT NULL
          AND (1 - (dc.embedding <=> $2::vector)) >= 0.55
        ORDER BY dc.embedding <=> $2::vector
        LIMIT 8
        """,
        str(user_id),
        _vector_literal(embedding),
    )

    related: list[dict[str, str]] = []
    for row in rows:
        related.append(
            {
                "slug": str(row["slug"] or ""),
                "title": str(row["title"] or ""),
                "chunk_text": str(row["chunk_text"] or ""),
            }
        )
    return related, None


def _related_context_block(related_context: list[dict[str, str]]) -> str:
    if not related_context:
        return ""
    rendered = []
    for item in related_context:
        rendered.append(
            f"### Related: {item['title']} ({item['slug']})\n"
            f"{item['chunk_text'][:1200]}"
        )
    return "\n\nRelated context from retrieval (top articles/chunks):\n" + "\n\n".join(rendered)


async def _fetch_candidate_slugs(conn: asyncpg.Connection, user_id: str) -> list[str]:
    slug_rows = await conn.fetch(
        """
        SELECT slug
        FROM articles
        WHERE user_id = $1::uuid
        ORDER BY updated_at DESC
        LIMIT 300
        """,
        str(user_id),
    )
    return [str(r["slug"]) for r in slug_rows if r.get("slug")]


async def _run_structured_absorb(
    provider: LLMProvider,
    *,
    user_prompt: str,
    max_tokens: int,
) -> tuple[dict[str, Any] | None, str | None]:
    result = await provider.complete(prompt=user_prompt, max_tokens=max_tokens, schema=ABSORB_ARTICLE_SCHEMA)
    if not result.get("ok"):
        return None, str(result.get("error") or "llm_failed")
    parsed, err = parse_llm_json(str(result.get("text") or ""))
    if err:
        return None, err
    validated, verr = validate_absorb_payload(parsed)
    if verr:
        return None, verr
    return validated, None


async def _extract_facets(
    provider: LLMProvider,
    *,
    body_md: str,
    fallback_provider: LLMProvider | None = None,
) -> tuple[dict[str, list[str]] | None, str | None]:
    async def _single_provider_run(p: LLMProvider) -> tuple[dict[str, list[str]] | None, str | None]:
        result = await p.complete(
            prompt=build_facets_prompt(body_md),
            max_tokens=768,
            schema=FACETS_SCHEMA,
        )
        if not result.get("ok") or not str(result.get("text") or "").strip():
            # Some models ignore/reject structured format; retry plain JSON prompt.
            result = await p.complete(
                prompt=build_facets_prompt(body_md),
                max_tokens=768,
                schema=None,
            )
        if not result.get("ok"):
            return None, str(result.get("error") or "facets_llm_failed")
        parsed, err = parse_llm_json(str(result.get("text") or ""))
        if err:
            return None, f"facets_{err}"
        validated, verr = validate_facets_payload(parsed)
        if verr:
            return None, verr
        return validated, None

    facets, err = await _single_provider_run(provider)
    if facets is not None or fallback_provider is None:
        return facets, err
    fallback_facets, fallback_err = await _single_provider_run(fallback_provider)
    if fallback_facets is not None:
        return fallback_facets, None
    return None, f"primary:{err}; fallback:{fallback_err}"


async def _upsert_article(
    conn: asyncpg.Connection,
    user_id: str,
    slug: str,
    title: str,
    body_md: str,
    facets: dict[str, Any],
    context: str | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO articles (user_id, slug, title, body_md, facets, context)
        VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
        ON CONFLICT (user_id, slug) DO UPDATE SET
          title = EXCLUDED.title,
          body_md = EXCLUDED.body_md,
          facets = EXCLUDED.facets,
          context = EXCLUDED.context,
          updated_at = now()
        """,
        str(user_id),
        slug,
        title,
        body_md,
        json.dumps(facets),
        context,
    )


async def _sync_backlinks_for_article(
    conn: asyncpg.Connection,
    user_id: str,
    from_slug: str,
    body_md: str,
) -> int:
    """Replace outbound wikilinks for this article (AST extraction, excludes code/headings)."""
    await conn.execute(
        """
        DELETE FROM backlinks
        WHERE user_id = $1::uuid AND from_slug = $2
        """,
        str(user_id),
        from_slug,
    )
    targets = extract_wikilinks_ast(body_md)
    inserted = 0
    for raw in targets:
        to_slug = slugify(str(raw), max_len=96)
        if not to_slug or to_slug == from_slug:
            continue
        await conn.execute(
            """
            INSERT INTO backlinks (user_id, from_slug, to_slug, link_type)
            VALUES ($1::uuid, $2, $3, 'wikilink')
            ON CONFLICT (user_id, from_slug, to_slug) DO NOTHING
            """,
            str(user_id),
            from_slug,
            to_slug,
        )
        inserted += 1
    return inserted


async def handle(job_data: dict[str, Any], conn: asyncpg.Connection) -> dict[str, Any]:
    entry_id = job_data.get("entry_id")
    user_id = job_data.get("user_id")
    if not entry_id or not user_id:
        return {"handled_by": "absorb", "skipped": "missing_identifiers"}

    await conn.execute("SELECT set_config('app.current_tenant', $1, true)", str(user_id))
    row = await conn.fetchrow(
        """
        SELECT body, source_type
        FROM raw_entries
        WHERE id = $1::uuid
          AND user_id = $2::uuid
        LIMIT 1
        """,
        str(entry_id),
        str(user_id),
    )
    if not row:
        return {"handled_by": "absorb", "entry_id": str(entry_id), "skipped": "entry_not_found"}

    body = str(row["body"] or "")
    source_type = str(row["source_type"] or "unknown")

    related_context, retrieval_error = await _fetch_related_context(
        conn=conn,
        user_id=str(user_id),
        entry_text=body,
    )
    rag_block = _related_context_block(related_context)

    base_candidates = await _fetch_candidate_slugs(conn, str(user_id))
    provider = _get_provider()
    facets_provider = _get_facets_provider()
    facets_fallback_provider = _get_facets_fallback_provider()

    sections = parse_long_pdf_sections(body)
    created: list[tuple[str, str]] = []
    errors: list[str] = []

    if sections:
        extra_slugs: list[str] = list(base_candidates)
        section_writes: list[tuple[dict[str, Any], str]] = []
        for i, sec in enumerate(sections[:LONG_PDF_MAX_SECTIONS]):
            heading = str(sec.get("heading") or f"section-{i}")
            content = str(sec.get("content") or "")[:SECTION_BODY_MAX]
            hint_slug = slugify(heading)
            provenance = build_provenance_block(str(entry_id), source_type, i, heading)
            user_part = (
                f"{provenance}"
                f"Suggested slug (you may refine): {hint_slug}\n\n"
                f"Section heading: {heading}\n\n"
                f"Section content:\n{content}\n\n"
                f"{build_structured_absorb_task()}"
            )
            prompt = build_absorb_prompt(
                source_type=source_type,
                entry_text=user_part + rag_block,
                candidate_slugs=extra_slugs,
            )
            payload, err = await _run_structured_absorb(provider, user_prompt=prompt, max_tokens=4096)
            if err or not payload:
                errors.append(f"section_{i}:{err}")
                continue
            extracted_facets, facets_err = await _extract_facets(
                facets_provider,
                body_md=payload["body_md"],
                fallback_provider=facets_fallback_provider,
            )
            if facets_err:
                errors.append(f"section_{i}:facets:{facets_err}")
                payload["facets"] = {}
            else:
                payload["facets"] = extracted_facets or {}
            ctx = json.dumps(
                {"entry_id": str(entry_id), "section_index": i, "heading": heading},
                ensure_ascii=True,
            )
            section_writes.append((payload, ctx))
            created.append((payload["slug"], payload["title"]))
            if payload["slug"] not in extra_slugs:
                extra_slugs.append(payload["slug"])

        primary_slug = None
        meta_payload = None
        meta_ctx = None
        if created:
            meta_prompt_base = build_absorb_prompt(
                source_type=source_type,
                entry_text=build_meta_overview_prompt(str(entry_id), source_type, created) + rag_block,
                candidate_slugs=extra_slugs,
            )
            meta_prompt = meta_prompt_base + "\n\n" + build_structured_absorb_task()
            meta_payload, meta_err = await _run_structured_absorb(provider, user_prompt=meta_prompt, max_tokens=4096)
            if meta_err or not meta_payload:
                errors.append(f"meta:{meta_err}")
                primary_slug = created[0][0]
            else:
                meta_facets, meta_facets_err = await _extract_facets(
                    facets_provider,
                    body_md=meta_payload["body_md"],
                    fallback_provider=facets_fallback_provider,
                )
                if meta_facets_err:
                    errors.append(f"meta:facets:{meta_facets_err}")
                    meta_payload["facets"] = {}
                else:
                    meta_payload["facets"] = meta_facets or {}
                meta_ctx = json.dumps({"entry_id": str(entry_id), "kind": "meta_overview"}, ensure_ascii=True)
                primary_slug = meta_payload["slug"]

        backlink_counts: list[int] = []
        if section_writes:
            async with conn.transaction():
                for payload, ctx in section_writes:
                    await _upsert_article(
                        conn,
                        str(user_id),
                        payload["slug"],
                        payload["title"],
                        payload["body_md"],
                        payload["facets"],
                        ctx,
                    )
                    n = await _sync_backlinks_for_article(
                        conn, str(user_id), payload["slug"], payload["body_md"]
                    )
                    backlink_counts.append(n)
                if meta_payload and meta_ctx:
                    await _upsert_article(
                        conn,
                        str(user_id),
                        meta_payload["slug"],
                        meta_payload["title"],
                        meta_payload["body_md"],
                        meta_payload["facets"],
                        meta_ctx,
                    )
                    n_meta = await _sync_backlinks_for_article(
                        conn, str(user_id), meta_payload["slug"], meta_payload["body_md"]
                    )
                    backlink_counts.append(n_meta)
                await conn.execute(
                    """
                    UPDATE raw_entries
                    SET status = 'absorbed', absorbed_into = $2
                    WHERE id = $1::uuid AND user_id = $3::uuid
                    """,
                    str(entry_id),
                    primary_slug,
                    str(user_id),
                )

        return {
            "handled_by": "absorb",
            "entry_id": str(entry_id),
            "mode": "long_pdf_sections",
            "sections_processed": len(created),
            "primary_slug": primary_slug,
            "section_slugs": [s for s, _ in created],
            "backlink_rows_by_article": backlink_counts or None,
            "errors": errors or None,
            "related_context_count": len(related_context),
            "retrieval_error": retrieval_error,
            "candidate_slug_count": len(base_candidates),
            "ok": bool(section_writes),
        }

    provenance = build_provenance_block(str(entry_id), source_type, None, None)
    user_part = (
        f"{provenance}"
        f"Entry content:\n{body[:SINGLE_ENTRY_MAX]}\n\n"
        f"{build_structured_absorb_task()}"
    )
    prompt = build_absorb_prompt(
        source_type=source_type,
        entry_text=user_part + rag_block,
        candidate_slugs=base_candidates,
    )
    payload, err = await _run_structured_absorb(provider, user_prompt=prompt, max_tokens=8192)
    if err or not payload:
        return {
            "handled_by": "absorb",
            "entry_id": str(entry_id),
            "mode": "single",
            "ok": False,
            "error": err or "absorb_failed",
            "related_context_count": len(related_context),
            "retrieval_error": retrieval_error,
        }
    extracted_facets, facets_err = await _extract_facets(
        facets_provider,
        body_md=payload["body_md"],
        fallback_provider=facets_fallback_provider,
    )
    payload["facets"] = extracted_facets or {}

    ctx = json.dumps({"entry_id": str(entry_id)}, ensure_ascii=True)
    backlink_count = 0
    async with conn.transaction():
        await _upsert_article(
            conn,
            str(user_id),
            payload["slug"],
            payload["title"],
            payload["body_md"],
            payload["facets"],
            ctx,
        )
        backlink_count = await _sync_backlinks_for_article(
            conn, str(user_id), payload["slug"], payload["body_md"]
        )
        await conn.execute(
            """
            UPDATE raw_entries
            SET status = 'absorbed', absorbed_into = $2
            WHERE id = $1::uuid AND user_id = $3::uuid
            """,
            str(entry_id),
            payload["slug"],
            str(user_id),
        )

    return {
        "handled_by": "absorb",
        "entry_id": str(entry_id),
        "mode": "single",
        "ok": True,
        "slug": payload["slug"],
        "title": payload["title"],
        "backlink_rows_inserted": backlink_count,
        "facets_error": facets_err,
        "related_context_count": len(related_context),
        "retrieval_error": retrieval_error,
        "candidate_slug_count": len(base_candidates),
    }
