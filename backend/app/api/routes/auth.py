import asyncio
import hashlib
import hmac
import re
import time
from typing import Any, Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.app.api.deps import get_email_code_repository, get_refresh_token_repository, get_user_repository
from backend.app.config import settings
from backend.app.db.repositories.email_codes import CodeError, EmailCodeRepository
from backend.app.tools.email_sender import EmailSendError, send_code_email
from backend.app.observability.logging import get_logger
from backend.app.api.security import create_token
from backend.app.db.repositories.refresh_tokens import RefreshTokenRepository
from backend.app.db.repositories.users import UserRepository

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = get_logger("api.auth")

# --- Password rules (enforced here, not only in the browser) -----------------
MIN_PASSWORD_LENGTH = 8
# Upper bound so nobody can make the server hash a multi-megabyte "password".
MAX_PASSWORD_LENGTH = 128
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# --- Failed-login lockout ----------------------------------------------------
# After MAX_FAILED_LOGINS wrong attempts for one email, further attempts are
# refused for LOCKOUT_SECONDS. Kept in memory: it resets on a server restart
# and is per server process, which is fine as a brake on password guessing.
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
_MAX_TRACKED_EMAILS = 10_000
_failed_logins: Dict[str, list] = {}

# Same message for "no such email" and "wrong password", so the response never
# tells an attacker which emails have accounts.
INVALID_CREDENTIALS = "Invalid email or password."


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
    # One-time ticket from /auth/signup/verify-code: proves the email is real.
    verification_token: str = ""


class SendCodeRequest(BaseModel):
    email: str
    name: str = ""


class VerifyCodeRequest(BaseModel):
    email: str
    code: str


class ResetPasswordRequest(BaseModel):
    email: str
    verification_token: str
    password: str


class GoogleSignInRequest(BaseModel):
    credential: str


class AuthUserResponse(BaseModel):
    id: int
    name: str
    email: str
    has_profile: bool = True
    profile: Optional[Dict[str, Any]] = None
    token: str = ""
    # Long-lived key that /auth/refresh trades for a new short-lived token.
    refresh_token: str = ""


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    token: str
    # Empty when the key was already renewed a moment ago by another tab; the
    # caller then keeps the one it has stored.
    refresh_token: str = ""


# --- Password hashing --------------------------------------------------------
# Argon2id: deliberately slow and salted per password, so a leaked database
# cannot be cracked quickly and equal passwords do not produce equal hashes.
_hasher = PasswordHasher()
# Hash checked when the email does not exist, so a missing account takes about
# as long to answer as a wrong password.
_DUMMY_HASH = _hasher.hash("nutrigen-timing-placeholder")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _legacy_sha256_matches(stored_hash: str, input_password: str) -> bool:
    """Accounts created before Argon2 hold a plain or salted SHA-256 hash.

    These still verify so existing users can sign in; they are upgraded to
    Argon2 right after a successful login (see `needs_rehash`).
    """
    candidates = [hashlib.sha256(input_password.encode("utf-8")).hexdigest()]
    expected = stored_hash
    if "$" in stored_hash:
        salt, expected = stored_hash.split("$", 1)
        candidates = [
            hashlib.sha256((salt + input_password).encode("utf-8")).hexdigest(),
            hashlib.sha256((input_password + salt).encode("utf-8")).hexdigest(),
        ]
    return any(hmac.compare_digest(c, expected) for c in candidates)


def verify_password(stored_hash: str, input_password: str) -> bool:
    # An account with no stored hash has no password. It must never be
    # treated as "any password is fine".
    if not stored_hash:
        return False
    if stored_hash.startswith("$argon2"):
        try:
            return _hasher.verify(stored_hash, input_password)
        except (VerificationError, InvalidHashError):
            return False
    return _legacy_sha256_matches(stored_hash, input_password)


def needs_rehash(stored_hash: str) -> bool:
    if not stored_hash.startswith("$argon2"):
        return True
    return _hasher.check_needs_rehash(stored_hash)


# --- Shared sign-in response -------------------------------------------------
async def _auth_response(user, refresh_repo: RefreshTokenRepository) -> AuthUserResponse:
    has_profile = bool(user.age and user.height_cm and user.weight_kg)
    profile_data = {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "age": user.age,
        "sex": user.sex,
        "height_cm": user.height_cm,
        "weight_kg": user.weight_kg,
        "activity_level": user.activity_level,
        "goal": user.goal,
        "diet_type": user.diet_type,
        "dislikes": user.dislikes or [],
        "allergies": [a.allergen for a in getattr(user, "allergies", [])] if getattr(user, "allergies", None) else [],
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    } if has_profile else None
    return AuthUserResponse(
        id=user.id,
        name=user.name,
        email=user.email or "",
        has_profile=has_profile,
        profile=profile_data,
        token=create_token(user.id),
        refresh_token=await refresh_repo.issue(user.id),
    )


