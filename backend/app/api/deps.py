from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.session import get_db
from backend.app.db.repositories.users import UserRepository
from backend.app.db.repositories.plans import PlanRepository
from backend.app.db.repositories.trace import TraceRepository

async def get_user_repository(session: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(session)

async def get_plan_repository(session: AsyncSession = Depends(get_db)) -> PlanRepository:
    return PlanRepository(session)

async def get_trace_repository(session: AsyncSession = Depends(get_db)) -> TraceRepository:
    return TraceRepository(session)
