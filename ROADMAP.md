# Rohitpedia — Development Roadmap
**Last updated:** April 2026

Each phase builds directly on the previous. Do not skip ahead. Test gates must pass before the next phase begins.

---

## Phase 1 — Foundation and Capture Loop
**Duration:** Weeks 1–3 | **Effort:** ~40 hours | **Status:** Not started

### Objective
A working end-to-end pipeline: Telegram message → raw entry → wiki article → searchable. Two users with complete data isolation. Zero manual scripts.

### What you build

#### 1A — Database (Week 1, ~10 hours)

**Schema setup:**
```sql
-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for fuzzy text search

-- Core tables (all with RLS)
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  telegram_id BIGINT UNIQUE,
  active_context TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE raw_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  source_type TEXT NOT NULL, -- text|url|voice|image|pdf
  media_path TEXT,
  source_url TEXT,
  context TEXT,
  status TEXT DEFAULT 'pending', -- pending|processing|absorbed|failed
  absorbed_into TEXT, -- article slug
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE articles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  body_md TEXT NOT NULL DEFAULT '',
  context TEXT,
  facets JSONB DEFAULT '{}',
  importance INT DEFAULT 1,
  avoid JSONB DEFAULT '[]',
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, slug)
);

CREATE TABLE document_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  section_header TEXT,
  chunk_text TEXT NOT NULL,
  embedding vector(768),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX ON document_chunks (user_id);

CREATE TABLE backlinks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  from_slug TEXT NOT NULL,
  to_slug TEXT NOT NULL,
  link_type TEXT DEFAULT 'wikilink', -- wikilink|semantic_tunnel|differential
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, from_slug, to_slug)
);

CREATE TABLE media_files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  entry_id UUID REFERENCES raw_entries(id) ON DELETE CASCADE,
  file_path TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes BIGINT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS on every table
ALTER TABLE raw_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE backlinks ENABLE ROW LEVEL SECURITY;
ALTER TABLE media_files ENABLE ROW LEVEL SECURITY;

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

-- Full text search index
CREATE INDEX ON raw_entries USING GIN(to_tsvector('english', body));
CREATE INDEX ON articles USING GIN(to_tsvector('english', body_md));
```

**pg-boss setup:**
```sql
-- pg-boss manages its own schema. In Python:
-- from pgboss import PgBoss
-- boss = PgBoss(DATABASE_URL)
-- await boss.start()
```

#### 1B — FastAPI backend (Week 1–2, ~12 hours)

```
backend/
├── api/
│   ├── main.py           # FastAPI app, middleware
│   ├── webhook.py        # Telegram webhook handler
│   └── middleware.py     # RLS session variable injection
├── workers/
│   ├── ingest.py         # Media extraction
│   ├── absorb.py         # LLM synthesis
│   └── embed.py          # Vector embedding
├── llm/
│   ├── provider.py       # Abstract LLMProvider base class
│   ├── gemini.py         # Gemini Flash implementation
│   └── local.py          # Ollama implementation
└── db.py                 # SQLAlchemy async engine
```

**Key patterns:**

RLS middleware (every request and worker job):
```python
@app.middleware("http")
async def set_rls_context(request: Request, call_next):
    user_id = get_user_id_from_session(request)
    async with db.begin():
        await db.execute(
            f"SET LOCAL app.current_tenant = '{user_id}'"
        )
    return await call_next(request)
```

Worker RLS pattern:
```python
async def run_absorb_job(job_data: dict):
    user_id = job_data["user_id"]
    async with db.begin():
        await db.execute(f"SET LOCAL app.current_tenant = '{user_id}'")
        # all subsequent queries in this transaction respect RLS
        await _do_absorb(job_data["entry_id"])
```

#### 1C — Ingest worker (Week 2, ~8 hours)

URL extraction strategy (layered):
```python
async def extract_url(url: str) -> str:
    # Layer 1: Firecrawl (best quality, handles JS)
    if FIRECRAWL_API_KEY:
        result = await firecrawl_scrape(url)
        if result and len(result) > 200:
            return result

    # Layer 2: Jina Reader (free, no key, handles many paywalls)
    jina_url = f"https://r.jina.ai/{url}"
    result = await httpx_get(jina_url)
    if result and len(result) > 200:
        return result

    # Layer 3: trafilatura (fast, good for standard HTML)
    result = trafilatura.fetch_url(url)
    if result and len(trafilatura.extract(result) or "") > 200:
        return trafilatura.extract(result)

    # Layer 4: Playwright (slow, last resort for SPAs)
    result = await playwright_extract(url)
    if result and len(result) > 200:
        return result

    # Fallback: store URL + title only
    return await get_og_title(url) or url
```

