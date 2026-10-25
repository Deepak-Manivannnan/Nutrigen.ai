"""Single-meal refusal and offer, foods the user listed, and one-message-two-questions (no database, no live AI)."""
import pytest

from backend.app.orchestration import runner
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.orchestration.supervisor.intent import UserIntentExtraction
from backend.app.orchestration.supervisor.routing import (
    decide_next_step, find_repair_targets, missing_main_ingredients,
)
from backend.app.schemas.domain import (
    AllergyReport, AllergyViolation, DayPlan, Ingredient, Meal, MealPlan, NutritionReport,
)
from backend.tests.unit.test_llm_first_understanding import PROFILE, run, world  # noqa: F401
from backend.tests.unit.test_plan_request_flow import FakeGraph, graph, new_plan  # noqa: F401


def plan_with(foods_by_slot, days=2):
    """foods_by_slot: {(day, meal_type): ["chicken", ...]}"""
    out = []
    for d in range(1, days + 1):
        meals = []
        for t in ("breakfast", "lunch", "dinner"):
            names = foods_by_slot.get((d, t), ["rice"])
            meals.append(Meal(meal_type=t, recipe_name=f"{t} {d}", calories=600,
                              ingredients=[Ingredient(name=n, amount_grams=100) for n in names]))
        out.append(DayPlan(day_number=d, meals=meals))
    return MealPlan(days=out, target_calories=1800.0, diet_type="non-veg")


# ------------------------------------------------------------------ the ingredient check (plain code)
def test_missing_foods_are_found_ignoring_plural_and_case():
    plan = plan_with({(1, "lunch"): ["Chicken breast", "Tomatoes"], (2, "dinner"): ["spinach"]})
    assert missing_main_ingredients(plan, ["chicken", "tomato", "paneer", "Spinach"]) == ["paneer"]
    assert missing_main_ingredients(plan, ["tomatoes", "potatoes"]) == ["potatoes"]


def test_a_food_must_match_as_a_whole_word():
    plan = plan_with({(1, "lunch"): ["peanut butter"]})
    assert missing_main_ingredients(plan, ["pea"]) == ["pea"]                 # "pea" is not inside "peanut"
    assert missing_main_ingredients(plan, ["peanut"]) == []


def test_recipe_names_count_and_nothing_wanted_means_nothing_missing():
    plan = plan_with({})
    plan.days[0].meals[0].recipe_name = "Chicken Tikka Bowl"
    assert missing_main_ingredients(plan, ["chicken"]) == []
    assert missing_main_ingredients(plan, []) == [] and missing_main_ingredients(None, ["x"]) == []


def test_each_missing_food_gets_a_lunch_or_dinner_slot_spread_over_the_days():
    plan = plan_with({}, days=3)
    plan.days[0].meals[1].locked = True                                        # day 1 lunch is the user's
    t = find_repair_targets(plan, None, None, ["chicken", "tomato", "paneer"])
    slots = sorted((i.day_number, i.meal_type) for i in t.items)
    assert len(slots) == 3 and (1, "lunch") not in slots
    assert all(mt in ("lunch", "dinner") for _, mt in slots)
    assert any("chicken" in r for i in t.items for r in i.reasons)


def test_a_missing_food_alone_triggers_one_targeted_retry_then_is_let_go():
    plan = plan_with({}, days=2)
    ok_n, ok_a = NutritionReport(passed=True), AllergyReport(passed=True)

    first = decide_next_step(1, ok_n, ok_a, plan=plan, missing_ingredients=["chicken"])
    assert first.action == "retry_planner" and first.repair is not None
    assert "chicken" in first.feedback[0]

    last = decide_next_step(3, ok_n, ok_a, plan=plan, missing_ingredients=["chicken"])
    assert last.action == "proceed_shopper"                                    # soft: never holds back a safe plan


