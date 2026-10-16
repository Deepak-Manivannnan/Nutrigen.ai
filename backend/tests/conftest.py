"""Shared pytest helpers.

The big one is `throwaway_user`. Integration tests used to hardcode
`user_id = 1`, `user_id = 2`, ... and then delete every plan that belonged to
that id as setup. Those ids are real accounts, so running the suite silently
destroyed real users' saved plans. Tests now get their own throwaway account
instead.
"""
import uuid

import pytest_asyncio

from backend.app.db.repositories.users import UserRepository
from backend.app.db.session import AsyncSessionLocal
from backend.app.schemas.api import ProfileCreate


async def throwaway_user(profile: dict) -> int:
    """Create a fresh, uniquely-named user and return its id.

    The unique name matters as much as the row: a repeated run must not reuse
    (or collide with) anything a previous run left behind.
    """
    payload = dict(profile)
    payload["name"] = f"{profile.get('name', 'Test')} [t-{uuid.uuid4().hex[:10]}]"
    payload.setdefault("allergies", [])
    payload.setdefault("dislikes", [])
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).create(ProfileCreate(**payload))
        await session.commit()
        return user.id


async def clear_plans(user_id: int) -> None:
    """Delete only THIS test user's plans. Safe because the user is throwaway."""
    from backend.app.db.repositories.plans import PlanRepository

    async with AsyncSessionLocal() as session:
        repo = PlanRepository(session)
        for plan in await repo.get_all_by_user(user_id):
            await repo.delete_plan(plan.id)


@pytest_asyncio.fixture
async def new_user():
    """Factory fixture: `uid = await new_user(profile_dict)`."""
    return throwaway_user