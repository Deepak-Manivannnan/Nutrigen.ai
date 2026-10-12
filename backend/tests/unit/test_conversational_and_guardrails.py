import pytest
from backend.app.orchestration.supervisor.intent import extract_user_intent
from backend.app.guardrails.input_checks import check_out_of_scope_query
from backend.app.orchestration.runner import run_meal_planner
from backend.app.db.session import init_db
from backend.app.db.seed.seed import seed_allergen_sources

@pytest.mark.asyncio
async def test_fast_greeting_intent():
    for word in ["hi", "hello", "hey", "good morning", "howdy"]:
        extraction = await extract_user_intent(word)
        assert extraction.intent == "chat", f"Expected 'chat' for '{word}', got '{extraction.intent}'"

@pytest.mark.asyncio
async def test_app_info_intent():
    extraction = await extract_user_intent("What is this meal planner about?")
    assert extraction.intent == "app_info", f"Expected 'app_info', got '{extraction.intent}'"

@pytest.mark.asyncio
async def test_out_of_scope_intent():
    queries = [
        "What is the iPhone 18 cost in India?",
        "Who is the CEO of Apple?"
    ]
    for q in queries:
        extraction = await extract_user_intent(q)
        assert extraction.intent == "out_of_scope", f"Expected 'out_of_scope' for '{q}', got '{extraction.intent}'"

@pytest.mark.asyncio
async def test_semantic_out_of_scope_guardrail():
    is_out, reason = await check_out_of_scope_query("What is the iPhone 18 cost in India?")
    assert is_out is True
    assert "outside the domain" in reason

    is_in, _ = await check_out_of_scope_query("Plan a 3-day meal plan with oats")
    assert is_in is False



@pytest.mark.asyncio
async def test_greeting_does_not_launch_planner_swarm():
    """
    Sending 'hi' should return a warm conversational greeting without
    launching the 5-agent LangGraph workflow or generating a meal plan.
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
        "allergies": ["peanuts"],
        "dislikes": ["mushrooms"]
    }
    
    final_state = await run_meal_planner(
        user_id=1,
        user_message="hi",
        user_profile=user_profile
    )
    
    assert final_state.status == "conversational"
    assert final_state.current_plan is None
    assert "Deepak" in final_state.system_message or "NutriGen" in final_state.system_message

@pytest.mark.asyncio
async def test_out_of_scope_query_triggers_polite_guardrail():
    """
    Asking about unrelated topics like 'What is paracetamol?' should trigger
    the polite domain guardrail refusal rather than generating meals.
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
    
    final_state = await run_meal_planner(
        user_id=1,
        user_message="What is the iPhone 18 cost in India?",
        user_profile=user_profile
    )
    
    assert final_state.status == "out_of_scope"
    assert final_state.current_plan is None
    assert any(p in final_state.system_message.lower() for p in ["outside of food", "dedicated exclusively", "focused exclusively", "meal planning"])

@pytest.mark.asyncio
async def test_edit_meal_intent_extraction():
    extraction = await extract_user_intent("Swap Day 2 lunch with paneer tikka")
    assert extraction.intent == "edit_meal"
    assert extraction.edit_details is not None
    assert extraction.edit_details.day == 2
    assert extraction.edit_details.meal_type == "lunch"
    assert "paneer" in extraction.edit_details.instructions.lower()

@pytest.mark.asyncio
async def test_edit_meal_without_active_plan_returns_friendly_prompt():
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
    # User 99999 has no plan in DB
    final_state = await run_meal_planner(
        user_id=99999,
        user_message="Replace Day 1 dinner with vegetable soup",
        user_profile=user_profile
    )
    assert final_state.status == "conversational"
    assert final_state.current_plan is None
    assert "don't have an active meal plan yet" in final_state.system_message

