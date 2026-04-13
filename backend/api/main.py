from fastapi import FastAPI

from api.middleware import attach_tenant_context
from api.webhook import router as webhook_router

app = FastAPI(title="Rohitpedia API")
app.middleware("http")(attach_tenant_context)
app.include_router(webhook_router)


@app.get("/")
async def root():
    return {"ok": True, "service": "rohitpedia-api"}


@app.get("/health")
async def health_check():
    return {"ok": True}
