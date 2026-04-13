# ACTION_PLAN.md
## Current Sprint: Phase 1 — Foundation and Capture Loop

**Sprint goal:** End-to-end pipeline from Telegram capture to searchable wiki article. Two users isolated. Zero manual scripts.

**Context files to read before starting:**
- `docs/PROTOCOL.md` — role, workflow, standards
- `docs/SRS.md` — requirements (especially FR-CAP, FR-ABS, FR-ISO)
- `docs/ROADMAP.md` Phase 1 — detailed build instructions with code

---

## Task 1: Database schema and RLS setup
**Estimated time:** 6–8 hours
**Output:** Postgres running locally via Docker, all tables created, RLS verified

### Subtasks

**1.1 — Docker compose setup**
- [x] Create `docker-compose.yml` with Postgres 16 + pgvector
- [x] Set `POSTGRES_PASSWORD` from `.env`
- [x] Expose port 5432 locally
- [x] Add health check
- [x] Verify: `docker compose up -d && docker compose ps` shows healthy

```yaml
# docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: rohitpedia
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5
volumes:
  pgdata:
```

**1.2 — Create all core tables**
- [x] Run the SQL from `ROADMAP.md` Phase 1A exactly as written
- [x] Verify extensions enabled: `SELECT * FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')`
- [x] Verify all tables exist: `\dt` in psql
- [x] Verify HNSW index created: `\di document_chunks*`

**1.3 — Create Prisma schema (Next.js)**
- [x] Initialise Prisma in `web-ui/`
- [x] Mirror all tables in `schema.prisma`
- [x] Run `npx prisma migrate dev --name init`
- [x] Verify: `npx prisma studio` shows all tables

**1.4 — Verify RLS isolation**
- [x] Create two test users (user_a, user_b) via SQL
- [x] Insert article as user_a (with SET app.current_tenant)
- [x] Query as user_b — verify 0 rows returned
- [x] This test MUST PASS before any other work begins

```sql
-- Test script: run this manually
BEGIN;
SELECT set_config('app.current_tenant', 'user-a-uuid', true);
INSERT INTO articles (id, user_id, slug, title, body_md)
  VALUES (gen_random_uuid(), 'user-a-uuid'::uuid, 'test', 'Test', 'body');
COMMIT;

BEGIN;
SELECT set_config('app.current_tenant', 'user-b-uuid', true);
SELECT count(*) FROM articles WHERE slug = 'test';
-- MUST return 0
COMMIT;
```

**Context:** SRS.md FR-ISO-01 through FR-ISO-05, ROADMAP.md Phase 1A

---

## Task 2: FastAPI backend skeleton
**Estimated time:** 4–5 hours
**Output:** FastAPI app running, Telegram webhook endpoint reachable, RLS middleware working

### Subtasks

**2.1 — Project structure**
- [x] Create `backend/` directory structure from ROADMAP.md
- [x] Create `requirements.txt` with: fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, python-telegram-bot, httpx, pydantic

**2.2 — Database connection**
- [x] Create `backend/db.py` with async SQLAlchemy engine
- [x] Connection string from `DATABASE_URL` env var
- [x] Pool settings: `pool_size=10, max_overflow=20, pool_pre_ping=True`

**2.3 — RLS middleware**
- [x] Create `backend/api/middleware.py`
- [x] Middleware extracts user_id from session/telegram context
- [x] Executes `SET LOCAL app.current_tenant = '{user_id}'` before every request
- [x] Verify: manually call an endpoint, check Postgres session variables

**2.4 — Telegram webhook endpoint**
- [x] Create `backend/api/webhook.py`
- [x] Validate `X-Telegram-Bot-Api-Secret-Token` header
- [x] Parse Telegram Update object
- [x] Write raw_entry to DB
- [x] Return HTTP 200 immediately (before any processing)
- [x] Enqueue ingest job

**2.5 — Local webhook testing**
- [x] Install ngrok
- [x] `ngrok http 8123` → get public URL
- [x] `python bot/setup_webhook.py --url https://xxxx.ngrok.io/webhook/telegram`
- [x] Send test message to bot → verify raw_entry appears in DB

**Context:** SRS.md FR-CAP-01, FR-CAP-02, FR-ISO-03, PROTOCOL.md Section 4

