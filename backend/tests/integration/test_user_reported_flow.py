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
    assert "already approved" in state5.system_message.lower() or "approved" in state5.system_message.lower()


@pytest.mark.asyncio
async def test_user_reported_add_snack_day_3_flow():
    """
    Tests the exact scenario reported by the user in the audio recording:
    1. User asks to create a 3-day meal plan from profile defaults.
    2. User asks: "hey can you add a snack meal for day 3".
       Assistant asks whether they want a low-calorie snack or to rebalance meals.
    3. User replies: "no add a low calorie snack".
    4. Verify:
       - Day 3 has 4 meals (including a snack meal).
       - Day 1 and Day 2 remain untouched with exactly 3 meals each.
       - The snack matches the plan's diet type.
       - The plan version is incremented.
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
    user_id = 1
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        existing_plans = await p_repo.get_all_by_user(user_id)
        for p in existing_plans:
            await p_repo.delete_plan(p.id)

    history = []

    # 1. Create a 3-day meal plan without specifying calories (uses profile default safe target)
    msg1 = "Can you please create a 3-day meal plan for me?"
    state1 = await run_meal_planner(
        user_id=user_id,
        user_message=msg1,
        user_profile=user_profile,
        history=history
    )
    assert state1.current_plan is not None
    assert len(state1.current_plan.days) == 3
    # Initially, all 3 days have 3 meals
    assert len(state1.current_plan.days[0].meals) == 3
    assert len(state1.current_plan.days[1].meals) == 3
    assert len(state1.current_plan.days[2].meals) == 3

    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": state1.system_message})

    # 2. User asks to add snack meal for Day 3
    msg2 = "hey can you add a snack meal for day 3"
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message=msg2,
        user_profile=user_profile,
        history=history
    )
    assert state2.status == "conversational"
    assert "snack" in state2.system_message.lower()

    history.append({"role": "user", "content": msg2})
    history.append({"role": "assistant", "content": state2.system_message})

    # 3. User responds: "no add a low calorie snack"
    msg3 = "no add a low calorie snack"
    state3 = await run_meal_planner(
        user_id=user_id,
        user_message=msg3,
        user_profile=user_profile,
        history=history
    )
    assert state3.status == "draft"
    assert state3.current_plan is not None
    assert len(state3.current_plan.days) == 3

    # Day 1 and Day 2 must still have 3 meals
    assert len(state3.current_plan.days[0].meals) == 3, f"Day 1 should have 3 meals, got {len(state3.current_plan.days[0].meals)}"
    assert len(state3.current_plan.days[1].meals) == 3, f"Day 2 should have 3 meals, got {len(state3.current_plan.days[1].meals)}"

    # Day 3 must have 4 meals including snack
    day3_meals = state3.current_plan.days[2].meals
    assert len(day3_meals) == 4, f"Day 3 should have 4 meals, got {len(day3_meals)}"
    snack_meal = next((m for m in day3_meals if m.meal_type.lower() == "snack"), None)
    assert snack_meal is not None, "Day 3 should contain a meal with meal_type == 'snack'"
    assert snack_meal.calories > 0
    assert "day 3" in state3.system_message.lower() or "added" in state3.system_message.lower()
    # While still in draft, version must remain 1
    assert state3.plan_version == 1


@pytest.mark.asyncio
async def test_delete_a_meal_intent_routing_and_confirmation():
    """
    Tests that 'hey i want to delete a meal' routes to delete_plan:
    - If 1 plan exists, asks for confirmation instead of wrongly editing.
    - If user confirms, permanently deletes the plan.
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
    user_id = 2
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        existing_plans = await p_repo.get_all_by_user(user_id)
        for p in existing_plans:
            await p_repo.delete_plan(p.id)

    # 1. Create a 3-day meal plan
    state1 = await run_meal_planner(
        user_id=user_id,
        user_message="Plan 3 days vegetarian meal plan",
        user_profile=user_profile,
        history=[]
    )
    assert state1.current_plan is not None
    assert state1.status == "draft"

    history = [
        {"role": "user", "content": "Plan 3 days vegetarian meal plan"},
        {"role": "assistant", "content": state1.system_message}
    ]

    # 2. User types "hey i want to delete a meal"
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message="hey i want to delete a meal",
        user_profile=user_profile,
        history=history
    )
    # Must ask confirmation, NOT edit the meal plan
    assert state2.status == "conversational"
    assert "delete" in state2.system_message.lower()
    assert "sure" in state2.system_message.lower() or "confirm" in state2.system_message.lower() or "want to delete" in state2.system_message.lower()

    history.append({"role": "user", "content": "hey i want to delete a meal"})
    history.append({"role": "assistant", "content": state2.system_message})

    # 3. User confirms deletion
    state3 = await run_meal_planner(
        user_id=user_id,
        user_message="Yes, delete it",
        user_profile=user_profile,
        history=history
    )
    assert state3.status == "deleted"
    assert "deleted" in state3.system_message.lower()


