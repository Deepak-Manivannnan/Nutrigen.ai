from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from backend.app.db.models import Plan
from backend.app.db.repositories.plans import PlanRepository
from backend.app.api.deps import get_plan_repository

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
        status=plan.status,
        version=getattr(plan, "version", 1) or 1,
        grocery_list=plan.grocery_list,
        created_at=plan.created_at,
        meals=meals_data
    )

@router.get("", response_model=List[PlanDetailResponse])
async def list_plans(
    user_id: Optional[int] = None,
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    """
    Fetch all plans, optionally filtered by user_id.
    """
    if user_id:
        plans = await plan_repo.get_all_by_user(user_id)
    else:
        stmt = (
            select(Plan)
            .order_by(Plan.id.desc())
            .options(selectinload(Plan.meals))
        )
        result = await plan_repo.session.execute(stmt)
        plans = list(result.scalars().all())
    return [serialize_plan(p) for p in plans]

@router.get("/{plan_id}", response_model=PlanDetailResponse)
async def get_plan_by_id(
    plan_id: int,
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    plan = await plan_repo.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    return serialize_plan(plan)

@router.patch("/{plan_id}/rename", response_model=PlanDetailResponse)
async def rename_plan(
    plan_id: int,
    payload: PlanRenameRequest,
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    """
    Rename a meal plan by ID.
    """
    plan = await plan_repo.rename_plan(plan_id, payload.name)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return serialize_plan(plan)

@router.post("/{plan_id}/approve", response_model=PlanDetailResponse)
async def approve_plan(
    plan_id: int,
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    """
    Human-in-the-loop: User reviews the generated meal plan and approves it.
    Generates/finalizes the grocery list via Shopper Agent, and updates status from 'draft' to 'approved'.
    """
    plan = await plan_repo.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    # Generate grocery list via Shopper Agent
    from backend.app.agents.shopper.agent import ShopperAgent
    try:
        domain_plan = PlanRepository.to_meal_plan_domain(plan)
        shopper = ShopperAgent()
        grocery_list = await shopper.generate_grocery_list(
            meal_plan=domain_plan,
            task_id=f"approve-{plan_id}"
        )
        plan.grocery_list = grocery_list.model_dump()
    except Exception as e:
        logger.warning(f"Error generating grocery list on approve for plan #{plan_id}: {e}")

    plan.status = "approved"
    await plan_repo.session.commit()
    await plan_repo.session.refresh(plan)
    return serialize_plan(plan)

@router.delete("/{plan_id}")
async def delete_plan(
    plan_id: int,
    plan_repo: PlanRepository = Depends(get_plan_repository)
):
    """
    Delete a meal plan by ID.
    """
    deleted = await plan_repo.delete_plan(plan_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return {"message": "Plan deleted successfully", "id": plan_id}

