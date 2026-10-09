import pytest
from backend.app.guardrails.calorie_rules import calculate_mifflin_st_jeor
from backend.app.guardrails.input_checks import validate_input_request
from backend.app.schemas.api import ProfileCreate, ProfileUpdate
from backend.app.db.session import init_db, AsyncSessionLocal
from backend.app.db.repositories.users import UserRepository

@pytest.mark.asyncio
async def test_mifflin_st_jeor_calculation():
    # 28 yo male, 175cm, 75kg, medium activity
    res = calculate_mifflin_st_jeor(28, "male", 175.0, 75.0, "medium", "maintain")
    assert res["is_eligible"] is True
    assert 1600 < res["bmr"] < 1800
    assert 2400 < res["tdee"] < 2800
    assert res["min_safe_calories"] >= 1200

@pytest.mark.asyncio
async def test_input_guardrail():
    profile = {
        "age": 30,
        "sex": "female",
        "height_cm": 165.0,
        "weight_kg": 60.0,
        "activity_level": "medium",
        "goal": "maintain",
        "diet_type": "veg",
        "allergies": ["peanuts"]
    }

    # Safe calories
    is_safe, msg, meta = validate_input_request(profile, 1800.0, 5)
    assert is_safe is True

    # Dangerous low calorie (e.g. 500 kcal)
    is_safe, msg, meta = validate_input_request(profile, 500.0, 5)
    assert is_safe is False
    assert "clinically safe daily range" in msg

    # Fixed meal dietary conflict (veg profile with chicken request)
    is_safe, msg, meta = validate_input_request(profile, 1800.0, 5, fixed_meals={"lunch": "grilled chicken"})
    assert is_safe is False
    assert "conflicts with your vegetarian diet" in msg

    # Fixed meal allergy conflict
    is_safe, msg, meta = validate_input_request(profile, 1800.0, 5, fixed_meals={"breakfast": "toast with peanuts"})
    assert is_safe is False
    assert "contains 'peanuts'" in msg

    # Under-18 test case
    minor_profile = dict(profile, age=16)
    is_safe, msg, meta = validate_input_request(minor_profile, 1800.0, 5)
    assert is_safe is False
    assert "under 18" in msg.lower()

@pytest.mark.asyncio
async def test_user_repository_crud():
    await init_db()
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        profile_data = ProfileCreate(
            name="Deepak",
            age=26,
            sex="male",
            height_cm=178.0,
            weight_kg=74.0,
            activity_level="medium",
            goal="maintain",
            diet_type="veg",
            dislikes=["bitter gourd"],
            allergies=["shellfish", "peanuts"]
        )

        user = await repo.create(profile_data)
        assert user.id is not None
        assert user.name == "Deepak"
        assert len(user.allergies) == 2

        # Fetch
        fetched = await repo.get_by_id(user.id)
        assert fetched is not None
        assert fetched.age == 26

        # Update
        updated = await repo.update(user.id, ProfileUpdate(weight_kg=72.0))
        assert updated.weight_kg == 72.0
