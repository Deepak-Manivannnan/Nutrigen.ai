import pytest
from backend.app.schemas.state import PlanState
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, NutritionReport, AllergyReport, GroceryList, GroceryItem
from backend.app.agents.planner.agent import PlannerAgent
from backend.app.agents.nutrition.agent import NutritionAgent
from backend.app.agents.allergy.agent import AllergyAgent
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.orchestration.graph import create_meal_planner_graph
from backend.tests.fakes.fake_llm import FakeLLMClient
from backend.app.db.session import init_db

@pytest.mark.asyncio
async def test_full_graph_happy_path():
    await init_db()

    fake_p_llm = FakeLLMClient()
    fake_n_llm = FakeLLMClient()
    fake_a_llm = FakeLLMClient()
    fake_s_llm = FakeLLMClient()

    # Pre-configure fake outputs
    mock_plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                total_calories=1800.0,
                meals=[
                    Meal(
                        meal_type="breakfast",
                        recipe_name="Fruit & Oats",
                        calories=450.0,
                        ingredients=[
                            Ingredient(name="rolled oats", amount_grams=80.0),
                            Ingredient(name="whole milk", amount_grams=150.0),
                            Ingredient(name="apple", amount_grams=90.0)
                        ]
                    ),
                    Meal(
                        meal_type="lunch",
                        recipe_name="Rice & Lentils",
                        calories=750.0,
                        ingredients=[
                            Ingredient(name="rice", amount_grams=250.0),
                            Ingredient(name="lentils", amount_grams=250.0),
                            Ingredient(name="ghee", amount_grams=15.0)
                        ]
                    ),
                    Meal(
                        meal_type="dinner",
                        recipe_name="Steamed Veggies & Chicken",
                        calories=600.0,
                        ingredients=[
                            Ingredient(name="chicken breast", amount_grams=250.0),
                            Ingredient(name="broccoli", amount_grams=200.0),
                            Ingredient(name="olive oil", amount_grams=14.0)
                        ]
                    )
                ]
            )
        ]
    )
    fake_p_llm.queue_structured_response(mock_plan)

    mock_nutrition = NutritionReport(
        passed=True,
        issues=[],
        summary="Plan is nutritionally balanced."
    )
    fake_n_llm.queue_structured_response(mock_nutrition)

    mock_allergy = AllergyReport(
        passed=True,
        violations=[],
        summary="Plan contains no allergens."
    )
    fake_a_llm.queue_structured_response(mock_allergy)

    planner = PlannerAgent(client=fake_p_llm, max_steps=1)
    nutrition = NutritionAgent(client=fake_n_llm, max_steps=1)
    allergy = AllergyAgent(client=fake_a_llm, max_steps=1)
    supervisor = SupervisorAgent()

    graph = create_meal_planner_graph(
        planner=planner,
        nutrition=nutrition,
        allergy=allergy,
        supervisor=supervisor
    )

    initial_state = PlanState(
        task_id="test-task-1",
        user_id=1,
        user_profile={
            "name": "Jordan",
            "age": 28,
            "sex": "male",
            "height_cm": 175.0,
            "weight_kg": 70.0,
            "activity_level": "medium",
            "goal": "maintain",
            "diet_type": "veg",
            "allergies": []
        },
        days=1,
        calorie_target=1800.0
    )

    result = await graph.ainvoke(initial_state)
    final_state = PlanState.model_validate(result)

    assert final_state.status == "reviewing"
    assert final_state.current_plan is not None
    # Drafting never builds a grocery list; that happens only on approval.
    assert final_state.grocery_list is None
    assert len(final_state.trace) >= 4

    # Check architectural constraint: all messages must be to or from supervisor
    for msg in final_state.trace:
        assert msg.from_agent == "supervisor" or msg.to_agent == "supervisor"

@pytest.mark.asyncio
async def test_graph_input_guardrail_block():
    graph = create_meal_planner_graph()

    # Dangerous low calorie request (500 kcal)
    state = PlanState(
        task_id="test-task-fail",
        user_id=2,
        user_profile={
            "name": "Sam",
            "age": 25,
            "sex": "female",
            "height_cm": 165.0,
            "weight_kg": 60.0,
            "activity_level": "low",
            "diet_type": "veg",
            "allergies": []
        },
        days=3,
        calorie_target=500.0
    )

    result = await graph.ainvoke(state)
    final_state = PlanState.model_validate(result)

    assert final_state.status == "failed"
    assert "clinically safe daily range" in final_state.system_message
    assert final_state.current_plan is None

