from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional
from backend.app.schemas.api import ProfileCreate, ProfileUpdate, ProfileResponse, CalorieRangeResponse
from backend.app.db.repositories.users import UserRepository
from backend.app.api.deps import get_user_repository, get_authenticated_user_id
from backend.app.guardrails.calorie_rules import calculate_mifflin_st_jeor
from backend.app.tools.profile_normalizer import normalize_food_restrictions

router = APIRouter(prefix="/profile", tags=["Profile"])

@router.post("", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_user_profile(
    profile: ProfileCreate,
    caller_id: int = Depends(get_authenticated_user_id),
    user_repo: UserRepository = Depends(get_user_repository)
):
    """Onboarding submit for the signed-in user.

    This used to insert a brand-new, credential-less user row, which meant an
    account created through /auth/register could never be completed: onboarding
    built a second user and the original stayed permanently profile-less. It now
    applies the submitted details to the caller's own account, and requires a
    token so unowned profiles cannot be created anonymously.
    """
    # LLM-based structured extraction & typo correction for food dislikes & allergies
    clean_d, clean_a = await normalize_food_restrictions(profile.dislikes, profile.allergies)
    profile.dislikes = clean_d
    profile.allergies = clean_a

    user = await user_repo.update(
        caller_id,
        ProfileUpdate(
            name=profile.name,
            age=profile.age,
            sex=profile.sex,
            height_cm=profile.height_cm,
            weight_kg=profile.weight_kg,
            activity_level=profile.activity_level,
            goal=profile.goal,
            diet_type=profile.diet_type,
            dislikes=profile.dislikes,
            allergies=profile.allergies,
        ),
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No profile found")

    allergies = [a.allergen for a in user.allergies]

    return ProfileResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        age=user.age,
        sex=user.sex,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        activity_level=user.activity_level,
        goal=user.goal,
        diet_type=user.diet_type,
        dislikes=user.dislikes or [],
        allergies=allergies,
        min_safe_calories=user.min_safe_calories,
        max_safe_calories=user.max_safe_calories,
        suggested_target_calories=user.suggested_target_calories,
        created_at=user.created_at,
        updated_at=user.updated_at
    )

@router.get("/me", response_model=ProfileResponse)
async def get_current_profile(
    caller_id: int = Depends(get_authenticated_user_id),
    user_repo: UserRepository = Depends(get_user_repository)
):
    """Always the signed-in user's own profile.

    This used to fall back to `get_first()`, returning whichever account was
    created first, to anyone who simply called it without an id.
    """
    user = await user_repo.get_by_id(caller_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No profile found")

    allergies = [a.allergen for a in user.allergies]
    return ProfileResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        age=user.age,
        sex=user.sex,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        activity_level=user.activity_level,
        goal=user.goal,
        diet_type=user.diet_type,
        dislikes=user.dislikes or [],
        allergies=allergies,
        min_safe_calories=user.min_safe_calories,
        max_safe_calories=user.max_safe_calories,
        suggested_target_calories=user.suggested_target_calories,
        created_at=user.created_at,
        updated_at=user.updated_at
    )

@router.get("/{user_id}", response_model=ProfileResponse)
async def get_user_profile(
    user_id: int,
    caller_id: int = Depends(get_authenticated_user_id),
    user_repo: UserRepository = Depends(get_user_repository)
):
    # A caller may only ever read their own profile; otherwise knowing a user id
    # is enough to read someone else's weight, diet and allergies.
    if user_id != caller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own profile.",
        )
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")

    allergies = [a.allergen for a in user.allergies]
    return ProfileResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        age=user.age,
        sex=user.sex,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        activity_level=user.activity_level,
        goal=user.goal,
        diet_type=user.diet_type,
        dislikes=user.dislikes or [],
        allergies=allergies,
        min_safe_calories=user.min_safe_calories,
        max_safe_calories=user.max_safe_calories,
        suggested_target_calories=user.suggested_target_calories,
        created_at=user.created_at,
        updated_at=user.updated_at
    )

@router.put("/{user_id}", response_model=ProfileResponse)
async def update_user_profile(
    user_id: int,
    updates: ProfileUpdate,
    caller_id: int = Depends(get_authenticated_user_id),
    user_repo: UserRepository = Depends(get_user_repository)
):
    # Without this check any caller could rewrite another account's profile.
    if user_id != caller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own profile.",
        )
    if updates.dislikes is not None or updates.allergies is not None:
        clean_d, clean_a = await normalize_food_restrictions(
            updates.dislikes or [],
            updates.allergies or []
        )
        if updates.dislikes is not None:
            updates.dislikes = clean_d
        if updates.allergies is not None:
            updates.allergies = clean_a

    user = await user_repo.update(user_id, updates)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")

    allergies = [a.allergen for a in user.allergies]
    return ProfileResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        age=user.age,
        sex=user.sex,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        activity_level=user.activity_level,
        goal=user.goal,
        diet_type=user.diet_type,
        dislikes=user.dislikes or [],
        allergies=allergies,
        min_safe_calories=user.min_safe_calories,
        max_safe_calories=user.max_safe_calories,
        suggested_target_calories=user.suggested_target_calories,
        created_at=user.created_at,
        updated_at=user.updated_at
    )

@router.get("/{user_id}/calorie-range", response_model=CalorieRangeResponse)
async def get_user_calorie_range(
    user_id: int,
    caller_id: int = Depends(get_authenticated_user_id),
    user_repo: UserRepository = Depends(get_user_repository)
):
    if user_id != caller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own calorie range.",
        )
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")

    # Return persisted calorie data if available, otherwise calculate
    if user.min_safe_calories is not None and user.max_safe_calories is not None and user.suggested_target_calories is not None:
        return CalorieRangeResponse(
            bmr=user.bmr if user.bmr is not None else 0,
            tdee=user.tdee if user.tdee is not None else 0,
            min_safe_calories=user.min_safe_calories,
            max_safe_calories=user.max_safe_calories,
            suggested_target=user.suggested_target_calories,
            is_eligible=True,
            message=None
        )

    # A registered-but-not-yet-onboarded user has no height, weight or age, so
    # the calculation below cannot run. Say so instead of raising a 500.
    if not (user.age and user.sex and user.height_cm and user.weight_kg and user.activity_level):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete your profile (age, sex, height, weight, activity level) to get a calorie range.",
        )

    # Fallback to calculation if not persisted yet
    result = calculate_mifflin_st_jeor(
        age=user.age,
        sex=user.sex,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        activity_level=user.activity_level,
        goal=user.goal or "maintain"
    )
    return CalorieRangeResponse(**result)
