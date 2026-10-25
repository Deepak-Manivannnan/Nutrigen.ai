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

from backend.app.api.deps import get_refresh_token_repository, get_user_repository
from backend.app.api.security import create_token
from backend.app.db.repositories.refresh_tokens import RefreshTokenRepository
from backend.app.db.repositories.users import UserRepository

router = APIRouter(prefix="/auth", tags=["Auth"])

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

    has_profile = bool(user.age and user.height_cm and user.weight_kg)
    profile_data = {
        "id": user.id,
        "name": user.name,
        "email": user.email or email,
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
        email=user.email or email,
        has_profile=has_profile,
        profile=profile_data,
        token=create_token(user.id),
        refresh_token=await refresh_repo.issue(user.id),
    )

@router.post("/register", response_model=AuthUserResponse)
async def register(
    payload: RegisterRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    refresh_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
):
    name = payload.name.strip()
    email = payload.email.strip().lower()

    # Plain 400s with readable text: the sign-up page shows `detail` as-is.
    if not name or len(name) > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please enter your name (up to 100 characters).")
    if len(email) > 255 or not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please enter a valid email address.")
    if len(payload.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )
    if len(payload.password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at most {MAX_PASSWORD_LENGTH} characters.",
        )

    existing = await user_repo.get_by_email(email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An account with this email already exists.")

    password_hash = await asyncio.to_thread(hash_password, payload.password)
    user = await user_repo.create_account(
        name=name,
        email=email,
        password_hash=password_hash
    )

    return AuthUserResponse(
        id=user.id,
        name=user.name,
        email=user.email or email,
        has_profile=False,
        profile=None,
        token=create_token(user.id),
        refresh_token=await refresh_repo.issue(user.id),
    )

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
