"""
Grocery list and final safety check run only when the user approves.

- Drafting (the graph) stops at a reviewed draft: no Shopper, no grocery list.
- On approval the Shopper builds the list, then the output guardrail re-checks
  the plan AND the list. Pass -> approved with the list. Fail -> still a draft,
  no list saved, and the user is told why.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.app.db.models import Plan, PlanMeal
from backend.app.orchestration import approval, runner
from backend.app.orchestration.graph import create_meal_planner_graph
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.orchestration.supervisor.intent import UserIntentExtraction
from backend.app.schemas.domain import (
    DayPlan, GroceryItem, GroceryList, Ingredient, Meal, MealPlan,
)
from backend.app.schemas.state import PlanState
from backend.tests.unit.test_llm_first_understanding import FakeRepo, PROFILE, run, world  # noqa: F401
from backend.tests.integration.test_targeted_repair_graph import (
    FakeAllergy, FakeNutrition, FakePlanner, PROFILE as GRAPH_PROFILE,
)


def _meal_plan(ingredient="rice", diet="veg"):
    meal = Meal(meal_type="lunch", recipe_name="Bowl", diet_type=diet, source="ai_generated",
                ingredients=[Ingredient(name=ingredient, amount_grams=100.0)], calories=500.0)
    return MealPlan(days=[DayPlan(day_number=1, meals=[meal], total_calories=500.0)],
                    target_calories=1800.0, diet_type=diet)


def _fake_shopper(items):
    class FakeShopper:
        calls = 0

        async def generate_grocery_list(self, meal_plan, task_id=None):
            FakeShopper.calls += 1
            return GroceryList(items=[GroceryItem(name=n, total_grams=100.0) for n in items],
                               total_estimated_cost=50.0)
    return FakeShopper


# ------------------------------------------------------------ drafting: no Shopper
async def test_reviewed_draft_ends_without_a_grocery_list_or_the_shopper():
    graph = create_meal_planner_graph(
        planner=FakePlanner(), nutrition=FakeNutrition(), allergy=FakeAllergy(),
        supervisor=SupervisorAgent(),
    )
    state = PlanState(task_id="t-draft", user_id=1, user_profile=GRAPH_PROFILE, days=2, calorie_target=2550.0)

    result = PlanState.model_validate(await graph.ainvoke(state))

    assert result.status == "reviewing"          # what the runner saves as Draft
    assert result.current_plan is not None
    assert result.grocery_list is None
    assert not [m for m in result.trace if "shopper" in (m.to_agent + m.from_agent)]


# ------------------------------------------------- approval: Shopper + final check
async def test_clean_plan_and_list_pass_the_final_check(monkeypatch):
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["rice", "salt"]))
    grocery = await approval.build_and_verify_grocery(_meal_plan(), ["peanut"], session=None, task_id="a-1")
    assert [i.name for i in grocery.items] == ["rice", "salt"]


async def test_an_allergen_in_the_grocery_list_blocks_approval(monkeypatch):
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["rice", "peanut butter"]))
    with pytest.raises(approval.ApprovalBlocked) as blocked:
        await approval.build_and_verify_grocery(_meal_plan(), ["peanut"], session=None, task_id="a-2")
    assert "peanut butter" in blocked.value.reason
    assert "still a draft" in blocked.value.reason
    assert "Critical safety failure" not in blocked.value.reason


async def test_a_diet_violation_in_the_meals_blocks_approval(monkeypatch):
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["rice"]))
    with pytest.raises(approval.ApprovalBlocked) as blocked:
        await approval.build_and_verify_grocery(_meal_plan("chicken breast"), [], session=None, task_id="a-3")
    assert "chicken" in blocked.value.reason


# -------------------------------------------------------- chat "approve it" path
class _Session:
    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


class ApproveRepo(FakeRepo):
    session = _Session()

    async def get_by_id(self, plan_id):
        return next((p for p in FakeRepo.plans if p.id == plan_id), None)


def _draft_plan():
    meal = PlanMeal(id=11, plan_id=1, day=1, meal_type="lunch", recipe_name="Bowl", source="ai_generated", locked=False,
                    ingredients=[{"name": "rice", "amount_grams": 100.0}], calories=500.0, macros={})
    return Plan(id=1, user_id=1, name="Veg Day", days=1, calorie_target=1800.0, diet_type="veg",
                status="draft", version=1, meals=[meal], created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))


@pytest.fixture
def approving(world, monkeypatch):
    FakeRepo.plans = [_draft_plan()]
    monkeypatch.setattr(runner, "PlanRepository", ApproveRepo)
    return world


async def test_chat_approval_saves_the_list_when_the_final_check_passes(approving, monkeypatch):
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["rice"]))
    state = await run(approving, "approve it", UserIntentExtraction(intent="approve_plan", target_plan_id=1))

    plan = FakeRepo.plans[0]
    assert state.status == "approved"
    assert plan.status == "approved"
    assert plan.grocery_list and plan.grocery_list["items"][0]["name"] == "rice"


async def test_chat_approval_keeps_the_draft_and_saves_no_list_when_the_final_check_fails(approving, monkeypatch):
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["peanut butter"]))
    profile_with_allergy = dict(PROFILE, allergies=["peanut"])
    approving.scripted["intent"] = UserIntentExtraction(intent="approve_plan", target_plan_id=1)
    state = await runner.run_meal_planner(user_id=1, user_message="approve it", user_profile=profile_with_allergy,
                                          history=None, pending_workflow=None)

    plan = FakeRepo.plans[0]
    assert state.status == "draft"
    assert plan.status == "draft"
    assert not plan.grocery_list
    assert "NOT approved" in approving.replies[-1]["context"]
    assert "peanut butter" in approving.replies[-1]["context"]



# ------------------------------------------------------------ Approve button path
class _ButtonRepo:
    def __init__(self, plan):
        self.plan = plan
        self.session = _Session()

    async def get_by_id(self, plan_id):
        return self.plan


class _Users:
    def __init__(self, session):
        pass

    async def get_by_id(self, user_id):
        return SimpleNamespace(allergies=[SimpleNamespace(allergen="peanut")])


@pytest.fixture
def button(monkeypatch):
    from backend.app.db.repositories import users
    monkeypatch.setattr(users, "UserRepository", _Users)
    from backend.app.api.routes import plans as plans_route
    return plans_route


async def test_approve_button_saves_the_list_when_the_final_check_passes(button, monkeypatch):
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["rice"]))
    plan = _draft_plan()
    await button.approve_plan(plan_id=1, caller_id=1, plan_repo=_ButtonRepo(plan))
    assert plan.status == "approved"
    assert plan.grocery_list["items"][0]["name"] == "rice"


async def test_approve_button_keeps_the_draft_and_explains_when_the_final_check_fails(button, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["peanut butter"]))
    plan = _draft_plan()
    with pytest.raises(HTTPException) as err:
        await button.approve_plan(plan_id=1, caller_id=1, plan_repo=_ButtonRepo(plan))
    assert err.value.status_code == 422
    assert "peanut butter" in err.value.detail
    assert plan.status == "draft"
    assert not plan.grocery_list
