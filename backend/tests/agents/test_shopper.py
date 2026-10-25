import pytest
from backend.app.agents.shopper import agent as shopper_module
from backend.app.agents.shopper.agent import ShopperAgent, MissingItemsResponse, MissingItemEstimate
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, GroceryList
from backend.tests.fakes.fake_llm import FakeLLMClient


def _plan():
    return MealPlan(
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


@pytest.fixture
def mandi_knows_only_rice(monkeypatch):
    """Fake price tool: wholesale data exists for rice, nothing for cardamom."""
    async def fake_get_price(name):
        if name == "rice":
            return {"found": True, "price_per_kg": 45.0}
        return {"found": False}
    monkeypatch.setattr(shopper_module, "get_price_tool", fake_get_price)


@pytest.mark.asyncio
async def test_shopper_agent_build_grocery(mandi_knows_only_rice):
    """Quantities and costs come from code; the AI only prices what the market data lacks."""
    fake_llm = FakeLLMClient()
    fake_llm.queue_structured_response(MissingItemsResponse(items=[
        MissingItemEstimate(name="cardamom", price_per_kg=1500.0, category="spices")
    ]))

    agent = ShopperAgent(client=fake_llm, max_steps=4)
    result = await agent.build_grocery_list(_plan())

    assert isinstance(result, GroceryList)
    by_name = {i.name: i for i in result.items}
    assert set(by_name) == {"rice", "cardamom"}

    # Rice: amount from the plan (0.5 kg), price from the market tool.
    assert by_name["rice"].total_grams == 500.0
    assert by_name["rice"].price_source == "mandi_api"
    assert by_name["rice"].estimated_cost == 22.5          # 0.5 kg x 45

    # Cardamom: amount from the plan (10 g), price estimated by the AI and labelled as such.
    assert by_name["cardamom"].total_grams == 10.0
    assert by_name["cardamom"].price_source == "ai_estimate"
    assert by_name["cardamom"].category == "spices"
    assert by_name["cardamom"].estimated_cost == 15.0      # 0.01 kg x 1500

    assert result.total_estimated_cost == 37.5
    assert result.currency == "INR"
    # Only ONE AI call was made: the price estimate. Nothing else is left to the AI.
    assert len(fake_llm.call_history) == 1


@pytest.mark.asyncio
async def test_shopper_uses_labelled_baseline_when_ai_price_estimate_fails(mandi_knows_only_rice):
    """If the AI cannot estimate a price, a built-in baseline is used and still labelled an estimate."""
    class BrokenLLM(FakeLLMClient):
        async def generate_structured(self, *args, **kwargs):
            raise RuntimeError("AI unavailable")

    agent = ShopperAgent(client=BrokenLLM(), max_steps=4)
    result = await agent.build_grocery_list(_plan())

    cardamom = next(i for i in result.items if i.name == "cardamom")
    assert cardamom.price_source == "ai_estimate"
    assert cardamom.price_per_kg == 500.0                  # baseline for spices
    assert cardamom.estimated_cost == 5.0
    assert result.total_estimated_cost == 27.5
