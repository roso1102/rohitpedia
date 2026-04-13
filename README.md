# Rohitpedia — Your Personal Intelligence System

> *Send a message. Your knowledge builds itself.*

---

## What is this? (For anyone)

You know that feeling when you read something great, save it somewhere, and then never find it again? Or when two ideas you saved months apart are obviously connected — but you never noticed?

Rohitpedia fixes both of those problems.

You send messages to a Telegram bot — text, voice notes, articles, PDFs, images, whatever. The system reads everything, organises it into a personal wiki (like your own Wikipedia), and then — the interesting part — it finds connections between things you saved that you never consciously linked. A note about turmeric from six months ago connecting to an article about inflammation you saved last week. A wallpaper idea connecting to the appliances you liked, suggesting a colour palette. Things your brain would have connected eventually — surfaced now.

It is a second brain that actually works, because you never have to organise anything.

---

## What it does (slightly more detail, still plain English)

**Capture:** Send anything to a Telegram bot. Text, URLs, voice memos, PDFs, images. It accepts all of it. You can also type directly in the web app.

**Wiki building:** Everything you save gets turned into structured wiki articles automatically. You save three notes about turmeric — the system writes a clean `Turmeric` article with sections, links to related articles, and structured tags (health properties, cuisine, colour). You didn't write any of it.

**Knowledge graph:** All your articles connect to each other through links. You can see your entire knowledge base as an interactive graph — every idea connected to every other idea it relates to. Click any node, read the article.

**Tunnel discovery:** The system finds non-obvious connections. Not just "these two articles are similar" — but "this cooking note from six months ago and this health research you saved yesterday are connected through this specific compound." Surfaces these as suggestions. You approve or reject each one. Nothing enters your wiki without your say.

**Memory:** The system notices when you have enough on a topic to write something publishable. It surfaces old ideas when they become relevant again. It detects contradictions in your preferences. It sends you a weekly digest of what's worth reviewing.

**Search:** Ask the Telegram bot "what did I save about X?" and get an answer inline, in Telegram, within 2 seconds — with a link to the full wiki article if you want to go deeper.

---

## The technical version

### Architecture overview

Rohitpedia is a hybrid system: a **Next.js** web application for reading and intelligence review, a **FastAPI** Python backend for intelligence processing and webhook handling, and a single **PostgreSQL** database (with pgvector extension) that stores everything — articles, embeddings, backlinks, and job queues.

```
Telegram / Web UI
      │
      ▼
FastAPI webhook (< 1s response)
      │
      ▼
pg-boss job queue (Postgres-native)
      │
   ┌──┴──────────────────┐
   │                     │
Ingest worker      Intelligence worker
(media extraction) (nightly: tunnels,
                    decay, resurface)
   │
Absorb worker
(Gemini Flash → wiki article)
   │
Embed worker
(nomic-embed → pgvector)
   │
   ▼
PostgreSQL + pgvector
(articles, chunks, backlinks,
 tunnel_suggestions, raw_entries)
   │
   ▼
Next.js Web UI + Telegram bot replies
```

### Key design decisions

**One database for everything.** PostgreSQL with pgvector handles relational data, full-text search, and vector embeddings in one ACID-compliant system. No separate Qdrant, no file system, no Redis (locally).

**Row-Level Security (RLS) from day one.** Every table has `user_id`. The database enforces isolation at the engine level — no application-level filtering that can be forgotten.

**Event-driven, never blocking.** Telegram messages are acknowledged in under 1 second. All heavy work (LLM calls, embedding, PDF extraction) runs in background workers via pg-boss job queue.

**LLMs used minimally and precisely.** The LLM touches exactly 10 task types. Everything else — backlinks, vector search, PPR graph scoring, decay — is deterministic code. LLM cost for 8 users at normal usage: under $2/month total.

**Chunked multi-vector storage.** Wiki articles are split by section headers before embedding. Each section gets its own vector. This solves the fundamental limitation of single-vector embeddings for multi-topic articles.

**SerenQA-style tunnel discovery.** Non-obvious connections are found by combining vector similarity (Qdrant-style kNN on pgvector) with Personalized PageRank on the knowledge graph, scored by an RNS metric (Relevance × Novelty × Surprise) that specifically rewards non-obvious connections and penalises already-linked pairs.

---

## Tech stack

| Layer | Technology | Why |
|---|---|---|
| Web UI | Next.js 14 (App Router) | SSR, API routes, React |
| Backend API | FastAPI (Python) | Async webhooks, ML ecosystem |
| Database | PostgreSQL 16 + pgvector | Unified relational + vector |
| Job queue | pg-boss | Postgres-native, no Redis |
| Embeddings | nomic-embed-text (Ollama) | Free, local, 768-dim, excellent |
| LLM (absorb) | Gemini Flash 2.5 | Best quality/cost, 1M context |
| LLM (facets) | Phi-3 mini (local SLM) | Structured JSON, free |
| LLM (vision) | Gemini Flash / LLaVA | Image description |
| Transcription | whisper.cpp | Free, local, CPU-capable |
| PDF extraction | Docling / pymupdf | Structure-preserving markdown |
| URL extraction | Firecrawl → trafilatura → Jina | Layered fallback |
| ORM | Prisma (Next.js) + SQLAlchemy (FastAPI) | Type-safe queries |
| Capture | Telegram bot (python-telegram-bot) | Low-friction mobile capture |

---

## Project structure

