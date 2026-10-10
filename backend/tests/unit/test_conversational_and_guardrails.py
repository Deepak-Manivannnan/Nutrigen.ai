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
async def test_scan_history_for_plan_context_markdown_bold():
    from backend.app.orchestration.supervisor.intent import _scan_history_for_plan_context
    history = [
        {"role": "user", "content": "can u create a veg meal plan for 5 days with 1800kcal/day"},
        {"role": "assistant", "content": "Your profile preference is **Non-Vegetarian**, but you requested a **Vegetarian** meal plan. In addition, your requested target of **1,800 kcal/day** is below your maintenance range of 2,150 – 2,350 kcal.\n\nCan I create a **5-day Vegetarian** plan for you? Would you like me to plan it at your requested **1,800 kcal/day**, or at your recommended target of **2,250 kcal/day**?"}
    ]
    ctx = _scan_history_for_plan_context(history)
    assert ctx["days"] == 5
    assert ctx["calories"] == 1800.0
    assert ctx["diet_type"] == "veg"
    assert ctx["recommended_calories"] == 2250.0

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

def test_extract_non_veg_spaced_and_meat_exclusions():
    from backend.app.orchestration.supervisor.intent import _extract_plan_parameters_from_text
    msg = "okay can u create another plan with 1900 kcal/day non veg meal plan for 5 days and exclude pork, beef, turkey"
    res = _extract_plan_parameters_from_text(msg)
    assert res["diet_type"] == "non-veg", f"Expected non-veg, got {res['diet_type']}"
    assert res["days"] == 5
    assert res["calories"] == 1900.0
    assert "pork" in res["exclusions"]
    assert "beef" in res["exclusions"]
    assert "turkey" in res["exclusions"]

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