@pytest.mark.asyncio
async def test_graph_allergy_veto_replanning_loop():
    await init_db()

    fake_p_llm = FakeLLMClient()
    fake_n_llm = FakeLLMClient()
    fake_a_llm = FakeLLMClient()
    fake_s_llm = FakeLLMClient()

    # Plan with peanut butter (1825 kcal, fails allergy due to peanuts)
    bad_plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                total_calories=1825.0,
                meals=[
                    Meal(
                        meal_type="breakfast",
                        recipe_name="Peanut butter toast",
                        ingredients=[
                            Ingredient(name="peanut butter", amount_grams=35.0),
                            Ingredient(name="whole wheat bread", amount_grams=150.0)
                        ]
                    ),
                    Meal(
                        meal_type="lunch",
                        recipe_name="Chicken & Rice",
                        ingredients=[
                            Ingredient(name="white rice", amount_grams=300.0),
                            Ingredient(name="chicken breast", amount_grams=200.0)
                        ]
                    ),
                    Meal(
                        meal_type="dinner",
                        recipe_name="Potatoes & Olive Oil",
                        ingredients=[
                            Ingredient(name="potato", amount_grams=400.0),
                            Ingredient(name="olive oil", amount_grams=25.0)
                        ]
                    )
                ]
            )
        ]
    )
    # Fixed plan with oats (1821 kcal, passes allergy and passes nutrition)
    good_plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                total_calories=1821.0,
                meals=[
                    Meal(
                        meal_type="breakfast",
                        recipe_name="Oatmeal & Milk",
                        ingredients=[
                            Ingredient(name="rolled oats", amount_grams=100.0),
                            Ingredient(name="whole milk", amount_grams=300.0)
                        ]
                    ),
                    Meal(
                        meal_type="lunch",
                        recipe_name="Chicken & Rice",
                        ingredients=[
                            Ingredient(name="white rice", amount_grams=300.0),
                            Ingredient(name="chicken breast", amount_grams=200.0)
                        ]
                    ),
                    Meal(
                        meal_type="dinner",
                        recipe_name="Potatoes & Olive Oil",
                        ingredients=[
                            Ingredient(name="potato", amount_grams=400.0),
                            Ingredient(name="olive oil", amount_grams=25.0)
                        ]
                    )
                ]
            )
        ]
    )
    # Queue bad plan for round 1, good plan for round 2
    fake_p_llm.queue_structured_response(bad_plan)
    fake_p_llm.queue_structured_response(good_plan)

    # Nutrition passes on both
    fake_n_llm.queue_structured_response(NutritionReport(passed=True, issues=[]))
    fake_n_llm.queue_structured_response(NutritionReport(passed=True, issues=[]))

    # Allergy vetoes on round 1, passes on round 2
    from backend.app.schemas.domain import AllergyViolation
    fake_a_llm.queue_structured_response(AllergyReport(
        passed=False,
        violations=[AllergyViolation(day_number=1, meal_type="breakfast", ingredient="peanut butter", allergen="peanuts", reason="Peanut allergy hazard")],
        summary="Peanut veto"
    ))
    fake_a_llm.queue_structured_response(AllergyReport(passed=True, violations=[]))

    planner = PlannerAgent(client=fake_p_llm, max_steps=1)
    nutrition = NutritionAgent(client=fake_n_llm, max_steps=1)
    allergy = AllergyAgent(client=fake_a_llm, max_steps=1)
    supervisor = SupervisorAgent()

    graph = create_meal_planner_graph(
        planner=planner,
        nutrition=nutrition,
        allergy=allergy,
        supervisor=supervisor
    )

    state = PlanState(
        task_id="test-replanning-task",
        user_id=3,
        user_profile={
            "name": "Robin",
            "age": 30,
            "sex": "female",
            "height_cm": 165.0,
            "weight_kg": 60.0,
            "activity_level": "medium",
            "diet_type": "veg",
            "allergies": ["peanuts"]
        },
        days=1,
        calorie_target=1800.0
    )

    result = await graph.ainvoke(state)
    final_state = PlanState.model_validate(result)

    assert final_state.status == "reviewing"
    assert final_state.round == 2
    assert final_state.plan_version == 2
    # Drafting never builds a grocery list; that happens only on approval.
    assert final_state.grocery_list is None