---

## Task 3: Ingest worker — media extraction
**Estimated time:** 8–10 hours
**Output:** All media types extracted correctly, stored in raw_entries and media_files

### Subtasks

**3.1 — Worker infrastructure (pg-boss)**
- [x] Install: `pip install pgboss`
- [x] Create `backend/workers/runner.py` — polls pg-boss, dispatches to handlers
- [x] Create job types: `ingest`, `absorb`, `embed`
- [x] Test: enqueue dummy job → worker picks it up → logs completion

**3.2 — Text extraction (trivial)**
- [x] Text messages: store body as-is in raw_entries.body
- [x] Add `source_type = 'text'`

**3.3 — URL extraction (layered)**
- [x] Install: `pip install firecrawl-py trafilatura httpx`
- [x] Implement layered strategy from ROADMAP.md (Firecrawl → Jina → trafilatura → Playwright)
- [x] Test with 5 URLs: a normal article, a Medium article, a Twitter/X link, a PDF link, a 404
- [x] Each layer logs which one succeeded

**3.4 — Voice transcription**
- [x] Install whisper.cpp (see README setup section)
- [x] Download Telegram .ogg file → save to `./media/`
- [x] Run `whisper-cpp -m models/ggml-base.en.bin -f audio.ogg`
- [x] Store transcript as body, media_path pointing to .ogg
- [x] Test: send a 30-second voice message, verify transcript is reasonable

**3.5 — Image processing**
- [x] Phase-1 implementation switched to API-first: Gemini Flash vision (no EasyOCR install yet)
- [x] Download image from Telegram → save to `./media/`
- [x] Use Gemini Flash to extract text (if present) or return concise image description
- [x] Local model path (EasyOCR/LLaVA) deferred and will be added in a later phase
- [x] Test: screenshot with text, photo of food, diagram

**3.6 — PDF processing (Docling-first)**
- [ ] Install: `pip install docling pymupdf pytesseract`
- [ ] Docling path: `converter.convert(path).document.export_to_markdown()`
- [ ] Fallback: pymupdf text extraction
- [ ] Scanned pages: extract page as image → Tesseract OCR
- [ ] Short PDF (≤ 20 pages): store full markdown as body
- [ ] Long PDF (> 20 pages): split by headings → store sections list in body as JSON
- [ ] Test: 5-page article PDF, 80-page report PDF, scanned document

**3.7 — Media file storage**
- [ ] Save all media to `./media/{user_id}/{yyyy}/{mm}/`
- [ ] Insert row in `media_files` table
- [ ] Verify: after processing, media_files row exists with correct path and mime_type

**Context:** SRS.md FR-CAP-03 through FR-CAP-10, ROADMAP.md Phase 1C

---

## Task 4: Absorb worker — LLM synthesis
**Estimated time:** 8–10 hours
**Output:** Raw entries synthesised into wiki articles with facets and wikilinks

### Subtasks

**4.1 — LLM provider abstraction**
- [ ] Create `backend/llm/provider.py` — abstract base class `LLMProvider`
- [ ] Methods: `complete(prompt, max_tokens, schema) -> dict`
- [ ] Create `backend/llm/gemini.py` — Gemini Flash 2.5 implementation
- [ ] Create `backend/llm/local.py` — Ollama implementation (for Phi-3 mini)
- [ ] Provider selected by `LLM_ABSORB_PROVIDER` env var

**4.2 — System prompt engineering**
- [ ] Write the absorb system prompt (formatting rules, facet schema, wikilink rules)
- [ ] Key constraint in prompt: "Only create [[wikilinks]] to slugs in the provided candidate list. Never invent slug names."
- [ ] Implement Gemini context caching for this prompt

**4.3 — RAG routing (pre-absorb embedding search)**
- [ ] Install: `pip install sentence-transformers` or use Ollama HTTP API for nomic-embed
- [ ] Embed the raw entry text
- [ ] Query pgvector for top-8 related articles (min cosine 0.55)
- [ ] These articles passed to LLM as context

**4.4 — Absorb call**
- [ ] Build prompt: system + entry text + top-8 article bodies + slug list
- [ ] Call Gemini Flash 2.5
- [ ] Parse response: expects `{slug, title, body_md, facets}`
- [ ] Validate response schema
- [ ] Test: send 3 notes about turmeric → single turmeric.md article with health facets

