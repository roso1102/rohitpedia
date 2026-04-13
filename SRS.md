# Software Requirements Specification (SRS)
## Rohitpedia — Personal Intelligence Knowledge System
**Version:** 1.0 | **Date:** April 2026 | **Status:** Active Development

---

## 1. Introduction

### 1.1 Purpose

This document specifies the functional and non-functional requirements for Rohitpedia, a multi-user personal knowledge intelligence system. It serves as the authoritative reference for development decisions, testing criteria, and architectural trade-offs.

### 1.2 Scope

Rohitpedia is a hosted web application and Telegram bot that enables users to capture information from multiple sources, automatically synthesises it into a structured wiki, discovers non-obvious connections between saved knowledge, and surfaces intelligent memory aids. The system supports multiple isolated users sharing infrastructure but never sharing data.

### 1.3 Definitions

| Term | Definition |
|---|---|
| Raw entry | Original captured content, stored unmodified |
| Wiki article | LLM-synthesised structured markdown document |
| Wikilink | `[[slug]]` reference from one article to another |
| Backlink | Reverse index entry: article B linked from article A |
| Context | Life-chapter tag grouping related articles (e.g. `house-2026`) |
| Facet | Structured typed field extracted from an article (e.g. `colors: [cream]`) |
| Tunnel | Non-obvious connection between two articles discovered by the intelligence engine |
| Tier 2 tunnel | Found via embedding similarity + PPR graph walk |
| Tier 3 tunnel | Found via facet bundle differential synthesis |
| RNS score | Relevance × Novelty × Surprise composite score from SerenQA framework |
| PPR | Personalized PageRank — graph-structural proximity scoring |
| Absorb pass | LLM processing of raw entries into wiki articles |
| Decay | Exponential time-weighting that reduces old captures' influence |
| ERA summary | Auto-generated article summarising a closed life-chapter context |

---

## 2. System Overview

### 2.1 System context

```
External actors:
  User → Telegram Bot (primary capture)
  User → Web UI (reading, review, search)
  
Internal services:
  FastAPI webhook server
  pg-boss workers (ingest, absorb, embed, intelligence)
  PostgreSQL + pgvector (single database)
  Gemini Flash API (absorb, rationale, synthesis)
  Phi-3 mini via Ollama (facets, intent, classification)
  nomic-embed-text via Ollama (vector embeddings)
  whisper.cpp (voice transcription)
  Docling + pymupdf (PDF extraction)
  Firecrawl → Jina Reader → trafilatura (URL extraction)
```

### 2.2 User roles

**Primary user (knowledge worker):** Captures content via Telegram daily, reviews intelligence suggestions weekly, reads wiki and searches as needed.

**System administrator (same person for MVP):** Manages infrastructure, monitors job queue, configures LLM providers.

---

## 3. Functional Requirements

### 3.1 Capture (FR-CAP)

**FR-CAP-01:** System SHALL accept text messages from Telegram and store them as raw entries within 3 seconds of receipt.

**FR-CAP-02:** System SHALL acknowledge all Telegram messages with a `✓` confirmation within 1 second, regardless of processing state.

**FR-CAP-03:** System SHALL extract clean text from URLs using a layered strategy: Firecrawl API → Jina Reader (`r.jina.ai`) → trafilatura → Playwright headless. Fallback to URL + page title if all layers return < 200 characters.

**FR-CAP-04:** System SHALL transcribe voice messages (.ogg, .mp3, .m4a) using whisper.cpp locally. Transcription accuracy SHALL meet the `base.en` model standard (≈ 95% WER on clear speech).

**FR-CAP-05:** System SHALL process images via two paths: (a) EasyOCR for images containing text (detected by text pixel density heuristic), (b) LLaVA 7B or Gemini Flash vision for photos/illustrations. Output stored as description text.

**FR-CAP-06:** System SHALL extract text from PDFs using Docling (structure-preserving markdown) with pymupdf fallback. Scanned PDF pages SHALL be processed via Tesseract OCR.

**FR-CAP-07 (PDF short ≤ 20 pages):** Full extracted markdown SHALL be stored in raw_entries.body. A single absorb call SHALL synthesise it into a wiki article.

**FR-CAP-08 (PDF long > 20 pages):** System SHALL split by chapter/heading boundaries or 20-page windows. Each section SHALL be absorbed independently. A meta-absorb pass SHALL create a hub article with links to section articles. Full raw markdown SHALL be stored regardless.

**FR-CAP-09:** System SHALL support `/context <slug>` Telegram command to set a sticky context. All subsequent captures SHALL inherit this context until `/context clear` is issued.

**FR-CAP-10:** Original media files (images, audio, PDFs) SHALL be stored in `./media/{user_id}/{year}/{month}/` locally, with path recorded in `media_files` table. Images SHALL be compressed to JPEG 85% quality. PDFs stored as-is.