def test_missing_foods_do_not_weaken_a_safety_veto():
    plan = plan_with({}, days=2)
    veto = AllergyReport(passed=False, violations=[AllergyViolation(
        day_number=1, meal_type="dinner", ingredient="peanut", allergen="peanut", reason="contains peanuts")])
    d = decide_next_step(3, NutritionReport(passed=True), veto, plan=plan, missing_ingredients=["chicken"])
    assert d.action == "escalate"
    d2 = decide_next_step(1, NutritionReport(passed=True), veto, plan=plan, missing_ingredients=["chicken"])
    assert d2.action == "retry_planner" and any("chicken" in f for f in d2.feedback)


# ------------------------------------------------------------------ what the Planner is told
async def test_style_and_main_foods_reach_every_planner_prompt():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.tests.fakes.fake_llm import FakeLLMClient

    llm = FakeLLMClient()
    llm.queue_structured_response(plan_with({}, days=1))
    await PlannerAgent(client=llm, max_steps=1).generate_plan(
        days=1, calorie_target=1800.0, user_profile=dict(PROFILE),
        style_notes=["high protein"], main_ingredients=["chicken", "tomato"])
    prompts = " ".join(c["prompt"] for c in llm.call_history)
    assert "STYLE: the user wants the food to be high protein" in prompts
    assert "MAIN INGREDIENTS: the user has or wants chicken, tomato" in prompts


async def test_repaired_meals_are_built_around_the_foods_too():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.app.schemas.domain import RepairItem, RepairTargets
    from backend.tests.agents.test_planner_repair import offline_nutrition  # noqa: F401
    from backend.tests.fakes.fake_llm import FakeLLMClient

    llm = FakeLLMClient()
    llm.queue_structured_response(Meal(meal_type="lunch", recipe_name="Chicken Curry", calories=600,
                                       ingredients=[Ingredient(name="chicken", amount_grams=200)]))
    targets = RepairTargets(items=[RepairItem(day_number=1, meal_type="lunch", reasons=["it must be built around chicken"])])
    await PlannerAgent(client=llm, max_steps=1).repair_plan(
        plan_with({}, days=1), targets, 1800.0, dict(PROFILE), version=2,
        style_notes=["high protein"], main_ingredients=["chicken"])
    prompt = llm.call_history[0]["prompt"]
    assert "MAIN INGREDIENTS" in prompt and "chicken" in prompt and "high protein" in prompt


def test_the_new_fields_are_cleaned_up():
    u = UserIntentExtraction(intent="new_plan", main_ingredients=["Chicken", " tomato", "chicken"], style_notes="High Protein")
    assert u.main_ingredients == ["chicken", "tomato"] and u.style_notes == ["high protein"]


# ------------------------------------------------------------------ the polite refusal and offer
async def test_a_request_the_app_cannot_do_gets_an_offer_and_keeps_the_details(world, graph):
    intent = UserIntentExtraction(
        intent="new_plan", unsupported_reason="wants only dinners, not whole days",
        requested_cuisine="South Indian", style_notes=["high protein"], main_ingredients=["chicken"],
    )
    state = await run(world, "create a high protein South Indian dinner plan with chicken", intent)

    assert graph.started == []                                             # nothing was built
    assert state.pending_workflow.pending_conflicts == ["capability_offer"]
    ctx = world.replies[-1]["context"]
    assert "wants only dinners, not whole days" in ctx
    assert "whole days" in ctx and "1 up to 7 days" in ctx                 # says what it CAN do
    assert "South Indian" in ctx and "high protein" in ctx                 # acknowledges what they said
    assert "you are only asking" in ctx
    carry = state.pending_workflow.extracted_info["carry"]
    assert carry["cuisine"] == "South Indian" and carry["style_notes"] == ["high protein"] and carry["main_ingredients"] == ["chicken"]


async def test_yes_to_the_offer_builds_the_full_day_plan_with_everything_they_asked(world, graph):
    first = await run(world, "high protein dinner plan with chicken", UserIntentExtraction(
        intent="new_plan", unsupported_reason="only dinners", requested_cuisine="South Indian",
        style_notes=["high protein"], main_ingredients=["chicken"]))

    # "yes, 5 days": the model reads it as a plan request with 5 days and no unsupported flag.
    await run(world, "yes, 5 days", new_plan(requested_days=5, requested_calories=None), pending=first.pending_workflow)

    assert len(graph.started) == 1
    s = graph.started[0]
    assert s.days == 5 and s.cuisine == "South Indian"
    assert s.style_notes == ["high protein"] and s.main_ingredients == ["chicken"]


