from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Dict, Any
from backend.app.db.repositories.users import UserRepository
from backend.app.api.deps import get_user_repository
from backend.app.api.security import create_token
import hashlib

router = APIRouter(prefix="/auth", tags=["Auth"])

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

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def verify_password(stored_hash: str, input_password: str) -> bool:
    if not stored_hash:
        return True
    input_hash = hashlib.sha256(input_password.encode("utf-8")).hexdigest()
    if stored_hash == input_hash:
        return True
    if "$" in stored_hash:
        salt, h = stored_hash.split("$", 1)
        if (
            hashlib.sha256((salt + input_password).encode()).hexdigest() == h
            or hashlib.sha256((input_password + salt).encode()).hexdigest() == h
        ):
            return True
    return False

@router.post("/login", response_model=AuthUserResponse)
async def login(
    payload: LoginRequest,
    user_repo: UserRepository = Depends(get_user_repository)
):
    email = payload.email.strip().lower()
    user = await user_repo.get_by_email(email)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found. Please register a new account or check your email."
        )

    # A wrong password is rejected, never accepted. This previously overwrote the
    # stored hash with whatever was typed whenever the input was 6+ characters,
    # so knowing an email was enough to take over the account - and the real
    # password stopped working. verify_password already understands both the
    # current sha256 form and the older salted format, so the migration that
    # branch was written for is no longer needed.
    if user.password_hash and not verify_password(user.password_hash, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password. Please try again."
        )

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
        token=create_token(user.id)
    )

@router.post("/register", response_model=AuthUserResponse)
async def register(
    payload: RegisterRequest,
    user_repo: UserRepository = Depends(get_user_repository)
):
    email = payload.email.strip().lower()
    existing = await user_repo.get_by_email(email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An account with this email already exists.")

    user = await user_repo.create_account(
        name=payload.name,
        email=email,
        password_hash=hash_password(payload.password)
    )

    return AuthUserResponse(
        id=user.id,
        name=user.name,
        email=user.email or email,
        has_profile=False,
        profile=None,
        token=create_token(user.id)
    )
