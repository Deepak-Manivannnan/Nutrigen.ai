from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.schemas.api import ChatRequest, ChatResponse
from backend.app.db.session import get_db
from backend.app.db.repositories.users import UserRepository
from backend.app.db.repositories.plans import PlanRepository
from backend.app.api.deps import get_user_repository, get_plan_repository
from backend.app.orchestration.runner import run_meal_planner
from backend.app.observability.logging import get_logger

logger = get_logger("api.chat")
router = APIRouter(prefix="/chat", tags=["Chat & Agents"])

@router.post("", response_model=ChatResponse)
async def chat_interaction(
    payload: ChatRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    session: AsyncSession = Depends(get_db)
):
    user = await user_repo.get_by_id(payload.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {payload.user_id} does not exist. Please create a profile first."
        )

    # Format user profile for agent orchestration
    user_profile = {
        "name": user.name,
        "age": user.age,
        "sex": user.sex,
        "height_cm": user.height_cm,
        "weight_kg": user.weight_kg,
        "activity_level": user.activity_level,
        "goal": user.goal,
        "diet_type": user.diet_type,
        "dislikes": user.dislikes or [],
        "allergies": [a.allergen for a in user.allergies],
        "min_safe_calories": user.min_safe_calories,
        "max_safe_calories": user.max_safe_calories,
        "suggested_target_calories": user.suggested_target_calories,
    }

    try:
        final_state = await run_meal_planner(
            user_id=user.id,
            user_message=payload.message,
            user_profile=user_profile,
            session=session,
            task_id=payload.task_id,
            history=payload.history,
            pending_workflow=payload.pending_workflow
        )
    except Exception as e:
        logger.error(f"Error during agent execution: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error executing agent workflow: {str(e)}"
        )

    # Find persisted plan ID if created
    plan_id = getattr(final_state, "plan_id", None)
    if not plan_id and final_state.current_plan and final_state.status in ("reviewing", "approved", "draft"):
        plan_repo = PlanRepository(session)
        latest_plan = await plan_repo.get_latest_by_user(user.id)
        if latest_plan:
            plan_id = latest_plan.id

    resp_text = final_state.system_message or "Plan generation complete."
    is_wf = (
        final_state.intent in ("new_plan", "edit_meal", "grocery_only")
        and final_state.status not in ("conversational", "out_of_scope")
    )
    return ChatResponse(
        response=resp_text,
        task_id=final_state.task_id,
        status=final_state.status,
        plan_id=plan_id,
        intent=final_state.intent,
        is_workflow=is_wf,
        deleted_plan_id=final_state.deleted_plan_id,
        quick_options=final_state.quick_options,
        pending_workflow=final_state.pending_workflow.model_dump() if final_state.pending_workflow else None
    )