@pytest.mark.asyncio
async def test_llm_extracts_plan_context_from_history():
    """Test that LLM-based intent extraction correctly infers plan context from conversation history."""
    from backend.app.orchestration.supervisor.intent import extract_user_intent
    history = [
        {"role": "user", "content": "can u create a veg meal plan for 5 days with 1800kcal/day"},
        {"role": "assistant", "content": "Your profile preference is **Non-Vegetarian**, but you requested a **Vegetarian** meal plan. In addition, your requested target of **1,800 kcal/day** is below your maintenance range of 2,150 – 2,350 kcal.\n\nCan I create a **5-day Vegetarian** plan for you? Would you like me to plan it at your requested **1,800 kcal/day**, or at your recommended target of **2,250 kcal/day**?"}
    ]
    # User confirms with advised calorie range
    user_msg = "oops i forgot about my calorie range thanks for reminding and u can create a veg plan with my advised calorie range"
    extraction = await extract_user_intent(user_msg, history=history)
    assert extraction.intent == "new_plan"
    assert extraction.requested_days == 5
    assert extraction.requested_calories == 2250.0
    assert extraction.requested_diet_type == "veg"
    assert extraction.diet_conflict_confirmed is True
    assert extraction.chosen_diet_type == "veg"

@pytest.mark.asyncio
async def test_user_reported_calorie_and_diet_acceptance():
    from backend.app.orchestration.supervisor.intent import extract_user_intent
    history = [
        {"role": "user", "content": "can u create a veg meal plan for 5 days with 1800kcal/day"},
        {"role": "assistant", "content": "Your profile preference is **Non-Vegetarian**, but you requested a **Vegetarian** meal plan. In addition, your requested target of **1,800 kcal/day** is below your maintenance range of 2,150 – 2,350 kcal.\n\nCan I create a **5-day Vegetarian** plan for you? Would you like me to plan it at your requested **1,800 kcal/day**, or at your recommended target of **2,250 kcal/day**?"}
    ]
    user_msg = "oops i forgot about my calorie range thanks for remining and u can create a veg plan with my advised calorie range"
    extraction = await extract_user_intent(user_msg, history=history)
    assert extraction.intent == "new_plan"
    assert extraction.diet_conflict_confirmed is True
    assert extraction.chosen_diet_type == "veg"
    assert extraction.requested_diet_type == "veg"
    assert extraction.requested_days == 5
    assert extraction.requested_calories == 2250.0

@pytest.mark.asyncio
async def test_user_reported_update_plan_selection():
    from backend.app.orchestration.supervisor.intent import extract_user_intent
    history = [
        {"role": "user", "content": "hey i want to update a meal plan"},
        {"role": "assistant", "content": "I'd be happy to help you with that! Since you have both the 5-day vegetarian plan and the 3-day non-vegetarian plan saved, could you let me know which one you'd like to update today?"}
    ]
    user_msg = "the 5 day meal plan"
    extraction = await extract_user_intent(user_msg, history=history)
    assert extraction.intent == "edit_meal"
    assert extraction.is_update_request is True
    assert extraction.edit_details is None

def test_find_referenced_plan_by_day_count():
    from backend.app.orchestration.runner import find_referenced_plan
    from unittest.mock import MagicMock
    p1 = MagicMock(id=1, days=5, diet_type="veg")
    p1.name = "5-Day Veg Plan"
    p2 = MagicMock(id=2, days=3, diet_type="non-veg")
    p2.name = "3-Day Non-veg Plan"
    all_plans = [p1, p2]
    
    # User says "the 5 day meal plan"
    matched = find_referenced_plan("the 5 day meal plan", all_plans, fallback_to_first=False)
    assert matched is not None
    assert matched.id == 1
    assert matched.name == "5-Day Veg Plan"
    
    # User says "the 3-day plan"
    matched_3 = find_referenced_plan("the 3-day plan", all_plans, fallback_to_first=False)
    assert matched_3 is not None
    assert matched_3.id == 2