**FR-CAP-11:** System SHALL detect query intent in Telegram messages (phrases starting with what/find/show/tell me about/search) and route to the search pipeline instead of the capture pipeline.

### 3.2 Absorb (FR-ABS)

**FR-ABS-01:** Absorb worker SHALL run within 90 seconds of ingest completion for text and URL captures.

**FR-ABS-02:** Before calling the LLM, absorb worker SHALL embed the raw entry and retrieve the top-8 semantically related existing articles from pgvector. These SHALL be provided as context to the LLM (RAG routing).

**FR-ABS-03:** LLM SHALL be instructed to: synthesise content into the most relevant existing article OR create a new article, extract facets as structured JSON, insert `[[wikilinks]]` only to articles provided in context (never hallucinate links to non-existent articles).

**FR-ABS-04:** After LLM returns markdown, system SHALL parse wikilinks using `markdown-it-py` AST parser (not regex). All identified links SHALL be written to the `backlinks` table as atomic operations within the same transaction as the article upsert.

**FR-ABS-05:** Absorb SHALL use Gemini context caching for the system prompt (formatting rules, facet schema, writing standards). Cache SHALL be reused across all absorb calls in the same day.

**FR-ABS-06:** Absorb SHALL be idempotent. Re-running on the same raw entry SHALL produce the same article state (not append duplicates).

**FR-ABS-07:** Facet extraction SHALL use a separate, cheaper model call (Phi-3 mini locally) with a structured JSON schema prompt. Output SHALL be validated against the schema before storage.

### 3.3 Embedding (FR-EMB)

**FR-EMB-01:** After absorb, embed worker SHALL chunk the wiki article by `##`/`###` markdown headers. Sections longer than 512 tokens SHALL be recursively split with 100-token overlap.

**FR-EMB-02:** Each chunk SHALL be embedded using nomic-embed-text (768 dimensions). Chunks SHALL be stored in `document_chunks` with: `article_id`, `user_id`, `chunk_index`, `section_header`, `chunk_text`, `embedding vector(768)`.

**FR-EMB-03:** The HNSW index on `document_chunks.embedding` SHALL use `m=16, ef_construction=64`. Index SHALL filter on `user_id` before vector comparison.

**FR-EMB-04:** `embed_state` table SHALL track last embedded timestamp per article. Re-embedding SHALL only occur if article `updated_at` is newer than last embed timestamp.

### 3.4 Intelligence — Tunnels (FR-TUN)

**FR-TUN-01:** Intelligence cron SHALL run nightly at 2am per user. Total runtime SHALL not exceed 15 minutes for 5000 articles.

**FR-TUN-02:** For each article modified in the past 7 days, system SHALL: (a) run pgvector kNN (top-15, min_score=0.55, filtered by user_id), (b) run PPR on the user's knowledge graph (seed = modified article, alpha=0.85, top-15 structural neighbours).

**FR-TUN-03:** Candidate pools SHALL be merged and filtered: remove pairs in `dislike_pairs`, remove already-linked pairs (check both directions), remove self-links.

**FR-TUN-04:** RNS scoring SHALL be applied: `R = 0.6×emb_score + 0.4×ppr_score`, `N = 0 if already linked else 1.0`, `S = 1/(1 + hops×0.3)`, `decay_w = e^(-λ × days_since_updated)`, `final = R × N × (0.5 + 0.5×S) × decay_w`. Tier assignment: > 0.75 = Tier2, > 0.50 = Tier3, else discard.

**FR-TUN-05:** Top-8 non-noise candidates per source article SHALL be passed to Gemini Flash for rationale generation. One LLM call per source article, ~15k tokens. Rationale SHALL explain the non-obvious connection in plain language.

**FR-TUN-06:** Results SHALL be written to `tunnel_suggestions` table with status `pending`. System SHALL NOT auto-insert any wikilinks.

**FR-TUN-07:** User accept action SHALL: mark suggestion status as `accepted`, return the `[[wikilink]]` text for the user to insert manually (or trigger a nudge to insert it). User reject action SHALL: write pair to `dislike_pairs`, mark suggestion status as `rejected`, pair SHALL never resurface.

### 3.5 Intelligence — Differential Synthesis (FR-DIFF)

**FR-DIFF-01:** For each active context with ≥ 5 articles, diff synthesis SHALL run nightly. Input: JSON bundle of all article facets in that context (~4k tokens). Model: Phi-3 mini locally or Gemini Flash 8B.

**FR-DIFF-02:** LLM SHALL identify: convergent patterns (e.g. colour palette consensus), divergent tensions (contradictory preferences), non-obvious bridges (value appearing across multiple facet categories).

