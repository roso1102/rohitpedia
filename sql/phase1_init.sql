CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  telegram_id BIGINT UNIQUE,
  active_context TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  source_type TEXT NOT NULL,
  media_path TEXT,
  source_url TEXT,
  context TEXT,
  status TEXT DEFAULT 'pending',
  absorbed_into TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS articles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  body_md TEXT NOT NULL DEFAULT '',
  context TEXT,
  facets JSONB DEFAULT '{}',
  importance INT DEFAULT 1,
  avoid JSONB DEFAULT '[]',
  embed_state TIMESTAMPTZ,
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, slug)
);

CREATE TABLE IF NOT EXISTS document_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  section_header TEXT,
  chunk_text TEXT NOT NULL,
  chunk_meta JSONB DEFAULT '{}',
  embedding vector(768),
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(article_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
  ON document_chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_document_chunks_user_id ON document_chunks (user_id);

CREATE TABLE IF NOT EXISTS backlinks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  from_slug TEXT NOT NULL,
  to_slug TEXT NOT NULL,
  link_type TEXT DEFAULT 'wikilink',
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, from_slug, to_slug)
);

CREATE TABLE IF NOT EXISTS media_files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  entry_id UUID REFERENCES raw_entries(id) ON DELETE CASCADE,
  file_path TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes BIGINT,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE raw_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE backlinks ENABLE ROW LEVEL SECURITY;
ALTER TABLE media_files ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS iso_raw_entries ON raw_entries;
DROP POLICY IF EXISTS iso_articles ON articles;
DROP POLICY IF EXISTS iso_chunks ON document_chunks;
DROP POLICY IF EXISTS iso_backlinks ON backlinks;
DROP POLICY IF EXISTS iso_media ON media_files;

CREATE POLICY iso_raw_entries ON raw_entries
  USING (user_id = current_setting('app.current_tenant')::uuid);
CREATE POLICY iso_articles ON articles
  USING (user_id = current_setting('app.current_tenant')::uuid);
CREATE POLICY iso_chunks ON document_chunks
  USING (user_id = current_setting('app.current_tenant')::uuid);
CREATE POLICY iso_backlinks ON backlinks
  USING (user_id = current_setting('app.current_tenant')::uuid);
CREATE POLICY iso_media ON media_files
  USING (user_id = current_setting('app.current_tenant')::uuid);

CREATE INDEX IF NOT EXISTS idx_raw_entries_fts ON raw_entries USING GIN(to_tsvector('english', body));
CREATE INDEX IF NOT EXISTS idx_articles_fts ON articles USING GIN(to_tsvector('english', body_md));