PDF extraction (Docling-first):
```python
async def extract_pdf(file_path: str, page_count: int) -> dict:
    # Use Docling for structure-preserving markdown
    converter = DocumentConverter()
    result = converter.convert(file_path)
    markdown = result.document.export_to_markdown()

    if page_count <= 20:
        return {"mode": "full", "content": markdown}
    else:
        # Split into sections for hierarchical absorb
        sections = split_by_headings(markdown, max_pages=20)
        return {"mode": "hierarchical", "sections": sections}
```

#### 1D — Absorb worker (Week 2, ~8 hours)

Core absorb pattern:
```python
async def absorb_entry(entry_id: str, user_id: str):
    entry = await get_entry(entry_id)

    # 1. Embed entry for RAG routing
    entry_vec = await embed(entry.body[:2000])

    # 2. Find top-8 related existing articles
    candidates = await pgvector_knn(
        vector=entry_vec, user_id=user_id, top_k=8
    )
    context_articles = await get_articles_by_slugs(
        [c.slug for c in candidates], user_id
    )

    # 3. Call LLM with cached system prompt
    result = await llm.absorb(
        raw_entry=entry.body,
        context_articles=context_articles,
        existing_slugs=[a.slug for a in get_all_articles(user_id)]
    )
    # result: {slug, title, body_md, facets}

    # 4. Parse wikilinks via AST (not regex)
    links = extract_wikilinks_ast(result.body_md)

    # 5. Write everything in one transaction
    async with db.begin():
        await upsert_article(result, user_id)
        await write_backlinks(result.slug, links, user_id)
        await write_facets(result.slug, result.facets, user_id)
        await mark_entry_absorbed(entry_id, result.slug)
```

Wikilink AST extraction:
```python
from markdown_it import MarkdownIt

def extract_wikilinks_ast(markdown: str) -> list[str]:
    """Extract [[slug]] links using AST parser, ignores code blocks."""
    md = MarkdownIt()
    tokens = md.parse(markdown)
    links = []
    for token in flatten_tokens(tokens):
        if token.type == "inline":
            # find [[...]] pattern only in inline content, not code spans
            for child in token.children or []:
                if child.type not in ("code_inline", "fence"):
                    matches = re.findall(r'\[\[([^\]]+)\]\]', child.content)
                    links.extend(matches)
    return list(set(links))
```

#### 1E — Telegram bot and query handler (Week 3, ~6 hours)

```python
# Query detection heuristic
QUERY_PATTERNS = [
    r"^(what|find|show|tell me|search|look up|do i have)",
    r"^(where did i save|what did i save about|recall|remember)",
]

async def handle_message(update: Update):
    text = update.message.text or ""
    if is_query(text):
        await handle_query(update, text)
    else:
        await handle_capture(update, update.message)

async def handle_query(update, text: str):
    user_id = await get_user_id(update.effective_user.id)
    query_vec = await embed(text)
    chunks = await pgvector_knn(query_vec, user_id, top_k=5)
    articles = dedupe_to_articles(chunks)[:3]
    reply = format_telegram_reply(articles, text)
    await update.message.reply_text(reply, parse_mode="Markdown")
```

#### 1F — Next.js web UI basic (Week 3, ~6 hours)

Pages: article reader, backlinks panel, article index, context filter, basic search.

---

### Phase 1 testing parameters

Run all tests before moving to Phase 2.

#### Isolation tests (most critical)

```python
# Test: user A cannot see user B's data
async def test_rls_isolation():
    user_a = await create_test_user()
    user_b = await create_test_user()

    # Create article as user A
    await set_rls(user_a.id)
    await create_article("turmeric", "Turmeric content", user_a.id)

    # Query as user B — must return 0 rows
    await set_rls(user_b.id)
    result = await db.execute("SELECT * FROM articles WHERE slug = 'turmeric'")
    assert len(result.rows) == 0, "RLS BREACH: user B can see user A's article"
```

#### Capture pipeline tests

