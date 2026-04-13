# SUBJECT GUIDE: PostgreSQL + pgvector
## Rohitpedia Engineering Standards

---

## Core principles

**Never bypass RLS.** Every query executes within an RLS session. No exceptions.
**pgvector is not magic.** It's approximate nearest neighbour search. Test recall, don't assume it.
**Transactions protect data integrity.** Related writes = one transaction.

---

## RLS implementation

### Session variable pattern (FastAPI)
```python
# Set at the START of every request / worker job
# before the first DB query
async def set_rls_context(db: AsyncSession, user_id: str):
    await db.execute(
        text("SET LOCAL app.current_tenant = :uid"),
        {"uid": str(user_id)}
    )
```

### RLS policy creation (for every table)
```sql
-- Pattern to apply to ALL tables
ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;

-- Block all access by default (belt and suspenders)
ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;

-- Allow SELECT only to owning tenant
CREATE POLICY tenant_select ON {table_name}
  FOR SELECT USING (user_id = current_setting('app.current_tenant')::uuid);

-- Allow INSERT only for own tenant_id
CREATE POLICY tenant_insert ON {table_name}
  FOR INSERT WITH CHECK (user_id = current_setting('app.current_tenant')::uuid);

-- Allow UPDATE only for own rows
CREATE POLICY tenant_update ON {table_name}
  FOR UPDATE USING (user_id = current_setting('app.current_tenant')::uuid);

-- Allow DELETE only for own rows
CREATE POLICY tenant_delete ON {table_name}
  FOR DELETE USING (user_id = current_setting('app.current_tenant')::uuid);
```

### Testing RLS (run this after every schema change)
```sql
-- This script MUST return 0 for every table to pass the gate
DO $$
DECLARE
    user_a UUID := gen_random_uuid();
    user_b UUID := gen_random_uuid();
    row_count INT;
BEGIN
    -- Setup
    INSERT INTO users (id) VALUES (user_a), (user_b);

    -- Write as user_a
    PERFORM set_config('app.current_tenant', user_a::text, true);
    INSERT INTO articles (id, user_id, slug, title, body_md)
    VALUES (gen_random_uuid(), user_a, 'test-rls', 'Test', 'body');

    -- Read as user_b
    PERFORM set_config('app.current_tenant', user_b::text, true);
    SELECT count(*) INTO row_count FROM articles WHERE slug = 'test-rls';

    IF row_count != 0 THEN
        RAISE EXCEPTION 'RLS BREACH: user_b can see user_a data. Count: %', row_count;
    END IF;

    -- Cleanup
    DELETE FROM users WHERE id IN (user_a, user_b);
    RAISE NOTICE 'RLS test PASSED';
END $$;
```

---

## pgvector usage

### Index creation (HNSW)
```sql
-- Create AFTER loading initial data, not before
-- Building on empty table is fine; rebuilding on 100k rows takes minutes
CREATE INDEX document_chunks_embedding_idx
  ON document_chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Partial index per user is NOT recommended at your scale
-- (complicates planning, marginal benefit at < 1M vectors)
-- Add user_id filter in query WHERE clause instead
```

### kNN query pattern (with user_id pre-filter)
```sql
-- IMPORTANT: user_id filter must be in WHERE, not just RLS
-- This allows the query planner to narrow the search space
-- before traversing the HNSW graph

SELECT
    dc.chunk_text,
    dc.article_id,
    dc.section_header,
    1 - (dc.embedding <=> $1::vector) AS cosine_similarity
FROM document_chunks dc
WHERE dc.user_id = current_setting('app.current_tenant')::uuid
  AND 1 - (dc.embedding <=> $1::vector) > $2  -- min_score threshold
ORDER BY dc.embedding <=> $1::vector
LIMIT $3;  -- top_k
```

Python equivalent:
```python
async def pgvector_knn(
    query_vec: list[float],
    db: AsyncSession,
    top_k: int = 15,
    min_score: float = 0.55
) -> list[ChunkResult]:
    result = await db.execute(
        text("""
            SELECT dc.chunk_text, dc.article_id, dc.section_header,
                   a.slug, a.title,
                   1 - (dc.embedding <=> :vec::vector) as score
            FROM document_chunks dc
            JOIN articles a ON dc.article_id = a.id
            WHERE dc.user_id = current_setting('app.current_tenant')::uuid
              AND 1 - (dc.embedding <=> :vec::vector) > :min_score
            ORDER BY dc.embedding <=> :vec::vector
            LIMIT :top_k
        """),
        {"vec": str(query_vec), "min_score": min_score, "top_k": top_k}
    )
    return [ChunkResult(**row._mapping) for row in result]
```

### Deduplicate chunks to articles
```python
def dedupe_to_articles(chunks: list[ChunkResult]) -> list[ArticleResult]:
    """Keep the best-scoring chunk per article."""
    seen: dict[str, ChunkResult] = {}
    for chunk in chunks:
        if chunk.slug not in seen or chunk.score > seen[chunk.slug].score:
            seen[chunk.slug] = chunk
    return sorted(seen.values(), key=lambda x: x.score, reverse=True)
```