@pytest.mark.asyncio
async def test_llm_extracts_non_veg_and_exclusions():
    """Test that LLM-based intent extraction correctly handles non-veg diet and exclusions."""
    from backend.app.orchestration.supervisor.intent import extract_user_intent
    msg = "okay can u create another plan with 1900 kcal/day non veg meal plan for 5 days and exclude pork, beef, turkey"
    extraction = await extract_user_intent(msg)
    assert extraction.intent == "new_plan"
    assert extraction.requested_diet_type == "non-veg"
    assert extraction.requested_days == 5
    assert extraction.requested_calories == 1900.0
    assert "pork" in extraction.requested_exclusions
    assert "beef" in extraction.requested_exclusions
    assert "turkey" in extraction.requested_exclusions

@pytest.mark.asyncio
async def test_multi_turn_edit_meal_slot_inherits_good_plan():
    from backend.app.orchestration.supervisor.intent import extract_user_intent
    from backend.app.orchestration.runner import find_referenced_plan
    from unittest.mock import MagicMock

    history = [
        {"role": "user", "content": "hey i want to update a meal plan"},
        {"role": "assistant", "content": 'No problem at all! Would you like to update the 5-Day Veg Plan or your "Good" plan?'},
        {"role": "user", "content": "good plan"},
        {"role": "assistant", "content": 'Got it, let\'s update your "Good" plan! What specific changes would you like to make to your meals?'}
    ]
    user_msg = "hey i want you to replace the day 1 dinner meal with something else"

    extraction = await extract_user_intent(user_msg, history=history)
    assert extraction.intent == "edit_meal"
    assert extraction.edit_details is not None
    assert extraction.edit_details.day == 1
    assert extraction.edit_details.meal_type == "dinner"
    assert extraction.target_plan_name is not None
    assert "good" in extraction.target_plan_name.lower()

    # Verify find_referenced_plan resolves the Good plan from history
    p_veg = MagicMock(id=1, days=5, diet_type="veg")
    p_veg.name = "5-Day Veg Plan"
    p_good = MagicMock(id=2, days=3, diet_type="non-veg")
    p_good.name = "Good plan"
    all_plans = [p_veg, p_good]

    matched = find_referenced_plan(user_msg, all_plans, target_name=extraction.target_plan_name, history=history)
    assert matched is not None
    assert matched.id == 2
    assert matched.name == "Good plan"

@pytest.mark.asyncio
async def test_llm_extracts_exclusions_from_creation_and_update():
    """Test that LLM-based intent extraction correctly handles exclusions in creation and update messages."""
    from backend.app.orchestration.supervisor.intent import extract_user_intent

    # Test initial creation phrasing with "by excluding the beef, pork and turkey"
    creation_msg = "create a 3 day meal plan by excluding the beef, pork and turkey"
    creation_res = await extract_user_intent(creation_msg)
    assert creation_res.intent == "new_plan"
    assert "beef" in creation_res.requested_exclusions
    assert "pork" in creation_res.requested_exclusions
    assert "turkey" in creation_res.requested_exclusions

    # Test update phrasing with "replacing the recipes which contains pork, beef, and turkey with different recipes"
    update_msg = "I just update the recently created meal plan by replacing the recipes which contains pork, beef, and turkey with different recipes"
    update_res = await extract_user_intent(update_msg)
    assert update_res.intent == "edit_meal"
    assert "pork" in update_res.requested_exclusions
    assert "beef" in update_res.requested_exclusions
    assert "turkey" in update_res.requested_exclusions
    assert update_res.is_update_request is False
    assert update_res.edit_details is not None
    assert hasattr(update_res.edit_details, "dish_to_replace")

