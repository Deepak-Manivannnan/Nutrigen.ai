import re
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.schemas.state import PlanState, PendingWorkflow
from backend.app.orchestration.graph import create_meal_planner_graph
from backend.app.orchestration.supervisor.intent import extract_user_intent, EditDetails
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.db.repositories.plans import PlanRepository
from backend.app.db.session import AsyncSessionLocal
from backend.app.observability.logging import get_logger
from backend.app.config import settings

logger = get_logger("orchestration.runner")

def find_referenced_plan(
    user_message: str,
    all_plans: List[Any],
    target_id: Optional[int] = None,
    target_name: Optional[str] = None,
    fallback_to_first: bool = True,
    history: Optional[List[Dict[str, Any]]] = None
) -> Optional[Any]:
    """Helper to cleanly resolve referenced meal plan without exposing internal IDs."""
    if not all_plans:
        return None
    if target_id:
        p = next((p for p in all_plans if p.id == target_id), None)
        if p:
            return p
    if target_name:
        t_clean = target_name.strip().lower()
        p = next((p for p in all_plans if p.name.strip().lower() == t_clean or t_clean in p.name.strip().lower() or p.name.strip().lower() in t_clean), None)
        if p:
            return p
        if t_clean in ("good", "good plan"):
            p = next((p for p in all_plans if "good" in p.name.strip().lower()), None)
            if p:
                return p

    clean_msg = user_message.strip().lower()
    for p in sorted(all_plans, key=lambda x: len(str(getattr(x, "name", ""))), reverse=True):
        p_name = str(getattr(p, "name", "")).strip().lower()
        if p_name and p_name in clean_msg:
            return p
        base_name = p_name.replace("plan", "").strip()
        if base_name and len(base_name) >= 3 and (f'"{base_name}"' in clean_msg or f"'{base_name}'" in clean_msg or f" {base_name} " in f" {clean_msg} "):
            return p

    # Match by day count (e.g. "5 day meal plan", "the 5 day", "5-day")
    m_days = re.search(r'\b(\d+)\s*(?:-| )?days?\b', clean_msg)
    if m_days:
        req_d = int(m_days.group(1))
        matching_by_days = [p for p in all_plans if getattr(p, "days", None) == req_d]
        if len(matching_by_days) == 1:
            return matching_by_days[0]
        elif len(matching_by_days) > 1:
            for p in matching_by_days:
                p_diet = (getattr(p, "diet_type", None) or "").lower()
                if p_diet and p_diet in clean_msg:
                    return p
            return matching_by_days[0]

    # Match by diet type (e.g. "the vegetarian plan", "the non-veg plan")
    for diet_kw in ["vegetarian", "veg", "non-vegetarian", "non-veg", "vegan"]:
        if diet_kw in clean_msg:
            norm_diet = "veg" if diet_kw in ["veg", "vegetarian"] else ("non-veg" if diet_kw in ["non-veg", "non-vegetarian"] else "vegan")
            matching_by_diet = [p for p in all_plans if (getattr(p, "diet_type", None) or "").lower() == norm_diet]
            if len(matching_by_diet) == 1:
                return matching_by_diet[0]
            elif len(matching_by_diet) > 1:
                return matching_by_diet[0]

    # Match from conversation history (if active plan was selected/discussed in previous turn)
    if history:
        for h in reversed(history):
            c = (h.get("content") or "").strip().lower()
            if not c:
                continue
            for p in sorted(all_plans, key=lambda x: len(str(getattr(x, "name", ""))), reverse=True):
                p_name = str(getattr(p, "name", "")).strip().lower()
                if p_name and (p_name in c or f'"{p_name}"' in c or f"'{p_name}'" in c):
                    return p
                base_name = p_name.replace("plan", "").strip()
                if base_name and len(base_name) >= 3 and (f'"{base_name}"' in c or f"'{base_name}'" in c or f" {base_name} " in f" {c} " or c.startswith(base_name) or c == base_name):
                    return p
            m_h_days = re.search(r'\b(\d+)\s*(?:-| )?days?\b', c)
            if m_h_days:
                h_d = int(m_h_days.group(1))
                h_matches = [p for p in all_plans if getattr(p, "days", None) == h_d]
                if len(h_matches) == 1:
                    return h_matches[0]

    if fallback_to_first:
        return all_plans[0]
    return None

# =========================================================================
# Pending-workflow interruption helpers
# =========================================================================

_NEW_PLAN_MARKERS = (
    "new meal plan", "new plan", "another meal plan", "another plan",
    "start a new", "make a new", "create another", "fresh meal plan",
    "fresh plan", "new one", "a different plan", "different meal plan",
    "start over", "start fresh",
    "new veg", "new vegetarian", "new vegan", "new non-veg", "new nonveg",
    "another veg", "another vegetarian", "another vegan", "another non-veg",
)

_PLAN_CREATE_VERBS = ("create", "make", "build", "generate", "plan a", "plan an", "plan another")

# Explicit acknowledgements that the user wants to abandon the pending workflow
# and start over with a brand-new plan (as opposed to a fresh competing request
# that should itself be interrupted).
_NEW_CHOICE_PHRASES = (
    "new plan instead", "new meal plan instead", "start a new plan instead",
    "start fresh", "start over", "yes, create a new", "no, create a new",
    "yes, start a new", "no, start a new", "create a new plan instead",
    "create a new meal plan instead", "let's start fresh", "lets start fresh",
    "forget it and create a new", "drop it and create a new",
)

_LIST_PLANS_PATTERNS = (
    "list my plans", "list my meal plans", "list the plans", "list the meal plans",
    "list my plan", "list my meal plan", "list the plan", "list the meal plan",
    "show my plans", "show my meal plans", "show me my plans", "show me my meal plans",
    "show existing plans", "view my plans", "view my meal plans", "view existing plans",
    "view existing plan", "view my plan",
    "what plans do i have", "which plans do i have",
    "all my plans", "all my meal plans", "my plan list",
)


def _mentions_diet(msg_lower: str) -> bool:
    return any(k in msg_lower for k in (
        "veg", "vegan", "non-veg", "nonveg", "vegetarian", "non vegetarian"
    ))


def _mentions_calories(msg_lower: str) -> bool:
    return bool(re.search(
        r"(\d[\d,]{2,4}\s*(kcal|calories|cal\b))|((kcal|calories|cal\b)[^\d]{0,8}\d[\d,]{2,4})",
        msg_lower
    ))


def is_new_plan_request(user_message: str) -> bool:
    low = user_message.lower()
    return any(m in low for m in _NEW_PLAN_MARKERS)


def is_list_plans_request(user_message: str) -> bool:
    low = user_message.lower()
    return any(p in low for p in _LIST_PLANS_PATTERNS)


def _answered_pending_questions(user_message: str, intent_data) -> dict:
    """Whether THIS message answers the diet/calorie confirmation questions."""
    low = user_message.lower()
    d = _mentions_diet(low)
    c = _mentions_calories(low)
    user_diet = (intent_data.chosen_diet_type or intent_data.requested_diet_type) if d else None
    user_cal = (
        intent_data.chosen_calorie_target
        if intent_data.chosen_calorie_target is not None
        else intent_data.requested_calories
    ) if c else None
    return {"answered_diet": user_diet is not None, "answered_cal": user_cal is not None}


def _interruption_prompt_text(pending, intent_data) -> str:
    """Deterministic text asking whether to continue pending work or do the new request."""
    if pending.workflow_type == "edit_meal":
        workflow_desc = "updating your meal plan"
    else:
        workflow_desc = "creating your new meal plan"
    req_label = {
        "new_plan": "create a new meal plan",
        "rename_plan": "rename one of your existing plans",
        "delete_plan": "delete a meal plan",
        "approve_plan": "approve a meal plan",
        "edit_meal": "update an existing meal plan",
    }.get(intent_data.intent, "do that for you")
    return (
        f"I can help you with that — you'd like me to {req_label}.\n\n"
        f"Just to keep things straight: we were in the middle of {workflow_desc} for you, "
        f"and the pending questions haven't been answered yet.\n\n"
        f"Which would you like to do?"
    )


def _interruption_quick_options(pending, intent_data, interrupted_message: str) -> list:
    resume_label = "Continue the update" if pending.workflow_type == "edit_meal" else "Continue existing plan"
    new_label = {
        "new_plan": "Create the new plan",
        "rename_plan": "Rename the plan",
        "delete_plan": "Delete the plan",
        "approve_plan": "Approve the plan",
        "edit_meal": "Update the plan",
    }.get(intent_data.intent, "Do it instead")
    return [
        {"label": resume_label, "value": "Resume the plan"},
        # Echoing the competing request lets the next click re-run it fresh once
        # the pending workflow is dropped.
        {"label": new_label, "value": interrupted_message},
        {"label": "Cancel", "value": "Cancel"},
    ]


