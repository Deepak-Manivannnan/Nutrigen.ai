import pytest
from backend.app.orchestration.runner import run_meal_planner
from backend.app.db.session import init_db, AsyncSessionLocal
from backend.app.db.repositories.plans import PlanRepository
from backend.app.tools.diet_rules import check_diet_compliance

@pytest.mark.asyncio
async def test_user_reported_multi_turn_and_approval_flow():
    await init_db()
    
    # User Deepak with non-veg default profile
    user_profile = {
        "name": "Deepak",
        "age": 28,
        "sex": "male",
        "height_cm": 175,
        "weight_kg": 72,
        "activity_level": "medium",
        "goal": "maintain",
        "diet_type": "non-veg",
        "allergies": [],
        "dislikes": []
    }
    user_id = 1
    
    history = []
    
    # -------------------------------------------------------------------------
    # Turn 1: User requests 3-day non-veg plan with 1900 kcal
    # -------------------------------------------------------------------------
    msg1 = "okay now i want you to create a non veg meal plan with 1900 kcal/day and make it as a 3 day plan"
    state1 = await run_meal_planner(
        user_id=user_id,
        user_message=msg1,
        user_profile=user_profile,
        history=history
    )
    assert state1.status == "conversational"
    assert "safe range" in state1.system_message.lower() or "recommended target" in state1.system_message.lower()
    
    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": state1.system_message})
    
    # -------------------------------------------------------------------------
    # Turn 2: User says "okay and can u make it as a veg meal"
    # -------------------------------------------------------------------------
    msg2 = "okay and can u make it as a veg meal"
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message=msg2,
        user_profile=user_profile,
        history=history
    )
    # Must ask Diet Preference Conflict question cleanly without filler brochure speech
    assert state2.status == "conversational"
    assert "profile preference is" in state2.system_message.lower()
    assert "vegetarian" in state2.system_message.lower()
    assert "beyond crafting" not in state2.system_message.lower()
    
    history.append({"role": "user", "content": msg2})
    history.append({"role": "assistant", "content": state2.system_message})
    
    # -------------------------------------------------------------------------
    # Turn 3: User confirms "no i want veg meal plan"
    # -------------------------------------------------------------------------
    msg3 = "no i want veg meal plan"
    state3 = await run_meal_planner(
        user_id=user_id,
        user_message=msg3,
        user_profile=user_profile,
        history=history
    )
    # Plan must be drafted
    assert state3.current_plan is not None
    assert len(state3.current_plan.days) == 3
    assert state3.current_plan.diet_type == "veg"
    assert state3.status in ("draft", "drafting", "reviewing")
    
    # Check that ALL meals in all 3 days are 100% vegetarian (NO chicken, beef, salmon, turkey, eggs)
    all_violations = []
    for day in state3.current_plan.days:
        for meal in day.meals:
            ing_names = [ing.name for ing in meal.ingredients]
            names_to_check = ing_names + [meal.recipe_name]
            viols = check_diet_compliance("veg", names_to_check)
            if viols:
                all_violations.extend(viols)
    
    assert len(all_violations) == 0, f"Found non-vegetarian items in veg plan: {all_violations}"
    
    # -------------------------------------------------------------------------
    # Turn 4: User says "approve the plan" in chat
    # -------------------------------------------------------------------------
    msg4 = "approve the plan"
    state4 = await run_meal_planner(
        user_id=user_id,
        user_message=msg4,
        user_profile=user_profile,
        history=history
    )
    assert state4.status == "approved"
    assert state4.plan_id is not None
    assert "approved" in state4.system_message.lower()
    
    # Turn 5: User says "approve the plan" AGAIN when it is already approved
    state5 = await run_meal_planner(
        user_id=user_id,
        user_message=msg4,
        user_profile=user_profile,
        history=history
    )
    assert state5.status == "approved"
    assert "already approved" in state5.system_message.lower()
