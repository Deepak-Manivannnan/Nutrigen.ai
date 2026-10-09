import pytest
from backend.app.tools.allergens import check_allergens, check_direct_allergen_match
from backend.app.agents.allergy.agent import AllergyAgent
from backend.app.agents.base import AgentStepAction
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, AllergyReport, InvestigatedItem
from backend.tests.fakes.fake_llm import FakeLLMClient

@pytest.mark.asyncio
async def test_collapsed_spacing_and_hyphen_matching():
    """Test that words without spaces or with hyphens match their allergens."""
    # Coconut oil without space
    violations = await check_allergens(["coconut"], ["coconutoil"])
    assert len(violations) == 1
    assert violations[0]["allergen"] == "coconut"

    # Coconut-oil with hyphen
    violations2 = await check_allergens(["coconut"], ["coconut-oil"])
    assert len(violations2) == 1

    # Peanut butter collapsed
    violations3 = await check_allergens(["peanuts"], ["crunchy peanutbutter"])
    assert len(violations3) == 1
    assert violations3[0]["allergen"] == "peanuts"

    # Almond milk collapsed
    violations4 = await check_allergens(["tree nuts"], ["vanilla almondmilk"])
    assert len(violations4) == 1

@pytest.mark.asyncio
async def test_false_positive_exceptions_skeptical_verification():
    """Verify that linguistic overlaps do NOT cause false positive vetoes."""
    # 1. Eggplant is NOT poultry egg
    egg_violations = await check_allergens(["egg", "eggs"], ["grilled eggplant", "roasted aubergine", "eggplant parmesan without egg"])
    assert len(egg_violations) == 0

    # 2. Butternut squash is a vegetable, NOT a nut
    squash_violations = await check_allergens(["nut", "nuts", "tree nuts"], ["roasted butternut squash", "butternut soup"])
    assert len(squash_violations) == 0

    # 3. Nutmeg is a dried seed spice, NOT a tree nut
    nutmeg_violations = await check_allergens(["nut", "nuts", "tree nuts"], ["pumpkin pie with ground nutmeg"])
    assert len(nutmeg_violations) == 0

    # 4. Water chestnuts are aquatic tubers, NOT tree nuts
    chestnut_violations = await check_allergens(["tree nuts"], ["water chestnuts in vegetable stir fry"])
    assert len(chestnut_violations) == 0

    # 5. Plant-based milks and non-dairy butters do NOT violate dairy
    dairy_violations = await check_allergens(["dairy", "milk"], [
        "unsweetened almond milk",
        "creamy oat milk",
        "organic coconut milk",
        "soy milk",
        "creamy peanut butter",
        "cacao butter",
        "cocoa butter"
    ])
    assert len(dairy_violations) == 0

@pytest.mark.asyncio
async def test_canonical_synonyms_and_plurals():
    """Test canonical roots and plural forms."""
    # Tree nut detects walnut and cashew
    v_nuts = await check_allergens(["tree nuts"], ["walnut salad", "roasted cashews", "pistachio gelato"])
    assert len(v_nuts) == 3

    # Dairy detects cheese, yogurt, butter, ghee
    v_dairy = await check_allergens(["dairy"], ["cheddar cheese", "greek yogurt", "clarified ghee"])
    assert len(v_dairy) == 3

    # Shellfish detects shrimp, prawn, crab, oysters
    v_shellfish = await check_allergens(["shellfish"], ["grilled shrimp", "king prawn", "crab cake", "fresh oysters"])
    assert len(v_shellfish) == 4

    # Gluten / wheat detects semolina, couscous, maida
    v_gluten = await check_allergens(["gluten"], ["durum semolina pasta", "moroccan couscous", "maida flour flatbread"])
    assert len(v_gluten) == 3

@pytest.mark.asyncio
async def test_custom_user_allergies_outside_canonical_list():
    """Custom allergy not in predefined map should correctly singularize and match."""
    # Strawberries
    v_straw = await check_allergens(["strawberries"], ["fresh strawberry slices", "strawberry jam"])
    assert len(v_straw) == 2

    # Kiwi
    v_kiwi = await check_allergens(["kiwi"], ["diced kiwis"])
    assert len(v_kiwi) == 1