def append_resume_prompt(state, response_text: str, pending) -> None:
    """Answer a question, then ask whether to continue the pending workflow or start fresh."""
    workflow_desc = (
        "updating your meal plan" if pending.workflow_type == "edit_meal" else "creating your meal plan"
    )
    resume_prompt = (
        f"\n\nWe were in the middle of {workflow_desc} for you. "
        f"Would you like to continue with that, or start a new plan instead?"
    )
    state.system_message = f"{response_text}{resume_prompt}"
    state.status = "conversational"
    resume_label = "Continue the update" if pending.workflow_type == "edit_meal" else "Resume"
    state.quick_options = [
        {"label": resume_label, "value": "Continue creating the plan"},
        {"label": "New Plan", "value": "Create a new plan instead"},
    ] + list(state.quick_options or [])


def ask_for_new_plan_specs(state, user_profile) -> None:
    """After the user abandons a pending workflow for a new plan, ask for the specs."""
    sug = int(user_profile.get("suggested_target_calories") or 2100)
    p_diet = str(user_profile.get("diet_type", "veg")).lower()
    state.pending_workflow = None
    state.system_message = (
        "Got it — let's start a fresh meal plan. Tell me how many days (e.g. 3, 5, or 7), "
        "your calorie target, and your diet preference (veg / non-veg / vegan), "
        "and I'll put one together for you."
    )
    state.status = "conversational"
    state.quick_options = [
        {"label": f"Plan 3 days ({sug:,} kcal)", "value": f"Plan a 3-day {p_diet} meal plan for {sug} kcal"},
        {"label": f"Plan 7 days ({sug:,} kcal)", "value": f"Plan a 7-day {p_diet} meal plan for {sug} kcal"},
    ]


def make_edit_pending(
    response_text: str,
    quick_options: list,
    stage: str,
    target_plan_name: str,
    user_message: str,
    user_profile: Dict[str, Any],
) -> PendingWorkflow:
    """Persist an in-progress plan-update clarification so it survives navigation
    and can be resumed after an interruption (e.g. the user asks for a new plan)."""
    return PendingWorkflow(
        workflow_type="edit_meal",
        pending_conflicts=["edit_clarification"],
        asked_questions=[response_text],
        extracted_info={
            "edit_stage": stage,
            "target_plan_name": target_plan_name,
            "reprompt_text": response_text,
            "reprompt_quick_options": list(quick_options or []),
        },
        next_expected_decision="edit_clarification",
        original_intent="edit_meal",
        original_message=user_message,
        user_profile_at_start=dict(user_profile or {}),
        created_at=datetime.utcnow().isoformat()
    )