### Upsert pattern for chunks
```sql
-- When re-embedding a changed article, upsert not insert
INSERT INTO document_chunks (id, article_id, user_id, chunk_index, section_header, chunk_text, embedding)
VALUES ($1, $2, $3, $4, $5, $6, $7::vector)
ON CONFLICT (article_id, chunk_index)
DO UPDATE SET
    chunk_text = EXCLUDED.chunk_text,
    embedding = EXCLUDED.embedding,
    section_header = EXCLUDED.section_header;
```

---

## Indexing strategy

### B-Tree indexes (for pre-filtering)
```sql
-- These must exist for FANNS performance
CREATE INDEX ON articles (user_id);
CREATE INDEX ON articles (user_id, context);
CREATE INDEX ON articles (user_id, slug);
CREATE INDEX ON document_chunks (user_id);
CREATE INDEX ON document_chunks (article_id);
CREATE INDEX ON backlinks (user_id, to_slug);  -- for backlink queries
CREATE INDEX ON backlinks (user_id, from_slug); -- for forward link queries
```

### Full-text search index
```sql
CREATE INDEX ON raw_entries USING GIN(to_tsvector('english', body));
CREATE INDEX ON articles USING GIN(to_tsvector('english', body_md));

-- Query:
SELECT slug, title FROM articles
WHERE to_tsvector('english', body_md) @@ plainto_tsquery('english', $1)
  AND user_id = current_setting('app.current_tenant')::uuid;
```

---

## Query performance

### Always use EXPLAIN ANALYZE in development
```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM document_chunks
WHERE user_id = 'xxx'::uuid
ORDER BY embedding <=> '[...]'::vector
LIMIT 15;
```

Red flags in output:
- `Seq Scan on document_chunks` instead of `Index Scan` → missing B-Tree index on user_id
- `Filter: (user_id = ...)` happening after vector scan → pre-filter not working
- Very high `Actual Rows` with low `Rows Removed` → threshold not filtering effectively

### Connection pooling
```python
# Never create a new engine per request
# Create once at app startup, reuse everywhere
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,           # base connections
    max_overflow=20,        # burst connections
    pool_timeout=30,        # wait up to 30s for connection
    pool_pre_ping=True,     # verify connection alive before use
    pool_recycle=3600,      # recycle connections every hour
)
```

---

## Migrations

### Never modify a deployed migration file
Every schema change = new migration file.

```bash
# Add a column
npx prisma migrate dev --name add_importance_to_articles
# → creates migrations/YYYYMMDDHHMMSS_add_importance_to_articles/migration.sql

# Review the generated SQL before committing
cat prisma/migrations/*/migration.sql
```

### Include RLS in migration
```sql
-- migrations/001_initial_schema/migration.sql
CREATE TABLE articles (...);
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON articles ...;
```

### Rollback strategy
```sql
-- Always write a down migration in comments
-- For Cloud: Railway managed Postgres doesn't support automatic rollback
-- Keep last 3 migration files as a reference for manual rollback if needed
```

---

## JSONB vs relational columns

### What goes in JSONB (facets)
```sql
-- Facets: flexible schema, queried but not filtered-for-vector-search
-- OK in JSONB because we're not using facets as vector pre-filters
articles.facets = '{"health": ["anti-inflammatory"], "color": ["yellow"]}'

-- Query JSONB:
SELECT slug FROM articles
WHERE facets @> '{"health": ["anti-inflammatory"]}'::jsonb
  AND user_id = current_setting('app.current_tenant')::uuid;

-- Create GIN index for JSONB queries
CREATE INDEX ON articles USING GIN(facets);
```

### What must NOT go in JSONB (isolation fields)
```sql
-- user_id, context MUST be explicit columns with B-Tree indexes
-- Never put user_id inside a JSONB field — RLS cannot filter on it

-- WRONG
articles.metadata = '{"user_id": "xxx", "context": "house-2026"}'

-- RIGHT
articles.user_id = 'xxx'::uuid  -- explicit column, RLS works on it
articles.context = 'house-2026'  -- explicit column, B-Tree index
```

---

## Common mistakes

```sql
-- MISTAKE 1: Forgetting ON DELETE CASCADE
CREATE TABLE document_chunks (
    article_id UUID REFERENCES articles(id)  -- no CASCADE
);
-- If article is deleted, chunks remain as orphans
-- FIX: REFERENCES articles(id) ON DELETE CASCADE

-- MISTAKE 2: Missing index on vector column
-- Running kNN without HNSW index = full table scan O(n)
-- FIX: CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)

-- MISTAKE 3: Checking similarity wrong direction
-- pgvector <=> is DISTANCE (lower = more similar)
-- To get similarity, subtract from 1
SELECT 1 - (embedding <=> $query::vector) AS similarity  -- CORRECT
SELECT embedding <=> $query::vector AS similarity        -- WRONG (this is distance)

-- MISTAKE 4: Vector stored as text
embedding TEXT  -- wrong type, no index possible
-- FIX:
embedding vector(768)  -- correct type

-- MISTAKE 5: Running kNN before setting RLS
await db.execute("SELECT ... ORDER BY embedding <=> ...")  -- no RLS set yet!
-- FIX: Always set_config first, then query
```