@pytest.mark.asyncio
async def test_completely_safe_meals_produce_zero_violations():
    """A meal plan with safe ingredients must pass cleanly."""
    safe_ingredients = [
        "grilled chicken breast",
        "steamed white rice",
        "broccoli florets",
        "extra virgin olive oil",
        "sea salt",
        "black pepper"
    ]
    violations = await check_allergens(["peanuts", "shellfish", "dairy", "tree nuts", "gluten", "eggs"], safe_ingredients)
    assert len(violations) == 0

@pytest.mark.asyncio
async def test_input_type_resilience():
    """Ensure tool handles JSON strings, comma strings, or raw lists from LLMs."""
    # JSON strings
    v1 = await check_allergens('["peanuts"]', '["peanut butter", "apple"]')
    assert len(v1) == 1

    # Comma-separated strings
    v2 = await check_allergens("peanuts, dairy", "peanut butter, cheddar cheese, rice")
    assert len(v2) == 2

@pytest.mark.asyncio
async def test_hybrid_agent_overrules_hallucinated_llm_pass():
    """
    SKEPTICAL TEST: If the LLM hallucinates passed: True on a meal with 'coconutoil'
    for a user with a coconut allergy, Layer 1 deterministic code MUST overrule the LLM
    and enforce a Hard Veto (passed: False).
    """
    fake_llm = FakeLLMClient()
    # Step 1: LLM finishes early claiming plan is completely safe
    fake_llm.queue_structured_response(AgentStepAction(
        thought="I see coconutoil, but I mistakenly think it is safe.",
        action="finish",
        finish_summary="I approve this plan."
    ))
    # Step 2: LLM produces report claiming passed=True
    fake_report = AllergyReport(
        passed=True,
        violations=[],
        investigated_items=[InvestigatedItem(ingredient="coconutoil", status="safe")],
        summary="All clear! Plan is safe."
    )
    fake_llm.queue_structured_response(fake_report)

    agent = AllergyAgent(client=fake_llm, max_steps=3)
    plan = MealPlan(
        target_calories=1800.0,
        days=[
            DayPlan(
                day_number=1,
                meals=[
                    Meal(
                        meal_type="dinner",
                        recipe_name="Curry",
                        ingredients=[Ingredient(name="coconutoil", amount_grams=15.0)]
                    )
                ]
            )
        ]
    )

    report = await agent.review_plan(plan, user_allergies=["coconut"])

    # Guaranteed Deterministic Override
    assert report.passed is False
    assert len(report.violations) == 1
    assert report.violations[0].ingredient == "coconutoil"
    assert report.violations[0].allergen == "coconut"
    assert "Hard veto enforced" in report.summary

@pytest.mark.asyncio
async def test_hybrid_agent_passes_clean_plan_with_false_positive_traps():
    """
    SKEPTICAL TEST: Meal plan contains eggplant, butternut squash, and nutmeg.
    User is allergic to eggs and tree nuts.
    The agent MUST PASS cleanly and NOT falsely veto.
    """
    fake_llm = FakeLLMClient()
    fake_llm.queue_structured_response(AgentStepAction(
        thought="Inspected eggplant, butternut squash, and nutmeg. None are allergic hazards for egg and tree nuts.",
        action="finish",
        finish_summary="Approved, no true allergen violations."
    ))
    fake_report = AllergyReport(
        passed=True,
        violations=[],
        investigated_items=[
            InvestigatedItem(ingredient="eggplant", status="safe"),
            InvestigatedItem(ingredient="butternut squash", status="safe"),
            InvestigatedItem(ingredient="nutmeg", status="safe")
        ],
        summary="Plan is safe. Eggplant, butternut squash, and nutmeg are not true allergens."
    )
    fake_llm.queue_structured_response(fake_report)

    agent = AllergyAgent(client=fake_llm, max_steps=3)
    plan = MealPlan(
        target_calories=2000.0,
        days=[
            DayPlan(
                day_number=1,
                meals=[
                    Meal(
                        meal_type="lunch",
                        recipe_name="Roasted Veggie Bowl",
                        ingredients=[
                            Ingredient(name="grilled eggplant", amount_grams=100.0),
                            Ingredient(name="roasted butternut squash", amount_grams=150.0),
                            Ingredient(name="pinch of ground nutmeg", amount_grams=2.0)
                        ]
                    )
                ]
            )
        ]
    )

    report = await agent.review_plan(plan, user_allergies=["eggs", "tree nuts"])

    assert report.passed is True
    assert len(report.violations) == 0