def test_planner_sanitize_diet_cleans_excluded_meats():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient
    from unittest.mock import MagicMock

    llm_mock = MagicMock()
    planner = PlannerAgent(client=llm_mock)

    # Meal plan with a pork meal and beef meal
    pork_meal = Meal(
        meal_type="lunch",
        recipe_name="Roasted Pork Chops with Apples",
        calories=600.0,
        protein_g=40.0,
        carbs_g=30.0,
        fat_g=20.0,
        ingredients=[Ingredient(name="Pork Chops", amount_grams=180.0)],
        instructions="Cook pork chops."
    )
    beef_meal = Meal(
        meal_type="dinner",
        recipe_name="Grilled Beef Sirloin Steak",
        calories=550.0,
        protein_g=42.0,
        carbs_g=20.0,
        fat_g=18.0,
        ingredients=[Ingredient(name="Beef Steak", amount_grams=170.0)],
        instructions="Grill steak."
    )
    day = DayPlan(
        day_number=1,
        meals=[pork_meal, beef_meal],
        total_calories=1150.0,
        total_protein_g=82.0,
        total_carbs_g=50.0,
        total_fat_g=38.0
    )
    plan = MealPlan(
        name="3-Day Non-Veg Plan",
        days=[day],
        target_calories=2000.0,
        average_daily_calories=2000.0,
        diet_type="non-veg",
        version=1
    )

    user_profile = {"diet_type": "non-veg", "allergies": ["pork", "beef", "turkey"]}
    planner._sanitize_diet_compliance(plan, "non-veg", 2000.0, user_profile)

    # Verify both dishes were sanitized and replaced with compliant alternatives
    names = [m.recipe_name.lower() for m in plan.days[0].meals]
    assert not any("pork" in n for n in names)
    assert not any("beef" in n for n in names)
    assert not any("turkey" in n for n in names)
    assert any("chicken" in n or "fish" in n or "egg" in n or "paneer" in n for n in names)

@pytest.mark.asyncio
async def test_modify_meal_replaces_snack_on_correct_day_without_appending():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient
    from backend.app.orchestration.supervisor.intent import EditDetails
    from unittest.mock import AsyncMock

    llm_mock = AsyncMock()
    # Mock replacement snack
    rep_snack = Meal(
        meal_type="snack",
        recipe_name="Greek Yogurt with Mixed Berries and Walnuts",
        calories=200.0,
        protein_g=15.0,
        carbs_g=20.0,
        fat_g=5.0,
        ingredients=[Ingredient(name="Greek Yogurt", amount_grams=150.0)],
        instructions="Mix yogurt with berries."
    )
    llm_mock.generate_structured.return_value = rep_snack
    planner = PlannerAgent(client=llm_mock)

    # 3-day plan where ONLY Day 2 has a snack
    day1 = DayPlan(day_number=1, meals=[Meal(meal_type="breakfast", recipe_name="Eggs", calories=500, protein_g=30, carbs_g=40, fat_g=15, ingredients=[], instructions="Cook")], total_calories=500, total_protein_g=30, total_carbs_g=40, total_fat_g=15)
    day2_snack = Meal(meal_type="snack", recipe_name="Old Apple Snack", calories=180, protein_g=2, carbs_g=30, fat_g=1, ingredients=[], instructions="Eat")
    day2 = DayPlan(day_number=2, meals=[Meal(meal_type="breakfast", recipe_name="Oats", calories=500, protein_g=20, carbs_g=60, fat_g=10, ingredients=[], instructions="Cook"), day2_snack], total_calories=680, total_protein_g=22, total_carbs_g=90, total_fat_g=11)
    day3 = DayPlan(day_number=3, meals=[Meal(meal_type="breakfast", recipe_name="Toast", calories=500, protein_g=20, carbs_g=60, fat_g=10, ingredients=[], instructions="Cook")], total_calories=500, total_protein_g=20, total_carbs_g=60, total_fat_g=10)

    current_plan = MealPlan(
        name="3-Day Veg Plan",
        days=[day1, day2, day3],
        target_calories=2000.0,
        average_daily_calories=2000.0,
        diet_type="veg",
        version=1
    )

    # User says: "that added snack is not good, can you please replace that particularly added snack with something else"
    # Notice: day is omitted!
    edit_details = EditDetails(
        meal_type="snack",
        day=None,
        instructions="that added snack is not good, can you please replace that particularly added snack with something else"
    )

    updated = await planner.modify_meal(
        current_plan=current_plan,
        edit_details=edit_details,
        user_profile={"diet_type": "veg", "allergies": []}
    )

    # Verify Day 1 still has NO snack
    assert not any(m.meal_type.lower() == "snack" for m in updated.days[0].meals)
    # Verify Day 2 STILL has exactly 1 snack, and it was replaced in place
    day2_snacks = [m for m in updated.days[1].meals if m.meal_type.lower() == "snack"]
    assert len(day2_snacks) == 1
    assert day2_snacks[0].recipe_name == "Greek Yogurt with Mixed Berries and Walnuts"

