from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.session import get_db
from backend.app.db.repositories.users import UserRepository
from backend.app.db.repositories.plans import PlanRepository
from backend.app.db.repositories.trace import TraceRepository
from backend.app.db.repositories.refresh_tokens import RefreshTokenRepository
from backend.app.db.repositories.email_codes import EmailCodeRepository
from backend.app.api.security import decode_token
from backend.app.account_context import is_deleted

async def get_user_repository(session: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(session)

async def get_plan_repository(session: AsyncSession = Depends(get_db)) -> PlanRepository:
    return PlanRepository(session)

async def get_trace_repository(session: AsyncSession = Depends(get_db)) -> TraceRepository:
    return TraceRepository(session)

async def get_refresh_token_repository(session: AsyncSession = Depends(get_db)) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)

async def get_email_code_repository(session: AsyncSession = Depends(get_db)) -> EmailCodeRepository:
    return EmailCodeRepository(session)


def get_authenticated_user_id(authorization: str = Header(default="")) -> int:
    """Identify the caller from their bearer token.

    Every endpoint that touches one user's data depends on this. Previously the
    user came from a query parameter or a path segment, so anyone could read or
    modify another account by guessing an id.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in.",
        )
    user_id = decode_token(authorization.split(" ", 1)[1].strip())
    # A deleted account's still-unexpired token must stop working at once.
    if user_id is None or is_deleted(user_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
        )
    return user_id
