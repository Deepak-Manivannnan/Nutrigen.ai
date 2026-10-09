from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from backend.app.config import settings

import sys
from sqlalchemy.pool import NullPool

is_testing = "pytest" in sys.modules or settings.app_env in ("test", "testing")

engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool if is_testing else None,
    pool_pre_ping=not is_testing,
    pool_recycle=3600 if not is_testing else -1,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text
        migrations = [
            "ALTER TABLE users ADD COLUMN email VARCHAR(255) UNIQUE",
            "ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)",
            "ALTER TABLE plans ADD COLUMN name VARCHAR(150)",
            "ALTER TABLE plans ADD COLUMN version INT NOT NULL DEFAULT 1",
            "ALTER TABLE plans ADD COLUMN diet_type VARCHAR(50) DEFAULT 'veg'",
            "ALTER TABLE plan_meals ADD COLUMN diet_type VARCHAR(50) DEFAULT 'veg'",
        ]
        for stmt in migrations:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass


