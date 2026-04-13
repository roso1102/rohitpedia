import os
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required in environment variables.")

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        tenant = getattr(request.state, "current_tenant", None)
        telegram_user_id = getattr(request.state, "telegram_user_id", None)

        if not tenant and telegram_user_id is not None:
            user_lookup = await session.execute(
                text("SELECT id FROM users WHERE telegram_id = :telegram_id LIMIT 1"),
                {"telegram_id": telegram_user_id},
            )
            tenant = user_lookup.scalar_one_or_none()
            if tenant:
                request.state.current_tenant = str(tenant)

        if tenant:
            await session.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant)},
            )
        yield session
