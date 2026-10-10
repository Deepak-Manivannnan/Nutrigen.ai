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


@pytest.mark.asyncio
async def test_user_reported_combined_conflict_immediate_generation():
    """
    Tests Issue 1:
    User asks for 5-day veg plan at 1800 kcal (conflicting with non-veg profile and 2150-2350 maintenance).
    Assistant asks combined diet + calorie conflict question offering recommended 2250 kcal.
    User accepts: 'oops i forgot about my calorie range thanks for remining and u can create a veg plan with my advised calorie range'
    Assistant MUST immediately generate the 5-day vegetarian plan at 2250 kcal without re-prompting.
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
        "diet_type": "non-veg",
        "allergies": [],
        "dislikes": []
    }
    user_id = 4
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        existing_plans = await p_repo.get_all_by_user(user_id)
        for p in existing_plans:
            await p_repo.delete_plan(p.id)

    history = []

    # Turn 1: User requests 5-day veg plan with 1800 kcal/day
    msg1 = "okay can u create a veg meal plan for 5 days with 1800kcal/day"
    state1 = await run_meal_planner(
        user_id=user_id,
        user_message=msg1,
        user_profile=user_profile,
        history=history
    )
    assert state1.status == "conversational"
    assert "vegetarian" in state1.system_message.lower()
    assert "1,800" in state1.system_message or "1800" in state1.system_message
    assert "recommended target of" in state1.system_message.lower()

    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": state1.system_message})

    # Turn 2: User confirms advised calorie range and veg plan
    msg2 = "oops i forgot about my calorie range thanks for remining and u can create a veg plan with my advised calorie range"
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message=msg2,
        user_profile=user_profile,
        history=history
    )
    # Must immediately draft the plan, NOT ask another question!
    assert state2.status in ("draft", "drafting", "reviewing"), f"Expected drafted plan, got {state2.status}: {state2.system_message}"
    assert state2.current_plan is not None
    assert len(state2.current_plan.days) == 5
    assert state2.current_plan.diet_type == "veg"
    assert state2.current_plan.target_calories >= 2000


@pytest.mark.asyncio
async def test_user_reported_update_plan_selection_flow():
    """
    Tests Issue 2:
    User has multiple plans (5-Day Veg Plan and 3-Day Non-veg Plan).
    User says 'hey i want to update a meal plan' -> Assistant asks which one.
    User answers 'the 5 day meal plan'.
    Assistant MUST ask what they want to update in '5-Day Veg Plan', and NEVER trigger LangGraph/create a new plan.
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
        "diet_type": "non-veg",
        "allergies": [],
        "dislikes": []
    }
    user_id = 5
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        existing_plans = await p_repo.get_all_by_user(user_id)
        for p in existing_plans:
            await p_repo.delete_plan(p.id)

    # Pre-create two plans in database for user
    from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient
    dummy_meals = [
        Meal(meal_type="breakfast", recipe_name="Poha", calories=400, protein_g=10, carbs_g=60, fat_g=10, ingredients=[Ingredient(name="Poha", amount_grams=100, unit="g")]),
        Meal(meal_type="lunch", recipe_name="Dal Rice", calories=700, protein_g=25, carbs_g=90, fat_g=15, ingredients=[Ingredient(name="Rice", amount_grams=150, unit="g")]),
        Meal(meal_type="dinner", recipe_name="Roti Dal", calories=500, protein_g=20, carbs_g=70, fat_g=12, ingredients=[Ingredient(name="Roti", amount_grams=100, unit="g")]),
    ]
    plan_5d = MealPlan(
        name="5-Day Veg Plan",
        diet_type="veg",
        target_calories=2250,
        days=[DayPlan(day_number=i, meals=dummy_meals, total_calories=1600, total_protein_g=55, total_carbs_g=220, total_fat_g=37) for i in range(1, 6)]
    )
    plan_3d = MealPlan(
        name="3-Day Non-veg Plan",
        diet_type="non-veg",
        target_calories=2250,
        days=[DayPlan(day_number=i, meals=dummy_meals, total_calories=1600, total_protein_g=55, total_carbs_g=220, total_fat_g=37) for i in range(1, 4)]
    )

    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        created1 = await p_repo.create_plan(user_id=user_id, days=5, calorie_target=2250, meal_plan=plan_5d, status="draft", name="5-Day Veg Plan", diet_type="veg")
        created2 = await p_repo.create_plan(user_id=user_id, days=3, calorie_target=2250, meal_plan=plan_3d, status="approved", name="3-Day Non-veg Plan", diet_type="non-veg")

    history = []

    # Turn 1: User says 'hey i want to update a meal plan'
    msg1 = "hey i want to update a meal plan"
    state1 = await run_meal_planner(
        user_id=user_id,
        user_message=msg1,
        user_profile=user_profile,
        history=history
    )
    assert state1.status == "conversational"
    assert "which" in state1.system_message.lower()

    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": state1.system_message})

    # Turn 2: User responds 'the 5 day meal plan'
    msg2 = "the 5 day meal plan"
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message=msg2,
        user_profile=user_profile,
        history=history
    )
    # Must ask what they would like to update in the 5-day plan
    assert state2.status == "conversational"
    assert "5-day veg plan" in state2.system_message.lower() or "5-day" in state2.system_message.lower()
    assert "what" in state2.system_message.lower() or "like to update" in state2.system_message.lower() or "change" in state2.system_message.lower()

    # Verify no new plan was inserted into the database!
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        plans_after = await p_repo.get_all_by_user(user_id)
        assert len(plans_after) == 2, f"Expected 2 plans, but found {len(plans_after)} plans in database!"


