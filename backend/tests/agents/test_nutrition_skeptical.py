import pytest
from backend.app.tools.nutrition.usda import lookup_nutrition
from backend.app.tools.nutrition.staples import get_staple_nutrition
from backend.app.tools.nutrition.calculator import (
    calculate_ingredient_nutrition,
    calculate_meal_nutrition,
    audit_meal_plan_nutrition
)
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, NutritionReport, NutritionIssue, DailyNutritionTotal
from backend.app.agents.nutrition.agent import NutritionAgent
from backend.app.agents.base import AgentStepAction
from backend.tests.fakes.fake_llm import FakeLLMClient

@pytest.mark.asyncio
async def test_usda_skeptical_whole_food_matching():
    """
    SKEPTICAL TEST: Verify that generic terms match authentic whole foods,
    NOT processed by-products (e.g. chicken != chicken spread, egg != egg white).
    """
    # 1. Chicken must resolve to whole chicken meat (~165 kcal, ~31g protein)
    chicken = await lookup_nutrition("chicken")
    assert chicken["found"] is True
    assert 150.0 <= chicken["calories"] <= 220.0
    assert chicken["protein_g"] >= 25.0

    # 2. Egg must resolve to whole egg (~143 kcal), NOT egg white (52 kcal)
    egg = await lookup_nutrition("egg")
    assert egg["found"] is True
    assert egg["calories"] > 100.0  # Whole egg is ~143 kcal, egg white is only 52 kcal
    assert egg["fat_g"] > 5.0       # Egg white has ~0g fat, whole egg has ~9.5g fat

    # 3. White rice must resolve to rice grain (~130 kcal), NOT rice flour
    rice = await lookup_nutrition("white rice")
    assert rice["found"] is True
    assert 100.0 <= rice["calories"] <= 150.0
    assert rice["carbs_g"] >= 20.0

    # 4. Olive oil must resolve to pure fat (~884 kcal)
    oil = await lookup_nutrition("olive oil")
    assert oil["found"] is True
    assert oil["calories"] >= 800.0
    assert oil["fat_g"] >= 90.0

@pytest.mark.asyncio
async def test_deterministic_portion_scaling_math():
    """Verify that ingredient gram scaling is exact."""
    # 200g chicken breast at 165 kcal/100g = exactly 330.0 kcal, 62.0g protein
    c_nut = await calculate_ingredient_nutrition("chicken breast", 200.0)
    assert c_nut["calories"] == 330.0
    assert c_nut["protein_g"] == 62.0

    # 50g oats at 389 kcal/100g = 194.5 kcal, carbs: 66.3 / 2 = 33.15 -> rounds to 33.1
    oats_nut = await calculate_ingredient_nutrition("oats", 50.0)
    assert oats_nut["calories"] == 194.5
    assert oats_nut["carbs_g"] == 33.1

@pytest.mark.asyncio
async def test_deterministic_meal_summation():
    """Verify that meal calories are computed by exact addition of ingredients."""
    meal = Meal(
        meal_type="lunch",
        recipe_name="Chicken Rice Bowl",
        ingredients=[
            Ingredient(name="chicken breast", amount_grams=150.0),  # 165 * 1.5 = 247.5
            Ingredient(name="white rice", amount_grams=200.0),       # 130 * 2.0 = 260.0
            Ingredient(name="olive oil", amount_grams=10.0)          # 884 * 0.1 = 88.4
        ]
    )
    result = await calculate_meal_nutrition(meal)
    expected_cal = round(247.5 + 260.0 + 88.4, 1)  # 595.9
    assert result["calories"] == expected_cal
    assert result["protein_g"] == round(31.0 * 1.5 + 2.7 * 2.0, 1)  # 46.5 + 5.4 = 51.9

def _meal(meal_type, name, ingredients):
    return Meal(meal_type=meal_type, recipe_name=name,
                ingredients=[Ingredient(name=n, amount_grams=g) for n, g in ingredients])


def _balanced_day(day_number, scale=1.0):
    """A realistic ~2,000 kcal day of ordinary foods (all in the offline staples table)."""
    return DayPlan(day_number=day_number, meals=[
        _meal("breakfast", "Oats and banana", [("oats", 100 * scale), ("whole milk", 200 * scale), ("banana", 100 * scale)]),
        _meal("lunch", "Paneer dal rice", [("rice", 250 * scale), ("lentils", 200 * scale), ("paneer", 100 * scale)]),
        _meal("dinner", "Chicken rice bowl", [("chicken breast", 150 * scale), ("rice", 150 * scale), ("olive oil", 10 * scale), ("broccoli", 150 * scale)]),
    ])