def _clean_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 255 or not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please enter a valid email address.")
    return email


def _check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")


def _code_http_error(err: CodeError) -> HTTPException:
    return HTTPException(status_code=err.status, detail=err.reason)


async def _send_code(codes: EmailCodeRepository, email: str, purpose: str) -> None:
    try:
        code = await codes.create(email, purpose)
    except CodeError as err:
        raise _code_http_error(err)
    try:
        await send_code_email(email, code, purpose)
    except EmailSendError as err:
        logger.warning(f"Could not send {purpose} code email: {err}")
        # The user never got this code, so it must not block a retry.
        await codes.discard_latest(email, purpose)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="We couldn't send the code right now. Please try again in a minute.")


# --- Lockout helpers ---------------------------------------------------------
def _recent_failures(email: str) -> list:
    cutoff = time.time() - LOCKOUT_SECONDS
    recent = [t for t in _failed_logins.get(email, []) if t > cutoff]
    if recent:
        _failed_logins[email] = recent
    else:
        _failed_logins.pop(email, None)
    return recent


def _check_not_locked(email: str) -> None:
    recent = _recent_failures(email)
    if len(recent) >= MAX_FAILED_LOGINS:
        wait_minutes = max(1, int((recent[0] + LOCKOUT_SECONDS - time.time()) // 60) + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed sign-in attempts. Please try again in about {wait_minutes} minute(s).",
        )


def _record_failure(email: str) -> None:
    if email not in _failed_logins and len(_failed_logins) >= _MAX_TRACKED_EMAILS:
        # Drop the oldest tracked email so memory stays bounded.
        oldest = min(_failed_logins, key=lambda k: _failed_logins[k][-1])
        _failed_logins.pop(oldest, None)
    _failed_logins.setdefault(email, []).append(time.time())


@router.post("/login", response_model=AuthUserResponse)
async def login(
    payload: LoginRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    refresh_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
):
    email = payload.email.strip().lower()
    _check_not_locked(email)

    user = await user_repo.get_by_email(email)

    # Hashing is CPU-heavy by design; run it off the event loop so one login
    # does not freeze every other request.
    stored_hash = (user.password_hash or "") if user else ""
    if user and stored_hash:
        password_ok = await asyncio.to_thread(verify_password, stored_hash, payload.password)
    else:
        await asyncio.to_thread(verify_password, _DUMMY_HASH, payload.password)
        password_ok = False

    if user and not stored_hash and getattr(user, "google_id", None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account uses Google sign-in. Please use Continue with Google, "
                   "or set a password with Forgot password.",
        )

    if not user or not password_ok:
        _record_failure(email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    _failed_logins.pop(email, None)

    # Quietly upgrade old SHA-256 hashes to Argon2 now that we hold the
    # correct plain password.
    if needs_rehash(stored_hash):
        new_hash = await asyncio.to_thread(hash_password, payload.password)
        await user_repo.set_password_hash(user, new_hash)

    return await _auth_response(user, refresh_repo)

@router.post("/register", response_model=AuthUserResponse)
async def register(
    payload: RegisterRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    refresh_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
    codes: EmailCodeRepository = Depends(get_email_code_repository),
):
    name = payload.name.strip()
    email = payload.email.strip().lower()

    # Plain 400s with readable text: the sign-up page shows `detail` as-is.
    if not name or len(name) > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please enter your name (up to 100 characters).")
    email = _clean_email(email)
    _check_password_rules(payload.password)

    existing = await user_repo.get_by_email(email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An account with this email already exists.")

    # The email must have been proven with a code first (/auth/signup/*).
    try:
        await codes.consume_ticket(email, "signup", payload.verification_token)
    except CodeError as err:
        raise _code_http_error(err)

    password_hash = await asyncio.to_thread(hash_password, payload.password)
    user = await user_repo.create_account(
        name=name,
        email=email,
        password_hash=password_hash,
        email_verified=True,
    )
    return await _auth_response(user, refresh_repo)


# --- Sign-up email verification ---------------------------------------------
@router.post("/signup/send-code")
async def signup_send_code(
    payload: SendCodeRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    codes: EmailCodeRepository = Depends(get_email_code_repository),
):
    email = _clean_email(payload.email)
    if await user_repo.get_by_email(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="An account with this email already exists. Please sign in instead.")
    await _send_code(codes, email, "signup")
    return {"message": f"We sent a 6-digit code to {email}.",
            "resend_after": settings.email_code_resend_seconds}


@router.post("/signup/verify-code")
async def signup_verify_code(
    payload: VerifyCodeRequest,
    codes: EmailCodeRepository = Depends(get_email_code_repository),
):
    email = _clean_email(payload.email)
    try:
        ticket = await codes.verify(email, "signup", payload.code)
    except CodeError as err:
        raise _code_http_error(err)
    return {"verification_token": ticket}


# --- Forgot password ----------------------------------------------------------
_RESET_SENT = "If an account exists for this email, we sent a 6-digit code to it."


@router.post("/password/send-code")
async def password_send_code(
    payload: SendCodeRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    codes: EmailCodeRepository = Depends(get_email_code_repository),
):
    email = _clean_email(payload.email)
    # Same answer whether or not the account exists, so this can't be used to
    # find out who has an account.
    if await user_repo.get_by_email(email):
        await _send_code(codes, email, "reset")
    return {"message": _RESET_SENT, "resend_after": settings.email_code_resend_seconds}


@router.post("/password/verify-code")
async def password_verify_code(
    payload: VerifyCodeRequest,
    codes: EmailCodeRepository = Depends(get_email_code_repository),
):
    email = _clean_email(payload.email)
    try:
        ticket = await codes.verify(email, "reset", payload.code)
    except CodeError as err:
        raise _code_http_error(err)
    return {"verification_token": ticket}


@router.post("/password/reset", response_model=AuthUserResponse)
async def password_reset(
    payload: ResetPasswordRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    refresh_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
    codes: EmailCodeRepository = Depends(get_email_code_repository),
):
    email = _clean_email(payload.email)
    _check_password_rules(payload.password)
    user = await user_repo.get_by_email(email)
    try:
        await codes.consume_ticket(email, "reset", payload.verification_token)
    except CodeError as err:
        raise _code_http_error(err)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please verify your email again.")
    new_hash = await asyncio.to_thread(hash_password, payload.password)
    await user_repo.set_password_hash(user, new_hash)
    # Resetting through an emailed code proves the email belongs to them.
    if not user.email_verified:
        user.email_verified = True
        await user_repo.session.commit()
    _failed_logins.pop(email, None)
    return await _auth_response(user, refresh_repo)


# --- Google sign-in -----------------------------------------------------------
def verify_google_credential(credential: str) -> dict:
    """Check Google's signed ID token: signature, expiry, issuer and audience.

    Raises ValueError when the token is not valid for this app.
    """
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
    return id_token.verify_oauth2_token(credential, google_requests.Request(), settings.google_client_id)


@router.post("/google", response_model=AuthUserResponse)
async def google_sign_in(
    payload: GoogleSignInRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    refresh_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
):
    """Sign in or sign up with Google. One endpoint for both."""
    if not settings.google_client_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Google sign-in is not available right now.")
    try:
        info = await asyncio.to_thread(verify_google_credential, payload.credential)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Google sign-in failed. Please try again.")

    google_id = str(info.get("sub") or "")
    email = str(info.get("email") or "").strip().lower()
    if not google_id or not email or info.get("email_verified") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Your Google account's email isn't verified, so we can't use it to sign in.")

    user = await user_repo.get_by_google_id(google_id)
    if user is None:
        user = await user_repo.get_by_email(email)
        if user is not None:
            # Same verified email: same account, now also reachable via Google.
            await user_repo.link_google(user, google_id)
        else:
            # New account. The name is copied from Google once; after that the
            # app's own (editable) name is used and never overwritten.
            name = (str(info.get("name") or "").strip() or email.split("@", 1)[0])[:100]
            user = await user_repo.create_account(
                name=name, email=email, password_hash=None,
                email_verified=True, google_id=google_id,
            )
    return await _auth_response(user, refresh_repo)

@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    payload: RefreshRequest,
    refresh_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
):
    """Trade the long-lived key for a new short-lived token and a new long-lived key."""
    result = await refresh_repo.rotate(payload.refresh_token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
        )
    return RefreshResponse(
        token=create_token(result.user_id),
        refresh_token=result.refresh_token or "",
    )


@router.post("/logout")
async def logout(
    payload: RefreshRequest,
    refresh_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
):
    """Sign out for good on this device: the long-lived key stops working."""
    await refresh_repo.revoke(payload.refresh_token)
    return {"message": "Signed out"}