async def test_an_ordinary_request_is_never_refused(world, graph):
    await run(world, "a 3 day high protein plan", new_plan(requested_days=3, style_notes=["high protein"], requested_calories=None))
    assert len(graph.started) == 1


# ------------------------------------------------------------------ "I only have these foods"
async def test_only_these_foods_with_no_days_asks_both_things_in_one_message(world, graph):
    intent = UserIntentExtraction(intent="new_plan", main_ingredients=["chicken", "tomato"], ingredients_only=True)
    state = await run(world, "I have only chicken and tomatoes, make me a plan", intent)

    assert graph.started == []
    assert state.pending_workflow.pending_conflicts == ["ingredients_offer", "days_missing"]
    ctx = world.replies[-1]["context"]
    assert "chicken, tomato" in ctx and "MAIN ingredients" in ctx and "how many days" in ctx
    assert len(world.replies) == 1                                         # one message, not two


async def test_only_these_foods_with_days_given_asks_just_the_honest_question(world, graph):
    intent = UserIntentExtraction(intent="new_plan", requested_days=3, main_ingredients=["chicken"], ingredients_only=True)
    state = await run(world, "3 days, I have only chicken", intent)

    assert state.pending_workflow.pending_conflicts == ["ingredients_offer"]
    assert "how many days" not in world.replies[-1]["context"]


async def test_after_the_offer_the_plan_is_built_around_the_foods_and_not_asked_again(world, graph):
    first = await run(world, "I have only chicken and tomatoes", UserIntentExtraction(
        intent="new_plan", main_ingredients=["chicken", "tomato"], ingredients_only=True))
    # Even if the model still flags "only" on the answer, the offer is not repeated.
    await run(world, "yes, 3 days", new_plan(requested_days=3, requested_calories=None, ingredients_only=True),
              pending=first.pending_workflow)

    assert len(graph.started) == 1
    assert graph.started[0].main_ingredients == ["chicken", "tomato"] and graph.started[0].days == 3


async def test_a_plain_wish_to_include_a_food_is_not_questioned(world, graph):
    await run(world, "3 day plan with chicken", new_plan(requested_days=3, requested_calories=None, main_ingredients=["chicken"]))
    assert len(graph.started) == 1 and graph.started[0].main_ingredients == ["chicken"]


# ------------------------------------------------------------------ fewer, fuller questions
async def test_a_diet_conflict_and_missing_days_are_asked_together(world, graph, monkeypatch):
    seen = {}

    async def conflict_prompt(self, **kwargs):
        seen.update(kwargs)
        return "(conflict question that also asks the days)"
    monkeypatch.setattr(SupervisorAgent, "generate_diet_conflict_prompt", conflict_prompt)

    state = await run(world, "non-veg plan", new_plan(requested_diet_type="non-veg", requested_calories=None))

    assert seen["days_val"] is None                                        # the prompt is told the days are unknown
    assert "days_missing" in state.pending_workflow.pending_conflicts
    assert "diet_conflict" in state.pending_workflow.pending_conflicts
    assert graph.started == []


async def test_an_unsafe_calorie_target_and_missing_days_are_asked_together(world, graph):
    state = await run(world, "a plan at 500 kcal", new_plan(requested_calories=500.0))

    assert "calorie_guardrail" in state.pending_workflow.pending_conflicts
    assert "days_missing" in state.pending_workflow.pending_conflicts
    assert "also not said how many days" in world.replies[-1]["context"]
    assert graph.started == []


async def test_once_the_other_questions_are_settled_a_missing_length_is_still_asked(world, graph):
    # A safe calorie target and no days: the days question comes by itself.
    state = await run(world, "a plan at 2050 kcal", new_plan(requested_calories=2050.0))
    assert state.pending_workflow.pending_conflicts == ["days_missing"]