def _vegetable_only_day(day_number):
    """So little energy that no amount of portion balancing can reach 2,000 kcal."""
    return DayPlan(day_number=day_number, meals=[
        _meal("breakfast", "Cucumber plate", [("cucumber", 200), ("tomato", 100)]),
        _meal("lunch", "Garden salad", [("tomato", 250), ("cucumber", 200), ("spinach", 100)]),
        _meal("dinner", "Greens", [("spinach", 250), ("broccoli", 200)]),
    ])


@pytest.mark.asyncio
async def test_plan_tolerance_audit_pass_and_fail():
    """Verify the +/-5% calorie tolerance in pure Python code.

    The audit first balances portions to single-person size (ingredients are capped,
    meals are scaled toward their slot's share of the day), so a day only fails when
    even the balanced portions cannot reach the target - e.g. a vegetable-only day.
    """
    target = 2000.0
    plan = MealPlan(
        target_calories=target,
        days=[_balanced_day(1), _vegetable_only_day(2), _vegetable_only_day(3)],
    )

    passed, issues, daily_totals = await audit_meal_plan_nutrition(plan, target_calories=target, tolerance_pct=0.05)

    assert passed is False
    assert len(daily_totals) == 3
    # Day 1 lands inside [1900, 2100] and raises nothing.
    day1 = next(t for t in daily_totals if t.day_number == 1)
    assert abs(day1.calories - target) <= 0.05 * target
    assert {i.day_number for i in issues} == {2, 3}
    # Both flagged days are far under target, and the issue says by how much.
    for issue in issues:
        total = next(t for t in daily_totals if t.day_number == issue.day_number)
        assert issue.diff_calories < -0.05 * target
        assert issue.diff_calories == round(total.calories - target, 1)


@pytest.mark.asyncio
async def test_audit_balances_oversized_portions_instead_of_failing_them():
    """Portions twice as big as one person eats are scaled down, so the day passes."""
    target = 2000.0
    plan = MealPlan(target_calories=target, days=[_balanced_day(1, scale=2.0)])

    passed, issues, daily_totals = await audit_meal_plan_nutrition(plan, target_calories=target, tolerance_pct=0.05)

    assert passed is True and issues == []
    assert abs(daily_totals[0].calories - target) <= 0.05 * target


@pytest.fixture
def three_meal_veg_day_plan():
    return MealPlan(target_calories=1800.0, days=[_vegetable_only_day(1)])


@pytest.mark.asyncio
async def test_nutrition_agent_overrules_hallucinated_llm_math(three_meal_veg_day_plan):
    """
    SKEPTICAL TEST: if the LLM claims a far-too-low day is exactly 1800 kcal and marks
    passed: True, the Nutrition Agent's code math MUST overrule it - passed False, the
    day flagged, and the totals taken from the code, not from the LLM.
    """
    fake_llm = FakeLLMClient()
    fake_llm.queue_structured_response(AgentStepAction(
        thought="I glance at the plan and think the calories look fine.",
        action="finish",
        finish_summary="Plan looks balanced."
    ))
    # LLM hallucinates an inaccurate report
    fake_report = NutritionReport(
        passed=True,
        issues=[],
        summary="LLM says: Everything is exactly 1800 kcal!",
        daily_totals=[DailyNutritionTotal(day_number=1, calories=1800.0, protein_g=50.0, carbs_g=200.0, fat_g=40.0)]
    )
    fake_llm.queue_structured_response(fake_report)

    agent = NutritionAgent(client=fake_llm, max_steps=3)
    target = 1800.0
    plan = three_meal_veg_day_plan

    # What the deterministic code says about this very plan (audited on a copy,
    # because the audit balances portions in place).
    _, expected_issues, expected_totals = await audit_meal_plan_nutrition(plan.model_copy(deep=True), target)
    assert len(expected_issues) == 1

    report = await agent.review_plan(
        plan=plan,
        calorie_target=target,
        user_profile={"goal": "maintain", "activity_level": "medium"}
    )

    # GUARANTEED CODE OVERRIDE
    assert report.passed is False
    assert len(report.issues) == 1
    assert report.issues[0].day_number == 1
    assert report.issues[0].diff_calories < -0.05 * target
    assert report.issues[0].diff_calories == expected_issues[0].diff_calories
    assert report.daily_totals[0].calories == expected_totals[0].calories   # code total...
    assert report.daily_totals[0].calories != 1800.0                        # ...NOT the LLM's invention