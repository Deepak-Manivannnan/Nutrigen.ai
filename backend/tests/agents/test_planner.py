import pytest
from backend.app.agents.planner.agent import PlannerAgent
from backend.app.agents.base import AgentStepAction
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient
from backend.tests.fakes.fake_llm import FakeLLMClient

@pytest.mark.asyncio
async def test_planner_agent_run():
    fake_llm = FakeLLMClient()
    
    # 1. Step 1: LLM decides to search recipes
    fake_llm.queue_structured_response(AgentStepAction(
        thought="I should search for healthy chicken recipes to include for lunch.",
        action="call_tool",
        tool_name="search_recipes",
        tool_args={"query": "chicken"}
    ))

    # 2. Step 2: LLM observes recipe results and finishes
    fake_llm.queue_structured_response(AgentStepAction(
        thought="I have gathered sufficient recipe information. Ready to finalize the plan.",
        action="finish",
        finish_summary="Selected 3 balanced meals for Day 1."
    ))

    # 3. Finalization: Returns structured MealPlan
    expected_plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                total_calories=1780.0,
                meals=[
                    Meal(
                        meal_type="breakfast",
                        recipe_name="Oatmeal with fruit",
                        source="ai_generated",
                        calories=450.0,
                        ingredients=[Ingredient(name="rolled oats", amount_grams=80.0)]
                    ),
                    Meal(
                        meal_type="lunch",
                        recipe_name="Chicken Rice Bowl",
                        source="api",
                        calories=750.0,
                        ingredients=[Ingredient(name="chicken breast", amount_grams=180.0)]
                    ),
                    Meal(
                        meal_type="dinner",
                        recipe_name="Steamed Veggies & Paneer",
                        source="ai_generated",
                        calories=580.0,
                        ingredients=[Ingredient(name="paneer", amount_grams=120.0)]
                    )
                ]
            )
        ]
    )
    fake_llm.queue_structured_response(expected_plan)

    agent = PlannerAgent(client=fake_llm, max_steps=4)
    profile = {
        "name": "Alex",
        "diet_type": "non-veg",
        "dislikes": ["fish"],
        "allergies": []
    }

    result = await agent.generate_plan(days=1, calorie_target=1800.0, user_profile=profile)
    assert isinstance(result, MealPlan)
    assert len(result.days) == 1
    assert result.days[0].day_number == 1
    assert len(result.days[0].meals) == 3
    assert result.days[0].meals[1].source == "api"
    assert result.target_calories == 1800.0
