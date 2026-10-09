import pytest
from backend.app.agents.nutrition.agent import NutritionAgent
from backend.app.agents.base import AgentStepAction
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, NutritionReport, NutritionIssue
from backend.tests.fakes.fake_llm import FakeLLMClient

@pytest.mark.asyncio
async def test_nutrition_agent_review():
    fake_llm = FakeLLMClient()

    # Step 1: LLM decides to lookup USDA nutrition for apple
    fake_llm.queue_structured_response(AgentStepAction(
        thought="I should lookup nutritional data for apple.",
        action="call_tool",
        tool_name="lookup_nutrition",
        tool_args={"food_name": "apple"}
    ))

    # Step 2: LLM finishes and outputs report
    fake_llm.queue_structured_response(AgentStepAction(
        thought="Nutritional review complete.",
        action="finish",
        finish_summary="Checked calorie targets."
    ))

    expected_report = NutritionReport(
        passed=True,
        issues=[],
        summary="Plan meets 1800 calorie target within ±5% tolerance.",
        daily_totals=[{"day": 1, "calories": 1820.0, "protein_g": 65.0, "carbs_g": 220.0, "fat_g": 55.0}]
    )
    fake_llm.queue_structured_response(expected_report)

    agent = NutritionAgent(client=fake_llm, max_steps=4)
    plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                total_calories=1820.0,
                meals=[
                    Meal(
                        meal_type="breakfast",
                        recipe_name="Fruit & Oatmeal",
                        ingredients=[
                            Ingredient(name="oats", amount_grams=100.0),
                            Ingredient(name="apple", amount_grams=150.0)
                        ]
                    ),
                    Meal(
                        meal_type="lunch",
                        recipe_name="Chicken & Rice",
                        ingredients=[
                            Ingredient(name="chicken breast", amount_grams=300.0),
                            Ingredient(name="white rice", amount_grams=400.0),
                            Ingredient(name="olive oil", amount_grams=15.0)
                        ]
                    ),
                    Meal(
                        meal_type="snack",
                        recipe_name="Peanut Butter Snack",
                        ingredients=[
                            Ingredient(name="peanut butter", amount_grams=35.0)
                        ]
                    )
                ]
            )
        ]
    )

    report = await agent.review_plan(plan=plan, calorie_target=1800.0, user_profile={})
    assert isinstance(report, NutritionReport)
    assert report.passed is True
    assert len(report.daily_totals) == 1
