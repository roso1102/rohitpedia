-- Task 5: embed worker — add columns for incremental embedding and chunk metadata.
-- Safe to run on existing DBs (idempotent).

ALTER TABLE articles ADD COLUMN IF NOT EXISTS embed_state TIMESTAMPTZ;

ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS chunk_meta JSONB DEFAULT '{}'::jsonb;

-- Enforce one row per (article, chunk_index) for upserts / clean re-embeds.
CREATE UNIQUE INDEX IF NOT EXISTS document_chunks_article_id_chunk_index_key
  ON document_chunks (article_id, chunk_index);