**4.5 — AST wikilink extraction**
- [ ] Install: `pip install markdown-it-py`
- [ ] Function: `extract_wikilinks_ast(markdown) -> list[str]`
- [ ] Must NOT extract links from code blocks or code spans
- [ ] Test: markdown with links in body, in code blocks, in headings — verify only body links extracted

**4.6 — Transactional write**
- [ ] Article upsert + backlinks + facets in one `async with db.begin()` block
- [ ] Verify: after absorb, check article exists, backlinks table has rows, facets populated

**4.7 — Facet extraction (Phi-3 mini)**
- [ ] Separate call after absorb: send article body to Phi-3 mini with JSON schema prompt
- [ ] Schema: `{category[], themes[], colors[], health[], cuisine[], style[], sentiment}`
- [ ] Validate output against schema
- [ ] Store in `articles.facets`
- [ ] Test: turmeric article → `{health: ["anti-inflammatory"], cuisine: ["Indian"], color: ["yellow"]}`

**Context:** SRS.md FR-ABS-01 through FR-ABS-07, ROADMAP.md Phase 1D

---

## Task 5: Embed worker
**Estimated time:** 4 hours
**Output:** All wiki articles chunked and indexed in pgvector

### Subtasks

**5.1 — Chunking function**
- [ ] `chunk_article(body_md) -> list[Chunk]`
- [ ] Split by `##` and `###` headers
- [ ] Long sections (> 512 tokens): recursive split with 100-token overlap
- [ ] Minimum chunk size: 80 chars
- [ ] Each Chunk has: `index`, `header`, `text`

**5.2 — Embedding and upsert**
- [ ] For each chunk: call nomic-embed-text via Ollama
- [ ] Upsert into `document_chunks`: `ON CONFLICT (article_id, chunk_index) DO UPDATE`
- [ ] Track embed state: update `articles.embed_state` timestamp
- [ ] Only re-embed if `articles.updated_at > embed_state`

**5.3 — Verify index**
- [ ] After embedding 20 test articles, run: `SELECT count(*) FROM document_chunks`
- [ ] Run a test kNN query: `SELECT chunk_text FROM document_chunks ORDER BY embedding <=> '[...]' LIMIT 5`
- [ ] Verify results are semantically relevant

**Context:** SRS.md FR-EMB-01 through FR-EMB-04

---

## Task 6: Telegram bot — query mode
**Estimated time:** 4 hours
**Output:** Bot answers "what did I save about X" inline within 2 seconds

### Subtasks

**6.1 — Intent detection**
- [ ] `is_query(text) -> bool`
- [ ] Patterns: starts with what/find/show me/tell me/search/do i have/where did i save
- [ ] Test 20 messages — verify correct classification

**6.2 — Query pipeline**
- [ ] Embed query text
- [ ] pgvector kNN (top-5, user_id filtered)
- [ ] Deduplicate to article level (top-3)
- [ ] Format Telegram message: title, 2-sentence summary, wiki link

**6.3 — Reply formatting**
```python
def format_query_reply(articles: list[Article], query: str) -> str:
    lines = [f"📖 Here's what I found about *{query}*:\n"]
    for art in articles:
        lines.append(f"*{art.title}*")
        lines.append(f"{art.body_md[:200].strip()}...")
        lines.append(f"[Read more →]({WEBAPP_URL}/wiki/{art.slug})\n")
    return "\n".join(lines)
```

**6.4 — Latency test**
- [ ] Send query → measure time to bot reply
- [ ] Must be < 2 seconds p95
- [ ] If > 2s, profile: is it embed latency, DB query, or formatting?

**Context:** SRS.md FR-CAP-11, FR-SEARCH-03

---

## Task 7: Next.js web UI — Phase 1 pages
**Estimated time:** 6–8 hours
**Output:** Article reader, backlinks panel, article index, context filter, basic search

### Subtasks

