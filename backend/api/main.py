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
async def ensure_ingest_queue() -> None:
    async with engine.begin() as conn:
        # Ensure pg-boss queues exist after service restarts.
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                  PERFORM pgboss.create_queue('ingest', '{"policy":"standard"}'::jsonb);
                  PERFORM pgboss.create_queue('absorb', '{"policy":"standard"}'::jsonb);
                  PERFORM pgboss.create_queue('embed', '{"policy":"standard"}'::jsonb);
                EXCEPTION
                  WHEN OTHERS THEN
                    -- queues may already exist; startup should remain resilient
                    NULL;
                END
                $$;
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
