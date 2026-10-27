"""Password hashing, sign-in lockout and register validation (no database or AI)."""
import hashlib

import pytest
from fastapi import HTTPException

from backend.app.api.routes import auth


class FakeUser:
    def __init__(self, password_hash):
        self.id = 1
        self.name = "Test"
        self.email = "t@example.com"
        self.password_hash = password_hash
        self.age = self.height_cm = self.weight_kg = None
        self.sex = self.activity_level = self.goal = self.diet_type = None
        self.dislikes = []
        self.allergies = []
        self.created_at = self.updated_at = None


class FakeRepo:
    def __init__(self, user=None):
        self.user = user
        self.created = None

    async def get_by_email(self, email):
        return self.user

    async def set_password_hash(self, user, new_hash):
        user.password_hash = new_hash

    async def create_account(self, name, email, password_hash, **kw):
        self.created = (name, email, password_hash)
        return FakeUser(password_hash)


class FakeRefreshRepo:
    async def issue(self, user_id):
        return "refresh-key"


def _login(payload, repo):
    return auth.login(payload, repo, FakeRefreshRepo())


class FakeCodes:
    async def consume_ticket(self, email, purpose, ticket):
        return None


def _register(payload, repo):
    return auth.register(payload, repo, FakeRefreshRepo(), FakeCodes())


@pytest.fixture(autouse=True)
def _clear_lockouts():
    auth._failed_logins.clear()
    yield
    auth._failed_logins.clear()


def test_new_hashes_are_argon2_and_salted():
    a, b = auth.hash_password("correct horse"), auth.hash_password("correct horse")
    assert a.startswith("$argon2") and a != b
    assert auth.verify_password(a, "correct horse")
    assert not auth.verify_password(a, "wrong")


def test_empty_hash_never_verifies():
    assert auth.verify_password("", "anything") is False
    assert auth.verify_password("", "") is False


def test_legacy_sha256_still_verifies_and_is_flagged_for_upgrade():
    legacy = hashlib.sha256(b"oldpass1").hexdigest()
    assert auth.verify_password(legacy, "oldpass1")
    assert not auth.verify_password(legacy, "oldpass2")
    assert auth.needs_rehash(legacy)
    assert not auth.needs_rehash(auth.hash_password("x" * 10))


@pytest.mark.asyncio
async def test_login_upgrades_legacy_hash():
    user = FakeUser(hashlib.sha256(b"oldpass1").hexdigest())
    res = await _login(auth.LoginRequest(email="T@Example.com ", password="oldpass1"), FakeRepo(user))
    assert res.token
    assert user.password_hash.startswith("$argon2")


@pytest.mark.asyncio
async def test_wrong_password_and_unknown_email_give_same_message():
    user = FakeUser(auth.hash_password("rightpass1"))
    with pytest.raises(HTTPException) as wrong:
        await _login(auth.LoginRequest(email="t@example.com", password="nope"), FakeRepo(user))
    with pytest.raises(HTTPException) as unknown:
        await _login(auth.LoginRequest(email="x@example.com", password="nope"), FakeRepo(None))
    assert wrong.value.status_code == unknown.value.status_code == 401
    assert wrong.value.detail == unknown.value.detail == auth.INVALID_CREDENTIALS


@pytest.mark.asyncio
async def test_account_without_hash_cannot_log_in():
    with pytest.raises(HTTPException) as exc:
        await _login(auth.LoginRequest(email="t@example.com", password="anything"), FakeRepo(FakeUser(None)))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_lockout_after_repeated_failures_then_correct_password_blocked():
    user = FakeUser(auth.hash_password("rightpass1"))
    repo = FakeRepo(user)
    for _ in range(auth.MAX_FAILED_LOGINS):
        with pytest.raises(HTTPException) as exc:
            await _login(auth.LoginRequest(email="t@example.com", password="bad"), repo)
        assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as locked:
        await _login(auth.LoginRequest(email="t@example.com", password="rightpass1"), repo)
    assert locked.value.status_code == 429


@pytest.mark.asyncio
async def test_successful_login_clears_failures():
    user = FakeUser(auth.hash_password("rightpass1"))
    repo = FakeRepo(user)
    for _ in range(auth.MAX_FAILED_LOGINS - 1):
        with pytest.raises(HTTPException):
            await _login(auth.LoginRequest(email="t@example.com", password="bad"), repo)
    await _login(auth.LoginRequest(email="t@example.com", password="rightpass1"), repo)
    assert "t@example.com" not in auth._failed_logins


@pytest.mark.asyncio
@pytest.mark.parametrize("name,email,password,fragment", [
    ("A", "not-an-email", "longenough1", "valid email"),
    ("A", "a@b.co", "short", "at least 8"),
    ("A", "a@b.co", "x" * 129, "at most 128"),
    ("  ", "a@b.co", "longenough1", "name"),
])
async def test_register_rejects_bad_input(name, email, password, fragment):
    repo = FakeRepo(None)
    with pytest.raises(HTTPException) as exc:
        await _register(auth.RegisterRequest(name=name, email=email, password=password), repo)
    assert exc.value.status_code == 400
    assert fragment in exc.value.detail
    assert repo.created is None


@pytest.mark.asyncio
async def test_register_stores_argon2_hash():
    repo = FakeRepo(None)
    await _register(auth.RegisterRequest(name=" Asha ", email="A@B.co", password="longenough1"), repo)
    name, email, stored = repo.created
    assert (name, email) == ("Asha", "a@b.co")
    assert stored.startswith("$argon2")