@pytest.mark.asyncio
async def test_user_reported_profile_info_conversational_response():
    """
    Tests Issue 1:
    User asks 'okay can u please tell me about my profile'.
    Supervisor retrieves profile details & calorie calculations and generates
    conversational response through the LLM.
    """
    await init_db()
    user_profile = {
        "name": "Deepak",
        "age": 24,
        "sex": "male",
        "height_cm": 165.0,
        "weight_kg": 72.0,
        "activity_level": "low",
        "goal": "maintain",
        "diet_type": "non-veg",
        "allergies": [],
        "dislikes": []
    }
    user_id = 6

    state = await run_meal_planner(
        user_id=user_id,
        user_message="okay can u please tell me about my profile",
        user_profile=user_profile,
        history=[]
    )
    assert state.status == "conversational"
    assert any(k in state.system_message.lower() for k in ["165", "72", "maintain", "24", "non-vegetarian", "non veg", "calorie"])


@pytest.mark.asyncio
async def test_user_reported_non_veg_spaced_with_exclusions_and_calorie_confirm():
    """
    Tests Issue 2:
    User asks 'okay can u create another plan with 1900 kcal/day non veg meal plan for 5 days and exclude pork, beef, turkey'.
    'non veg' (spaced) must be detected as non-veg, and exclusions pork, beef, turkey captured.
    Assistant asks calorie confirmation. User replies 'okay'.
    Resulting meal plan must be 5 days, non-veg, excluding pork, beef, and turkey.
    """
    await init_db()
    user_profile = {
        "name": "Deepak",
        "age": 24,
        "sex": "male",
        "height_cm": 165.0,
        "weight_kg": 72.0,
        "activity_level": "low",
        "goal": "maintain",
        "diet_type": "non-veg",
        "allergies": [],
        "dislikes": []
    }
    user_id = 7
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        existing = await p_repo.get_all_by_user(user_id)
        for p in existing:
            await p_repo.delete_plan(p.id)

    history = []
    msg1 = "okay can u create another plan with 1900 kcal/day non veg meal plan for 5 days and exclude pork, beef, turkey"
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

    # Turn 2: User says "okay"
    msg2 = "okay"
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message=msg2,
        user_profile=user_profile,
        history=history
    )
    assert state2.status in ("draft", "drafting", "reviewing")
    assert state2.current_plan is not None
    assert len(state2.current_plan.days) == 5
    assert state2.current_plan.diet_type == "non-veg"
    assert "veg" not in state2.current_plan.name.lower() or "non-veg" in state2.current_plan.name.lower()

    # Verify exclusions: no meal or ingredient contains pork, beef, or turkey
    for d in state2.current_plan.days:
        for m in d.meals:
            dish = m.recipe_name.lower()
            assert "pork" not in dish, f"Found pork in {dish}"
            assert "beef" not in dish, f"Found beef in {dish}"
            assert "turkey" not in dish, f"Found turkey in {dish}"
            for ing in (m.ingredients or []):
                i_name = ing.name.lower()
                assert "pork" not in i_name, f"Found pork in ingredient {i_name}"
                assert "beef" not in i_name, f"Found beef in ingredient {i_name}"
                assert "turkey" not in i_name, f"Found turkey in ingredient {i_name}"


