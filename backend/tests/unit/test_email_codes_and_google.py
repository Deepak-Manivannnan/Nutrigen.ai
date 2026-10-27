"""
Email codes, verified sign-up, forgot password and Google sign-in.

The code repository runs against an in-memory SQLite copy of the tables, so
nothing here touches the live database. Brevo and Google are faked.
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.api.routes import auth
from backend.app.db.models import EmailCode
from backend.app.db.repositories import email_codes
from backend.app.db.repositories.email_codes import CodeError, EmailCodeRepository


# ----------------------------------------------------------------- fixtures
@pytest.fixture
async def codes():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(EmailCode.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield EmailCodeRepository(session)
    await engine.dispose()


def _age(repo, seconds):
    """Pretend every stored code was created `seconds` ago."""
    async def run():
        for r in await repo._rows("a@b.co", "signup"):
            r.created_at -= timedelta(seconds=seconds)
            r.expires_at -= timedelta(seconds=seconds)
            if r.verified_at:
                r.verified_at -= timedelta(seconds=seconds)
        await repo.session.commit()
    return run()


# ----------------------------------------------------------- code repository
async def test_right_code_gives_a_ticket_that_works_once(codes):
    code = await codes.create("a@b.co", "signup")
    assert len(code) == 6 and code.isdigit()
    ticket = await codes.verify("a@b.co", "signup", code)
    await codes.consume_ticket("a@b.co", "signup", ticket)
    with pytest.raises(CodeError):
        await codes.consume_ticket("a@b.co", "signup", ticket)


async def test_code_is_not_stored_in_plain_text(codes):
    code = await codes.create("a@b.co", "signup")
    row = (await codes._rows("a@b.co", "signup"))[0]
    assert code not in row.code_hash


async def test_wrong_code_counts_down_and_locks_after_five(codes):
    code = await codes.create("a@b.co", "signup")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(CodeError):
            await codes.verify("a@b.co", "signup", wrong)
    with pytest.raises(CodeError) as err:
        await codes.verify("a@b.co", "signup", code)  # even the right one now
    assert "new code" in err.value.reason


async def test_expired_code_is_refused(codes):
    code = await codes.create("a@b.co", "signup")
    await _age(codes, 11 * 60)
    with pytest.raises(CodeError) as err:
        await codes.verify("a@b.co", "signup", code)
    assert "expired" in err.value.reason


async def test_resend_needs_a_short_wait_and_old_code_stops_working(codes):
    first = await codes.create("a@b.co", "signup")
    with pytest.raises(CodeError) as err:
        await codes.create("a@b.co", "signup")
    assert err.value.status == 429
    await _age(codes, 61)
    second = await codes.create("a@b.co", "signup")
    if first != second:
        with pytest.raises(CodeError):
            await codes.verify("a@b.co", "signup", first)
    assert await codes.verify("a@b.co", "signup", second)


async def test_a_ticket_for_one_email_does_not_work_for_another(codes):
    ticket = await codes.verify("a@b.co", "signup", await codes.create("a@b.co", "signup"))
    with pytest.raises(CodeError):
        await codes.consume_ticket("other@b.co", "signup", ticket)


async def test_signup_ticket_cannot_reset_a_password(codes):
    ticket = await codes.verify("a@b.co", "signup", await codes.create("a@b.co", "signup"))
    with pytest.raises(CodeError):
        await codes.consume_ticket("a@b.co", "reset", ticket)


# ---------------------------------------------------------------- endpoints
class FakeUser(SimpleNamespace):
    pass


def _user(**kw):
    base = dict(id=7, name="Asha", email="a@b.co", password_hash="$argon2-old", email_verified=None,
                google_id=None, age=None, height_cm=None, weight_kg=None, sex=None, activity_level=None,
                goal=None, diet_type=None, dislikes=[], allergies=[], created_at=None, updated_at=None)
    base.update(kw)
    return FakeUser(**base)


class Users:
    def __init__(self, user=None):
        self.user = user
        self.created = None
        self.session = SimpleNamespace(commit=self._noop)

    async def _noop(self):
        pass

    async def get_by_email(self, email):
        return self.user if self.user and self.user.email == email else None

    async def get_by_google_id(self, gid):
        return self.user if self.user and self.user.google_id == gid else None

    async def create_account(self, name, email, password_hash, email_verified=None, google_id=None):
        self.created = _user(id=8, name=name, email=email, password_hash=password_hash,
                             email_verified=email_verified, google_id=google_id)
        return self.created

    async def link_google(self, user, gid):
        if not user.email_verified:
            user.password_hash = None
        user.google_id, user.email_verified = gid, True

    async def set_password_hash(self, user, h):
        user.password_hash = h


class Refresh:
    async def issue(self, uid):
        return "refresh"


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def fake_send(to, code, purpose):
        out.append((to, code, purpose))
    monkeypatch.setattr(auth, "send_code_email", fake_send)
    return out


async def test_full_signup_with_a_code(codes, sent):
    users = Users()
    await auth.signup_send_code(auth.SendCodeRequest(email="A@B.co", name="Asha"), users, codes)
    (to, code, purpose), = sent
    assert (to, purpose) == ("a@b.co", "signup")
    ticket = (await auth.signup_verify_code(auth.VerifyCodeRequest(email="a@b.co", code=code), codes))["verification_token"]
    res = await auth.register(auth.RegisterRequest(name="Asha", email="a@b.co", password="longenough1",
                                                   verification_token=ticket), users, Refresh(), codes)
    assert res.token and users.created.email_verified is True
    assert users.created.password_hash.startswith("$argon2")


async def test_register_without_a_verified_email_is_refused(codes):
    with pytest.raises(HTTPException) as err:
        await auth.register(auth.RegisterRequest(name="Asha", email="a@b.co", password="longenough1",
                                                 verification_token="made-up"), Users(), Refresh(), codes)
    assert err.value.status_code == 400


async def test_no_code_is_sent_for_an_email_that_already_has_an_account(codes, sent):
    with pytest.raises(HTTPException):
        await auth.signup_send_code(auth.SendCodeRequest(email="a@b.co"), Users(_user()), codes)
    assert sent == []


async def test_forgot_password_gives_the_same_answer_for_unknown_emails(codes, sent):
    known = await auth.password_send_code(auth.SendCodeRequest(email="a@b.co"), Users(_user()), codes)
    unknown = await auth.password_send_code(auth.SendCodeRequest(email="x@b.co"), Users(_user()), codes)
    assert known["message"].replace("a@b.co", "") == unknown["message"].replace("x@b.co", "")
    assert [s[0] for s in sent] == ["a@b.co"]


async def test_reset_password_with_a_code(codes, sent):
    user = _user(password_hash=None, google_id="g-1", email_verified=True)
    users = Users(user)
    await auth.password_send_code(auth.SendCodeRequest(email="a@b.co"), users, codes)
    ticket = (await auth.password_verify_code(auth.VerifyCodeRequest(email="a@b.co", code=sent[0][1]), codes))["verification_token"]
    res = await auth.password_reset(auth.ResetPasswordRequest(email="a@b.co", verification_token=ticket,
                                                              password="newpassword1"), users, Refresh(), codes)
    assert res.token and user.password_hash.startswith("$argon2")


async def test_email_failure_gives_a_friendly_error(codes, monkeypatch):
    async def broken(*a):
        raise auth.EmailSendError("down")
    monkeypatch.setattr(auth, "send_code_email", broken)
    with pytest.raises(HTTPException) as err:
        await auth.signup_send_code(auth.SendCodeRequest(email="a@b.co"), Users(), codes)
    assert err.value.status_code == 503
    # The unsent code doesn't block an immediate retry.
    monkeypatch.setattr(auth, "send_code_email", lambda *a: _ok())
    await auth.signup_send_code(auth.SendCodeRequest(email="a@b.co"), Users(), codes)


async def _ok():
    return None


# ------------------------------------------------------------- Google sign-in
@pytest.fixture
def google(monkeypatch):
    monkeypatch.setattr(auth.settings, "google_client_id", "test-client.apps.googleusercontent.com")
    info = {"sub": "g-123", "email": "a@b.co", "email_verified": True, "name": "Deepak"}
    monkeypatch.setattr(auth, "verify_google_credential", lambda cred: info)
    return info


async def test_google_creates_a_new_account_with_googles_name(google):
    users = Users()
    res = await auth.google_sign_in(auth.GoogleSignInRequest(credential="x"), users, Refresh())
    assert res.name == "Deepak" and users.created.google_id == "g-123"
    assert users.created.password_hash is None and res.has_profile is False


async def test_google_keeps_the_name_the_user_changed(google):
    users = Users(_user(name="Deepak One", google_id="g-123", email_verified=True))
    res = await auth.google_sign_in(auth.GoogleSignInRequest(credential="x"), users, Refresh())
    assert res.name == "Deepak One" and users.created is None


async def test_google_links_to_an_existing_verified_email_account(google):
    user = _user(email_verified=True, password_hash="$argon2-keep")
    await auth.google_sign_in(auth.GoogleSignInRequest(credential="x"), Users(user), Refresh())
    assert user.google_id == "g-123" and user.password_hash == "$argon2-keep"


async def test_google_on_an_old_unverified_account_removes_the_old_password(google):
    user = _user(email_verified=None, password_hash="$argon2-squatter")
    await auth.google_sign_in(auth.GoogleSignInRequest(credential="x"), Users(user), Refresh())
    assert user.password_hash is None and user.email_verified is True


async def test_google_with_an_unverified_email_is_refused(google):
    google["email_verified"] = False
    with pytest.raises(HTTPException) as err:
        await auth.google_sign_in(auth.GoogleSignInRequest(credential="x"), Users(), Refresh())
    assert err.value.status_code == 401


async def test_an_invalid_google_token_is_refused(monkeypatch):
    monkeypatch.setattr(auth.settings, "google_client_id", "test-client")

    def bad(cred):
        raise ValueError("wrong audience")
    monkeypatch.setattr(auth, "verify_google_credential", bad)
    with pytest.raises(HTTPException) as err:
        await auth.google_sign_in(auth.GoogleSignInRequest(credential="forged"), Users(), Refresh())
    assert err.value.status_code == 401


def test_real_google_check_rejects_a_forged_token(monkeypatch):
    monkeypatch.setattr(auth.settings, "google_client_id", "test-client.apps.googleusercontent.com")
    with pytest.raises(Exception):
        auth.verify_google_credential("not.a.real.token")


async def test_google_only_account_gets_the_generic_login_error():
    user = _user(password_hash=None, google_id="g-1", email_verified=True)
    with pytest.raises(HTTPException) as err:
        await auth.login(auth.LoginRequest(email="g-only@b.co", password="whatever1"),
                         Users(_user(email="g-only@b.co", password_hash=None, google_id="g-1")), Refresh())
    # Same answer as a wrong password: it must not reveal the account uses Google.
    assert err.value.detail == auth.INVALID_CREDENTIALS
    assert "Google" not in err.value.detail
    auth._failed_logins.pop("g-only@b.co", None)


# ------------------------------------------------- Google access-token button
class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def _fake_google_http(monkeypatch, tokeninfo, userinfo):
    import httpx

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            return tokeninfo if "tokeninfo" in url else userinfo
    monkeypatch.setattr(httpx, "AsyncClient", Client)


async def test_google_access_token_signs_up_a_new_user(monkeypatch):
    monkeypatch.setattr(auth.settings, "google_client_id", "my-app")
    _fake_google_http(monkeypatch,
                      FakeResp(200, {"aud": "my-app", "sub": "g-9"}),
                      FakeResp(200, {"sub": "g-9", "email": "New@B.co", "email_verified": True, "name": "Ravi"}))
    users = Users()
    res = await auth.google_sign_in(auth.GoogleSignInRequest(access_token="tok"), users, Refresh())
    assert res.name == "Ravi" and users.created.email == "new@b.co" and users.created.google_id == "g-9"


async def test_google_access_token_issued_to_another_app_is_refused(monkeypatch):
    monkeypatch.setattr(auth.settings, "google_client_id", "my-app")
    _fake_google_http(monkeypatch,
                      FakeResp(200, {"aud": "someone-elses-app", "azp": "someone-elses-app", "sub": "g-9"}),
                      FakeResp(200, {"sub": "g-9", "email": "a@b.co", "email_verified": True}))
    with pytest.raises(HTTPException) as err:
        await auth.google_sign_in(auth.GoogleSignInRequest(access_token="stolen"), Users(), Refresh())
    assert err.value.status_code == 401


async def test_google_access_token_with_unverified_email_is_refused(monkeypatch):
    monkeypatch.setattr(auth.settings, "google_client_id", "my-app")
    _fake_google_http(monkeypatch,
                      FakeResp(200, {"aud": "my-app", "sub": "g-9"}),
                      FakeResp(200, {"sub": "g-9", "email": "a@b.co", "email_verified": "false"}))
    with pytest.raises(HTTPException) as err:
        await auth.google_sign_in(auth.GoogleSignInRequest(access_token="tok"), Users(), Refresh())
    assert err.value.status_code == 401


async def test_an_expired_google_access_token_is_refused(monkeypatch):
    monkeypatch.setattr(auth.settings, "google_client_id", "my-app")
    _fake_google_http(monkeypatch, FakeResp(400, {"error": "invalid_token"}), FakeResp(401, {}))
    with pytest.raises(HTTPException) as err:
        await auth.google_sign_in(auth.GoogleSignInRequest(access_token="old"), Users(), Refresh())
    assert err.value.status_code == 401


async def test_code_sent_message_names_the_email_and_is_the_same_for_unknown_emails(codes, sent):
    known = await auth.password_send_code(auth.SendCodeRequest(email="a@b.co"), Users(_user()), codes)
    unknown = await auth.password_send_code(auth.SendCodeRequest(email="x@b.co"), Users(_user()), codes)
    assert known["message"] == "Check your inbox. We've sent a code to a@b.co."
    assert unknown["message"] == "Check your inbox. We've sent a code to x@b.co."
    assert "If an account" not in known["message"]
