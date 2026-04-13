from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware import attach_tenant_context
from api.webhook import router as webhook_router
from db import engine, get_db_session

app = FastAPI(title="Rohitpedia API")
app.middleware("http")(attach_tenant_context)
app.include_router(webhook_router)


@app.on_event("startup")
async def ensure_ingest_jobs_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ingest_jobs (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  entry_id UUID NOT NULL REFERENCES raw_entries(id) ON DELETE CASCADE,
                  status TEXT NOT NULL DEFAULT 'pending',
                  payload JSONB DEFAULT '{}'::jsonb,
                  created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )


@app.get("/")
async def root():
    return {"ok": True, "service": "rohitpedia-api"}


@app.get("/health")
async def health_check():
    return {"ok": True}


@app.get("/debug/tenant")
async def debug_tenant(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(text("SELECT current_setting('app.current_tenant', true)"))
    return {"tenant": result.scalar_one_or_none()}