@pytest.mark.asyncio
async def test_missing_day_or_slot_prompts_user():
    from backend.app.orchestration.runner import run_meal_planner
    from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient
    from unittest.mock import AsyncMock, patch

    # Mock plan in DB
    day1 = DayPlan(day_number=1, meals=[Meal(meal_type="breakfast", recipe_name="Eggs", calories=500, protein_g=30, carbs_g=40, fat_g=15, ingredients=[], instructions="Cook"), Meal(meal_type="lunch", recipe_name="Salad", calories=600, protein_g=20, carbs_g=60, fat_g=20, ingredients=[], instructions="Cook"), Meal(meal_type="dinner", recipe_name="Soup", calories=500, protein_g=25, carbs_g=50, fat_g=15, ingredients=[], instructions="Cook")], total_calories=1600, total_protein_g=75, total_carbs_g=150, total_fat_g=50)
    day2 = DayPlan(day_number=2, meals=[Meal(meal_type="breakfast", recipe_name="Oats", calories=500, protein_g=20, carbs_g=60, fat_g=10, ingredients=[], instructions="Cook"), Meal(meal_type="lunch", recipe_name="Rice", calories=600, protein_g=20, carbs_g=60, fat_g=20, ingredients=[], instructions="Cook"), Meal(meal_type="dinner", recipe_name="Curry", calories=500, protein_g=25, carbs_g=50, fat_g=15, ingredients=[], instructions="Cook")], total_calories=1600, total_protein_g=65, total_carbs_g=170, total_fat_g=45)
    plan = MealPlan(name="3-Day Veg Plan", days=[day1, day2], target_calories=2000.0, average_daily_calories=1600.0, diet_type="veg", version=1)

    mock_db_plan = AsyncMock()
    mock_db_plan.id = 1
    mock_db_plan.name = "3-Day Veg Plan"
    mock_db_plan.days = 2
    mock_db_plan.calorie_target = 2000.0
    mock_db_plan.diet_type = "veg"
    mock_db_plan.status = "draft"
    mock_db_plan.version = 1
    mock_db_plan.meal_plan = plan.model_dump()

    with patch("backend.app.orchestration.runner.PlanRepository") as MockRepo:
        repo_inst = AsyncMock()
        repo_inst.get_all_by_user.return_value = [mock_db_plan]
        MockRepo.return_value = repo_inst
        MockRepo.to_meal_plan_domain.return_value = plan

        # Case 1: Slot specified (dinner), but day is missing across 2-day plan
        state = await run_meal_planner(
            user_id=1,
            user_message="I want to update my plan: swap dinner with paneer tikka",
            user_profile={"name": "Alice", "diet_type": "veg"}
        )
        assert state.status == "conversational"
        # Quick options provide choice of Day 1 or Day 2
        labels = [opt["label"].lower() for opt in (state.quick_options or [])]
        assert any("day 1 dinner" in l for l in labels)
        assert any("day 2 dinner" in l for l in labels)

        # Case 2: Day specified (Day 2), but meal slot is missing
        state2 = await run_meal_planner(
            user_id=1,
            user_message="I want to update Day 2 of my plan with a different meal",
            user_profile={"name": "Alice", "diet_type": "veg"}
        )
        assert state2.status == "conversational"
        labels2 = [opt["label"].lower() for opt in (state2.quick_options or [])]
        assert any("day 2 breakfast" in l for l in labels2)
        assert any("day 2 lunch" in l for l in labels2)
        assert any("day 2 dinner" in l for l in labels2)



