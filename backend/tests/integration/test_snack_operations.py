import pytest
from backend.app.orchestration.runner import run_meal_planner
from backend.app.db.session import init_db, AsyncSessionLocal
from backend.app.db.repositories.plans import PlanRepository
from backend.app.tools.diet_rules import check_diet_compliance
from backend.tests.conftest import throwaway_user


@pytest.mark.asyncio
async def test_create_meal_plan_with_snacks_from_scratch():
    """
    Verifies that when a user explicitly requests snacks when creating a meal plan:
    - The plan is created with 4 meals per day (breakfast, lunch, dinner, snack).
    - Diet compliance is strictly observed for all meals including snacks.
    """
    await init_db()
    user_profile = {
        "name": "Deepak",
        "age": 28,
        "sex": "male",
        "height_cm": 175,
        "weight_kg": 72,
        "activity_level": "medium",
        "goal": "maintain",
        "diet_type": "veg",
        "allergies": [],
        "dislikes": []
    }
    user_id = await throwaway_user(user_profile)

    msg = "Please create a 3-day vegetarian meal plan with snack meal also for 2600 kcal"
    state = await run_meal_planner(
        user_id=user_id,
        user_message=msg,
        user_profile=user_profile,
        history=[]
    )

    assert state.current_plan is not None, f"Expected current_plan, got status={state.status}, msg={state.system_message}"
    assert len(state.current_plan.days) == 3

    for day in state.current_plan.days:
        meal_types = [m.meal_type.lower() for m in day.meals]
        assert "snack" in meal_types, f"Day {day.day_number} should have a snack meal, got {meal_types}"
        assert len(day.meals) == 4, f"Day {day.day_number} should have 4 meals, got {len(day.meals)}"
        
        # Verify vegetarian diet compliance for each meal
        for m in day.meals:
            ing_names = [i.name for i in m.ingredients] + [m.recipe_name]
            violations = check_diet_compliance("veg", ing_names)
            assert not violations, f"Day {day.day_number} meal '{m.recipe_name}' has veg violations: {violations}"


@pytest.mark.asyncio
async def test_add_snack_to_all_days_and_remove():
    """
    Verifies that:
    1. 'Add a snack meal for all days' adds a snack to all days without asking 'which day'.
    2. 'Remove the snack meal for all days' removes snacks from all days.
    """
    await init_db()
    user_profile = {
        "name": "Deepak",
        "age": 28,
        "sex": "male",
        "height_cm": 175,
        "weight_kg": 72,
        "activity_level": "medium",
        "goal": "maintain",
        "diet_type": "veg",
        "allergies": [],
        "dislikes": []
    }
    user_id = await throwaway_user(user_profile)

    history = []

    # 1. Create a 3-day standard plan (3 meals per day)
    msg1 = "Create a 3-day vegetarian meal plan for 2600 kcal"
    state1 = await run_meal_planner(
        user_id=user_id,
        user_message=msg1,
        user_profile=user_profile,
        history=history
    )
    assert state1.current_plan is not None
    assert len(state1.current_plan.days) == 3
    for d in state1.current_plan.days:
        assert len(d.meals) == 3

    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": state1.system_message})

    # 2. Add snack to all days
    msg2 = "Add a snack meal for all days"
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message=msg2,
        user_profile=user_profile,
        history=history
    )
    assert state2.current_plan is not None, f"Expected plan update, got status={state2.status}, msg={state2.system_message}"
    for d in state2.current_plan.days:
        assert len(d.meals) == 4, f"Day {d.day_number} should have 4 meals after adding snack, got {len(d.meals)}"
        snack = next((m for m in d.meals if m.meal_type.lower() == "snack"), None)
        assert snack is not None, f"Day {d.day_number} missing snack"

    history.append({"role": "user", "content": msg2})
    history.append({"role": "assistant", "content": state2.system_message})

    # 3. Remove snack from all days
    msg3 = "Remove the snack meal from all days"
    state3 = await run_meal_planner(
        user_id=user_id,
        user_message=msg3,
        user_profile=user_profile,
        history=history
    )
    assert state3.current_plan is not None
    for d in state3.current_plan.days:
        snack = next((m for m in d.meals if m.meal_type.lower() == "snack"), None)
        assert snack is None, f"Day {d.day_number} should NOT have snack after removal"
        assert len(d.meals) == 3, f"Day {d.day_number} should have 3 meals after snack removal, got {len(d.meals)}"
