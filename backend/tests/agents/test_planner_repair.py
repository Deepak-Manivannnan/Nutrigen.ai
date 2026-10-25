"""Planner: redo only what the Supervisor flagged, keep everything else (no database, no AI).`n`nThe Planner receives a RepairTargets instruction from the Supervisor; it never sees the`nNutrition or Allergy reports. The report-to-instruction step is tested in`ntests/unit/test_supervisor_repair_targets.py."""
import pytest

from backend.app.agents.planner.agent import PlannerAgent
from backend.app.schemas.domain import DayPlan, Ingredient, Meal, MealPlan, RepairItem, RepairTargets
from backend.app.tools.nutrition import calculator
from backend.tests.fakes.fake_llm import FakeLLMClient

TARGET = 1800.0
PROFILE = {"name": "T", "diet_type": "non-veg", "allergies": ["peanut"], "dislikes": []}


@pytest.fixture(autouse=True)
def offline_nutrition(monkeypatch):
    """2 kcal per gram, no macros: deterministic and no USDA lookups."""
    async def fake_meal_nutrition(meal):
        total = sum(i.amount_grams for i in meal.ingredients) * 2.0
        return {"calories": round(total, 1), "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    monkeypatch.setattr(calculator, "calculate_meal_nutrition", fake_meal_nutrition)


def meal(meal_type, name, grams, locked=False, ingredient="rice"):
    parts = [grams * 0.5, grams * 0.5]
    return Meal(
        meal_type=meal_type, recipe_name=name, locked=locked, diet_type="non-veg",
        source="ai_generated", calories=grams * 2.0,
        ingredients=[Ingredient(name=ingredient, amount_grams=parts[0]), Ingredient(name="lentils", amount_grams=parts[1])],
        instructions="1. Rinse everything well.\n2. Cook until tender.\n3. Season and serve warm.",
    )


def make_plan(days=3):
    out = []
    for d in range(1, days + 1):
        out.append(DayPlan(
            day_number=d,
            meals=[meal("breakfast", f"Breakfast {d}", 270), meal("lunch", f"Lunch {d}", 360), meal("dinner", f"Dinner {d}", 270)],
            total_calories=1800.0,
        ))
    return MealPlan(name="P", days=out, target_calories=TARGET, diet_type="non-veg", average_daily_calories=1800.0)


def redo_meal(day, meal_type, why="it contained 'peanut butter' (contains peanuts)"):
    return RepairTargets(items=[RepairItem(day_number=day, meal_type=meal_type, reasons=[why])])


def redo_day(day, why="Day is 400 kcal over target. Action: Reduce portions"):
    return RepairTargets(items=[RepairItem(day_number=day, meal_type=None, reasons=[why])])

# ---------------------------------------------------------------- repair_plan
def untouched(original, repaired, except_slots):
    for od, rd in zip(original.days, repaired.days):
        for om, rm in zip(od.meals, rd.meals):
            if (od.day_number, om.meal_type) not in except_slots:
                assert om.model_dump() == rm.model_dump(), f"day {od.day_number} {om.meal_type} changed"


async def test_one_bad_meal_out_of_nine_only_that_meal_changes():
    original = make_plan(3)
    original.days[1].meals[1].ingredients[0].name = "peanut butter"   # day 2 lunch
    llm = FakeLLMClient()
    llm.queue_structured_response(meal("lunch", "Chicken Curry", 360, ingredient="chicken"))
    planner = PlannerAgent(client=llm, max_steps=1)
    targets = redo_meal(2, "lunch")

    repaired = await planner.repair_plan(original, targets, TARGET, PROFILE, version=2)

    assert len(llm.call_history) == 1                       # the AI was asked for ONE meal
    assert "Day 2" in llm.call_history[0]["prompt"] and "peanut butter" in llm.call_history[0]["prompt"]
    new_lunch = repaired.days[1].meals[1]
    assert new_lunch.recipe_name == "Chicken Curry" and new_lunch.meal_type == "lunch"
    assert all("peanut" not in i.name for i in new_lunch.ingredients)
    untouched(original, repaired, {(2, "lunch")})            # the other 8 are identical
    assert repaired.version == 2
    assert repaired.days[1].total_calories == pytest.approx(1800, abs=1)   # day total kept


async def test_calorie_problem_redoes_only_that_day():
    original = make_plan(3)
    for m in original.days[2].meals:                         # day 3 way over target
        for ing in m.ingredients:
            ing.amount_grams *= 1.6
        m.calories *= 1.6
    llm = FakeLLMClient()
    for t, n in (("breakfast", "Poha"), ("lunch", "Dal Rice"), ("dinner", "Veg Pulao")):
        llm.queue_structured_response(meal(t, n, {"breakfast": 270, "lunch": 360, "dinner": 270}[t]))
    planner = PlannerAgent(client=llm, max_steps=1)
    targets = redo_day(3)

    repaired = await planner.repair_plan(original, targets, TARGET, PROFILE, version=2)

    assert len(llm.call_history) == 3                        # three meals for the one day
    assert [m.recipe_name for m in repaired.days[2].meals] == ["Poha", "Dal Rice", "Veg Pulao"]
    untouched(original, repaired, {(3, "breakfast"), (3, "lunch"), (3, "dinner")})
    assert abs(repaired.days[2].total_calories - TARGET) <= 0.05 * TARGET


async def test_locked_meal_in_a_flagged_day_is_kept():
    original = make_plan(2)
    original.days[0].meals[0].locked = True
    llm = FakeLLMClient()
    llm.queue_structured_response(meal("lunch", "New Lunch", 360))
    llm.queue_structured_response(meal("dinner", "New Dinner", 270))
    planner = PlannerAgent(client=llm, max_steps=1)
    targets = redo_day(1)

    repaired = await planner.repair_plan(original, targets, TARGET, PROFILE, version=2)

    assert len(llm.call_history) == 2                        # breakfast was not regenerated
    assert repaired.days[0].meals[0].model_dump() == original.days[0].meals[0].model_dump()
    assert repaired.days[0].meals[0].locked is True


async def test_replacement_that_breaks_the_allergy_is_retried_once():
    original = make_plan(1)
    llm = FakeLLMClient()
    llm.queue_structured_response(meal("lunch", "Satay", 360, ingredient="peanut butter"))   # still bad
    llm.queue_structured_response(meal("lunch", "Chicken Rice", 360, ingredient="chicken"))   # fine
    planner = PlannerAgent(client=llm, max_steps=1)
    targets = redo_meal(1, "lunch")

    repaired = await planner.repair_plan(original, targets, TARGET, PROFILE, version=2)

    assert len(llm.call_history) == 2
    assert "rejected" in llm.call_history[1]["prompt"]
    assert repaired.days[0].meals[1].recipe_name == "Chicken Rice"


async def test_untouched_days_are_never_sent_to_the_ai():
    original = make_plan(3)
    llm = FakeLLMClient()
    llm.queue_structured_response(meal("dinner", "Fish Curry", 270, ingredient="fish"))
    planner = PlannerAgent(client=llm, max_steps=1)
    targets = redo_meal(3, "dinner")
    await planner.repair_plan(original, targets, TARGET, PROFILE, version=2)
    prompt = llm.call_history[0]["prompt"]
    assert "Day 3" in prompt and "Day 1" not in prompt and "Day 2" not in prompt
