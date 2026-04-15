-- Task 5: embed_state on articles, chunk_meta + unique (article_id, chunk_index) on document_chunks

ALTER TABLE "articles" ADD COLUMN IF NOT EXISTS "embed_state" TIMESTAMPTZ(6);

ALTER TABLE "document_chunks" ADD COLUMN IF NOT EXISTS "chunk_meta" JSONB DEFAULT '{}';

CREATE UNIQUE INDEX IF NOT EXISTS "document_chunks_article_id_chunk_index_key"
  ON "document_chunks"("article_id", "chunk_index");