@pytest.mark.asyncio
async def test_user_reported_multi_turn_edit_meal_slot_preserves_selected_good_plan():
    """
    Tests Issue 3:
    User has '5-Day Veg Plan' and 'Good plan'.
    Turn 1: 'hey i want to update a meal plan' -> Assistant asks which one.
    Turn 2: 'good plan' -> Assistant confirms 'Got it, let's update your "Good" plan!'.
    Turn 3: 'hey i want you to replace the day 1 dinner meal with something else'.
    The system MUST update 'Good plan', and leave '5-Day Veg Plan' completely untouched.
    """
    await init_db()
    user_profile = {
        "name": "Deepak",
        "age": 24,
        "sex": "male",
        "height_cm": 165.0,
        "weight_kg": 72.0,
        "activity_level": "low",
        "goal": "maintain",
        "diet_type": "non-veg",
        "allergies": [],
        "dislikes": []
    }
    user_id = 8
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        existing = await p_repo.get_all_by_user(user_id)
        for p in existing:
            await p_repo.delete_plan(p.id)

    from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient
    meals_veg = [
        Meal(meal_type="breakfast", recipe_name="Paneer Bhurji", calories=700, protein_g=30, carbs_g=50, fat_g=20, ingredients=[Ingredient(name="Paneer", amount_grams=150, unit="g")]),
        Meal(meal_type="lunch", recipe_name="Rajma Masala", calories=900, protein_g=35, carbs_g=100, fat_g=25, ingredients=[Ingredient(name="Rajma", amount_grams=200, unit="g")]),
        Meal(meal_type="dinner", recipe_name="Palak Tofu", calories=600, protein_g=25, carbs_g=60, fat_g=15, ingredients=[Ingredient(name="Tofu", amount_grams=150, unit="g")]),
    ]
    meals_good = [
        Meal(meal_type="breakfast", recipe_name="Egg Toast", calories=600, protein_g=30, carbs_g=50, fat_g=15, ingredients=[Ingredient(name="Eggs", amount_grams=100, unit="g")]),
        Meal(meal_type="lunch", recipe_name="Chicken Rice", calories=950, protein_g=50, carbs_g=90, fat_g=25, ingredients=[Ingredient(name="Chicken", amount_grams=200, unit="g")]),
        Meal(meal_type="dinner", recipe_name="Fish Curry", calories=700, protein_g=40, carbs_g=50, fat_g=20, ingredients=[Ingredient(name="Fish", amount_grams=180, unit="g")]),
    ]

    plan_5d = MealPlan(
        name="5-Day Veg Plan",
        diet_type="veg",
        target_calories=2250,
        days=[DayPlan(day_number=i, meals=meals_veg, total_calories=2200, total_protein_g=90, total_carbs_g=210, total_fat_g=60) for i in range(1, 6)]
    )
    plan_good = MealPlan(
        name="Good plan",
        diet_type="non-veg",
        target_calories=2250,
        days=[DayPlan(day_number=i, meals=meals_good, total_calories=2250, total_protein_g=120, total_carbs_g=190, total_fat_g=60) for i in range(1, 4)]
    )

    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        db_p1 = await p_repo.create_plan(user_id=user_id, days=5, calorie_target=2250, meal_plan=plan_5d, status="draft", name="5-Day Veg Plan", diet_type="veg")
        db_p2 = await p_repo.create_plan(user_id=user_id, days=3, calorie_target=2250, meal_plan=plan_good, status="approved", name="Good plan", diet_type="non-veg")
        id_veg = db_p1.id
        id_good = db_p2.id

    history = []

    # Turn 1: User says 'hey i want to update a meal plan'
    msg1 = "hey i want to update a meal plan"
    state1 = await run_meal_planner(
        user_id=user_id,
        user_message=msg1,
        user_profile=user_profile,
        history=history
    )
    assert state1.status == "conversational"
    assert "5-day veg plan" in state1.system_message.lower()
    assert "good" in state1.system_message.lower()

    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": state1.system_message})

    # Turn 2: User responds 'good plan'
    msg2 = "good plan"
    state2 = await run_meal_planner(
        user_id=user_id,
        user_message=msg2,
        user_profile=user_profile,
        history=history
    )
    assert state2.status == "conversational"
    assert "good" in state2.system_message.lower()

    history.append({"role": "user", "content": msg2})
    history.append({"role": "assistant", "content": state2.system_message})

    # Turn 3: User says 'hey i want you to replace the day 1 dinner meal with something else'
    msg3 = "hey i want you to replace the day 1 dinner meal with something else"
    state3 = await run_meal_planner(
        user_id=user_id,
        user_message=msg3,
        user_profile=user_profile,
        history=history
    )

    # Must update Good plan!
    assert state3.current_plan is not None
    assert "good" in state3.current_plan.name.lower()
    assert state3.plan_id == id_good

    # Verify database: Good plan is updated, 5-Day Veg Plan version & meals unchanged
    async with AsyncSessionLocal() as session:
        p_repo = PlanRepository(session)
        p_veg_db = await p_repo.get_by_id(id_veg)
        p_good_db = await p_repo.get_by_id(id_good)
        assert p_veg_db.version == 1
        assert p_good_db.version == 2
        # Check day 1 dinner in Good plan was updated from Fish Curry
        d1_dinner = next(m for m in p_good_db.meals if m.day == 1 and m.meal_type == "dinner")
        assert d1_dinner.recipe_name != "Fish Curry"