**FR-DIFF-03:** Output SHALL be written to `diff_suggestions` table. Results SHALL appear in the Intelligence > Diff Viewer page.

### 3.6 Memory and Resurface (FR-MEM)

**FR-MEM-01:** Nightly resurface engine SHALL detect: post-ready (≥ 5 articles sharing a dominant facet value in same context), resurgence (article with decay < 0.2 whose facets appear in ≥ 3 recent captures), era-close (context activity < 5% of historical average for 14 days AND ≥ 8 wiki articles exist).

**FR-MEM-02:** Time-capsule check-ins SHALL run weekly. System SHALL select ≤ 5 articles older than 180 days with decay < 0.2 and push Telegram messages: "You saved [title] — still relevant? [Keep] [Archive] [Dislike]". User response SHALL update the article's `importance` score or add to `avoid` facets.

**FR-MEM-03:** Conflict detection SHALL run in two passes: (1) deterministic — find any facet value appearing in both `facets` and `avoid` within same context, (2) LLM second pass on flagged pairs only. Results written to `conflicts` table.

**FR-MEM-04:** On era-close signal, system SHALL draft an era summary article using Gemini Pro/Claude Sonnet (highest quality call in the system). User SHALL review before publication.

### 3.7 Edit Nudges (FR-NUDGE)

**FR-NUDGE-01:** Nightly nudge engine SHALL detect: stubs (`len(body) < 300 AND entry_mentions >= 3`), oversized articles (`section_count >= 10`), uncertain facets (value containing `?`), high-decay articles (`decay_score < 0.10`), unresolved wikilinks (reference to slug with no article), duplicate articles (embedding cosine > 0.85 between two articles of same user).

**FR-NUDGE-02:** Nudges SHALL be written to `nudges` table. Each nudge SHALL include: type, affected article slug, detected reason, suggested action.

**FR-NUDGE-03:** Weekly Telegram digest SHALL report: N tunnels pending, M memory items, K nudges. SHALL include deep link to Intelligence dashboard.

### 3.8 Search (FR-SEARCH)

**FR-SEARCH-01:** Keyword search SHALL use PostgreSQL `tsvector` full-text search on `raw_entries.body` and `articles.body_md`. Results SHALL return within 500ms.

**FR-SEARCH-02:** Semantic search SHALL embed the query using nomic-embed-text and run pgvector kNN on `document_chunks`, filtered by `user_id` and optionally by `context`. Top-20 chunks SHALL be deduplicated to article level. Results SHALL return within 800ms.

**FR-SEARCH-03:** Telegram query answering SHALL: embed query, kNN search (top-5 articles), format top-3 as Telegram message with article title, 2-sentence summary, wiki deep link. Response SHALL be delivered within 2 seconds.

### 3.9 Multi-user isolation (FR-ISO)

**FR-ISO-01:** All tables SHALL have `user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE`.

**FR-ISO-02:** PostgreSQL Row-Level Security SHALL be enabled on all tables. Policy: `USING (user_id = current_setting('app.current_tenant')::uuid)`.

**FR-ISO-03:** FastAPI middleware SHALL set `SET app.current_tenant = '{user_uuid}'` at the start of every request and worker job. This SHALL occur before any database query.

**FR-ISO-04:** pgvector queries SHALL include `user_id = $current_user` as a filter condition. This filter SHALL be evaluated before the vector index traversal (pre-filter), not after (post-filter). This is achieved by placing the filter in a WHERE clause on a query that uses the index with `user_id` included.

**FR-ISO-05:** Telegram chat_id to user_id mapping SHALL be stored in `users.telegram_id`. All bot messages SHALL be resolved to `user_id` before any processing begins.

---

## 4. Non-Functional Requirements

### 4.1 Performance

**NFR-PERF-01:** Telegram webhook acknowledgement: < 1 second p99.

**NFR-PERF-02:** Wiki article page load: < 1 second p95 for up to 2000 articles per user.

**NFR-PERF-03:** Semantic search: < 800ms p95.

**NFR-PERF-04:** Backlink query: < 50ms (indexed SQL, no vector involved).

**NFR-PERF-05:** Absorb completion (text/URL): < 90 seconds from capture.

**NFR-PERF-06:** Intelligence nightly cron: < 15 minutes for 5000 articles per user.

**NFR-PERF-07:** Graph render: < 2 seconds for 500 nodes in browser.

### 4.2 Scalability

**NFR-SCALE-01:** System SHALL support 4–8 concurrent users in Phase 1 without degradation.

**NFR-SCALE-02:** pgvector HNSW index SHALL handle 250,000 chunks (10 users × 5000 articles × 5 chunks) with sub-100ms query latency.