| Test | Command | Expected result |
|---|---|---|
| Text capture | Send "hello world" to bot | raw_entry created, ✓ reply in < 1s |
| URL capture | Send `https://example.com` | Article with extracted text created in < 90s |
| Voice capture | Send 30s voice memo | Transcription appears in raw_entry.body |
| Image with text | Send screenshot | OCR text extracted correctly |
| Image no text | Send photo | LLaVA description in body |
| PDF ≤ 20 pages | Send 15-page PDF | Single wiki article created |
| PDF > 20 pages | Send 80-page PDF | Multiple section articles + hub |
| Context setting | `/context test-ctx` then send text | entry.context = 'test-ctx' |

#### Query test

```
User sends: "what did i save about turmeric"
Expected: Telegram reply with 2-3 sentences + wiki link, within 2 seconds
```

#### Performance baseline

```bash
# Load 100 articles, run search
python tests/perf/test_search_latency.py --articles 100 --queries 50
# Expected: p95 < 800ms
```

### Phase 1 expected outputs

- Two users can independently capture content with zero data bleed
- Every Telegram message produces a wiki article within 90 seconds
- Bot search answers queries in < 2 seconds
- Web UI shows articles, backlinks, basic graph
- Zero manual script runs required after initial setup

---

## Phase 2 — Intelligence Layer
**Duration:** Weeks 4–7 | **Effort:** ~50 hours

### Objective

Non-obvious connection discovery. Nightly intelligence cron producing tunnel suggestions that are genuinely surprising, with LLM-written rationales. Human review gate working end-to-end.

### What you build

#### 2A — Additional database tables

```sql
CREATE TABLE tunnel_suggestions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source_slug TEXT NOT NULL,
  candidate_slug TEXT NOT NULL,
  rns_score FLOAT,
  emb_score FLOAT,
  ppr_score FLOAT,
  hop_count INT,
  decay_weight FLOAT,
  tier TEXT, -- tier2|tier3
  rationale TEXT,
  status TEXT DEFAULT 'pending', -- pending|accepted|rejected
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, source_slug, candidate_slug)
);

CREATE TABLE dislike_pairs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug_a TEXT NOT NULL,
  slug_b TEXT NOT NULL,
  rejected_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, slug_a, slug_b)
);

CREATE TABLE diff_suggestions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  context TEXT NOT NULL,
  synthesis_text TEXT NOT NULL,
  convergences JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE tunnel_suggestions ENABLE ROW LEVEL SECURITY;
ALTER TABLE dislike_pairs ENABLE ROW LEVEL SECURITY;
ALTER TABLE diff_suggestions ENABLE ROW LEVEL SECURITY;
-- (add policies as per Phase 1 pattern)
```

#### 2B — Intelligence engine

```
backend/intelligence/
├── graph.py        # NetworkX graph builder from backlinks
├── tunnels.py      # PPR + kNN + RNS scoring
├── differential.py # Facet bundle synthesis
├── decay.py        # Exponential time weighting
└── cron.py         # Nightly orchestrator
```

Core tunnel engine:
```python
import networkx as nx

async def run_tunnel_discovery(user_id: str):
    # 1. Build graph from backlinks
    edges = await get_all_backlinks(user_id)
    G = nx.DiGraph()
    for edge in edges:
        G.add_edge(edge.from_slug, edge.to_slug, type=edge.link_type)

    # 2. Get recently modified articles
    recent = await get_articles_modified_since(user_id, days=7)

    for article in recent:
        # 3. PPR from this article
        ppr_scores = nx.pagerank(
            G, alpha=0.85,
            personalization={article.slug: 1.0}
        )
        ppr_scores.pop(article.slug, None)
        ppr_candidates = sorted(
            ppr_scores.items(), key=lambda x: x[1], reverse=True
        )[:15]

        # 4. Vector kNN candidates
        article_vec = await get_article_embedding(article.slug, user_id)
        knn_candidates = await pgvector_knn(
            article_vec, user_id, top_k=15, min_score=0.55,
            exclude_slugs=[article.slug]
        )

        # 5. Get existing links (both directions)
        existing = await get_linked_slugs_bidirectional(article.slug, user_id)
        dislikes = await get_dislike_pairs(user_id)

        # 6. Merge + score
        all_candidates = merge_candidates(ppr_candidates, knn_candidates)
        scored = []
        for slug, emb_score, ppr_score, hops in all_candidates:
            if slug in existing or (article.slug, slug) in dislikes:
                continue
            rns = compute_rns(emb_score, ppr_score, hops, article.updated_at)
            if rns.tier != "noise":
                scored.append({
                    "slug": slug,
                    "rns": rns.final,
                    "emb": emb_score,
                    "ppr": ppr_score,
                    "hops": hops,
                    "tier": rns.tier
                })

        # 7. Top-8 to LLM for rationale
        top8 = sorted(scored, key=lambda x: x["rns"], reverse=True)[:8]
        if top8:
            rationales = await llm.generate_rationales(
                article, top8, user_id
            )
            await write_tunnel_suggestions(article.slug, rationales, user_id)
```

