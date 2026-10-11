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
            "ALTER TABLE users ADD COLUMN bmr FLOAT",
            "ALTER TABLE users ADD COLUMN tdee FLOAT",
            "ALTER TABLE users ADD COLUMN min_safe_calories FLOAT",
            "ALTER TABLE users ADD COLUMN max_safe_calories FLOAT",
            "ALTER TABLE users ADD COLUMN suggested_target_calories FLOAT",
        ]
        for stmt in migrations:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass

    # Sanitize any existing plans in database to enforce vegetarian rules & single-person portions
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from backend.app.db.models import Plan
        from backend.app.db.repositories.plans import PlanRepository
        from backend.app.tools.nutrition.calculator import normalize_single_person_plan

        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Plan).options(selectinload(Plan.meals)))
            existing_plans = res.scalars().all()
            for p in existing_plans:
                p_diet = (p.diet_type or "veg").lower()
                needs_update = False
                for m in (p.meals or []):
                    rec_name = (m.recipe_name or "").lower()
                    if (p_diet in ("veg", "vegetarian", "vegan") and any(term in rec_name for term in ["turkey", "chicken", "beef", "pork", "bacon", "fish", "meat"])) or (m.calories and m.calories > 1000):
                        needs_update = True
                        break

                if needs_update:
                    domain_p = PlanRepository.to_meal_plan_domain(p)
                    await normalize_single_person_plan(domain_p, p.calorie_target or 1850.0)
                    repo = PlanRepository(session)
                    await repo.update_plan(
                        plan_id=p.id,
                        meal_plan=domain_p,
                        grocery_list=None,
                        status=p.status or "approved",
                        increment_version=False
                    )
    except Exception:
        pass


