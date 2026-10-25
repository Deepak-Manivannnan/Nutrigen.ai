"""GET /api/chat/result/{task_id} requires sign-in and returns only the caller's replies."""
import pytest
from fastapi.testclient import TestClient

from backend.app.api.routes import chat
from backend.app.api.security import create_token
from backend.app.main import app
from backend.app.schemas.api import ChatResponse


@pytest.fixture(autouse=True)
def _clean_inbox():
    chat._RESPONSE_INBOX.clear()
    yield
    chat._RESPONSE_INBOX.clear()


def _auth(user_id):
    return {"Authorization": f"Bearer {create_token(user_id)}"}


def test_result_requires_sign_in():
    chat._inbox_store("t-abc12345", ChatResponse(response="secret plan"), user_id=1)
    client = TestClient(app)
    assert client.get("/api/chat/result/t-abc12345").status_code == 401
    assert client.get("/api/chat/result/t-abc12345", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_owner_gets_reply_other_user_gets_404():
    chat._inbox_store("t-abc12345", ChatResponse(response="secret plan"), user_id=1)
    client = TestClient(app)
    mine = client.get("/api/chat/result/t-abc12345", headers=_auth(1))
    assert mine.status_code == 200 and mine.json()["response"] == "secret plan"
    theirs = client.get("/api/chat/result/t-abc12345", headers=_auth(2))
    assert theirs.status_code == 404
    assert "secret plan" not in theirs.text


def test_same_task_id_cannot_overwrite_another_users_reply():
    chat._inbox_store("t-abc12345", ChatResponse(response="user one reply"), user_id=1)
    chat._inbox_store("t-abc12345", ChatResponse(response="attacker reply"), user_id=2)
    client = TestClient(app)
    assert client.get("/api/chat/result/t-abc12345", headers=_auth(1)).json()["response"] == "user one reply"
    assert client.get("/api/chat/result/t-abc12345", headers=_auth(2)).json()["response"] == "attacker reply"


# --- Failed runs are filed in the inbox too --------------------------------
from types import SimpleNamespace

from backend.app.api.deps import get_user_repository
from backend.app.db.session import get_db
from backend.app.llm.client import LLMUnavailableError


class _Repo:
    async def get_by_id(self, user_id):
        return SimpleNamespace(
            id=user_id, name="T", age=30, sex="male", height_cm=170, weight_kg=70,
            activity_level="low", goal="maintain", diet_type="veg", dislikes=[],
            allergies=[], min_safe_calories=1500, max_safe_calories=2500,
            suggested_target_calories=2000,
        )


async def _no_db():
    yield None


@pytest.fixture
def chat_client():
    app.dependency_overrides[get_user_repository] = lambda: _Repo()
    app.dependency_overrides[get_db] = _no_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _post(client, task_id, pending=None):
    return client.post(
        "/api/chat",
        headers=_auth(1),
        json={"user_id": 1, "message": "plan", "task_id": task_id, "pending_workflow": pending},
    )


def test_crashed_run_is_filed_in_inbox_and_hides_raw_error(chat_client, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("secret sql detail")
    monkeypatch.setattr(chat, "run_meal_planner", boom)

    res = _post(chat_client, "t-crash0001", pending={"workflow_type": "new_plan"})
    assert res.status_code == 500
    assert "secret sql detail" not in res.text

    # A reloaded page now finds the error straight away.
    got = chat_client.get("/api/chat/result/t-crash0001", headers=_auth(1))
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "failed"
    assert body["response"] == res.json()["detail"]
    assert "secret sql detail" not in got.text
    assert body["pending_workflow"] == {"workflow_type": "new_plan"}
    # ...and nobody else can read it.
    assert chat_client.get("/api/chat/result/t-crash0001", headers=_auth(2)).status_code == 404


def test_ai_unavailable_is_filed_in_inbox(chat_client, monkeypatch):
    async def down(**kwargs):
        raise LLMUnavailableError("quota exhausted for model x")
    monkeypatch.setattr(chat, "run_meal_planner", down)

    res = _post(chat_client, "t-down0001")
    assert res.status_code == 503
    got = chat_client.get("/api/chat/result/t-down0001", headers=_auth(1)).json()
    assert got["status"] == "failed"
    assert "busy" in got["response"]
    assert "quota" not in got["response"]


def test_failure_without_task_id_stores_nothing(chat_client, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("x")
    monkeypatch.setattr(chat, "run_meal_planner", boom)
    res = chat_client.post("/api/chat", headers=_auth(1), json={"user_id": 1, "message": "plan"})
    assert res.status_code == 500
    assert chat._RESPONSE_INBOX == {}
