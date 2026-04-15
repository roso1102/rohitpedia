from __future__ import annotations

import json
from typing import Any

import asyncpg

from llm.embeddings import embed_with_ollama
from workers.chunk_article import chunk_article, contextual_embed_text


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


async def handle(job_data: dict[str, Any], conn: asyncpg.Connection) -> dict[str, Any]:
    article_id = job_data.get("article_id")
    user_id = job_data.get("user_id")
    if not article_id or not user_id:
        return {"handled_by": "embed", "skipped": "missing_identifiers"}

    await conn.execute("SELECT set_config('app.current_tenant', $1, true)", str(user_id))

    row = await conn.fetchrow(
        """
        SELECT id, body_md, context, updated_at, embed_state
        FROM articles
        WHERE id = $1::uuid AND user_id = $2::uuid
        LIMIT 1
        """,
        str(article_id),
        str(user_id),
    )
    if not row:
        return {"handled_by": "embed", "article_id": str(article_id), "skipped": "article_not_found"}

    embed_state = row["embed_state"]
    updated_at = row["updated_at"]
    if embed_state is not None and updated_at is not None and embed_state >= updated_at:
        return {
            "handled_by": "embed",
            "article_id": str(article_id),
            "skipped": "already_current",
            "embed_state": str(embed_state),
        }

    body_md = str(row["body_md"] or "")
    ctx = row["context"]
    context_json = str(ctx) if ctx is not None else None
    chunks = chunk_article(body_md, doc_id=str(article_id), context_json=context_json)

    async with conn.transaction():
        await conn.execute(
            """
            DELETE FROM document_chunks
            WHERE article_id = $1::uuid AND user_id = $2::uuid
            """,
            str(article_id),
            str(user_id),
        )
        for ch in chunks:
            to_embed = contextual_embed_text(ch)[:12000]
            vec, err = await embed_with_ollama(to_embed)
            if not vec:
                raise RuntimeError(err or "embed_failed")
            await conn.execute(
                """
                INSERT INTO document_chunks
                  (article_id, user_id, chunk_index, section_header, chunk_text, embedding, chunk_meta)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::vector, $7::jsonb)
                """,
                str(article_id),
                str(user_id),
                ch.index,
                ch.header or None,
                ch.text,
                _vector_literal(vec),
                json.dumps(ch.meta),
            )
        await conn.execute(
            """
            UPDATE articles
            SET embed_state = now()
            WHERE id = $1::uuid AND user_id = $2::uuid
            """,
            str(article_id),
            str(user_id),
        )

    return {
        "handled_by": "embed",
        "article_id": str(article_id),
        "ok": True,
        "chunk_count": len(chunks),
    }
