"""Task 5.3: spot-check document_chunks count and optional kNN (requires Ollama + DB)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llm.embeddings import embed_with_ollama


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.getenv("DATABASE_URL", "")
    user_id = os.getenv("SPOTCHECK_USER_ID", "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb")
    query = os.getenv("EMBED_VERIFY_QUERY", "turmeric anti-inflammatory")

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT set_config('app.current_tenant', $1, true)", user_id)
        n = await conn.fetchval(
            """
            SELECT count(*)::bigint FROM document_chunks WHERE user_id = $1::uuid
            """,
            user_id,
        )
        print("document_chunks_count:", int(n or 0))

        vec, err = await embed_with_ollama(query[:12000])
        if not vec:
            print("knn_skipped:", err or "no_embedding")
            return
        literal = "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
        rows = await conn.fetch(
            """
            SELECT chunk_text
            FROM document_chunks
            WHERE user_id = $1::uuid AND embedding IS NOT NULL
            ORDER BY embedding <=> $2::vector
            LIMIT 5
            """,
            user_id,
            literal,
        )
        print("knn_top5:")
        for r in rows:
            t = str(r["chunk_text"] or "")
            print(" -", t[:200].replace("\n", " ") + ("..." if len(t) > 200 else ""))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
