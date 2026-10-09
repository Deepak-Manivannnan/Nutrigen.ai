import pytest
from backend.app.agents.allergy.agent import AllergyAgent
from backend.app.agents.base import AgentStepAction
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, AllergyReport, AllergyViolation
from backend.tests.fakes.fake_llm import FakeLLMClient
from backend.app.db.session import init_db
from backend.app.db.seed.seed import seed_allergen_sources

@pytest.mark.asyncio
async def test_allergy_agent_safe():
    agent = AllergyAgent()
    plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                meals=[
                    Meal(meal_type="breakfast", recipe_name="Fruit", ingredients=[Ingredient(name="apple", amount_grams=100.0)])
                ]
            )
        ]
    )

    # Empty user allergies should automatically pass
    report = await agent.review_plan(plan, user_allergies=[])
    assert report.passed is True
    assert len(report.violations) == 0

@pytest.mark.asyncio
async def test_allergy_agent_hard_veto():
    await init_db()
    await seed_allergen_sources()

    fake_llm = FakeLLMClient()
    # Step 1: LLM decides to check allergens
    fake_llm.queue_structured_response(AgentStepAction(
        thought="Checking meal ingredients against user peanut allergy.",
        action="call_tool",
        tool_name="check_allergens",
        tool_args={"user_allergies": ["peanuts"], "ingredients": ["peanut butter"]}
    ))

    # Step 2: LLM finishes and outputs report with violation
    fake_llm.queue_structured_response(AgentStepAction(
        thought="Found peanut butter. Enforcing hard veto.",
        action="finish",
        finish_summary="Vetoed meal due to peanut allergy."
    ))

    fake_report = AllergyReport(
        passed=False,
        violations=[
            AllergyViolation(
                day_number=1,
                meal_type="breakfast",
                ingredient="peanut butter",
                allergen="peanuts",
                reason="Contains peanuts"
            )
        ],
        summary="Hard veto enforced for peanuts."
    )
    fake_llm.queue_structured_response(fake_report)

    agent = AllergyAgent(client=fake_llm, max_steps=4)
    plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                meals=[
                    Meal(
                        meal_type="breakfast",
                        recipe_name="Toast with peanut butter",
                        ingredients=[Ingredient(name="peanut butter", amount_grams=30.0)]
                    )
                ]
            )
        ]
    )

    report = await agent.review_plan(plan, user_allergies=["peanuts"])
    assert report.passed is False
    assert len(report.violations) >= 1
    assert any(v.allergen == "peanuts" for v in report.violations)
