from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.schemas.api import ChatRequest, ChatResponse
from backend.app.db.session import get_db
from backend.app.db.repositories.users import UserRepository
from backend.app.db.repositories.plans import PlanRepository
from backend.app.api.deps import get_user_repository, get_plan_repository
from backend.app.orchestration.runner import run_meal_planner
from backend.app.observability.logging import get_logger
import time

logger = get_logger("api.chat")
router = APIRouter(prefix="/chat", tags=["Chat & Agents"])

# Delivery inbox: finished chat responses keyed by task_id so a client that
# reloaded mid-run can still collect its reply on boot ("anything finished
# while I was gone?"). In-memory with TTL; a server restart wipes it, in
# which case in-flight runs are dead anyway and the client falls back to its
# timeout message. Only successful responses are stored — failures surface
# through the normal error path, never the inbox.
_RESPONSE_INBOX: dict = {}
_INBOX_TTL_S = 30 * 60
_INBOX_MAX_ENTRIES = 200


def _inbox_store(task_id: str, response: ChatResponse) -> None:
    now = time.time()
    for key in [k for k, v in _RESPONSE_INBOX.items()
                if now - v.get("completed_at", 0) > _INBOX_TTL_S]:
        _RESPONSE_INBOX.pop(key, None)
    if len(_RESPONSE_INBOX) >= _INBOX_MAX_ENTRIES:
        oldest = min(_RESPONSE_INBOX, key=lambda k: _RESPONSE_INBOX[k].get("completed_at", 0))
        _RESPONSE_INBOX.pop(oldest, None)
    _RESPONSE_INBOX[task_id] = {"completed_at": now, "response": response.model_dump()}


@router.get("/result/{task_id}")
async def chat_result(task_id: str):
    """Fetch a finished chat response by task_id (delivery after reload).

    200 with the full ChatResponse envelope when the run completed,
    404 when unknown, still running, or expired.
    """
    entry = _RESPONSE_INBOX.get(task_id)
    if not entry or (time.time() - entry.get("completed_at", 0) > _INBOX_TTL_S):
        if entry:
            _RESPONSE_INBOX.pop(task_id, None)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No completed response for this task yet"
        )
    return entry["response"]

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
    chat_response = ChatResponse(
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
    # File the finished response in the delivery inbox under the client's
    # task_id so a reloaded client can still collect it.
    if payload.task_id:
        _inbox_store(payload.task_id, chat_response)
    return chat_response