**7.1 — Article reader page** (`/wiki/[slug]`)
- [ ] Server-side fetch article from Postgres via Prisma
- [ ] Render markdown with `react-markdown` + `remark-gfm`
- [ ] Convert `[[slug]]` wikilinks to Next.js `<Link>` components
- [ ] Sidebar: backlinks (query `SELECT from_slug FROM backlinks WHERE to_slug = $slug`)
- [ ] Sidebar: facets displayed as pills

**7.2 — Article index** (`/wiki`)
- [ ] List all user's articles (from Prisma + RLS via NextAuth session)
- [ ] Filter by context (dropdown)
- [ ] Sort by updated_at
- [ ] Article count per context

**7.3 — Keyword search** (`/search`)
- [ ] Server action: `SELECT * FROM articles WHERE to_tsvector('english', body_md) @@ plainto_tsquery($query)`
- [ ] Display results with highlighted snippets

**7.4 — Auth (Telegram-based)**
- [ ] User visits web app → if no session, show "Connect Telegram" button
- [ ] Telegram login widget (official Telegram auth widget)
- [ ] On auth: create/find user in DB, create session token
- [ ] NextAuth adapter with Postgres session store

**Context:** SRS.md FR-SEARCH-01, README.md Wiki tabs reference

---

## Task 8: End-to-end test and Phase 1 gate
**Estimated time:** 3 hours
**Output:** All Phase 1 tests pass

### Subtasks

**8.1 — Run full test suite**
```bash
cd backend && pytest tests/ -v
cd web-ui && npm run test
```

**8.2 — Manual end-to-end walkthrough**
- [ ] Send text message → article in wiki within 90s
- [ ] Send URL → extracted article in wiki
- [ ] Send voice note → transcript in wiki
- [ ] Send PDF → article(s) in wiki
- [ ] Send image → description in wiki
- [ ] Ask query → bot replies in < 2s
- [ ] Open web UI → article visible, backlinks shown, facets shown

**8.3 — Isolation verification (critical)**
- [ ] Create user A, send 5 messages
- [ ] Create user B, send 5 different messages
- [ ] Log in as user B → confirm zero of user A's articles visible
- [ ] Confirm this in DB directly with psql

**8.4 — Document completion**
- [ ] Update ROADMAP.md Phase 1 with completion date
- [ ] Note any deviations from the plan
- [ ] List any tech debt created (things done quickly that need revisiting)

---

## Upcoming tasks (Phase 2 preview)

These are NOT in scope for Phase 1. Do not start until Phase 1 gate passes.

- [ ] Intelligence database tables (tunnel_suggestions, dislike_pairs, diff_suggestions)
- [ ] NetworkX graph builder from backlinks
- [ ] PPR implementation
- [ ] RNS scoring function
- [ ] Nightly cron runner
- [ ] Intelligence UI (tunnels, diff viewer)
- [ ] D3 knowledge graph visualisation

---

## Known risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Gemini API rate limit hit | Medium | High | Implement exponential backoff, queue absorb jobs with delay |
| whisper.cpp inaccurate transcription | Low | Medium | Use `small.en` model if `base.en` quality insufficient |
| Docling fails on complex PDFs | Medium | Medium | pymupdf fallback always in place |
| pgvector HNSW index slow build | Low | Low | Build index after initial data load, not during schema creation |
| Firecrawl API key missing | High (env not set) | Low | Jina Reader works without any key as fallback |
| RLS accidentally bypassed | Low | Critical | Test isolation script in CI — block deploy if it fails |

---

## Environment setup reminder

```bash
# Minimum to start development
TELEGRAM_BOT_TOKEN=       # get from @BotFather
GEMINI_API_KEY=           # console.cloud.google.com
DATABASE_URL=postgresql://postgres:password@localhost:5432/rohitpedia
WEBAPP_URL=http://localhost:3000
LLM_ABSORB_PROVIDER=gemini
LLM_ABSORB_MODEL=gemini-2.5-flash
LLM_FACETS_PROVIDER=ollama
LLM_FACETS_MODEL=phi3:mini
EMBED_PROVIDER=ollama
EMBED_MODEL=nomic-embed-text
OLLAMA_HOST=http://localhost:11434
TRANSCRIBE_PROVIDER=local
WHISPER_MODEL_PATH=./models/ggml-base.en.bin
MEDIA_STORAGE=local
MEDIA_DIR=./media
```
