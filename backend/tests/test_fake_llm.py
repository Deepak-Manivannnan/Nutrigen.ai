import asyncio
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient
from backend.tests.fakes.fake_llm import FakeLLMClient

async def test_fake_llm():
    fake = FakeLLMClient()
    mock_plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                total_calories=1800.0,
                meals=[
                    Meal(meal_type='breakfast', recipe_name='Test', calories=450.0, ingredients=[Ingredient(name='oats', amount_grams=80.0)])
                ]
            )
        ]
    )
    fake.queue_structured_response(mock_plan)
    
    from backend.app.schemas.domain import MealPlan as MP
    result = await fake.generate_structured('test prompt', MP)
    print(f'Result: {result}')
    print(f'Days: {len(result.days) if result.days else 0}')
    assert result.days is not None
    assert len(result.days) == 1
    print("Test passed!")

if __name__ == "__main__":
    asyncio.run(test_fake_llm())