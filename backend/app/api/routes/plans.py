from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from backend.app.db.models import Plan, ist_now
from backend.app.db.repositories.plans import PlanRepository
from backend.app.api.deps import get_plan_repository, get_authenticated_user_id
from backend.app.observability.logging import get_logger

logger = get_logger("api.plans")

router = APIRouter(prefix="/plans", tags=["Meal Plans"])

class PlanMealResponse(BaseModel):
    id: int
    day: int
    meal_type: str
    diet_type: Optional[str] = "veg"
    locked: bool
    recipe_name: str
    source: str
    ingredients: List[Dict[str, Any]]
    calories: float
    instructions: Optional[str] = None
    macros: Dict[str, Any]

class PlanRenameRequest(BaseModel):
    name: str

class PlanDetailResponse(BaseModel):
    id: int
    user_id: int
    name: Optional[str] = None
    days: int
    calorie_target: float
    diet_type: Optional[str] = "veg"
    cuisine: Optional[str] = None
    status: str
    version: int = 1
    grocery_list: Optional[Dict[str, Any]] = None
    created_at: datetime
    meals: List[PlanMealResponse] = []

def serialize_plan(plan: Plan) -> PlanDetailResponse:
    plan_diet = getattr(plan, "diet_type", "veg") or "veg"
    meals_data = [
        PlanMealResponse(
            id=m.id,
            day=m.day,
            meal_type=m.meal_type,
            diet_type=getattr(m, "diet_type", None) or plan_diet,
            locked=m.locked,
            recipe_name=m.recipe_name,
            source=m.source,
            ingredients=m.ingredients or [],
            calories=m.calories,
            instructions=getattr(m, "instructions", None),
            macros=m.macros or {}
        )
        for m in (plan.meals or [])
    ]
    return PlanDetailResponse(
        id=plan.id,
        user_id=plan.user_id,
        name=getattr(plan, "name", None) or f"{plan.days}-Day Meal Plan",
        days=plan.days,
        calorie_target=plan.calorie_target,
        diet_type=plan_diet,
        cuisine=getattr(plan, "cuisine", None),
        status=plan.status,
        version=getattr(plan, "version", 1) or 1,
        grocery_list=plan.grocery_list,
        created_at=plan.created_at,
        meals=meals_data
    )

@router.get("", response_model=List[PlanDetailResponse])
async def list_plans(
    caller_id: int = Depends(get_authenticated_user_id),
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    """Fetch the signed-in user's plans.

    With no user_id this used to return every plan belonging to every account.
    """
    plans = await plan_repo.get_all_by_user(caller_id)
    return [serialize_plan(p) for p in plans]


def _ensure_owned(plan, caller_id: int) -> None:
    """Reject access to a plan that belongs to somebody else."""
    if plan is not None and plan.user_id != caller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this meal plan.",
        )


@router.get("/{plan_id}", response_model=PlanDetailResponse)
async def get_plan_by_id(
    plan_id: int,
    caller_id: int = Depends(get_authenticated_user_id),
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    plan = await plan_repo.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    _ensure_owned(plan, caller_id)

    return serialize_plan(plan)

@router.patch("/{plan_id}/rename", response_model=PlanDetailResponse)
async def rename_plan(
    plan_id: int,
    payload: PlanRenameRequest,
    caller_id: int = Depends(get_authenticated_user_id),
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    """
    Rename a meal plan by ID.
    """
    existing = await plan_repo.get_by_id(plan_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    _ensure_owned(existing, caller_id)

    plan = await plan_repo.rename_plan(plan_id, payload.name)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return serialize_plan(plan)

@router.post("/{plan_id}/approve", response_model=PlanDetailResponse)
async def approve_plan(
    plan_id: int,
    caller_id: int = Depends(get_authenticated_user_id),
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    """
    Human-in-the-loop: User reviews the generated meal plan and approves it.
    The Shopper builds the grocery list, then the output guardrail re-checks the
    plan and that list. Only if it passes is the list saved and the plan marked
    'approved'; otherwise it stays a draft with no list and a 422 explains why.
    Idempotent: if already approved, returns existing plan without regenerating grocery.
    """
    plan = await plan_repo.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    _ensure_owned(plan, caller_id)
    from backend.app.account_context import current_user_id
    current_user_id.set(caller_id)

    # Idempotency: if already approved, return existing plan
    if plan.status == "approved" and plan.grocery_list:
        return serialize_plan(plan)

    # Prevent concurrent approval
    if plan.status == "approving":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plan approval already in progress")

    # Set to approving to prevent duplicate approvals
    plan.status = "approving"
    await plan_repo.session.commit()

    # Shopper builds the grocery list, then the final safety check (output guardrail)
    from backend.app.orchestration.approval import build_and_verify_grocery, ApprovalBlocked
    from backend.app.db.repositories.users import UserRepository
    try:
        user = await UserRepository(plan_repo.session).get_by_id(caller_id)
        user_allergies = [a.allergen for a in (user.allergies if user else [])]
        domain_plan = PlanRepository.to_meal_plan_domain(plan)
        grocery_list = await build_and_verify_grocery(
            meal_plan=domain_plan,
            user_allergies=user_allergies,
            session=plan_repo.session,
            task_id=f"approve-{plan_id}"
        )
        plan.grocery_list = grocery_list.model_dump()
    except ApprovalBlocked as blocked:
        plan.status = "draft"  # Not approved, and no grocery list is saved
        await plan_repo.session.commit()
        logger.warning(f"Output guardrail blocked approval of plan #{plan_id}")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=blocked.reason)
    except Exception as e:
        plan.status = "draft"  # Revert on failure
        await plan_repo.session.commit()
        logger.warning(f"Error generating grocery list on approve for plan #{plan_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate grocery list")

    # Mark as approved with timestamp
    plan.status = "approved"
    plan.approved_at = ist_now()
    plan.updated_at = ist_now()
    await plan_repo.session.commit()
    await plan_repo.session.refresh(plan)
    return serialize_plan(plan)

@router.delete("/{plan_id}")
async def delete_plan(
    plan_id: int,
    caller_id: int = Depends(get_authenticated_user_id),
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    """
    Delete a meal plan by ID.
    """
    existing = await plan_repo.get_by_id(plan_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    _ensure_owned(existing, caller_id)

    deleted = await plan_repo.delete_plan(plan_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return {"message": "Plan deleted successfully", "id": plan_id}