**NFR-SCALE-03:** Database SHALL support up to 50GB total storage per user on local deployment (media included).

**NFR-SCALE-04:** Architecture SHALL support horizontal worker scaling (multiple pg-boss worker processes) without code changes.

### 4.3 Reliability

**NFR-REL-01:** Worker jobs SHALL be idempotent. Retry on failure SHALL produce the same result as first execution.

**NFR-REL-02:** LLM call failures SHALL be caught, logged, and retried up to 3 times with exponential backoff. After 3 failures, raw entry SHALL remain in `pending` status with error logged.

**NFR-REL-03:** Database transactions SHALL wrap: article upsert + backlink writes + facet writes. Partial completion is not acceptable.

**NFR-REL-04:** Media files SHALL be verified (size > 0, valid MIME type) before storage. Corrupt media SHALL fail gracefully with user notification.

### 4.4 Security

**NFR-SEC-01:** All database queries SHALL execute within an RLS session. No query SHALL bypass the `app.current_tenant` policy.

**NFR-SEC-02:** Telegram bot webhook SHALL validate the `X-Telegram-Bot-Api-Secret-Token` header on every incoming request.

**NFR-SEC-03:** User session tokens SHALL expire after 30 days. Tokens SHALL be stored as bcrypt hashes.

**NFR-SEC-04:** Media file paths SHALL be validated to prevent path traversal. No `..` sequences in media paths.

**NFR-SEC-05:** Slug inputs to API endpoints SHALL be validated against `^[a-zA-Z0-9._/-]+$` pattern.

**NFR-SEC-06:** LLM prompts SHALL not include raw user data from other users under any code path.

### 4.5 Data integrity

**NFR-DATA-01:** Raw entries SHALL never be deleted or modified after creation. They are append-only truth.

**NFR-DATA-02:** Backlinks table SHALL use `ON CONFLICT (from_slug, to_slug, user_id) DO NOTHING` for all inserts. No duplicate backlinks.

**NFR-DATA-03:** Dislike pairs SHALL be checked bidirectionally (a,b) and (b,a) before any tunnel suggestion is written.

**NFR-DATA-04:** Article slugs SHALL be unique per user. Slug format: `lowercase-hyphenated`. Auto-generated from title.

### 4.6 Observability

**NFR-OBS-01:** Every LLM call SHALL be logged to `llm_usage` table: user_id, model, tokens_in, tokens_out, duration_ms, task_type, success.

**NFR-OBS-02:** Worker jobs SHALL log start, completion, and failure to `job_logs` table.

**NFR-OBS-03:** All HTTP errors (4xx, 5xx) SHALL be logged with request context (no PII in logs).

---

## 5. Data model overview

### Core tables

```sql
users               -- auth + telegram binding
raw_entries         -- append-only capture log
articles            -- synthesised wiki content
document_chunks     -- vector embeddings (HNSW index)
backlinks           -- graph edges (wikilinks + tunnels)
facets              -- structured article metadata
tunnel_suggestions  -- pending/accepted/rejected tunnels
diff_suggestions    -- differential synthesis outputs
dislike_pairs       -- permanent rejection index
nudges              -- edit nudge queue
media_files         -- media storage index
llm_usage           -- LLM cost/performance logging
jobs                -- pg-boss managed
```

### RLS enforcement pattern

```sql
-- Applied to every table
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON articles
  USING (user_id = current_setting('app.current_tenant')::uuid);

-- FastAPI middleware (every request):
await db.execute(f"SET app.current_tenant = '{user_id}'")
```

---

## 6. External system interfaces

| System | Interface | Direction | Sensitivity |
|---|---|---|---|
| Telegram API | Webhook POST | Inbound | Bot token in header |
| Gemini Flash API | REST | Outbound | API key in env |
| Ollama (local) | HTTP REST | Outbound | localhost only |
| Firecrawl API | REST | Outbound | API key in env |
| Jina Reader | REST (no key) | Outbound | Public |
| whisper.cpp | Local process | Internal | n/a |
| Docling | Python lib | Internal | n/a |
| Cloudflare R2 | S3-compatible | Outbound (Phase 4) | Access key |

---

## 7. Acceptance criteria summary

| Phase | Key acceptance criteria |
|---|---|
| 1 | Telegram capture → wiki article in < 90s. Two users fully isolated. Bot query answers in < 2s. |
| 2 | Tunnel suggestions surface ≥ 1 non-obvious connection per 20 articles. Already-linked pairs score < 0.1 RNS. Nightly cron < 5 min for 200 articles. |
| 3 | Post-ready detection accurate for test context. Time-capsule prompts weekly. Era summary coherent. |
| 4 | p95 wiki load < 500ms for 5000 articles. Intelligence cron < 15 min for 5000 articles. |
