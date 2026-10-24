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
            "ALTER TABLE plans ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
            "ALTER TABLE plans ADD COLUMN approved_at DATETIME NULL",
            "ALTER TABLE plan_meals ADD COLUMN diet_type VARCHAR(50) DEFAULT 'veg'",
            "ALTER TABLE users ADD COLUMN bmr FLOAT",
            "ALTER TABLE users ADD COLUMN tdee FLOAT",
            "ALTER TABLE users ADD COLUMN min_safe_calories FLOAT",
            "ALTER TABLE users ADD COLUMN max_safe_calories FLOAT",
            "ALTER TABLE users ADD COLUMN suggested_target_calories FLOAT",
            # Relax the profile columns on databases created before onboarding was
            # split out of registration. A user row legitimately exists with these
            # empty between registering and finishing onboarding, and a fresh
            # schema inherited NOT NULL from the old model, which made MySQL
            # reject registration outright.
            "ALTER TABLE users MODIFY COLUMN age INT NULL",
            "ALTER TABLE users MODIFY COLUMN sex VARCHAR(20) NULL",
            "ALTER TABLE users MODIFY COLUMN height_cm FLOAT NULL",
            "ALTER TABLE users MODIFY COLUMN weight_kg FLOAT NULL",
            "ALTER TABLE users MODIFY COLUMN activity_level VARCHAR(20) NULL",
            "ALTER TABLE users MODIFY COLUMN diet_type VARCHAR(20) NULL",
        ]
        for stmt in migrations:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass




