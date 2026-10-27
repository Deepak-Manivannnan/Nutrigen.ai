"""
Delete account: confirmation (code or password), full data removal, tokens
refused afterwards, and a run in progress saving nothing.

Runs against an in-memory SQLite copy of the schema; Brevo is faked.
"""
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app import account_context
from backend.app.api import deps
from backend.app.api.routes import auth
from backend.app.api.security import create_token
from backend.app.db.models import (
    AgentTrace, EmailCode, Plan, PlanMeal, RefreshToken, User, UserAllergy,
)
from backend.app.db.repositories.email_codes import EmailCodeRepository
from backend.app.db.repositories.users import UserRepository
from backend.app.db.session import Base
from backend.tests.unit.test_llm_first_understanding import world  # noqa: F401
from backend.tests.unit.test_plan_request_flow import creating_graph  # noqa: F401


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def sent(monkeypatch):
    out = {"codes": [], "goodbye": []}

    async def fake_code(to, code, purpose):
        out["codes"].append((to, code, purpose))

    async def fake_goodbye(to, name="", used_google=False):
        out["goodbye"].append(to)
    monkeypatch.setattr(auth, "send_code_email", fake_code)
    monkeypatch.setattr(auth, "send_account_deleted_email", fake_goodbye)
    return out


async def _make_user(db, email="asha@b.co", verified=True, password_hash=None):
    now = datetime(2026, 1, 1)
    user = User(name="Asha", email=email, email_verified=verified, password_hash=password_hash,
                dislikes=[], created_at=now, updated_at=now)
    db.add(user)
    await db.flush()
    db.add(UserAllergy(user_id=user.id, allergen="peanut"))
    plan = Plan(user_id=user.id, name="Week", days=1, calorie_target=1800.0, status="approved",
                grocery_list={"items": [{"name": "rice"}]}, created_at=now, updated_at=now)
    db.add(plan)
    await db.flush()
    db.add(PlanMeal(plan_id=plan.id, day=1, meal_type="lunch", recipe_name="Bowl", ingredients=[], calories=500.0))
    db.add(RefreshToken(user_id=user.id, token_hash="h" * 64, family_id="f", created_at=now,
                        expires_at=datetime(2099, 1, 1)))
    db.add(AgentTrace(task_id="t-1", user_id=user.id, from_agent="supervisor", to_agent="planner_agent",
                      type="task", payload={"message": "plan for Asha"}, timestamp=now))
    db.add(AgentTrace(task_id="t-other", user_id=999, from_agent="supervisor", to_agent="planner_agent",
                      type="task", payload={}, timestamp=now))
    await db.commit()
    return await UserRepository(db).get_by_id(user.id)


async def _count(db, model, *where):
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar()


@pytest.fixture(autouse=True)
def _clean_deleted_set():
    account_context._deleted_user_ids.clear()
    yield
    account_context._deleted_user_ids.clear()


# ------------------------------------------------------------ full deletion
async def test_verified_account_is_deleted_with_a_code_and_nothing_is_left(db, sent):
    user = await _make_user(db)
    uid, repo, codes = user.id, UserRepository(db), EmailCodeRepository(db)

    start = await auth.delete_account_start(uid, repo, codes)
    assert start["method"] == "code" and start["masked_email"] == "a••a@b.co"
    (to, code, purpose), = sent["codes"]
    assert (to, purpose) == ("asha@b.co", "delete")

    res = await auth.delete_account_confirm(auth.DeleteAccountConfirmRequest(code=code), uid, repo, codes)
    assert res == {"deleted": True}

    assert await _count(db, User, User.id == uid) == 0
    assert await _count(db, Plan, Plan.user_id == uid) == 0
    assert await _count(db, PlanMeal) == 0
    assert await _count(db, UserAllergy, UserAllergy.user_id == uid) == 0
    assert await _count(db, RefreshToken, RefreshToken.user_id == uid) == 0
    assert await _count(db, AgentTrace, AgentTrace.user_id == uid) == 0
    assert await _count(db, EmailCode, EmailCode.email == "asha@b.co") == 0
    # Someone else's trace rows are untouched.
    assert await _count(db, AgentTrace, AgentTrace.user_id == 999) == 1
    assert sent["goodbye"] == ["asha@b.co"]


async def test_wrong_code_deletes_nothing(db, sent):
    user = await _make_user(db)
    repo, codes = UserRepository(db), EmailCodeRepository(db)
    await auth.delete_account_start(user.id, repo, codes)
    wrong = "000000" if sent["codes"][0][1] != "000000" else "111111"
    with pytest.raises(HTTPException) as err:
        await auth.delete_account_confirm(auth.DeleteAccountConfirmRequest(code=wrong), user.id, repo, codes)
    assert err.value.detail == "That code is not correct."
    assert await _count(db, User, User.id == user.id) == 1


async def test_a_password_reset_code_cannot_delete_the_account(db, sent):
    user = await _make_user(db)
    repo, codes = UserRepository(db), EmailCodeRepository(db)
    reset_code = await codes.create("asha@b.co", "reset")
    await auth.delete_account_start(user.id, repo, codes)
    with pytest.raises(HTTPException):
        await auth.delete_account_confirm(auth.DeleteAccountConfirmRequest(code=reset_code), user.id, repo, codes)
    assert await _count(db, User, User.id == user.id) == 1


# -------------------------------------------- old, never-verified accounts
async def test_unverified_old_account_confirms_with_its_password_and_gets_no_code(db, sent):
    user = await _make_user(db, email="old@example.com", verified=None,
                            password_hash=auth.hash_password("oldpassword1"))
    repo, codes = UserRepository(db), EmailCodeRepository(db)
    start = await auth.delete_account_start(user.id, repo, codes)
    assert start == {"method": "password"} and sent["codes"] == []

    with pytest.raises(HTTPException) as err:
        await auth.delete_account_confirm(auth.DeleteAccountConfirmRequest(password="wrong-one"), user.id, repo, codes)
    assert err.value.status_code == 400   # not 401: that would sign the user out
    assert await _count(db, User, User.id == user.id) == 1

    await auth.delete_account_confirm(auth.DeleteAccountConfirmRequest(password="oldpassword1"), user.id, repo, codes)
    assert await _count(db, User, User.id == user.id) == 0
    auth._failed_logins.pop("old@example.com", None)


# ------------------------------------------------ after deletion
async def test_a_deleted_accounts_token_stops_working_at_once(db, sent):
    user = await _make_user(db)
    token = create_token(user.id)
    assert deps.get_authenticated_user_id(f"Bearer {token}") == user.id
    repo, codes = UserRepository(db), EmailCodeRepository(db)
    await auth.delete_account_start(user.id, repo, codes)
    await auth.delete_account_confirm(auth.DeleteAccountConfirmRequest(code=sent["codes"][0][1]), user.id, repo, codes)
    with pytest.raises(HTTPException) as err:
        deps.get_authenticated_user_id(f"Bearer {token}")
    assert err.value.status_code == 401


async def test_the_same_email_can_sign_up_again_as_a_fresh_account(db, sent):
    user = await _make_user(db)
    old_id, repo, codes = user.id, UserRepository(db), EmailCodeRepository(db)
    await auth.delete_account_start(old_id, repo, codes)
    await auth.delete_account_confirm(auth.DeleteAccountConfirmRequest(code=sent["codes"][0][1]), old_id, repo, codes)

    fresh = await repo.create_account(name="Asha", email="asha@b.co", password_hash=None, email_verified=True)
    assert await _count(db, Plan, Plan.user_id == fresh.id) == 0
    # Even if the database hands out the old id again, the new account works.
    assert not account_context.is_deleted(fresh.id)
    assert deps.get_authenticated_user_id(f"Bearer {create_token(fresh.id)}") == fresh.id
    assert fresh.allergies == []


async def test_a_plan_finishing_after_deletion_is_not_saved(world, creating_graph):  # noqa: F811
    """The account is deleted while its plan is being built: nothing is saved."""
    from backend.tests.unit.test_plan_request_flow import CreatingRepo, new_plan
    from backend.tests.unit.test_llm_first_understanding import run

    class DeletingGraph:
        async def ainvoke(self, state):
            account_context.mark_deleted(1)          # user 1 deletes mid-run
            return await creating_graph.ainvoke(state)
    import backend.app.orchestration.runner as runner
    runner.create_meal_planner_graph = lambda *a, **k: DeletingGraph()

    state = await run(world, "3 day plan", new_plan(requested_days=3))
    assert CreatingRepo.created == []
    assert state.current_plan is None and state.status == "failed"


async def test_the_same_run_saves_normally_when_the_account_still_exists(world, creating_graph):  # noqa: F811
    from backend.tests.unit.test_plan_request_flow import CreatingRepo, new_plan
    from backend.tests.unit.test_llm_first_understanding import run
    await run(world, "3 day plan", new_plan(requested_days=3))
    assert len(CreatingRepo.created) == 1
