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

@pytest.mark.asyncio
async def test_plan_tolerance_audit_pass_and_fail():
    """Verify ±5% calorie tolerance boundaries in pure Python code."""
    target = 2000.0
    # ±5% is [1900.0, 2100.0]

    # Day 1: 1950 kcal (Pass)
    # Day 2: 2300 kcal (+300 kcal, Fail)
    # Day 3: 1700 kcal (-300 kcal, Fail)
    plan = MealPlan(
        target_calories=target,
        days=[
            DayPlan(day_number=1, meals=[
                Meal(meal_type="breakfast", recipe_name="M1", ingredients=[Ingredient(name="oats", amount_grams=501.3)]) # 389 * 5.013 ≈ 1950
            ]),
            DayPlan(day_number=2, meals=[
                Meal(meal_type="lunch", recipe_name="M2", ingredients=[Ingredient(name="chicken breast", amount_grams=1394.0)]) # 165 * 13.94 ≈ 2300
            ]),
            DayPlan(day_number=3, meals=[
                Meal(meal_type="dinner", recipe_name="M3", ingredients=[Ingredient(name="white rice", amount_grams=1307.7)]) # 130 * 13.077 ≈ 1700
            ])
        ]
    )

    passed, issues, daily_totals = await audit_meal_plan_nutrition(plan, target_calories=target, tolerance_pct=0.05)

    assert passed is False
    assert len(issues) == 2
    assert any(i.day_number == 2 and i.diff_calories > 0 for i in issues)
    assert any(i.day_number == 3 and i.diff_calories < 0 for i in issues)
    assert len(daily_totals) == 3

@pytest.mark.asyncio
async def test_nutrition_agent_overrules_hallucinated_llm_math():
    """
    SKEPTICAL TEST: If the LLM hallucinates that a 2400 kcal day is 1800 kcal
    and marks passed: True, the Nutrition Agent's code math MUST overrule it
    and enforce passed: False with exact diff_calories!
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
    # Create actual day with 2600 kcal worth of ingredients (far over target)
    plan = MealPlan(
        target_calories=target,
        days=[
            DayPlan(
                day_number=1,
                meals=[
                    Meal(
                        meal_type="lunch",
                        recipe_name="Huge Meal",
                        ingredients=[Ingredient(name="rice", amount_grams=2000.0)] # 130 * 20 = 2600 kcal
                    )
                ]
            )
        ]
    )

    report = await agent.review_plan(
        plan=plan,
        calorie_target=target,
        user_profile={"goal": "maintain", "activity_level": "medium"}
    )

    # GUARANTEED CODE OVERRIDE
    assert report.passed is False
    assert len(report.issues) == 1
    assert report.issues[0].day_number == 1
    assert report.issues[0].diff_calories == 800.0  # 2600 - 1800 = +800 kcal
    assert report.daily_totals[0].calories == 2600.0  # Code total, NOT LLM hallucinated 1800