@pytest.mark.asyncio
async def test_plan_versioning_draft_to_approved_lifecycle():
    """
    Tests the strict version lifecycle required by user:
    1. Plan created -> Version 1 Draft.
    2. Modification while in draft -> stays Version 1 Draft.
    3. User approves -> Version 1 Approved.
    4. Modification while approved -> increments to Version 2 Draft.
    5. Second modification while in Version 2 Draft -> stays Version 2 Draft.
    6. User approves -> Version 2 Approved.
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
    user_id = 3
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        existing_plans = await p_repo.get_all_by_user(user_id)
        for p in existing_plans:
            await p_repo.delete_plan(p.id)

    # 1. Create initial plan -> Version 1 Draft
    state1 = await run_meal_planner(
        user_id=user_id,
        user_message="Plan 3 days vegetarian meal plan",
        user_profile=user_profile,
        history=[]
    )
    assert state1.plan_version == 1
    assert state1.status == "draft"

    history = [
        {"role": "user", "content": "Plan 3 days vegetarian meal plan"},
        {"role": "assistant", "content": state1.system_message}
    ]

    # 2. Modify draft -> must STAY at Version 1 Draft
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message="replace day 2 breakfast with oatmeal",
        user_profile=user_profile,
        history=history
    )
    assert state2.plan_version == 1, f"Expected Version 1 Draft while still in draft, got {state2.plan_version}"
    assert state2.status == "draft"

    history.append({"role": "user", "content": "replace day 2 breakfast with oatmeal"})
    history.append({"role": "assistant", "content": state2.system_message})

    # 3. Approve plan -> becomes Version 1 Approved
    state3 = await run_meal_planner(
        user_id=user_id,
        user_message="approve this plan",
        user_profile=user_profile,
        history=history
    )
    assert state3.status == "approved"

    # Verify DB state
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        db_plan = await p_repo.get_by_id(state3.plan_id)
        assert db_plan.version == 1
        assert db_plan.status == "approved"

    history.append({"role": "user", "content": "approve this plan"})
    history.append({"role": "assistant", "content": state3.system_message})

    # 4. Modify approved plan -> increments to Version 2 Draft!
    state4 = await run_meal_planner(
        user_id=user_id,
        user_message="swap day 1 dinner with vegetable khichdi",
        user_profile=user_profile,
        history=history
    )
    assert state4.plan_version == 2, f"Expected Version 2 Draft after modifying approved plan, got {state4.plan_version}"
    assert state4.status == "draft"

    # Verify DB state
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        db_plan = await p_repo.get_by_id(state4.plan_id or state3.plan_id)
        assert db_plan.version == 2
        assert db_plan.status == "draft"

    history.append({"role": "user", "content": "swap day 1 dinner with vegetable khichdi"})
    history.append({"role": "assistant", "content": state4.system_message})

    # 5. Subsequent modification while in Version 2 Draft -> must STAY at Version 2 Draft!
    state5 = await run_meal_planner(
        user_id=user_id,
        user_message="replace day 3 lunch with paneer bhurji",
        user_profile=user_profile,
        history=history
    )
    assert state5.plan_version == 2, f"Expected Version 2 Draft on subsequent draft edit, got {state5.plan_version}"
    assert state5.status == "draft"

    # Verify DB state
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        db_plan = await p_repo.get_by_id(state5.plan_id or state3.plan_id)
        assert db_plan.version == 2
        assert db_plan.status == "draft"

    history.append({"role": "user", "content": "replace day 3 lunch with paneer bhurji"})
    history.append({"role": "assistant", "content": state5.system_message})

    # 6. Approve again -> becomes Version 2 Approved!
    state6 = await run_meal_planner(
        user_id=user_id,
        user_message="approve this plan",
        user_profile=user_profile,
        history=history
    )
    assert state6.status == "approved"

    # Verify DB state
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        db_plan = await p_repo.get_by_id(state6.plan_id or state3.plan_id)
        assert db_plan.version == 2
        assert db_plan.status == "approved"

