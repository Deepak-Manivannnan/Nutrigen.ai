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
    assert "cannot assist with topics outside of food" in final_state.system_message or "dedicated exclusively" in final_state.system_message

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

