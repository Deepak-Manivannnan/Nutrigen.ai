import pytest
from httpx import AsyncClient, ASGITransport
from backend.app.main import app

@pytest.mark.asyncio
async def test_health_check_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

@pytest.mark.asyncio
async def test_profile_api_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Valid profile creation
        valid_payload = {
            "name": "Sarah Connor",
            "age": 32,
            "sex": "female",
            "height_cm": 168.0,
            "weight_kg": 62.0,
            "activity_level": "high",
            "goal": "lose",
            "diet_type": "non-veg",
            "dislikes": ["bell pepper"],
            "allergies": ["peanuts", "shellfish"]
        }
        res_create = await ac.post("/api/profile", json=valid_payload)
        assert res_create.status_code == 201
        created_data = res_create.json()
        assert created_data["id"] is not None
        assert created_data["name"] == "Sarah Connor"
        user_id = created_data["id"]

        # 3. Retrieve profile
        res_get = await ac.get(f"/api/profile/{user_id}")
        assert res_get.status_code == 200
        assert res_get.json()["name"] == "Sarah Connor"
        assert "peanuts" in res_get.json()["allergies"]

        # 4. Calorie range calculation endpoint
        res_cal = await ac.get(f"/api/profile/{user_id}/calorie-range")
        assert res_cal.status_code == 200
        cal_data = res_cal.json()
        assert cal_data["is_eligible"] is True
        assert cal_data["bmr"] > 1200
        assert cal_data["min_safe_calories"] >= 1200