RNS computation:
```python
import math
from datetime import datetime

def compute_rns(emb: float, ppr: float, hops: int,
                updated_at: datetime, lambda_: float = 0.01) -> RNSResult:
    days = (datetime.now() - updated_at).days
    decay_w = math.exp(-lambda_ * days)

    R = 0.6 * emb + 0.4 * ppr
    N = 1.0  # already filtered already-linked, so always 1.0 here
    S = 1.0 / (1.0 + hops * 0.3)
    final = R * N * (0.5 + 0.5 * S) * decay_w

    tier = "tier2" if final > 0.75 else "tier3" if final > 0.50 else "noise"
    return RNSResult(final=final, tier=tier, decay_w=decay_w)
```

#### 2C — Intelligence UI

Intelligence tabs in Next.js:
- `/intelligence/tunnels` — review queue with accept/reject per card
- `/intelligence/diff/[context]` — differential synthesis results
- `/intelligence/updates` — audit log

#### 2D — Graph visualisation

D3 force-directed graph on `/graph` page. Nodes coloured by context, edge thickness by link type (wikilink > semantic_tunnel > differential). Click node → navigate to article.

---

### Phase 2 testing parameters

| Test | Verification method | Pass criterion |
|---|---|---|
| Already-linked pair score | Manually add wikilink A↔B, run tunnel, check score | RNS < 0.1 for linked pair |
| Dislike persistence | Reject a pair, run tunnel again next day | Pair absent from suggestions |
| Non-obvious discovery | Corpus: 20 articles with planted cross-context concept, run tunnel | ≥ 1 tunnel crossing the context boundary |
| Rationale quality | Manual review of 10 rationales | ≥ 7 rated "accurate and useful" |
| Bidirectional dislike | Reject (A,B), check if (B,A) also suppressed | Neither direction resurfaces |
| Nightly cron timing | Time cron on 200-article corpus | < 5 minutes total |
| Diff synthesis | Context with 10 articles sharing facets | ≥ 1 convergence insight identified |
| Graph renders | Open /graph with 200 nodes | Renders in < 2s, no crash |

### Phase 2 expected outputs

- 2–5 non-obvious tunnel suggestions per user per week on a 50+ article corpus
- < 20% of suggestions are obvious (already-linked pairs score near-zero)
- LLM rationales explain the non-obvious connection clearly
- Reject permanently suppresses pair in both directions
- Differential synthesis produces at least one "I wouldn't have noticed that" insight per context per week

---

## Phase 3 — Memory, Nudges, and Resurfacing
**Duration:** Weeks 8–11 | **Effort:** ~40 hours

### Objective

The system feels alive. Old ideas resurface when relevant. Contradictions are flagged. The weekly digest drives users back to the dashboard.

### What you build

#### 3A — Additional tables

```sql
CREATE TABLE nudges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL, -- stub|split|uncertain_facet|high_decay|unresolved_link|duplicate
  article_slug TEXT NOT NULL,
  detail TEXT,
  status TEXT DEFAULT 'pending', -- pending|actioned|dismissed
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE conflicts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  context TEXT NOT NULL,
  slug_a TEXT NOT NULL,
  slug_b TEXT NOT NULL,
  conflict_description TEXT,
  status TEXT DEFAULT 'unresolved', -- unresolved|resolved
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE era_summaries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  context TEXT NOT NULL,
  summary_md TEXT,
  status TEXT DEFAULT 'draft', -- draft|published
  created_at TIMESTAMPTZ DEFAULT now()
);
```

#### 3B — Resurface engine

