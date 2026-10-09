import pytest
from backend.app.agents.shopper.agent import ShopperAgent
from backend.app.agents.base import AgentStepAction
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, GroceryList, GroceryItem
from backend.tests.fakes.fake_llm import FakeLLMClient

@pytest.mark.asyncio
async def test_shopper_agent_build_grocery():
    fake_llm = FakeLLMClient()

    # Step 1: LLM decides to merge ingredients
    fake_llm.queue_structured_response(AgentStepAction(
        thought="Merging ingredients across all days.",
        action="call_tool",
        tool_name="merge_ingredients",
        tool_args={"meals": []}
    ))

    # Step 2: LLM finishes and outputs grocery list
    fake_llm.queue_structured_response(AgentStepAction(
        thought="All commodity prices resolved.",
        action="finish",
        finish_summary="Compiled grocery list."
    ))

    fake_grocery = GroceryList(
        items=[
            GroceryItem(
                name="rice",
                category="grains",
                total_grams=1000.0,
                display_unit="kg",
                price_per_kg=45.0,
                estimated_cost=45.0,
                price_source="mandi_api"
            ),
            GroceryItem(
                name="cardamom",
                category="spices",
                total_grams=20.0,
                display_unit="g",
                price_per_kg=1500.0,
                estimated_cost=30.0,
                price_source="ai_estimate"
            )
        ],
        total_estimated_cost=75.0,
        currency="INR"
    )
    fake_llm.queue_structured_response(fake_grocery)

    agent = ShopperAgent(client=fake_llm, max_steps=4)
    plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                meals=[
                    Meal(
                        meal_type="lunch",
                        recipe_name="Rice dish",
                        ingredients=[
                            Ingredient(name="rice", amount_grams=500.0),
                            Ingredient(name="cardamom", amount_grams=10.0)
                        ]
                    )
                ]
            )
        ]
    )

    result = await agent.build_grocery_list(plan)
    assert isinstance(result, GroceryList)
    assert len(result.items) == 2
    assert result.total_estimated_cost == 75.0
    assert result.items[0].price_source == "mandi_api"
    assert result.items[1].price_source == "ai_estimate"
