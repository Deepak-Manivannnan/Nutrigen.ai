from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.schemas.api import ChatRequest, ChatResponse
from backend.app.db.session import get_db
from backend.app.db.repositories.users import UserRepository
from backend.app.db.repositories.plans import PlanRepository
from backend.app.api.deps import get_user_repository, get_authenticated_user_id, get_plan_repository
from backend.app.orchestration.runner import run_meal_planner
from backend.app.observability.logging import get_logger
from backend.app.llm.client import LLMUnavailableError
import time
import uuid

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


def _inbox_store(task_id: str, response: ChatResponse, user_id: int) -> None:
    # Entries are keyed by (user, task) so one account can neither read nor
    # overwrite a reply that belongs to another account, even with the same id.
    now = time.time()
    for key in [k for k, v in _RESPONSE_INBOX.items()
                if now - v.get("completed_at", 0) > _INBOX_TTL_S]:
        _RESPONSE_INBOX.pop(key, None)
    if len(_RESPONSE_INBOX) >= _INBOX_MAX_ENTRIES:
        oldest = min(_RESPONSE_INBOX, key=lambda k: _RESPONSE_INBOX[k].get("completed_at", 0))
        _RESPONSE_INBOX.pop(oldest, None)
    _RESPONSE_INBOX[(user_id, task_id)] = {"completed_at": now, "response": response.model_dump()}


def _inbox_store_failure(payload: ChatRequest, user_id: int, message: str) -> None:
    """File a failed run in the inbox too.

    The browser that sent the request may already be gone (page reloaded), so
    the error response has nowhere to land. Filing it here lets the reloaded
    page collect the error right away instead of waiting out its timeout. The
    caller's pending workflow is handed back unchanged so a crash does not
    wipe a half-finished conversation.
    """
    if not payload.task_id:
        return
    _inbox_store(
        payload.task_id,
        ChatResponse(
            response=message,
            task_id=payload.task_id,
            status="failed",
            pending_workflow=payload.pending_workflow,
        ),
        user_id,
    )


@router.get("/result/{task_id}")
async def chat_result(
    task_id: str,
    caller_id: int = Depends(get_authenticated_user_id),
):
    """Fetch a finished chat response by task_id (delivery after reload).

    Requires sign-in and only returns replies that belong to the caller.
    200 with the full ChatResponse envelope when the run completed,
    404 when unknown, still running, expired, or owned by someone else
    (the same answer in every case, so nothing is revealed).
    """
    key = (caller_id, task_id)
    entry = _RESPONSE_INBOX.get(key)
    if not entry or (time.time() - entry.get("completed_at", 0) > _INBOX_TTL_S):
        if entry:
            _RESPONSE_INBOX.pop(key, None)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No completed response for this task yet"
        )
    return entry["response"]

@router.post("", response_model=ChatResponse)
async def chat_interaction(
    payload: ChatRequest,
    caller_id: int = Depends(get_authenticated_user_id),
    user_repo: UserRepository = Depends(get_user_repository),
    session: AsyncSession = Depends(get_db)
):
    # The caller is identified by their token. The user_id in the body is only
    # accepted when it matches, so one account cannot drive another account's
    # chat, plans or grocery list.
    if payload.user_id != caller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only chat as your own account.",
        )
    user = await user_repo.get_by_id(caller_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {caller_id} does not exist. Please create a profile first."
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
            pending_workflow=payload.pending_workflow,
            just_created_plan_id=payload.just_created_plan_id,
        )
    except LLMUnavailableError as e:
        # The AI service is down or out of quota. Say so plainly; the exact
        # error stays in the server log.
        logger.warning(f"AI service unavailable during chat: {e}")
        message = "Our AI service is busy right now. Please try again in a few minutes."
        _inbox_store_failure(payload, caller_id, message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=message
        )
    except Exception:
        # Never send the raw exception text to the user: it can contain SQL,
        # file paths or library internals. Log it in full on the server and
        # give the user a short reference to quote instead.
        error_ref = uuid.uuid4().hex[:8]
        logger.exception(f"Error during agent execution (ref {error_ref})")
        message = f"Something went wrong while handling your request. Please try again. (ref: {error_ref})"
        _inbox_store_failure(payload, caller_id, message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=message
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
        profile_updated=bool(getattr(final_state, "profile_updated", False)),
        deleted_plan_id=final_state.deleted_plan_id,
        pending_workflow=final_state.pending_workflow.model_dump() if final_state.pending_workflow else None
    )
    # File the finished response in the delivery inbox under the client's
    # task_id so a reloaded client can still collect it.
    if payload.task_id:
        _inbox_store(payload.task_id, chat_response, caller_id)
    return chat_response