```python
async def run_resurface(user_id: str):
    await detect_post_ready(user_id)
    await detect_resurgence(user_id)
    await detect_era_close(user_id)
    await run_conflict_detection(user_id)

async def detect_post_ready(user_id: str):
    # Find facet values appearing in >= 5 articles same context
    facet_counts = await count_facet_values_by_context(user_id)
    for context, facets in facet_counts.items():
        for value, count in facets.items():
            if count >= 5:
                await write_resurface_item(user_id, "post_ready", {
                    "context": context,
                    "theme": value,
                    "count": count
                })

async def detect_resurgence(user_id: str):
    # Articles with decay < 0.2 whose facets appear in last 30 days of entries
    old_articles = await get_articles_with_decay_below(user_id, 0.2)
    recent_facets = await get_recent_entry_facets(user_id, days=30)
    for article in old_articles:
        overlap = facet_overlap(article.facets, recent_facets)
        if overlap > 0.4:
            await write_resurface_item(user_id, "resurgence", {
                "slug": article.slug,
                "overlap_score": overlap
            })
```

#### 3C — Nudge engine

```python
async def run_nudges(user_id: str):
    articles = await get_all_articles(user_id)
    for article in articles:
        # Stub detection
        if len(article.body_md) < 300:
            mention_count = await count_entry_mentions(article.slug, user_id)
            if mention_count >= 3:
                await write_nudge(user_id, "stub", article.slug,
                    f"Only {len(article.body_md)} chars but mentioned {mention_count} times")

        # Oversized
        if count_sections(article.body_md) >= 10:
            await write_nudge(user_id, "split", article.slug,
                f"{count_sections(article.body_md)} sections — consider splitting")

        # Uncertain facets
        uncertain = [k for k,v in article.facets.items()
                     if isinstance(v, list) and any("?" in str(i) for i in v)]
        if uncertain:
            await write_nudge(user_id, "uncertain_facet", article.slug,
                f"Uncertain facets: {uncertain}")

        # High decay
        decay = compute_decay(article.updated_at)
        if decay < 0.10:
            await write_nudge(user_id, "high_decay", article.slug,
                f"Decay score: {decay:.2f} — last updated {article.updated_at}")

    # Duplicate detection (embedding similarity)
    pairs = await find_similar_articles(user_id, threshold=0.85)
    for a, b, score in pairs:
        await write_nudge(user_id, "duplicate", a,
            f"Possible duplicate of {b} (similarity: {score:.2f})")
```

#### 3D — Weekly Telegram digest

```python
async def send_weekly_digest(user_id: str):
    user = await get_user(user_id)
    pending_tunnels = await count_pending_tunnels(user_id)
    nudge_count = await count_pending_nudges(user_id)
    resurface_count = await count_resurface_items(user_id)

    if pending_tunnels + nudge_count + resurface_count == 0:
        return  # Nothing to report

    msg = f"""📊 *Weekly digest*

🔗 {pending_tunnels} tunnel suggestions pending review
🧠 {resurface_count} memory items worth revisiting
✏️ {nudge_count} edit suggestions

[Open Intelligence Dashboard →]({WEBAPP_URL}/intelligence)"""

    await bot.send_message(user.telegram_id, msg, parse_mode="Markdown")
```

---

### Phase 3 testing parameters

| Test | Pass criterion |
|---|---|
| Post-ready detection | Plant 5 articles with `color: cream` in same context → post-ready item appears |
| Resurgence accuracy | Create old article, add recent entries with same facets → resurgence detected |
| Era close signal | Create context, reduce activity to < 5% baseline for 14 days → era-close triggered |
| Conflict detection | Add `facets: {style: [open-plan]}` and `avoid: [open-plan]` in same context → conflict flagged |
| Time-capsule throttle | Manually trigger → sent once, not again within 7 days |
| Digest delivery | Manually trigger weekly digest → Telegram message arrives, all counts correct, link works |
| Era summary quality | Close a context with 10 articles → LLM draft captures main themes accurately |
| Nudge accuracy | > 70% of generated nudges are actionable (spot-check sample of 20) |

### Phase 3 expected outputs

- System feels intelligent and alive — surfaces things users forgot they saved
- Weekly digest achieves > 60% open rate (user clicks through to at least one item)
- Users act on at least 1 nudge per week
- Conflict detection catches at least 1 real contradiction per active context per month
- Era summaries accurately capture context themes on close

---

