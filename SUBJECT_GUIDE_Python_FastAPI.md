# SUBJECT GUIDE: Python + FastAPI
## Rohitpedia Engineering Standards

---

## Python standards

### Async everywhere
```python
# This project uses async Python throughout. Never use synchronous DB calls.
# WRONG
def get_article(slug: str) -> Article:
    return db.query(Article).filter_by(slug=slug).first()

# RIGHT
async def get_article(slug: str, user_id: str) -> Article | None:
    result = await db.execute(
        select(Article).where(Article.slug == slug)
    )
    return result.scalar_one_or_none()
```

### Type hints always
```python
# Every function parameter and return value typed
from typing import Optional

async def absorb_entry(
    entry_id: str,
    user_id: str,
    retry_count: int = 0
) -> dict[str, str] | None:
    ...
```

### Pydantic for all data validation
```python
from pydantic import BaseModel, field_validator

class AbsorbResult(BaseModel):
    slug: str
    title: str
    body_md: str
    facets: dict[str, list[str]]

    @field_validator('slug')
    def slug_format(cls, v: str) -> str:
        if not re.match(r'^[a-z0-9-]+$', v):
            raise ValueError('slug must be lowercase hyphenated')
        return v
```

### Error handling patterns
```python
# Use specific exception types, never catch bare Exception in business logic
class AbsorbError(Exception):
    """Raised when LLM synthesis fails"""
    pass

class ExtractionError(Exception):
    """Raised when media extraction fails"""
    pass

# In workers: catch, log, mark job failed
async def run_absorb_job(job_data: dict):
    try:
        await absorb_entry(job_data["entry_id"], job_data["user_id"])
    except AbsorbError as e:
        logger.error("absorb_failed", extra={
            "entry_id": job_data["entry_id"],
            "error": str(e)
        })
        await mark_entry_failed(job_data["entry_id"], str(e))
        raise  # re-raise so pg-boss marks job as failed
```

### Structured logging
```python
import structlog

logger = structlog.get_logger()

# Always include context, never raw user content
logger.info("absorb_complete",
    entry_id=entry_id,
    slug=result.slug,
    tokens_in=usage.input_tokens,
    duration_ms=elapsed
)

# Never do this
logger.info(f"Processing: {entry.body}")  # logs user content
```

---

## FastAPI standards

### Route organisation
```python
# One router per domain
# backend/api/routes/
#   webhook.py    — Telegram webhook
#   articles.py   — wiki article CRUD
#   search.py     — search endpoints
#   intelligence.py — tunnel review, nudges

from fastapi import APIRouter

router = APIRouter(prefix="/api/articles", tags=["articles"])

@router.get("/{slug}")
async def get_article(slug: str, session: Session = Depends(get_session)):
    ...
```

### Dependency injection for DB + RLS
```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

async def get_db_session(
    request: Request,
    db: AsyncSession = Depends(get_async_session)
) -> AsyncSession:
    """Injects DB session with RLS context set."""
    user_id = request.state.user_id  # set by auth middleware
    await db.execute(f"SET LOCAL app.current_tenant = '{user_id}'")
    try:
        yield db
    finally:
        await db.close()
```

### Request validation
```python
# Always validate inputs before DB operations
@router.post("/tunnels/{id}/reject")
async def reject_tunnel(
    id: UUID,
    body: RejectTunnelRequest,
    session: AsyncSession = Depends(get_db_session)
):
    # Validate slug format before DB write
    if not re.match(r'^[a-zA-Z0-9._/-]+$', body.candidate_slug):
        raise HTTPException(400, "Invalid slug format")
    ...
```

### Response models
```python
# Always define response models — never return raw DB objects
class ArticleResponse(BaseModel):
    slug: str
    title: str
    body_md: str
    context: str | None
    facets: dict
    updated_at: datetime
    backlink_count: int

    model_config = ConfigDict(from_attributes=True)
```

### Middleware ordering
```python
# Order matters. Auth must come before RLS which must come before routes.
app = FastAPI()
app.add_middleware(RLSMiddleware)      # 3rd: set DB session var
app.add_middleware(AuthMiddleware)     # 2nd: authenticate user
app.add_middleware(LoggingMiddleware)  # 1st: log request
```

---

## SQLAlchemy async patterns

### Model definition
```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, TIMESTAMP
from pgvector.sqlalchemy import Vector
import uuid

class Base(DeclarativeBase):
    pass

class Article(Base):
    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
```

### Upsert pattern
```python
from sqlalchemy.dialects.postgresql import insert

async def upsert_article(data: dict, user_id: str, db: AsyncSession):
    stmt = insert(Article).values(
        user_id=user_id,
        **data
    ).on_conflict_do_update(
        index_elements=["user_id", "slug"],
        set_={
            "title": data["title"],
            "body_md": data["body_md"],
            "updated_at": func.now()
        }
    )
    await db.execute(stmt)
```

### Transaction pattern
```python
async def absorb_with_transaction(entry_id: str, result: AbsorbResult, db: AsyncSession):
    async with db.begin():
        # All of these either all succeed or all fail
        await upsert_article(result.dict(), db=db)
        await write_backlinks(result.slug, result.links, db=db)
        await write_facets(result.slug, result.facets, db=db)
        await mark_entry_absorbed(entry_id, result.slug, db=db)
```

---

## Testing patterns

### Test database setup
```python
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:password@localhost/rohitpedia_test"

@pytest.fixture
async def db():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()

@pytest.fixture
async def test_user(db):
    user_id = str(uuid.uuid4())
    await db.execute(f"SET LOCAL app.current_tenant = '{user_id}'")
    yield user_id
    # Cleanup: CASCADE handles all child rows
    await db.execute(f"DELETE FROM users WHERE id = '{user_id}'")
    await db.commit()
```

### Mock LLM in tests
```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_absorb_creates_article(test_user):
    mock_result = AbsorbResult(
        slug="turmeric",
        title="Turmeric",
        body_md="Turmeric is a [[spice]]...",
        facets={"health": ["anti-inflammatory"]}
    )

    with patch("backend.workers.absorb.llm.complete", AsyncMock(return_value=mock_result)):
        await absorb_entry("test-entry-id", test_user)

    article = await get_article("turmeric", test_user)
    assert article is not None
    assert article.title == "Turmeric"
```

---

## Common mistakes to avoid

```python
# MISTAKE 1: String formatting in SQL (SQL injection)
await db.execute(f"SELECT * FROM articles WHERE slug = '{slug}'")
# FIX: Use parameterised queries
await db.execute(select(Article).where(Article.slug == slug))

# MISTAKE 2: Catching Exception silently
try:
    await absorb_entry(id, user_id)
except Exception:
    pass  # silently swallowed — you'll never know it failed
# FIX: Always log and re-raise or handle explicitly

# MISTAKE 3: Synchronous calls in async context
def embed_text(text: str) -> list[float]:
    return requests.post(OLLAMA_URL, json={"prompt": text}).json()
# FIX: Use httpx.AsyncClient

# MISTAKE 4: Missing await
result = get_article(slug)  # returns coroutine, not article
# FIX:
result = await get_article(slug)

# MISTAKE 5: DB session used outside request lifecycle
class MyWorker:
    db = AsyncSession(engine)  # shared session — WRONG
# FIX: Create new session per job, close when done
```
