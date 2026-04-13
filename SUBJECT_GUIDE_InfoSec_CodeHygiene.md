# SUBJECT GUIDE: InfoSec and Code Hygiene
## Rohitpedia Engineering Standards

---

## Security non-negotiables

These are not guidelines. Violating any of these is a critical bug.

### 1. Never bypass RLS
```python
# CRITICAL VIOLATION
await db.execute("SELECT * FROM articles")  # no RLS context set
await db.execute("SELECT * FROM articles WHERE user_id = 'hardcoded'")  # bypasses RLS

# CORRECT
await db.execute("SET LOCAL app.current_tenant = :uid", {"uid": user_id})
await db.execute("SELECT * FROM articles")  # RLS filters automatically
```

### 2. Never use string concatenation in SQL
```python
# SQL INJECTION VULNERABILITY
slug = request.query_params.get("slug")
await db.execute(f"SELECT * FROM articles WHERE slug = '{slug}'")

# CORRECT - parameterised query
await db.execute(
    text("SELECT * FROM articles WHERE slug = :slug"),
    {"slug": slug}
)
```

### 3. Always validate Telegram webhook secret
```python
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(403, "Invalid webhook secret")
    # ... process
```

### 4. Validate all slug inputs
```python
import re

SLUG_PATTERN = re.compile(r'^[a-zA-Z0-9._/-]+$')

def validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.match(slug):
        raise ValueError(f"Invalid slug format: {slug!r}")
    if '..' in slug:  # path traversal
        raise ValueError("Path traversal attempt detected")
    return slug
```

### 5. Validate media file paths
```python
from pathlib import Path

def safe_media_path(user_id: str, filename: str) -> Path:
    base = Path(settings.MEDIA_DIR).resolve()
    # Sanitise filename — strip path separators
    safe_name = Path(filename).name  # takes only the filename, not directory
    full_path = base / user_id / safe_name
    # Ensure path is still within base (no escape)
    full_path.resolve().relative_to(base)  # raises ValueError if outside
    return full_path
```

### 6. Never log PII
```python
# VIOLATION: logs user's private content
logger.info(f"Processing entry: {entry.body}")
logger.error(f"Failed for user: {user.telegram_id}")

# CORRECT: log only IDs and metadata
logger.info("entry_processing_start",
    entry_id=str(entry.id),
    source_type=entry.source_type,
    user_id_prefix=str(user_id)[:8]  # first 8 chars only
)
```

### 7. Secrets from environment only
```python
# VIOLATION
API_KEY = "AIzaSyXXXXXXX"

# CORRECT
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    gemini_api_key: str
    telegram_bot_token: str
    database_url: str
    telegram_webhook_secret: str

    class Config:
        env_file = ".env"

settings = Settings()  # raises if any required var missing
```

---

## Code hygiene standards

### Import organisation
```python
# Standard library
import os
import re
from datetime import datetime
from typing import Optional

# Third party
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

# Local
from backend.db import get_session
from backend.models import Article
from backend.llm.provider import get_provider
```

### Constants file
```python
# backend/config.py — all constants in one place
class Config:
    MIN_CHUNK_SIZE = 80           # chars
    MAX_CHUNK_TOKENS = 512        # tokens before split
    CHUNK_OVERLAP_TOKENS = 100
    KNN_DEFAULT_TOP_K = 15
    KNN_MIN_SCORE = 0.55
    TUNNEL_TIER2_THRESHOLD = 0.75
    TUNNEL_TIER3_THRESHOLD = 0.50
    PPR_ALPHA = 0.85
    DECAY_LAMBDA_DEFAULT = 0.01
    MAX_LLM_RETRIES = 3
    MEDIA_JPEG_QUALITY = 85
    SHORT_PDF_PAGE_LIMIT = 20
```