## Phase 4 — Scale, Cloud, Additional Sources
**Duration:** Weeks 12–16 | **Effort:** ~30 hours

### Objective

Cloud deployment. Sub-500ms p95 for 5000 articles. Additional ingestion sources. Production-grade security hardening.

### What you build

#### 4A — Cloud migration

```yaml
# railway.toml
[build]
  builder = "NIXPACKS"

[[services]]
  name = "web"
  source = "./web-ui"
  startCommand = "npm run start"
  
[[services]]
  name = "api"
  source = "./backend"
  startCommand = "uvicorn api.main:app --host 0.0.0.0 --port 8000"

[[services]]
  name = "worker"
  source = "./backend"
  startCommand = "python workers/runner.py"
```

Switch embedding provider:
```python
# Before (local): EMBED_PROVIDER=ollama
# After (cloud): EMBED_PROVIDER=nomic_api
# .env change only — no code change if abstraction is built correctly
```

Switch Celery + Redis (if pg-boss throughput insufficient at scale):
```python
# Only if pg-boss shows latency > 5s for job pickup at 100+ concurrent jobs
# Most likely not needed until 50+ users
```

#### 4B — pgvectorscale (if needed)

```sql
-- Only add if HNSW query latency > 100ms at current scale
-- Run EXPLAIN ANALYZE to check first
CREATE EXTENSION IF NOT EXISTS vectorscale;
CREATE INDEX ON document_chunks
  USING diskann (embedding vector_cosine_ops);
```

#### 4C — Email ingestion

```python
# Postmark inbound webhook
@app.post("/webhook/email")
async def handle_email(payload: EmailPayload):
    user = await find_user_by_email(payload.to_address)
    if not user:
        return  # Unknown recipient, ignore
    await enqueue_ingest(user.id, {
        "source_type": "email",
        "body": payload.text_body,
        "subject": payload.subject,
        "from": payload.from_address
    })
```

#### 4D — Performance targets

```
p95 wiki article load:          < 500ms  (5000 articles per user)
p95 semantic search:            < 300ms
p95 Telegram query answer:      < 1500ms
Intelligence nightly cron:      < 15 min (5000 articles)
Concurrent users (cloud):       20+ without degradation
```

---

### Phase 4 testing parameters

| Test | Method | Pass criterion |
|---|---|---|
| Scale load | Load 5000 articles per test user, run 100 queries | p95 < 500ms |
| Cloud isolation | Two real users on Railway instance | Zero data bleed |
| Email ingestion | Forward email to inbound address | Article appears in wiki < 90s |
| Worker restart resilience | Kill worker mid-job, restart | Job completes correctly on retry, no duplicate data |
| Backup and restore | Run pg_dump, restore to new instance, verify data | All articles, backlinks, facets intact |
| PgBouncer connection pool | Simulate 50 concurrent requests | No connection exhaustion errors |

---

## Phase 5 — Advanced Features (Future)
**Duration:** TBD | **Status:** Planned

- Conversational memory in Telegram (multi-turn context)
- Automatic context detection (LLM suggests context from capture patterns)
- Article version history
- HyDE (Hypothetical Document Embeddings) for query improvement
- ColBERT reranking for search quality
- GraphRAG integration (subgraph fed to LLM for context-aware answers)
- Browser extension capture
- Cross-user anonymous similarity (opt-in)
- Proactive Telegram pushes on accepted tunnels
- Local-first / on-device mode

---

## Engineering standards (all phases)

### Idempotency
Every worker function must be safe to run twice on the same input. Use `ON CONFLICT DO UPDATE` or `ON CONFLICT DO NOTHING` for all DB writes. Check "already processed" state at job start.

### Transaction boundaries
Article upsert + backlink writes + facet writes = one transaction. If any part fails, all roll back. Never leave partial state.

### LLM error handling
```python
for attempt in range(3):
    try:
        result = await llm.call(prompt)
        break
    except LLMError as e:
        if attempt == 2:
            await mark_entry_failed(entry_id, str(e))
            raise
        await asyncio.sleep(2 ** attempt)
```

### Connection pooling
```python
# SQLAlchemy async engine
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True
)
```

### Never log PII
```python
# Wrong
logger.info(f"Processing entry: {entry.body}")  # logs user content

# Right
logger.info(f"Processing entry: {entry.id} (type={entry.source_type})")
```
