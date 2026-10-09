import pytest
import httpx
from backend.app.main import app
from backend.app.db.session import init_db
from backend.app.db.seed.seed import seed_allergen_sources

@pytest.mark.asyncio
async def test_health_check_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

@pytest.mark.asyncio
async def test_chat_nonexistent_user():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/chat", json={"user_id": 999999, "message": "Make a plan"})
        assert resp.status_code == 404
        assert "does not exist" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_trace_endpoint_empty():
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trace/unknown-task-id-xyz")
        assert resp.status_code == 200
        assert resp.json() == []