```
rohitpedia/
├── web-ui/                    # Next.js frontend
│   ├── src/
│   │   ├── app/               # App Router pages
│   │   │   ├── wiki/          # Article reader
│   │   │   ├── intelligence/  # Tunnels, nudges, memory
│   │   │   ├── memory/        # Raw entry feed
│   │   │   ├── graph/         # Knowledge graph
│   │   │   ├── search/        # Hybrid search
│   │   │   └── contexts/      # Life chapters
│   │   ├── components/        # Shared UI components
│   │   └── lib/               # Database queries, utils
│   └── prisma/                # Schema + migrations
│
├── backend/                   # FastAPI Python backend
│   ├── api/                   # Webhook + REST endpoints
│   ├── workers/               # Background job handlers
│   │   ├── ingest.py          # Media extraction pipeline
│   │   ├── absorb.py          # LLM synthesis
│   │   ├── embed.py           # Vector embedding
│   │   └── intelligence.py    # Nightly cron
│   ├── intelligence/          # Core algorithms
│   │   ├── tunnels.py         # PPR + RNS scoring
│   │   ├── differential.py    # Facet bundle synthesis
│   │   ├── decay.py           # Temporal scoring
│   │   └── resurface.py       # Memory detection
│   └── llm/                   # LLM provider abstraction
│       ├── provider.py        # Base interface
│       ├── gemini.py          # Gemini Flash
│       └── local.py           # Ollama / Phi-3
│
├── bot/                       # Telegram bot
│   ├── handler.py             # Message routing
│   ├── query.py               # Search + reply
│   └── context.py             # /context commands
│
├── docs/                      # Documentation
│   ├── SRS.md
│   ├── ROADMAP.md
│   ├── PROTOCOL.md
│   ├── ACTION_PLAN.md
│   └── guides/
│
├── docker-compose.yml         # Local dev stack
├── .env.example               # Environment template
└── README.md
```

---

## Local development setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker Desktop (for Postgres)
- Ollama (for local embeddings and SLM)

### 1. Clone and install

```bash
git clone https://github.com/yourname/rohitpedia
cd rohitpedia

# Python backend
cd backend && python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Next.js frontend
cd ../web-ui && npm install
```

### 2. Start Postgres with pgvector

```bash
docker compose up -d postgres
```

### 3. Pull Ollama models

```bash
ollama pull nomic-embed-text   # embeddings
ollama pull phi3:mini          # facet extraction SLM
ollama pull llava:7b           # image description (optional, 4GB)
ollama pull qwen2.5:7b         # development LLM (optional)
```

### 4. Install whisper.cpp

```bash
# macOS
brew install whisper-cpp

# Linux
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp && make
./models/download-ggml-model.sh base.en
```

### 5. Configure environment

```bash
cp .env.example .env
# Edit .env:
# TELEGRAM_BOT_TOKEN=your_token
# GEMINI_API_KEY=your_key
# DATABASE_URL=postgresql://...
# QDRANT_URL=http://localhost:6333  # not needed — using pgvector
```

### 6. Run migrations and start

```bash
cd web-ui && npx prisma migrate dev
cd ../backend && python -m uvicorn api.main:app --reload --port 8000
cd ../web-ui && npm run dev
```

### 7. Set Telegram webhook

```bash
python bot/setup_webhook.py --url http://localhost:8000/webhook/telegram
# For local testing, use ngrok: ngrok http 8000
```

---

## Wiki tabs reference

Every article page has these tabs:

| Tab | Contents |
|---|---|
| Read | Rendered markdown prose with inline [[wikilinks]] |
| Backlinks | Every article that links to this one, with context snippets |
| Graph | Ego-graph — this article's connections, 2 hops |
| Facets | Structured tags (health, cuisine, colour, etc.) — editable |
| Raw entries | Original captures that contributed to this article |

Main navigation: **Home** · **Graph** · **Search** · **Intelligence** · **Memory** · **Capture** · **Contexts**

---

## Intelligence tabs reference

| Tab | What it shows |
|---|---|
| Tunnel Review | Proposed non-obvious connections — accept or reject each |
| Edit Nudges | Stubs to expand, articles to split, uncertain facets, duplicates |
| Memory Resurface | Post-ready alerts, time-capsule check-ins, conflict flags |
| Wiki Updates | Audit log — every accepted tunnel, absorb, rebuild |
| Diff Viewer | Per-context differential synthesis insights |

---

## Environment variables

```env
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=

# Database
DATABASE_URL=postgresql://postgres:password@localhost:5432/rohitpedia

# LLMs
GEMINI_API_KEY=
LLM_ABSORB_PROVIDER=gemini          # gemini | ollama
LLM_ABSORB_MODEL=gemini-2.5-flash
LLM_FACETS_PROVIDER=ollama          # use local SLM
LLM_FACETS_MODEL=phi3:mini
LLM_VISION_PROVIDER=gemini
LLM_QUERY_PROVIDER=gemini
LLM_QUERY_MODEL=gemini-2.5-flash-8b

# Embeddings
EMBED_PROVIDER=ollama               # ollama | nomic_api
EMBED_MODEL=nomic-embed-text
OLLAMA_HOST=http://localhost:11434

# Transcription
TRANSCRIBE_PROVIDER=local           # local | openai_api
WHISPER_MODEL_PATH=./models/ggml-base.en.bin

# URL extraction
FIRECRAWL_API_KEY=                  # optional, enhances URL extraction
JINA_READER_ENABLED=true            # free fallback

# Media storage
MEDIA_STORAGE=local                 # local | r2
MEDIA_DIR=./media
# R2 settings (cloud only)
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=

# Intelligence
INTELLIGENCE_CRON=0 2 * * *         # 2am nightly
DECAY_LAMBDA_DEFAULT=0.01
```

---

## Contributing

This project is currently private and in active development. Architecture decisions are documented in `docs/SRS.md`. Phase plan is in `docs/ROADMAP.md`. For context before editing any file, read `docs/PROTOCOL.md` and `.cursorrules`.