### Function length
- Functions should do one thing. If a function exceeds 40 lines, split it.
- Worker job handlers: orchestrate only (call sub-functions, don't implement logic inline).

```python
# WRONG: 80-line absorb function that does everything inline
async def run_absorb(entry_id):
    entry = await db.execute(...)
    vec = await httpx.post(OLLAMA_URL, ...)
    chunks = await db.execute(...)
    prompt = f"..."
    # ... 60 more lines

# RIGHT: orchestrator calls focused sub-functions
async def run_absorb(entry_id: str, user_id: str):
    entry = await get_entry(entry_id)
    candidates = await find_related_articles(entry.body, user_id)
    result = await synthesise_article(entry, candidates)
    links = extract_wikilinks_ast(result.body_md)
    await write_article_transaction(result, links, entry_id, user_id)
    await enqueue_embed_job(result.slug, user_id)
```

### Dead code removal
- Remove commented-out code. If you need history, use git.
- Remove `print()` statements before committing.
- Remove TODO comments older than one sprint (implement or delete).

### Dependency hygiene
```bash
# Check for unused imports
pip install autoflake
autoflake --remove-all-unused-imports -r backend/

# Check for security vulnerabilities
pip install pip-audit
pip-audit

# Format all Python files
pip install black isort
black backend/
isort backend/
```

### TypeScript hygiene (Next.js)
```typescript
// Use TypeScript strict mode
// tsconfig.json: "strict": true

// Never use `any`
const article: any = await fetchArticle(slug)  // WRONG
const article: Article = await fetchArticle(slug)  // CORRECT

// Always handle null/undefined explicitly
const slug = article?.slug ?? ""  // CORRECT
const slug = article.slug          // WRONG if article could be null

// Use zod for runtime validation of API responses
import { z } from "zod"
const ArticleSchema = z.object({
    slug: z.string(),
    title: z.string(),
    body_md: z.string(),
})
const article = ArticleSchema.parse(await response.json())
```

---

## Testing standards

### Test naming convention
```python
# Pattern: test_{what}_{condition}_{expected_outcome}
async def test_absorb_creates_article_when_new_entry_received():
async def test_rls_returns_zero_rows_for_wrong_user():
async def test_wikilink_extractor_ignores_code_blocks():
async def test_knn_filters_by_user_id_correctly():
```

### Coverage requirements (by category)
- **Security-critical** (RLS, auth, input validation): 100% coverage
- **Core workers** (ingest, absorb, embed): ≥ 80% coverage
- **Intelligence algorithms** (PPR, RNS, decay): ≥ 80% coverage
- **UI components**: ≥ 50% coverage

### What to mock
```python
# Always mock in unit tests:
# - LLM API calls (Gemini, Ollama)
# - External HTTP calls (Firecrawl, Jina, Telegram)
# - whisper.cpp process calls
# - File system operations

# Never mock in integration tests:
# - Database (use test DB)
# - RLS policies (must be tested for real)
```

---

## Pre-commit checklist

```bash
# Run before every commit

# Python
cd backend
black . --check                    # formatting
isort . --check-only               # import order
flake8 . --max-line-length 100     # linting
mypy . --ignore-missing-imports    # type checking
pytest tests/ -x --tb=short       # tests (fail fast)
pip-audit                          # security audit

# TypeScript
cd web-ui
npm run lint                       # ESLint
npm run type-check                 # TypeScript
npm run test                       # Jest tests

# Security scan
grep -r "hardcoded_secret\|password.*=.*\"\|api_key.*=.*\"" backend/
# Should return 0 results
```

---

## Incident response playbook

### Data isolation breach (highest severity)
```
1. Immediately revoke all user sessions
2. Check pg_activity for ongoing queries crossing tenant boundaries
3. Check RLS policies: SELECT * FROM pg_policies
4. Check app.current_tenant is set in ALL worker code paths
5. Notify affected users within 24 hours
6. Post-mortem within 48 hours
```

### Runaway LLM costs
```
1. Check llm_usage table: SELECT task_type, SUM(tokens_in + tokens_out), COUNT(*)
   FROM llm_usage WHERE created_at > now() - interval '1 hour' GROUP BY task_type
2. Identify the runaway task type
3. Disable that task's job in pg-boss
4. Fix the loop/retry bug
5. Add cost alert threshold to monitoring
```

### Database connection exhaustion
```
1. Check active connections: SELECT count(*) FROM pg_stat_activity
2. Kill idle connections: SELECT pg_terminate_backend(pid) FROM pg_stat_activity
   WHERE state = 'idle' AND query_start < now() - interval '5 minutes'
3. Check pool_size settings in SQLAlchemy engine
4. Add PgBouncer if needed (Phase 4)
```