async def run_meal_planner(
    user_id: int,
    user_message: str,
    user_profile: Dict[str, Any],
    session: Optional[AsyncSession] = None,
    task_id: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    pending_workflow: Optional[Any] = None
) -> PlanState:
    """
    Main entry point for running the multi-agent meal planning workflow:
    1. Extracts intent & parameters (days, calories, fixed meals, edit details, deletion, rename, diet conflict, snack).
    2. Instantiates PlanState.
    3. Handles conversational intents, profile inquiry, rename, snack questions, deletion, and updates.
    4. Handles diet preference conflict confirmation and safe calorie guardrails.
    5. Executes LangGraph hub-and-spoke workflow for plan creation / modification.
    6. Persists draft/updated plans with proper versioning and status.
    """
    tid = task_id or f"t-{uuid.uuid4().hex[:8]}"
    logger.info(f"Starting workflow run for task {tid} (user {user_id})")

    # Step 1: Supervisor intent extraction with history context
    # Pass pending workflow state to handle partial answers to pending questions
    # Convert dict to PendingWorkflow object if needed
    if pending_workflow and isinstance(pending_workflow, dict):
        pending_workflow = PendingWorkflow(**pending_workflow)
    
    intent_data = await extract_user_intent(user_message, history=history, pending_workflow=pending_workflow)
    logger.info(f"[{tid}] Extracted intent: {intent_data.intent}, days: {intent_data.requested_days}, calories: {intent_data.requested_calories}, diet: {intent_data.requested_diet_type}")

    # A "resume"/"continue" answer re-activates the pending workflow's original
    # intent BEFORE PlanState is built (PlanState.intent is a typed literal, and
    # the graph routes on it - so it must hold the real intent, not 'resume_request').
    if intent_data.intent == "resume_request" and pending_workflow and pending_workflow.original_intent:
        intent_data.intent = pending_workflow.original_intent

    # Merge standing fixed meals from profile if any
    fixed = {}
    if isinstance(intent_data.fixed_meals, list):
        for slot in intent_data.fixed_meals:
            fixed[slot.meal_type] = slot.dish
    elif isinstance(intent_data.fixed_meals, dict):
        fixed = dict(intent_data.fixed_meals)

    if "standing_fixed_meals" in user_profile and user_profile["standing_fixed_meals"]:
        for k, v in user_profile["standing_fixed_meals"].items():
            if k not in fixed:
                fixed[k] = v

    # Retrieve all user plans for multi-plan checks
    all_plans = []
    try:
        if session:
            repo = PlanRepository(session)
            all_plans = await repo.get_all_by_user(user_id)
        else:
            async with AsyncSessionLocal() as temp_session:
                repo = PlanRepository(temp_session)
                all_plans = await repo.get_all_by_user(user_id)
    except Exception as e:
        logger.warning(f"[{tid}] Failed to query user plans: {e}")

    # Determine existing active plan if editing or referencing
    existing_plan = None
    db_plan_id = None
    matched_plan = None
    if all_plans:
        matched_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name,
            history=history
        )
        if matched_plan:
            existing_plan = PlanRepository.to_meal_plan_domain(matched_plan)
            db_plan_id = matched_plan.id

    is_previously_approved = bool(matched_plan and getattr(matched_plan, "status", None) == "approved")
    current_version = (matched_plan.version or 1) if matched_plan else 1
    target_version = (current_version + 1) if is_previously_approved else current_version

    # If existing plan has a specific diet type, preserve it
    effective_plan_diet = getattr(existing_plan, "diet_type", None) or user_profile.get("diet_type") or "veg"

    state = PlanState(
        task_id=tid,
        user_id=user_id,
        user_profile=user_profile,
        intent=intent_data.intent,
        days=intent_data.requested_days or (len(existing_plan.days) if existing_plan else settings.default_plan_days),
        calorie_target=intent_data.requested_calories or (existing_plan.target_calories if existing_plan else None),
        fixed_meals=fixed,
        edit_details=intent_data.edit_details,
        plan_name=getattr(intent_data, "new_plan_name", None),
        current_plan=existing_plan if intent_data.intent == "edit_meal" else None,
        plan_version=target_version if (existing_plan and intent_data.intent == "edit_meal") else 1,
        round=1,
        max_rounds=3,
        status="drafting",
        pending_workflow=pending_workflow
    )

    # Supervisor is needed by the resume/cancel branches below, so instantiate it
    # before the pending-workflow handling.
    supervisor = SupervisorAgent()

    # =========================================================================
    # Handle Pending Workflow: Check for pending workflow and handle resume/cancel/out_of_scope
    # =========================================================================
    # Check if there's a pending workflow in the state (from previous request via history)
    pending_workflow = state.pending_workflow
    
    # Handle resume_request intent - user wants to continue the pending workflow
    if intent_data.intent == "resume_request" and pending_workflow:
        # Restore the pending workflow context
        state.pending_workflow = pending_workflow
        # Set intent to continue the original workflow
        if pending_workflow.original_intent:
            intent_data.intent = pending_workflow.original_intent
        # Clear the cancel flag if they're resuming
        state.system_message = None
    
    # Handle cancel_request intent - user wants to cancel the pending workflow
    elif intent_data.intent == "cancel_request" and pending_workflow:
        # Clear the pending workflow
        state.pending_workflow = None
        context_prompt = (
            "The user wants to cancel the pending meal plan creation and start fresh. "
            "In friendly, polite English, confirm that the previous meal plan creation has been cancelled "
            "and ask what they'd like to do instead (create a new plan, modify existing, etc.)."
        )
        response_text = await supervisor.generate_conversational_response(
            user_message=user_message,
            context=context_prompt,
            history=history,
            user_name=user_profile.get("name")
        )
        state.system_message = response_text
        state.status = "conversational"
        state.quick_options = [
            {"label": "Create new meal plan", "value": "Create a new meal plan"},
            {"label": "View existing plans", "value": "Show my meal plans"}
        ]
        return state

    # =========================================================================
    # Plan Listing (anytime): "show my meal plans" / "list my plans"
    # Lists the user's saved plans with per-plan shortcuts to the Plans tab.
    # If a workflow is pending, the list is followed by a continue/new prompt.
    # =========================================================================
    if is_list_plans_request(user_message):
        if not all_plans:
            list_text = (
                "You don't have any saved meal plans yet. I can create one for you — just "
                "tell me how many days, your calorie target, and your diet preference."
            )
            state.quick_options = [
                {"label": "Plan 3 days", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days", "value": "Plan 7 days vegetarian meal plan"},
            ]
        else:
            status_lbl = {
                "approved": "Approved", "draft": "Draft", "reviewing": "Reviewing",
                "escalated": "Escalated", "failed": "Failed",
            }
            lines = ["Here are your saved meal plans:"]
            for p in all_plans:
                name = getattr(p, "name", None) or f"Plan #{p.id}"
                days = getattr(p, "days", 0)
                cal = getattr(p, "calorie_target", None) or getattr(p, "target_calories", None)
                diet = str(getattr(p, "diet_type", "") or "").lower()
                diet_txt = "Non-Veg" if diet == "non-veg" else (diet.capitalize() if diet else "—")
                cal_txt = f"{int(cal):,} kcal" if cal else "—"
                st = status_lbl.get(getattr(p, "status", ""), "Draft")
                lines.append(f"- **{name}** — {days} days · {cal_txt} · {diet_txt} ({st})")
            list_text = "\n".join(lines)
            state.quick_options = [
                {"label": f"Open {getattr(p, 'name', None) or f'Plan #{p.id}'}", "value": f"__open_plan__:{p.id}"}
                for p in all_plans
            ] + [{"label": "Create new plan", "value": "Create a new meal plan"}]

        if state.pending_workflow:
            append_resume_prompt(state, list_text, state.pending_workflow)
        else:
            state.system_message = list_text
            state.status = "conversational"
        await supervisor.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="user",
            msg_type="approve",
            content=str(state.system_message or "")[:120] + "...",
            payload={"action": "list_plans"}
        )
        return state

    # =========================================================================
    # PENDING-WORKFLOW INTERRUPTION GATE
    # When a pending workflow exists (diet/calorie confirmation, or an in-progress
    # plan update), a competing request (new plan, rename, delete, approve, edit)
    # must not silently discard it. We either resolve an open interruption choice,
    # or ask the user whether to continue the pending work or proceed with the new
    # request. Informational questions get answered AND followed by that prompt.
    # =========================================================================
    pending_workflow = state.pending_workflow
    if pending_workflow:
        pinfo = dict(pending_workflow.extracted_info or {})
        awaiting_choice = bool(pinfo.get("awaiting_interruption_choice"))
        msg_lower = user_message.lower()
        is_resume_msg = (
            intent_data.intent == "resume_request"
            or any(k in msg_lower for k in (
                "resume", "continue", "carry on", "keep going", "keep it", "go on",
            ))
        )

        if awaiting_choice:
            if is_resume_msg:
                # Re-present the pending question(s) exactly as the user saw them.
                if pending_workflow.workflow_type == "edit_meal":
                    text = pinfo.get("reprompt_text") or "Where would you like to continue with your update?"
                    quick = pinfo.get("reprompt_quick_options")
                else:
                    text = pinfo.get("prompt_text") or "Let's continue creating your meal plan."
                    quick = pinfo.get("prompt_quick_options")
                new_info = {k: v for k, v in pinfo.items()
                            if k not in ("awaiting_interruption_choice", "interrupted_by", "interrupted_intent")}
                state.pending_workflow = pending_workflow.model_copy(update={"extracted_info": new_info})
                state.system_message = text
                state.status = "conversational"
                state.quick_options = quick or [
                    {"label": "Resume", "value": "Continue creating the plan"},
                    {"label": "New Plan", "value": "Create a new plan instead"},
                ]
                await supervisor.log_step(
                    state=state, from_agent="supervisor", to_agent="user",
                    msg_type="feedback", content=str(text)[:120] + "...",
                    payload={"action": "pending_resume"}
                )
                return state
            if intent_data.intent in ("chat", "app_info", "explain", "profile_info", "out_of_scope") and not any(
                phr in msg_lower for phr in _NEW_CHOICE_PHRASES
            ):
                # Answer the new question, then keep the interruption choice alive.
                answer = await supervisor.handle_conversational_intent(
                    state=state, user_message=user_message,
                    intent=intent_data.intent, history=history
                )
                workflow_desc = (
                    "updating your meal plan" if pending_workflow.workflow_type == "edit_meal"
                    else "creating your meal plan"
                )
                resume_prompt = (
                    f"\n\nWe were in the middle of {workflow_desc} for you. "
                    f"Would you like to continue with that, or start a new plan instead?"
                )
                state.system_message = f"{answer}{resume_prompt}"
                state.status = "conversational"
                resume_label = "Continue the update" if pending_workflow.workflow_type == "edit_meal" else "Resume"
                state.quick_options = [
                    {"label": resume_label, "value": "Continue creating the plan"},
                    {"label": "New Plan", "value": "Create a new plan instead"},
                ]
                return state
            # Anything else while awaiting = the user chose the competing request:
            # drop the pending workflow and handle this message as a fresh request.
            chosen_new = any(phr in msg_lower for phr in _NEW_CHOICE_PHRASES)
            has_params = _mentions_diet(msg_lower) or _mentions_calories(msg_lower)
            state.pending_workflow = None
            if (chosen_new or intent_data.intent == "new_plan") and not has_params:
                # Explicit "start a new plan instead" (or a bare "create a new meal
                # plan" echo): abandon the pending flow and ask for the new specs.
                ask_for_new_plan_specs(state, user_profile)
                await supervisor.log_step(
                    state=state, from_agent="supervisor", to_agent="user",
                    msg_type="feedback", content=str(state.system_message)[:120] + "...",
                    payload={"action": "new_plan_specs_request"}
                )
                return state
            # A concrete competing request (carries params, or a rename/delete/
            # approve/edit echo) continues below the gate with no pending workflow.
        elif is_resume_msg and pending_workflow.workflow_type == "edit_meal":
            # Resuming an in-progress update: re-present the stored question.
            text = pinfo.get("reprompt_text") or "Where would you like to continue with your update?"
            quick = pinfo.get("reprompt_quick_options")
            state.pending_workflow = pending_workflow
            state.system_message = text
            state.status = "conversational"
            state.quick_options = quick or [
                {"label": "Swap breakfast", "value": "Swap day 1 breakfast"},
                {"label": "Add a healthy snack", "value": "Add a snack to my meal plan"},
            ]
            await supervisor.log_step(
                state=state, from_agent="supervisor", to_agent="user",
                msg_type="feedback", content=str(text)[:120] + "...",
                payload={"action": "edit_pending_resume"}
            )
            return state
        elif not is_resume_msg:
            chosen_new = any(phr in msg_lower for phr in _NEW_CHOICE_PHRASES)
            if chosen_new:
                # The user answered a resume/new prompt with "start a new plan
                # instead": drop the pending flow (no re-interruption) and ask for
                # the new specs, or if this message already carries diet/calorie
                # params, fall through to process the concrete request fresh.
                if _mentions_diet(msg_lower) or _mentions_calories(msg_lower):
                    state.pending_workflow = None
                else:
                    ask_for_new_plan_specs(state, user_profile)
                    await supervisor.log_step(
                        state=state, from_agent="supervisor", to_agent="user",
                        msg_type="feedback", content=str(state.system_message)[:120] + "...",
                        payload={"action": "new_plan_specs_request"}
                    )
                    return state
            elif intent_data.intent in ("rename_plan", "delete_plan", "approve_plan"):
                competing = True
            elif intent_data.intent == "edit_meal":
                # A fresh update request interrupts plan CREATION; a follow-up to an
                # in-progress update is a continuation and is handled by the edit flow.
                competing = pending_workflow.workflow_type != "edit_meal"
            elif intent_data.intent == "new_plan":
                if pending_workflow.workflow_type == "edit_meal":
                    # Any new-plan request interrupts an in-progress update.
                    competing = True
                elif is_new_plan_request(user_message):
                    # An explicit "new plan" request interrupts pending confirmations.
                    competing = True
                else:
                    answered = _answered_pending_questions(user_message, intent_data)
                    resolved_any = answered["answered_diet"] or answered["answered_cal"]
                    has_create_verb = any(v in msg_lower for v in _PLAN_CREATE_VERBS)
                    competing = has_create_verb and not resolved_any
            else:
                competing = False

            if competing:
                state.system_message = _interruption_prompt_text(pending_workflow, intent_data)
                state.status = "conversational"
                state.quick_options = _interruption_quick_options(pending_workflow, intent_data, user_message)
                new_info = {
                    **pinfo,
                    "awaiting_interruption_choice": True,
                    "interrupted_by": user_message,
                    "interrupted_intent": intent_data.intent,
                }
                state.pending_workflow = pending_workflow.model_copy(update={"extracted_info": new_info})
                await supervisor.log_step(
                    state=state, from_agent="supervisor", to_agent="user",
                    msg_type="feedback", content=str(state.system_message)[:120] + "...",
                    payload={"action": "interruption_prompt", "competing_intent": intent_data.intent}
                )
                return state

    # =========================================================================
    # Step 2: Handle Profile Info Inquiry ("Tell about my profile")
    # =========================================================================
    if intent_data.intent == "profile_info":
        response_text = await supervisor.generate_profile_info_response(state, user_message, history=history)
        state.system_message = response_text
        state.status = "conversational"
        from backend.app.guardrails.calorie_rules import calculate_mifflin_st_jeor
        c_info = calculate_mifflin_st_jeor(
            age=int(user_profile.get("age", 26)),
            sex=str(user_profile.get("sex", "male")),
            height_cm=float(user_profile.get("height_cm", 175)),
            weight_kg=float(user_profile.get("weight_kg", 70)),
            activity_level=str(user_profile.get("activity_level", "medium")),
            goal=str(user_profile.get("goal", "maintain"))
        )
        sug = int(c_info["suggested_target"])
        p_diet = user_profile.get("diet_type", "veg")
        state.quick_options = [
            {"label": f"Plan 3 days ({sug:,} kcal)", "value": f"Plan a 3-day {p_diet} meal plan for {sug} kcal"},
            {"label": f"Plan 7 days ({sug:,} kcal)", "value": f"Plan a 7-day {p_diet} meal plan for {sug} kcal"}
        ]
        # A pending workflow is not discarded by a profile inquiry: answer it and
        # ask whether to continue the pending work or start a new plan.
        if state.pending_workflow:
            append_resume_prompt(state, response_text, state.pending_workflow)
        await supervisor.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="user",
            msg_type="approve",
            content=response_text[:120] + "...",
            payload={"intent": "profile_info"}
        )
        return state

    # =========================================================================
    # Step 3: Handle Rename Plan Intent
    # =========================================================================
    if intent_data.intent == "rename_plan":
        if len(all_plans) == 0:
            context_prompt = (
                "The user asked to rename a meal plan, but they currently have 0 saved meal plans in their account. "
                "In friendly, polite English, let them know they don't have any saved meal plans yet to rename, and invite them to create one first."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name
        )

        if target_plan and intent_data.new_plan_name:
            new_title = intent_data.new_plan_name.strip()
            try:
                if session:
                    repo = PlanRepository(session)
                    await repo.rename_plan(target_plan.id, new_title)
                else:
                    async with AsyncSessionLocal() as temp_session:
                        repo = PlanRepository(temp_session)
                        await repo.rename_plan(target_plan.id, new_title)
            except Exception as e:
                logger.error(f"[{tid}] Failed to rename plan: {e}")

            context_prompt = (
                f"Successfully renamed the user's meal plan to '{new_title}'. "
                f"In polite, natural English, confirm to the user that their meal plan has been renamed to '{new_title}'."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "approved"
            state.plan_id = target_plan.id
            return state
        elif target_plan and (len(all_plans) == 1 or intent_data.target_plan_name or intent_data.target_plan_id):
            context_prompt = (
                f"The user wants to rename their meal plan '{target_plan.name}'. "
                f"In friendly, simple English, ask what new name they would like to give to '{target_plan.name}'."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            return state
        else:
            plan_bullets = [f"• **\"{p.name}\"**" for p in all_plans]
            context_prompt = (
                f"The user wants to rename a meal plan, and they have {len(all_plans)} saved plans: {', '.join(p.name for p in all_plans)}. "
                f"In polite English, list their plans and ask which one they would like to rename."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": f"Rename {p.name}", "value": f"Rename \"{p.name}\" to "}
                for p in all_plans
            ]
            return state

    # =========================================================================
    # Step 4: Handle Plan Deletion Flow
    # =========================================================================
    if intent_data.intent == "delete_plan":
        # Check if previous assistant message in history asked for delete confirmation
        last_asst_content = ""
        if history:
            for h in reversed(history):
                if h.get("role") == "assistant":
                    last_asst_content = (h.get("content") or "").strip().lower()
                    break

        is_pending_confirmation = (
            "are you sure you want to delete" in last_asst_content
            or "can i delete it" in last_asst_content
            or "which meal plan should be deleted" in last_asst_content
            or "reply 'yes, delete it'" in last_asst_content
            or "reply \"yes, delete it\"" in last_asst_content
        )

        cleaned_user_msg = user_message.strip().lower()
        is_cancelling = any(w in cleaned_user_msg for w in ["no", "cancel", "don't", "keep it", "stop"])

        if is_pending_confirmation and is_cancelling:
            context_prompt = (
                "The user cancelled the deletion of their meal plan. "
                "Warmly and politely reassure them that their meal plan is kept safely, and ask if they would like to make any other adjustments instead."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            return state

        # Determine target plan if explicitly stated
        target_id = intent_data.target_plan_id
        if not target_id:
            m = re.search(r'(?:plan\s*#?|#)(\d+)', user_message, re.IGNORECASE)
            if m:
                target_id = int(m.group(1))

        # Check if user is confirming deletion
        is_confirming = intent_data.is_delete_confirmation or (
            is_pending_confirmation and any(w in cleaned_user_msg for w in ["yes", "yeah", "yep", "sure", "confirm", "proceed", "go ahead", "delete it", "ok", "okay"])
        )

        if is_confirming:
            plan_to_delete = find_referenced_plan(
                user_message,
                all_plans,
                target_id=target_id,
                target_name=intent_data.target_plan_name
            )

            if not plan_to_delete and is_pending_confirmation:
                m_prev = re.search(r'\*\*\"([^\"]+)\"\*\*', last_asst_content)
                if m_prev:
                    prev_name = m_prev.group(1).lower()
                    plan_to_delete = next((p for p in all_plans if p.name.lower() == prev_name or prev_name in p.name.lower()), None)

            if not plan_to_delete and len(all_plans) == 1:
                plan_to_delete = all_plans[0]

            if plan_to_delete:
                del_id = plan_to_delete.id
                del_name = plan_to_delete.name
                try:
                    if session:
                        repo = PlanRepository(session)
                        await repo.delete_plan(del_id)
                    else:
                        async with AsyncSessionLocal() as temp_session:
                            repo = PlanRepository(temp_session)
                            await repo.delete_plan(del_id)
                    logger.info(f"[{tid}] Deleted plan '{del_name}' (id #{del_id}) for user {user_id}")
                except Exception as e:
                    logger.error(f"[{tid}] Error executing plan deletion: {e}")

                remaining_count = len(all_plans) - 1
                context_prompt = (
                    f"Successfully deleted the meal plan '{del_name}' from the database. "
                    f"Remaining saved meal plans: {remaining_count}. "
                    f"In polite, natural English, confirm that '{del_name}' has been permanently deleted. "
                    f"{'Let them know they currently have no saved meal plans, and invite them to create a new one whenever ready.' if remaining_count == 0 else f'Let them know they have {remaining_count} meal plan remaining in their Plans tab.'}"
                )
                response_text = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = response_text
                state.status = "deleted"
                state.deleted_plan_id = del_id
                state.quick_options = [
                    {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                    {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
                ]
                return state
            else:
                context_prompt = (
                    "Could not determine which meal plan the user wants to delete. "
                    "In friendly, polite English, ask them to clarify which meal plan name they would like to delete."
                )
                response_text = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = response_text
                state.status = "conversational"
                return state

        # Case 0: 0 plans exist
        if len(all_plans) == 0:
            context_prompt = (
                "The user asked to delete a meal plan, but they currently have 0 active meal plans saved in their account. "
                "In friendly, polite English, let them know they don't have any active meal plans to delete, and invite them to create one whenever they are ready."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        # Case 1: 1 plan exists or user specified target plan by name or id
        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=target_id,
            target_name=intent_data.target_plan_name
        )
        if target_plan and (len(all_plans) == 1 or intent_data.target_plan_name or target_id):
            context_prompt = (
                f"The user wants to delete meal plan '{target_plan.name}'. "
                f"In clear, polite English, ask them to confirm if they are sure they want to delete '{target_plan.name}', and let them know they can reply 'Yes, delete it' or 'Cancel'."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": f"Yes, delete {target_plan.name}", "value": f"Yes, delete \"{target_plan.name}\""},
                {"label": "Cancel", "value": "Cancel"}
            ]
            return state

        # Case 2: Multiple plans exist and user did not name one
        plan_bullets = []
        options = []
        for p in all_plans:
            plan_bullets.append(f"• **\"{p.name}\"** ({p.days}-day, {int(p.calorie_target)} kcal/day)")
            options.append({"label": f"Delete {p.name}", "value": f"Delete \"{p.name}\""})
        options.append({"label": "Cancel", "value": "Cancel"})

        context_prompt = (
            f"The user wants to delete a meal plan, and they have multiple plans: {', '.join(p.name for p in all_plans)}. "
            f"In polite, natural English, ask which meal plan they would like to delete."
        )
        response_text = await supervisor.generate_conversational_response(
            user_message=user_message,
            context=context_prompt,
            history=history,
            user_name=user_profile.get("name")
        )
        state.system_message = response_text
        state.status = "conversational"
        state.quick_options = options
        return state

    # =========================================================================
    # Step 5b: Handle Approve Plan Request (via Chat)
    # =========================================================================
    if intent_data.intent == "approve_plan":
        # Check for draft plans that can be approved
        draft_plans = [p for p in all_plans if p.status == "draft"]
        
        if len(all_plans) == 0:
            context_prompt = (
                "The user asked to approve their meal plan, but they currently have 0 meal plans saved in their account. "
                "In friendly, polite English, let them know they don't have any meal plans saved yet to approve, and invite them to create one first."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            return state

        # If no draft plans exist, all plans are already approved
        if len(draft_plans) == 0:
            context_prompt = (
                "The user asked to approve a meal plan, but all their plans are already approved. "
                "In friendly, natural English, inform them there is no plan pending approval. "
                "All plans have been created and updated, and grocery lists are ready. "
                "They can check details in the Plans and Grocery tabs."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            return state

        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name
        )
        if not target_plan:
            target_plan = draft_plans[0]

        # Check if already approved
        if target_plan.status == "approved" and target_plan.grocery_list:
            context_prompt = (
                f"The meal plan '{target_plan.name}' is already approved (Version {target_plan.version}). "
                f"In polite, natural English, inform the user that this plan is already approved, "
                f"and they can view its full itemized grocery checklist and Mandi market rates in the Grocery tab. "
                f"Do NOT use the word 'approved' as a label - just mention it's Version {target_plan.version}."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "approved"
            state.plan_id = target_plan.id
            state.quick_options = [
                {"label": "View Grocery List →", "value": "Show my grocery list"}
            ]
            return state

        # Check if already approving (prevent duplicate)
        if target_plan.status == "approving":
            context_prompt = (
                f"The meal plan '{target_plan.name}' is currently being approved. "
                f"In polite English, let the user know the approval is in progress and to wait a moment."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            return state

        # Generate grocery list via Shopper Agent
        from backend.app.agents.shopper.agent import ShopperAgent
        from backend.app.db.models import ist_now
        try:
            # Mark as approving to prevent concurrent approvals
            if session:
                repo = PlanRepository(session)
                p_db = await repo.get_by_id(target_plan.id)
                if p_db:
                    p_db.status = "approving"
                    await repo.session.commit()
            else:
                async with AsyncSessionLocal() as temp_session:
                    repo = PlanRepository(temp_session)
                    p_db = await repo.get_by_id(target_plan.id)
                    if p_db:
                        p_db.status = "approving"
                        await repo.session.commit()

            domain_plan = PlanRepository.to_meal_plan_domain(target_plan)
            shopper = ShopperAgent()
            grocery_basket = await shopper.generate_grocery_list(
                meal_plan=domain_plan,
                task_id=f"approve-{target_plan.id}"
            )
            target_plan.grocery_list = grocery_basket.model_dump()
            target_plan.status = "approved"
            target_plan.approved_at = ist_now()
            target_plan.updated_at = ist_now()

            if session:
                repo = PlanRepository(session)
                p_db = await repo.get_by_id(target_plan.id)
                if p_db:
                    p_db.grocery_list = grocery_basket.model_dump()
                    p_db.status = "approved"
                    p_db.approved_at = ist_now()
                    p_db.updated_at = ist_now()
                    await repo.session.commit()
                    await repo.session.refresh(p_db)
            else:
                async with AsyncSessionLocal() as temp_session:
                    repo = PlanRepository(temp_session)
                    p_db = await repo.get_by_id(target_plan.id)
                    if p_db:
                        p_db.grocery_list = grocery_basket.model_dump()
                        p_db.status = "approved"
                        p_db.approved_at = ist_now()
                        p_db.updated_at = ist_now()
                        await repo.session.commit()

            cost = int(grocery_basket.total_estimated_cost or 0)
            context_prompt = (
                f"Successfully approved meal plan '{target_plan.name}' (now Version {target_plan.version + 1}). "
                f"Compiled an itemized grocery checklist with {len(grocery_basket.items)} ingredients based on wholesale Mandi benchmarks totaling approximately ₹{cost:,}. "
                f"In polite, warm, conversational English, let the user know their meal plan is approved and their grocery list is ready to review in the Grocery tab. "
                f"Mention they can view the approved plan (Version {target_plan.version + 1}) in the Plans tab."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "approved"
            state.plan_id = target_plan.id
            state.quick_options = [
                {"label": "View Grocery List →", "value": "Show my grocery list"}
            ]
            return state
        except Exception as e:
            # Revert status on failure
            if session:
                repo = PlanRepository(session)
                p_db = await repo.get_by_id(target_plan.id)
                if p_db:
                    p_db.status = "draft"
                    await repo.session.commit()
            else:
                async with AsyncSessionLocal() as temp_session:
                    repo = PlanRepository(temp_session)
                    p_db = await repo.get_by_id(target_plan.id)
                    if p_db:
                        p_db.status = "draft"
                        await repo.session.commit()
            
            logger.error(f"Error approving plan in chat: {e}")
            context_prompt = (
                f"Failed to generate grocery list for meal plan '{target_plan.name}' due to an error: {str(e)}. "
                f"The meal plan remains in draft status. "
                f"In polite, natural English, explain to the user that the meal plan was NOT approved because the grocery list could not be generated. "
                f"Tell them they can retry by clicking 'Approve' in the Plans tab, or by asking you again in chat. "
                f"Do NOT say the plan was approved."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "draft"
            state.quick_options = [
                {"label": "Retry Approval in Plans Tab", "value": "Approve my meal plan"},
                {"label": "Retry in Chat", "value": "approve this plan"}
            ]
            return state

    # =========================================================================
    # Step 6: Handle Edit / Update Plan Request
    # =========================================================================
    if intent_data.intent == "edit_meal":
        # Safeguard: Verify user query actually contains modification language
        has_edit_token = any(k in user_message.lower() for k in [
            "replace", "swap", "modify", "update", "change", "switch", "substitute", "edit",
            "breakfast", "lunch", "dinner", "snack"
        ])
        if len(all_plans) == 0:
            if not has_edit_token and not intent_data.is_update_request and not intent_data.edit_details:
                # Mistakenly routed to edit_meal; re-route to conversational app_info
                intent_data.intent = "app_info"
                response_text = await supervisor.handle_conversational_intent(
                    state=state,
                    user_message=user_message,
                    intent="app_info",
                    history=history
                )
                state.system_message = response_text
                state.status = "conversational"
                return state

            context_prompt = (
                "The user wants to update or edit their meal plan, but they don't have an active meal plan yet in their account. "
                "In friendly, polite English, let them know that you don't have an active meal plan yet to update, and warmly invite them to create one first. "
                "Include the phrase 'don\\'t have an active meal plan yet' naturally in your sentence."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        # Check if user provided specific modification instructions or requested ingredient exclusions
        has_excl = bool(intent_data.requested_exclusions and len(intent_data.requested_exclusions) > 0)
        has_specific_instructions = bool(
            has_excl
            or (
                intent_data.edit_details and (
                    getattr(intent_data.edit_details, "day", None)
                    or getattr(intent_data.edit_details, "meal_type", None)
                    or getattr(intent_data.edit_details, "dish_to_replace", None)
                    or any(w in (getattr(intent_data.edit_details, "instructions", "") or "").lower() for w in [
                        "swap", "replace", "change", "modify", "switch", "substitute", "snack", "pork", "beef", "turkey",
                        "meat", "chicken", "fish", "dairy", "exclude", "without", "remove", "add"
                    ])
                )
            )
        )

        if has_specific_instructions:
            intent_data.is_update_request = False

        if intent_data.is_update_request or not has_specific_instructions:
            target_p = find_referenced_plan(
                user_message,
                all_plans,
                target_id=intent_data.target_plan_id,
                target_name=intent_data.target_plan_name,
                fallback_to_first=False,
                history=history
            )
            if target_p or len(all_plans) == 1:
                target_plan_to_use = target_p or all_plans[0]
                context_prompt = (
                    f"The user wants to update their meal plan '{target_plan_to_use.name}'. "
                    f"In friendly, conversational English, ask what they would like to update in '{target_plan_to_use.name}' (e.g. swapping a meal on a specific day, adjusting ingredients, or adding a healthy snack)."
                )
                response_text = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = response_text
                state.status = "conversational"
                state.quick_options = [
                    {"label": "Swap breakfast", "value": f"I want to update \"{target_plan_to_use.name}\": swap day 1 breakfast with oatmeal"},
                    {"label": "Add a healthy snack", "value": f"Add a snack to \"{target_plan_to_use.name}\""},
                    {"label": "Change diet to vegetarian", "value": f"Update \"{target_plan_to_use.name}\" to vegetarian"}
                ]
                state.pending_workflow = make_edit_pending(
                    response_text, state.quick_options, "which_meal",
                    target_plan_to_use.name, user_message, user_profile
                )
                return state
            else:
                context_prompt = (
                    f"The user wants to update a meal plan, and they have multiple plans: {', '.join(p.name for p in all_plans)}. "
                    f"In friendly, conversational English, ask which meal plan they would like to update."
                )
                response_text = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = response_text
                state.status = "conversational"
                state.quick_options = [
                    {"label": f"Update {p.name}", "value": f"I want to update \"{p.name}\": "}
                    for p in all_plans
                ]
                state.pending_workflow = make_edit_pending(
                    response_text, state.quick_options, "which_plan",
                    "your meal plan", user_message, user_profile
                )
                return state

        # If user provided meal edit instructions, check if essential parameters are missing or ambiguous
        if existing_plan and intent_data.edit_details:
            cl_inst = (getattr(intent_data.edit_details, "instructions", "") or user_message).lower()
            is_plan_wide = (
                has_excl
                or any(w in cl_inst for w in ["all days", "every day", "entire plan", "whole plan", "all meals", "replace recipes", "replace the recipes", "replacing the recipes", "replacing recipes"])
                or (any(w in cl_inst for w in ["pork", "beef", "turkey", "meat", "chicken", "fish", "dairy"]) and any(w in cl_inst for w in ["replace", "exclude", "remove", "scrub", "without"]))
            )

            if not is_plan_wide:
                target_meal_type = getattr(intent_data.edit_details, "meal_type", None)
                if not target_meal_type:
                    for mt in ["snack", "breakfast", "lunch", "dinner"]:
                        if mt in cl_inst:
                            target_meal_type = mt
                            intent_data.edit_details.meal_type = mt
                            break

                target_day = getattr(intent_data.edit_details, "day", None)

                # Smart snack day resolution: If user is referring to a snack and day is omitted
                if target_meal_type == "snack" and target_day is None:
                    snack_days = [d.day_number for d in existing_plan.days if any(m.meal_type.lower() == "snack" for m in d.meals)]
                    if len(snack_days) == 1:
                        target_day = snack_days[0]
                        intent_data.edit_details.day = target_day

                is_entire_day = any(k in cl_inst for k in ["entire day", "whole day", "all meals on day", "all 3 meals", "all 4 meals"])

                # Check if meal slot is specified, but day is missing/ambiguous across a multi-day plan
                if target_meal_type and target_day is None and len(existing_plan.days) > 1:
                    plan_name_disp = getattr(existing_plan, "name", None) or getattr(matched_plan, "name", "your meal plan")
                    context_prompt = (
                        f"The user wants to replace or update their {target_meal_type} in '{plan_name_disp}', but did not specify which day. "
                        f"In polite, natural, friendly English, ask which day's {target_meal_type} they would like to replace (e.g. Day 1, Day 2, or Day 3)."
                    )
                    response_text = await supervisor.generate_conversational_response(
                        user_message=user_message,
                        context=context_prompt,
                        history=history,
                        user_name=user_profile.get("name")
                    )
                    state.system_message = response_text
                    state.status = "conversational"
                    state.quick_options = [
                        {"label": f"Day {d.day_number} {target_meal_type.capitalize()}", "value": f"Replace Day {d.day_number} {target_meal_type} in \"{plan_name_disp}\""}
                        for d in existing_plan.days
                    ]
                    state.pending_workflow = make_edit_pending(
                        response_text, state.quick_options, "which_day",
                        plan_name_disp, user_message, user_profile
                    )
                    return state

                # Check if day is specified, but meal slot is missing/ambiguous
                if not is_entire_day and target_day and not target_meal_type:
                    plan_name_disp = getattr(existing_plan, "name", None) or getattr(matched_plan, "name", "your meal plan")
                    context_prompt = (
                        f"The user wants to update Day {target_day} in '{plan_name_disp}', but did not specify which meal slot (Breakfast, Lunch, Dinner, or Snack). "
                        f"In polite, natural, friendly English, ask which meal on Day {target_day} they would like to replace."
                    )
                    response_text = await supervisor.generate_conversational_response(
                        user_message=user_message,
                        context=context_prompt,
                        history=history,
                        user_name=user_profile.get("name")
                    )
                    state.system_message = response_text
                    state.status = "conversational"
                    target_day_plan = next((d for d in existing_plan.days if d.day_number == target_day), existing_plan.days[0] if existing_plan.days else None)
                    meals_avail = target_day_plan.meals if target_day_plan else []
                    state.quick_options = [
                        {"label": f"Day {target_day} {m.meal_type.capitalize()}", "value": f"Replace Day {target_day} {m.meal_type} in \"{plan_name_disp}\""}
                        for m in meals_avail
                    ]
                    state.pending_workflow = make_edit_pending(
                        response_text, state.quick_options, "which_slot",
                        plan_name_disp, user_message, user_profile
                    )
                    return state

    # =========================================================================
    # Step 7: Handle General Conversational Intents (chat, app_info, out_of_scope, explain)
    # =========================================================================
    # =========================================================================
    # Step 7: Handle General Conversational Intents (chat, app_info, out_of_scope, explain)
    # =========================================================================
    if intent_data.intent in ("chat", "app_info", "out_of_scope", "explain"):
        # Answer the user's question first. If a workflow is pending, also ask
        # whether they'd like to continue it or start a new plan instead.
        response_text = await supervisor.handle_conversational_intent(
            state=state,
            user_message=user_message,
            intent=intent_data.intent,
            history=history
        )
        if state.pending_workflow:
            append_resume_prompt(state, response_text, state.pending_workflow)
        else:
            state.system_message = response_text
            state.status = "conversational" if intent_data.intent != "out_of_scope" else "out_of_scope"
        return state

    # =========================================================================
    # Step 8: Handle New Plan (Diet Conflict Check & Calorie Guardrails)
    # =========================================================================
    # Check and apply requested ingredient exclusions (e.g. pork, beef, turkey, dairy) for all planning/editing intents
    # Exclusions are separate from allergies - they only apply to the current meal plan, not permanently stored
    if intent_data.intent in ("new_plan", "edit_meal"):
        # Exclusions are passed to planner separately, not added to user profile allergies
        state.user_profile["allergies"] = list(user_profile.get("allergies", []))
        # Exclusions will be passed to planner via state.intent_data.requested_exclusions

    if intent_data.intent == "new_plan":

        profile_diet = str(user_profile.get("diet_type", "veg")).lower()

        # ------------------------------------------------------------------
        # Resolve requested parameters, carrying forward anything the user has
        # already told us while resolving a pending conflict question. This is
        # what lets a partial answer (e.g. "I prefer vegetarian") be honoured
        # without re-asking the question the user just answered.
        # ------------------------------------------------------------------
        pending = state.pending_workflow
        pending_info = dict(pending.extracted_info) if (pending and pending.extracted_info) else {}

        msg_lower = user_message.lower()
        mentions_diet = any(k in msg_lower for k in (
            "veg", "vegan", "non-veg", "nonveg", "vegetarian", "non vegetarian"
        ))
        mentions_cal = bool(re.search(
            r"(\d[\d,]{2,4}\s*(kcal|calories|cal\b))|((kcal|calories|cal\b)[^\d]{0,8}\d[\d,]{2,4})",
            msg_lower
        ))

        # Only trust a diet/calorie value from the LLM when the user actually
        # mentioned it in THIS message (guards against carries-over from history).
        user_diet = intent_data.chosen_diet_type or intent_data.requested_diet_type
        if not (user_diet and mentions_diet):
            user_diet = None
        user_cal = (
            intent_data.chosen_calorie_target
            if intent_data.chosen_calorie_target is not None
            else intent_data.requested_calories
        )
        if not (user_cal is not None and mentions_cal):
            user_cal = None

        req_diet = str(user_diet or pending_info.get("requested_diet") or "").lower() or None
        req_cal = user_cal if user_cal is not None else pending_info.get("cal_val")
        req_days = intent_data.requested_days or pending_info.get("days_val") or settings.default_plan_days

        # A conflict question that was already asked is considered resolved when
        # the user supplies the corresponding detail in this message.
        diet_pending = bool(pending_info.get("diet_pending"))
        cal_pending = bool(pending_info.get("cal_pending"))
        diet_resolved = bool(pending_info.get("diet_resolved")) or (diet_pending and user_diet is not None)
        cal_resolved = bool(pending_info.get("cal_resolved")) or (cal_pending and user_cal is not None)

        diet_conflict = bool(req_diet and req_diet != profile_diet)

        # ------------------------------------------------------------------
        # Diet conflict is still open -> ask the user (LLM-generated prompt).
        # Calorie safety is evaluated separately so a safe calorie target does
        # not get bundled into the diet question.
        # ------------------------------------------------------------------
        if diet_conflict and not diet_resolved:
            requested_diet = req_diet
            is_safe, err_msg, meta = supervisor.check_input_guardrail(
                user_profile=user_profile,
                requested_calories=req_cal,
                requested_days=req_days,
                fixed_meals=fixed,
                requested_diet_type=None
            )
            meta = dict(meta or {})
            meta["action"] = "diet_conflict"
            meta["profile_diet"] = profile_diet
            meta["requested_diet"] = requested_diet
            cal_val = int(req_cal or 0) or int(meta.get("suggested_target", 2000))
            days_val = req_days
            is_calorie_unsafe = not is_safe

            # Use LLM to generate natural conflict resolution prompt
            conflict_prompt = await supervisor.generate_diet_conflict_prompt(
                user_name=user_profile.get("name", "there"),
                profile_diet=profile_diet,
                requested_diet=requested_diet,
                cal_val=cal_val,
                days_val=days_val,
                is_calorie_unsafe=is_calorie_unsafe,
                meta=meta,
                history=history
            )
            state.system_message = conflict_prompt
            state.status = "conversational"
            
            # Quick options for one-click resolution
            req_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(requested_diet, requested_diet.capitalize())
            profile_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(profile_diet, profile_diet.capitalize())
            if is_calorie_unsafe:
                suggested = int(meta.get("suggested_target", 2250.0))
                state.quick_options = [
                    {"label": f"Plan {days_val} days at {cal_val:,} kcal ({req_display})", "value": f"Create {days_val}-day {req_display.lower()} meal plan for {cal_val} kcal (confirmed)"},
                    {"label": f"Plan {days_val} days at {suggested:,} kcal (Recommended)", "value": f"Create {days_val}-day {req_display.lower()} meal plan for {suggested} kcal (confirmed)"}
                ]
            else:
                state.quick_options = [
                    {"label": f"Create {req_display} Plan", "value": f"Create {days_val}-day {req_display.lower()} meal plan for {cal_val} kcal (confirmed)"},
                    {"label": f"Use {profile_display} (Default)", "value": f"Create {days_val}-day {profile_display.lower()} meal plan for {cal_val} kcal (confirmed)"}
                ]
            
            await supervisor.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="user",
                msg_type="feedback",
                content=conflict_prompt,
                payload={"action": "diet_conflict_prompt", "profile_diet": profile_diet, "requested_diet": requested_diet}
            )
            
            # Set pending workflow for diet conflict (and potentially calorie guardrail)
            state.pending_workflow = PendingWorkflow(
                workflow_type="new_plan",
                pending_conflicts=["diet_conflict"] + (["calorie_guardrail"] if is_calorie_unsafe else []),
                asked_questions=["diet_preference"] + (["calorie_confirmation"] if is_calorie_unsafe else []),
                extracted_info={
                    "profile_diet": profile_diet,
                    "requested_diet": requested_diet,
                    "cal_val": cal_val,
                    "days_val": days_val,
                    "is_calorie_unsafe": is_calorie_unsafe,
                    "diet_pending": True,
                    "cal_pending": bool(is_calorie_unsafe),
                    "diet_resolved": False,
                    "cal_resolved": False,
                    # Snapshot of the exact prompt shown, so resuming after an
                    # interruption re-presents the same question to the user.
                    "prompt_text": conflict_prompt,
                    "prompt_quick_options": list(state.quick_options or []),
                },
                next_expected_decision="diet_conflict_resolution",
                original_intent="new_plan",
                original_message=user_message,
                user_profile_at_start=user_profile.copy(),
                created_at=datetime.utcnow().isoformat()
            )
            return state

        # Resolved effective diet type for this plan
        effective_diet = user_diet or req_diet or profile_diet
        user_profile["diet_type"] = effective_diet
        state.user_profile["diet_type"] = effective_diet
        state.current_plan = None

        # Ensure state reflects requested days and calories explicitly
        state.days = req_days
        state.calorie_target = req_cal
        if getattr(intent_data, "new_plan_name", None):
            state.plan_name = intent_data.new_plan_name

        # Calorie Guardrail Check: evaluate against the resolved diet (no diet
        # conflict) so a confirmed diet no longer re-triggers the diet question.
        if not cal_resolved:
            is_safe, err_msg, meta = supervisor.check_input_guardrail(
                user_profile=user_profile,
                requested_calories=req_cal,
                requested_days=req_days,
                fixed_meals=fixed,
                requested_diet_type=None
            )
            if not is_safe:
                msg = err_msg or "The requested calorie target is outside safe clinical guidelines for your profile."
                state.system_message = msg
                state.status = "conversational"
                suggested = meta.get("suggested_target", 2350.0)
                min_safe = meta.get("min_safe", 2250.0)
                max_safe = meta.get("max_safe", 2450.0)
                diet_lbl = {"veg": "veg", "non-veg": "non-veg", "vegan": "vegan"}.get(effective_diet, "veg")
                state.quick_options = [
                    {"label": f"Plan at {int(suggested):,} kcal (Recommended)", "value": f"Plan {req_days} days {diet_lbl} for {int(suggested)} kcal"},
                    {"label": f"Plan at {int(min_safe):,} kcal (Min Safe)", "value": f"Plan {req_days} days {diet_lbl} for {int(min_safe)} kcal"},
                    {"label": f"Plan at {int(max_safe):,} kcal (Max Safe)", "value": f"Plan {req_days} days {diet_lbl} for {int(max_safe)} kcal"}
                ]
                await supervisor.log_step(
                    state=state,
                    from_agent="supervisor",
                    to_agent="user",
                    msg_type="feedback",
                    content=msg,
                    payload={"action": "guardrail_block", "calories": req_cal, "suggested": suggested}
                )
                # Store pending workflow so the next partial answer is understood
                state.pending_workflow = PendingWorkflow(
                    workflow_type="new_plan",
                    pending_conflicts=["calorie_guardrail"],
                    asked_questions=[msg],
                    extracted_info={
                        "effective_diet": effective_diet,
                        "requested_diet": req_diet,
                        "diet_resolved": True,
                        "cal_pending": True,
                        "cal_resolved": False,
                        "cal_val": req_cal if req_cal is not None else suggested,
                        "days_val": req_days,
                        "suggested": suggested,
                        "min_safe": min_safe,
                        "max_safe": max_safe,
                        "req_days": req_days,
                        # Snapshot of the exact prompt shown, so resuming after an
                        # interruption re-presents the same question to the user.
                        "prompt_text": msg,
                        "prompt_quick_options": list(state.quick_options or []),
                    },
                    next_expected_decision="calorie_guardrail_resolution",
                    original_intent="new_plan",
                    original_message=user_message,
                    user_profile_at_start=user_profile.copy(),
                    created_at=datetime.utcnow().isoformat()
                )
                return state

        # All conflicts resolved - clear any pending workflow before planning
        state.pending_workflow = None

    # If editing existing plan, make sure user_profile["diet_type"] reflects the existing plan's diet
    if intent_data.intent == "edit_meal" and existing_plan:
        user_profile["diet_type"] = effective_plan_diet
        state.user_profile["diet_type"] = effective_plan_diet

    # =========================================================================
    # Step 9: Execute LangGraph Workflow
    # =========================================================================
    # Store intent_data in state for graph nodes that need it (e.g., exclusions)
    state.intent_data = intent_data
    graph = create_meal_planner_graph()
    result = await graph.ainvoke(state)

    if isinstance(result, PlanState):
        final_state = result
    else:
        final_state = PlanState.model_validate(result)

    # Clear pending workflow on successful workflow completion
    if final_state.current_plan and final_state.status in ("reviewing", "approved", "draft"):
        final_state.pending_workflow = None

    # =========================================================================
    # Step 10: Persist Plan to Database & Format Friendly Response
    # =========================================================================
    # Persist plan if it exists and status indicates completion (reviewing/approved/escalated/failed)
    if final_state.current_plan and final_state.status in ("reviewing", "approved", "escalated", "failed"):
        try:
            if session:
                logger.info(f"[{tid}] Persisting plan to database for user {user_id}")
                repo = PlanRepository(session)
                if intent_data.intent == "edit_meal" and existing_plan and db_plan_id:
                    is_edit_previously_approved = bool(matched_plan and getattr(matched_plan, "status", None) == "approved")
                    new_version = (existing_plan.version or 1) + 1 if is_edit_previously_approved else (existing_plan.version or 1)
                    final_state.current_plan.version = new_version
                    persist_status = final_state.status if final_state.status in ("escalated", "failed") else "draft"
                    updated_p = await repo.update_plan(
                        plan_id=db_plan_id,
                        meal_plan=final_state.current_plan,
                        grocery_list=None,  # Reset grocery list until user approves!
                        status=persist_status,
                        increment_version=is_edit_previously_approved
                    )
                    final_state.plan_id = db_plan_id
                    final_state.plan_version = new_version
                    final_state.status = persist_status
                    clean_inst = getattr(intent_data.edit_details, "instructions", "") or user_message
                    clean_inst = re.sub(r'\(Plan\s*#\d+\):?', '', clean_inst, flags=re.IGNORECASE).strip()
                    plan_title = getattr(final_state.current_plan, "name", None) or (matched_plan.name if matched_plan else "your meal plan")
                    context_prompt = (
                        f"Successfully applied the user's edit request '{clean_inst}' to meal plan '{plan_title}'. "
                        f"The plan is now Version {new_version} Draft in the Plans tab. "
                        f"In natural, polite, friendly conversational English, let the user know their modification has been applied to '{plan_title}' and that they can review it in the Plans tab and approve it whenever ready."
                    )
                    final_state.system_message = await supervisor.generate_conversational_response(
                        user_message=user_message,
                        context=context_prompt,
                        history=history,
                        user_name=user_profile.get("name")
                    )
                    final_state.quick_options = [
                        {"label": f"Approve {plan_title}", "value": f"Approve \"{plan_title}\""},
                        {"label": "Modify in chat", "value": f"I want to update \"{plan_title}\": "}
                    ]
                    # The update completed - drop any lingering in-progress update state.
                    final_state.pending_workflow = None
                    logger.info(f"[{tid}] Updated existing plan '{plan_title}' to Version {new_version} Draft (incremented={is_edit_previously_approved}).")
                else:
                    persist_status = final_state.status if final_state.status in ("escalated", "failed") else "draft"
                    plan_diet_str = getattr(final_state.current_plan, "diet_type", None) or user_profile.get("diet_type", "veg")
                    final_state.current_plan.diet_type = plan_diet_str
                    display_diet = "Non-Veg" if plan_diet_str == "non-veg" else plan_diet_str.capitalize()
                    p_name = getattr(final_state, "plan_name", None) or getattr(final_state.current_plan, "name", None) or f"{len(final_state.current_plan.days)}-Day {display_diet} Plan"
                    final_state.current_plan.name = p_name
                    created_p = await repo.create_plan(
                        user_id=user_id,
                        days=len(final_state.current_plan.days),
                        calorie_target=final_state.current_plan.target_calories,
                        meal_plan=final_state.current_plan,
                        grocery_list=None,  # Do not generate grocery list until user approves!
                        status=persist_status,
                        name=p_name,
                        diet_type=plan_diet_str,
                        version=1
                    )
                    final_state.plan_id = created_p.id
                    final_state.plan_version = 1
                    final_state.status = persist_status
                    context_prompt = (
                        f"Successfully generated new meal plan '{p_name}' (Version 1 {persist_status.capitalize()}) with a target of {int(final_state.current_plan.target_calories):,} kcal/day across {len(final_state.current_plan.days)} days. "
                        f"In natural, polite, friendly conversational English, let the user know their meal plan has been created and is ready to review in the Plans tab, and invite them to approve it or make adjustments whenever ready."
                    )
                    final_state.system_message = await supervisor.generate_conversational_response(
                        user_message=user_message,
                        context=context_prompt,
                        history=history,
                        user_name=user_profile.get("name")
                    )
                    final_state.quick_options = [
                        {"label": f"Approve {p_name}", "value": f"Approve \"{p_name}\""},
                        {"label": "Modify in chat", "value": f"I want to update \"{p_name}\": "}
                    ]
                    logger.info(f"[{tid}] Persisted new plan to database as Version 1 Draft.")
            else:
                async with AsyncSessionLocal() as temp_session:
                    repo = PlanRepository(temp_session)
                    persist_status = final_state.status if final_state.status in ("escalated", "failed") else "draft"
                    if intent_data.intent == "edit_meal" and existing_plan and db_plan_id:
                        is_edit_previously_approved = bool(matched_plan and getattr(matched_plan, "status", None) == "approved")
                        new_version = (existing_plan.version or 1) + 1 if is_edit_previously_approved else (existing_plan.version or 1)
                        final_state.current_plan.version = new_version
                        await repo.update_plan(
                            plan_id=db_plan_id,
                            meal_plan=final_state.current_plan,
                            grocery_list=None,
                            status=persist_status,
                            increment_version=is_edit_previously_approved
                        )
                        final_state.plan_id = db_plan_id
                        final_state.plan_version = new_version
                        final_state.status = persist_status
                        clean_inst = getattr(intent_data.edit_details, "instructions", "") or user_message
                        clean_inst = re.sub(r'\(Plan\s*#\d+\):?', '', clean_inst, flags=re.IGNORECASE).strip()
                        plan_title = getattr(final_state.current_plan, "name", "your meal plan")
                        context_prompt = (
                            f"Successfully applied the user's edit request '{clean_inst}' to meal plan '{plan_title}'. "
                            f"The plan is now Version {new_version} Draft in the Plans tab. "
                            f"In natural, polite, friendly conversational English, let the user know their modification has been applied to '{plan_title}' and that they can review it in the Plans tab and approve it whenever ready."
                        )
                        final_state.system_message = await supervisor.generate_conversational_response(
                            user_message=user_message,
                            context=context_prompt,
                            history=history,
                            user_name=user_profile.get("name")
                        )
                        final_state.quick_options = [
                            {"label": f"Approve {plan_title}", "value": f"Approve \"{plan_title}\""},
                            {"label": "Modify in chat", "value": f"I want to update \"{plan_title}\": "}
                        ]
                        logger.info(f"[{tid}] Updated existing plan '{plan_title}' to Version {new_version} Draft (incremented={is_edit_previously_approved}).")
                    else:
                        plan_diet_str = getattr(final_state.current_plan, "diet_type", None) or user_profile.get("diet_type", "veg")
                        final_state.current_plan.diet_type = plan_diet_str
                        display_diet = "Non-Veg" if plan_diet_str == "non-veg" else plan_diet_str.capitalize()
                        p_name = getattr(final_state, "plan_name", None) or getattr(final_state.current_plan, "name", None) or f"{len(final_state.current_plan.days)}-Day {display_diet} Plan"
                        final_state.current_plan.name = p_name
                        persist_status = final_state.status if final_state.status in ("escalated", "failed") else "draft"
                        created_p = await repo.create_plan(
                            user_id=user_id,
                            days=len(final_state.current_plan.days),
                            calorie_target=final_state.current_plan.target_calories,
                            meal_plan=final_state.current_plan,
                            grocery_list=None,
                            status=persist_status,
                            name=p_name,
                            diet_type=plan_diet_str,
                            version=1
                        )
                        final_state.plan_id = created_p.id
                        final_state.plan_version = 1
                        final_state.status = persist_status
                        context_prompt = (
                            f"Successfully generated new meal plan '{p_name}' (Version 1 {persist_status.capitalize()}) with a target of {int(final_state.current_plan.target_calories):,} kcal/day across {len(final_state.current_plan.days)} days. "
                            f"In natural, polite, friendly conversational English, let the user know their meal plan has been created and is ready to review in the Plans tab, and invite them to approve it or make adjustments whenever ready."
                        )
                        final_state.system_message = await supervisor.generate_conversational_response(
                            user_message=user_message,
                            context=context_prompt,
                            history=history,
                            user_name=user_profile.get("name")
                        )
                        final_state.quick_options = [
                            {"label": f"Approve {p_name}", "value": f"Approve \"{p_name}\""},
                            {"label": "Modify in chat", "value": f"I want to update \"{p_name}\": "}
                        ]
        except Exception as e:
            logger.error(f"[{tid}] Failed to persist plan to database: {e}", exc_info=True)

    return final_state
