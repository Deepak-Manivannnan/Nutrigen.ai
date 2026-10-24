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
        # The classifier is told to extract the name "in quotes or mentioned",
        # so a real plan arrives here as '"Good" plan'. Compare without the
        # quotation marks: leaving them in meant an existing plan never
        # matched, and every caller then treated it as missing.
        t_clean = re.sub(r"[\"'`]+", "", target_name).strip().lower()
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


# "my plan", "the meal plan" - a reference that names nothing in particular.
# These must keep resolving through history and the single-plan fallback
# rather than being reported as missing, so they are excluded from the
# not-found check below. Anything else ("keto plan") claims a specific plan
# that either exists or does not.
_GENERIC_PLAN_REF = re.compile(
    r"^(?:my|our|the|this|that)?\s*(?:current|new|latest|saved)?\s*"
    r"(?:meal\s+)?plans?(?:\s+\d+)?$",
    re.IGNORECASE,
)


def _is_generic_plan_ref(name: Optional[str]) -> bool:
    """True when the extracted plan name carries no identifying words."""
    if not name or not name.strip():
        return True
    return bool(_GENERIC_PLAN_REF.match(name.strip()))


# =========================================================================
# Pending-workflow interruption helpers (LLM-first)
#
# Routing decisions here are driven by the LLM's structured intent
# (intent + confirmation flags + extracted params), never by matching
# keywords in the raw message. All user-facing wording is composed by the
# supervisor LLM from factual context; only quick-option *values* stay
# deterministic because the frontend treats them as machine contracts.
# =========================================================================

def _pending_answer_signals(intent_data) -> tuple:
    """Diet/calorie values the LLM extracted, split by confirmation backing."""
    diet_val = intent_data.chosen_diet_type or intent_data.requested_diet_type
    diet_flagged = bool(intent_data.diet_conflict_confirmed or intent_data.chosen_diet_type)
    if intent_data.chosen_calorie_target is not None:
        cal_val, cal_flagged = intent_data.chosen_calorie_target, True
    elif intent_data.requested_calories is not None:
        cal_val, cal_flagged = intent_data.requested_calories, bool(intent_data.calorie_guardrail_confirmed)
    else:
        cal_val, cal_flagged = None, False
    return diet_val, diet_flagged, cal_val, cal_flagged

def _llm_answer_signals(intent_data) -> dict:
    """Whether the LLM understood THIS message as answering pending questions.

    The classifier sets confirmation flags only when the user expresses the
    corresponding choice, so these flags — not substring matching — decide.
    """
    diet_val, diet_flagged, cal_val, cal_flagged = _pending_answer_signals(intent_data)
    return {
        "answered_diet": diet_flagged,
        "answered_cal": cal_flagged,
        "answered_any": diet_flagged or cal_flagged,
        "fresh_diet": intent_data.requested_diet_type or None,
        "fresh_cal": intent_data.requested_calories,
    }

def _contradicts_pending_answers(intent_data, pending_info: dict) -> bool:
    """Whether the LLM-extracted values contradict the pending offered options.

    A diet answer must be the requested diet or the profile diet; a calorie
    answer must be the pending target or one of the offered safe values. All
    compared as structured data from the pending state — no text matching.
    """
    diet_val, _, cal_val, _ = _pending_answer_signals(intent_data)
    if diet_val:
        allowed_diets = {
            str(v).lower()
            for v in (pending_info.get("requested_diet"), pending_info.get("profile_diet"))
            if v
        }
        if allowed_diets and str(diet_val).lower() not in allowed_diets:
            return True
    if cal_val is not None:
        allowed_cals = set()
        for key in ("cal_val", "suggested", "min_safe", "max_safe"):
            try:
                if pending_info.get(key) is not None:
                    allowed_cals.add(float(pending_info.get(key)))
            except (TypeError, ValueError):
                continue
        if allowed_cals and float(cal_val) not in allowed_cals:
            return True
    return False

def _is_complete_fresh_spec(intent_data) -> bool:
    """Whether the LLM extracted a self-contained new spec (diet + calories + days)."""
    diet_val, _, cal_val, _ = _pending_answer_signals(intent_data)
    return bool(diet_val is not None and cal_val is not None and intent_data.requested_days)

def _pending_answer_values(intent_data, pending_info: dict) -> tuple:
    """Resolve diet/calorie values for the new-plan branch from LLM signals.

    When a conflict question is open, only the LLM's confirmation-backed
    values (chosen_*/confirmed flags) count as answers — this guards against
    values carried over from history. For a fresh request there is no pending
    question, so the extracted request values are used directly.
    """
    diet_open = bool(pending_info.get("diet_pending")) and not bool(pending_info.get("diet_resolved"))
    cal_open = bool(pending_info.get("cal_pending")) and not bool(pending_info.get("cal_resolved"))
    if diet_open:
        user_diet = intent_data.chosen_diet_type or (
            intent_data.requested_diet_type if intent_data.diet_conflict_confirmed else None
        )
    else:
        user_diet = intent_data.chosen_diet_type or intent_data.requested_diet_type
    if cal_open:
        user_cal = (
            intent_data.chosen_calorie_target
            if intent_data.chosen_calorie_target is not None
            else (intent_data.requested_calories if intent_data.calorie_guardrail_confirmed else None)
        )
    else:
        user_cal = (
            intent_data.chosen_calorie_target
            if intent_data.chosen_calorie_target is not None
            else intent_data.requested_calories
        )
    return user_diet, user_cal

def _describe_competing_action(intent: str) -> str:
    return {
        "new_plan": "create a new meal plan",
        "rename_plan": "rename one of your existing plans",
        "delete_plan": "delete a meal plan",
        "approve_plan": "approve a meal plan",
        "edit_meal": "update an existing meal plan",
        "list_plans": "view your saved meal plans",
    }.get(intent, "do that")

def _describe_pending_work(pending) -> str:
    if pending.workflow_type == "edit_meal":
        return "updating your meal plan"
    if pending.workflow_type == "delete_plan":
        return "deleting one of your meal plans"
    if pending.workflow_type == "approve_plan":
        return "approving one of your meal plans"
    return "creating your new meal plan"

async def _compose_interruption_message(supervisor, *, user_message: str, user_name: str,
                                        pending, competing_intent: str,
                                        history) -> str:
    """LLM-composed: acknowledge the new request + ask continue-vs-new."""
    context_prompt = (
        f"The user just asked you to {_describe_competing_action(competing_intent)}: '{user_message}'. "
        f"However, you were in the middle of {_describe_pending_work(pending)} for them, "
        f"and the pending questions for that have not been answered yet. "
        f"In warm, natural conversational English (2-3 sentences), acknowledge what they just asked for, "
        f"mention you were in the middle of the other task, and ask which they would like to do: "
        f"continue the in-progress work, or proceed with the new request instead. Make it clear that picking the new "
        f"request means setting aside the in-progress work, so the choice is honest. End with the question. "
        f"CRITICAL: nothing has been done yet. Do NOT say you have renamed, deleted, approved, updated or "
        f"created anything, and do NOT report any result. You are only asking which they want."
    )
    return await supervisor.generate_conversational_response(
        user_message=user_message,
        context=context_prompt,
        history=history,
        user_name=user_name,
    )

async def append_resume_prompt(supervisor, state, response_text: str, pending,
                               user_message: str, history, user_name: str) -> None:
    """Keep a just-given answer verbatim, then append an LLM-composed bridge
    asking whether to continue the pending work or start a new plan."""
    workflow_desc = (
        "updating your meal plan" if pending.workflow_type == "edit_meal" else "creating your meal plan"
    )
    bridge_context = (
        f"You just answered the user's question. But you were also in the middle of {workflow_desc} "
        f"for them, with pending questions still unanswered. "
        f"Write ONE warm, natural bridge sentence (no greeting, no bullet points) that transitions from "
        f"the answer to asking whether they would like to continue that in-progress work or start a new plan instead. "
        f"Mention explicitly that you were in the middle of that task, so it is clear which work you mean."
    )
    bridge = await supervisor.generate_conversational_response(
        user_message=f"(continuing the conversation; the user had asked: '{user_message}')",
        context=bridge_context,
        history=history,
        user_name=user_name,
    )
    state.system_message = f"{response_text}\n\n{bridge}"
    state.status = "conversational"
    resume_label = "Continue the update" if pending.workflow_type == "edit_meal" else "Resume"

async def ask_for_new_plan_specs(supervisor, state, user_profile, user_message: str, history) -> None:
    """Offer profile defaults vs. custom specs for a new plan.

    The profile already holds diet/calorie preferences, so instead of asking
    for everything from scratch, the LLM offers: create with profile defaults,
    or share custom specifications. A pending state preserves the offer across
    navigation; quick options stay strictly relevant to this choice.
    """
    p_diet = str(user_profile.get("diet_type", "veg")).lower()
    diet_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(p_diet, p_diet.capitalize())
    sug = int(user_profile.get("suggested_target_calories") or 2100)
    days_default = settings.default_plan_days
    name = user_profile.get("name", "there")
    context_prompt = (
        f"The user wants a brand-new meal plan. Their saved profile already has {diet_display} diet and a "
        f"suggested target of {sug:,} kcal/day. "
        f"In warm, natural conversational English (2-3 sentences), say you'd be happy to build it, then ask: "
        f"shall I create a {days_default}-day plan with those profile preferences ({diet_display}, {sug:,} kcal/day), "
        f"or do they have their own specifications (days, calorie target, diet type)? End with the question."
    )
    specs_text = await supervisor.generate_conversational_response(
        user_message=user_message,
        context=context_prompt,
        history=history,
        user_name=name,
    )
    state.system_message = specs_text
    state.status = "conversational"
    defaults_value = f"Create a {days_default}-day {p_diet} meal plan for {sug} kcal with my profile defaults"
    # Preserve the offer (and the profile defaults) across navigation so a
    # follow-up "yes, use my profile" still resolves.
    state.pending_workflow = PendingWorkflow(
        workflow_type="new_plan",
        pending_conflicts=["specs_choice"],
        asked_questions=[specs_text],
        extracted_info={
            "awaiting_specs_choice": True,
            "profile_diet": p_diet,
            "suggested_calories": sug,
            "days_default": days_default,
            "prompt_text": specs_text,
        },
        next_expected_decision="specs_choice",
        original_intent="new_plan",
        original_message=user_message,
        user_profile_at_start=dict(user_profile or {}),
        created_at=datetime.utcnow().isoformat()
    )

async def ask_for_custom_specs(supervisor, state, user_profile, user_message: str,
                              history, pending) -> None:
    """The user took the "I'll give specs" branch: now actually ask for them.

    The defaults-vs-custom offer only asks WHICH path they want; once they pick
    custom specs we have to ask what those specs are. Replaying the stored
    offer text here is what made the bot answer "I have my own
    specifications" with the identical sentence it had just used.
    """
    p_diet = str(user_profile.get("diet_type", "veg")).lower()
    diet_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(
        p_diet, p_diet.capitalize())
    sug = int(user_profile.get("suggested_target_calories") or 2100)
    days_default = settings.default_plan_days
    name = user_profile.get("name", "there")

    context_prompt = (
        f"The user wants a brand-new meal plan with their OWN specifications rather than the "
        f"profile defaults (their profile suggests {diet_display}, {sug:,} kcal/day, "
        f"{days_default} days). "
        f"In warm, natural conversational English (2-3 sentences), no greeting, acknowledge that you'll "
        f"build it to their specifications, then ask them to tell you what they want: how many days, "
        f"what daily calorie target, and which diet (veg, non-veg or vegan). "
        f"You may mention any food to exclude or any meal they want locked, but only as an optional "
        f"extra, not as a list of forms to fill in. "
        f"IMPORTANT: this must be a NEW question that asks for the specifics they just offered to "
        f"give. Do NOT repeat or reword any earlier question about using their profile defaults."
    )
    specs_text = await supervisor.generate_conversational_response(
        user_message=user_message,
        context=context_prompt,
        history=history,
        user_name=name,
    )
    state.system_message = specs_text
    state.status = "conversational"
    # Carry the original offer's profile context forward and flip the flag, so
    # a later bare "7 days" still resolves against this new plan.
    info = dict(pending.extracted_info or {})
    info.update({
        "awaiting_specs_choice": False,
        "awaiting_custom_specs": True,
        "profile_diet": p_diet,
        "suggested_calories": sug,
        "days_default": days_default,
        "prompt_text": specs_text,
    })
    state.pending_workflow = pending.model_copy(update={"extracted_info": info})

def _tappable_plan_list(all_plans) -> str:
    """The numbered tappable plan list appended to a composed reply.

    One list only: any enumeration the LLM added itself is stripped first so
    the plans never render twice.
    """
    if not all_plans:
        return ""
    lines = [
        f"{i}. [{getattr(p, 'name', None) or f'Plan #{p.id}'}](plan://{p.id})"
        for i, p in enumerate(all_plans, start=1)
    ]
    return "\n".join(lines)

async def _answer_conversational_with_plans(supervisor, state, intent_data, all_plans,
                                            user_message, history, user_name) -> str:
    """Answer a conversational/question intent, appending the plan list when asked.

    Shared by every mid-workflow question branch so a "list my plans" asked
    while a workflow is pending behaves the same wherever it is asked from.
    """
    if intent_data.intent == "list_plans":
        names = ", ".join(getattr(p, "name", None) or f"Plan #{p.id}" for p in all_plans)
        if not all_plans:
            context = (
                "The user asked to see their saved meal plans, but they don't have any yet. "
                "In one warm sentence, let them know that and invite them to create one."
            )
        else:
            context = (
                f"The user asked to see their saved meal plans: {names}. "
                "In one warm sentence, tell them their plans are listed as tappable links below."
            )
        answer = await supervisor.generate_conversational_response(
            user_message=user_message,
            context=context,
            history=history,
            user_name=user_name,
        )
        answer = _strip_enumeration(answer)
        links = _tappable_plan_list(all_plans)
        return f"{answer}\n{links}" if links else answer

    return await supervisor.handle_conversational_intent(
        state=state, user_message=user_message,
        intent=intent_data.intent, history=history,
    )

async def unique_plan_name(repo, user_id: int, base_name: str) -> str:
    """Make a plan name unique within the user's account.

    Two plans called "7-Day Non-veg Plan" are indistinguishable in the Plans
    dropdown, which is how a leftover draft ended up looking like the approved
    one. Suffix the first duplicate with a counter, and the second with a date.
    """
    base = (base_name or "Meal Plan").strip()
    existing = {
        (getattr(p, "name", None) or "").strip().lower()
        for p in (await repo.get_all_by_user(user_id))
    }
    if base.lower() not in existing:
        return base
    for n in range(2, 100):
        candidate = f"{base} ({n})"
        if candidate.lower() not in existing:
            return candidate
    return f"{base} ({datetime.utcnow().strftime('%d %b %Y %H:%M')})"

def make_edit_pending(
    response_text: str,
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
        },
        next_expected_decision="edit_clarification",
        original_intent="edit_meal",
        original_message=user_message,
        user_profile_at_start=dict(user_profile or {}),
        created_at=datetime.utcnow().isoformat()
    )

def make_delete_pending(
    response_text: str,
    user_message: str,
    user_profile: Dict[str, Any],
    target_plan_name: Optional[str] = None,
) -> PendingWorkflow:
    """Persist a plan-deletion confirmation so 'Cancel' (and navigation) resolve
    against it instead of falling through to a generic no-session message."""
    return PendingWorkflow(
        workflow_type="delete_plan",
        pending_conflicts=["delete_confirmation"],
        asked_questions=[response_text],
        extracted_info={
            "target_plan_name": target_plan_name,
            "reprompt_text": response_text,
        },
        next_expected_decision="delete_confirmation",
        original_intent="delete_plan",
        original_message=user_message,
        user_profile_at_start=dict(user_profile or {}),
        created_at=datetime.utcnow().isoformat()
    )

def make_approve_pending(
    response_text: str,
    user_message: str,
    user_profile: Dict[str, Any],
    target_plan_name: Optional[str] = None,
) -> PendingWorkflow:
    """Persist a pending approval so a follow-up 'yes' — or a plan name — resolves
    against the plan we actually asked about, instead of whichever sorts first."""
    return PendingWorkflow(
        workflow_type="approve_plan",
        pending_conflicts=["approve_confirmation"],
        asked_questions=[response_text],
        extracted_info={
            "target_plan_name": target_plan_name,
            "reprompt_text": response_text,
        },
        next_expected_decision="approve_confirmation",
        original_intent="approve_plan",
        original_message=user_message,
        user_profile_at_start=dict(user_profile or {}),
        created_at=datetime.utcnow().isoformat(),
    )

# Phrases that report a completed plan action. Used to catch the model
# narrating an approval/rename/delete that never ran - the failure mode where
# the user is told "I've approved your plan" and the row stays in draft.
_DONE_CLAIM_RE = re.compile(
    r"\b(?:"
    # "I have gone ahead and officially approved ...", "I've successfully
    # renamed ..." - the filler words arrive in any order, so allow a run of
    # known ones rather than a fixed sequence.
    r"i(?:'ve| have| am| will| can)?\s+"
    r"(?:(?:just|already|officially|successfully|fully|now|"
    r"gone\s+ahead\s+and|out\s+there)\s+){0,4}"
    r"(?:approved|deleted|renamed|created|generated|built|updated|saved|"
    r"completed|finali[sz]ed|ready)"
    r"|all\s+done"
    r"|that'?s\s+done"
    r"|is\s+now\s+(?:ready|generated|approved|complete)"
    r")\b",
    re.IGNORECASE,
)


async def _ask_without_claiming(supervisor, *, user_message, context, history, user_name):
    """Compose a question, then reject any reply that claims an action happened.

    The ask-branches (approve, rename, delete, edit) all end in "should I go
    ahead?". The model sometimes answers its own question and narrates the
    result, which reads as a completed action that never ran. Detected here and
    rewritten into the question it was actually asked to ask.
    """
    text = await supervisor.generate_conversational_response(
        user_message=user_message, context=context,
        history=history, user_name=user_name,
    )
    if not _DONE_CLAIM_RE.search(text or ""):
        return text

    logger.info("Rewriting a reply that claimed an action which had not run.")
    return await supervisor.generate_conversational_response(
        user_message=user_message,
        context=(
            f"{context}\n"
            f"IMPORTANT CORRECTION: your previous attempt claimed that the work was already done. "
            f"It was NOT - nothing has been changed yet. Write only the question, asking whether they "
            f"want to proceed. Never say you have approved, deleted, renamed, created or updated anything."
        ),
        history=history,
        user_name=user_name,
    )


def _strip_enumeration(text: str) -> str:
    """Remove enumerated/bulleted lines from an LLM-composed list intro.

    The deterministic tappable plan list is appended separately, so any
    numbering the model adds itself would render the plans twice (once plain,
    once clickable). This keeps exactly one list. Structural line-pattern
    filtering of our own composed message — not input understanding.
    """
    if not text:
        return text
    kept = [ln for ln in text.split("\n")
            if not re.match(r"^\s*(\d+[.)]|[-•*])\s+", ln)]
    return "\n".join(kept).strip()

async def _decline_and_represent_pending(supervisor, state, pending, user_message: str,
                                         history, user_name: str) -> None:
    """Out-of-scope question asked mid-workflow: decline WITHOUT answering it,
    then re-present ONLY the pending question (stored snapshot). The declined
    answer must never be echoed or repeated anywhere in the reply."""
    decline = await supervisor.generate_out_of_scope_decline(
        user_message, history=history, user_name=user_name,
    )
    if pending.workflow_type in ("edit_meal", "approve_plan"):
        text = pending.extracted_info.get("reprompt_text") or "Where would you like to continue with your update?"
    elif pending.workflow_type == "delete_plan":
        text = pending.extracted_info.get("reprompt_text") or "Which meal plan would you like to delete?"
    else:
        text = pending.extracted_info.get("prompt_text") or "Let's continue creating your meal plan."
    state.system_message = f"{decline}\n\n{text}"
    state.status = "conversational"
    # Pending workflow is preserved as-is (still awaiting an answer).

def _same_dish(old_meal, new_meal) -> bool:
    """Whether the regenerated meal is the same dish (drift, not a change)."""
    a = (getattr(old_meal, "recipe_name", None) or "").strip().lower()
    b = (getattr(new_meal, "recipe_name", None) or "").strip().lower()
    return bool(a) and a == b

_DISH_STOPWORDS = {"dish", "dishes", "meal", "meals", "food", "foods", "recipe", "recipes",
                   "menu", "item", "items", "that", "this", "it", "the", "a", "an", "some", "all",
                   "snack", "snacks", "breakfast", "lunch", "dinner"}

def _edit_exclusion_terms(intent_data) -> list:
    """Effective exclusion terms for an edit: the LLM's requested_exclusions
    plus significant words from dish_to_replace (structured LLM output)."""
    exclusions = [str(e).strip().lower() for e in (getattr(intent_data, "requested_exclusions", None) or [])]
    exclusions = [e for e in exclusions if e]
    ed = getattr(intent_data, "edit_details", None)
    _dish_rep = (getattr(ed, "dish_to_replace", None) or "").strip().lower()
    for _tok in re.split(r"[^a-z]+", _dish_rep):
        if len(_tok) >= 3 and _tok not in _DISH_STOPWORDS and _tok not in exclusions:
            exclusions.append(_tok)
    return exclusions

def _meal_matches_exclusions(meal, exclusions) -> bool:
    """Whether a meal's recipe/ingredients contain any excluded ingredient."""
    if not exclusions:
        return False
    hay = " ".join(
        [getattr(meal, "recipe_name", None) or ""]
        + [getattr(ing, "name", None) or "" for ing in (getattr(meal, "ingredients", None) or [])]
    ).lower()
    for ex in exclusions:
        ex = str(ex or "").strip().lower()
        if ex and ex in hay:
            return True
    return False

def _surgically_merge_edit(old_plan, new_plan, intent_data):
    """Merge a regenerated plan over the previous version, keeping every
    non-targeted meal byte-identical (dish, calories, macros, ingredients,
    instructions) so unrelated dishes never drift.

    Only these slots keep the regenerated meal: the explicitly targeted
    (day, meal_type) slot, a whole targeted day, newly added slots (e.g. a new
    snack), slots whose original meal contained an excluded ingredient, or
    everything for an explicit plan-wide scope. Missing original slots are
    re-added unless the user asked to remove them.
    """
    ed = intent_data.edit_details if intent_data else None
    target_day = getattr(ed, "day", None) if ed else None
    target_meal = ((getattr(ed, "meal_type", None) or "").lower()) if ed else None
    operation = ((getattr(ed, "operation", None) or "replace")) if ed else "replace"
    scope = getattr(ed, "edit_scope", None) if ed else None
    exclusions = _edit_exclusion_terms(intent_data)

    old_by_slot = {}
    for d in (getattr(old_plan, "days", None) or []):
        for m in (getattr(d, "meals", None) or []):
            old_by_slot[(d.day_number, (getattr(m, "meal_type", None) or "").lower())] = m

    kept_count, restored_count = 0, 0
    merged = new_plan.model_copy(deep=True)
    for day in (getattr(merged, "days", None) or []):
        old_day = next((d for d in (getattr(old_plan, "days", None) or []) if d.day_number == day.day_number), None)
        kept_meals = []
        for meal in (getattr(day, "meals", None) or []):
            mtype = (getattr(meal, "meal_type", None) or "").lower()
            key = (day.day_number, mtype)
            old_meal = old_by_slot.get(key)
            # Precedence: an explicitly targeted slot wins first, then
            # exclusion matches, then plan-wide scope. Anything else that has
            # an original counterpart is restored byte-identical.
            if old_meal is None:
                keep_new = True  # slot the generator added; nothing to restore
            elif (
                target_day is not None
                and target_meal
                and day.day_number == target_day
                and mtype == target_meal
            ):
                keep_new = True  # the explicitly targeted slot
            elif target_meal and mtype == target_meal and target_day is None:
                keep_new = True  # plan-wide targeted slot (e.g. snack on all days)
            elif target_meal and mtype != target_meal:
                keep_new = False  # preserve non-targeted meals untouched!
            elif target_day is not None and target_meal:
                keep_new = False
            elif exclusions and _meal_matches_exclusions(old_meal, exclusions):
                keep_new = True  # original dish contained an excluded ingredient
            elif exclusions:
                keep_new = False
            elif scope == "plan":
                # Plan-wide rewrite: still restore slots the generator returned
                # under the SAME dish name (drift, not an intentional change).
                keep_new = not _same_dish(old_meal, meal)
            elif operation == "add":
                keep_new = True
            elif target_day is not None and not target_meal and day.day_number == target_day and scope == "day":
                keep_new = True  # whole targeted day
            elif target_day is None and not target_meal and scope != "plan":
                keep_new = True  # nothing targeted: preserve generator output
            else:
                keep_new = False
            if not keep_new and old_meal is not None:
                kept_meals.append(old_meal.model_copy(deep=True))
                restored_count += 1
            else:
                kept_meals.append(meal)
                kept_count += 1
        # Re-add original slots the generator dropped unintentionally.
        if old_day is not None:
            present = {(day.day_number, (getattr(m, "meal_type", None) or "").lower()) for m in kept_meals}
            for old_meal in (getattr(old_day, "meals", None) or []):
                otype = (getattr(old_meal, "meal_type", None) or "").lower()
                okey = (old_day.day_number, otype)
                if okey in present:
                    continue
                # Don't resurrect a slot the user asked to remove, or one whose
                # ingredients were excluded.
                if operation == "remove" and (target_day is None or target_day == old_day.day_number) and (not target_meal or otype == target_meal):
                    continue
                if exclusions and _meal_matches_exclusions(old_meal, exclusions):
                    continue
                kept_meals.append(old_meal.model_copy(deep=True))
                restored_count += 1
        day.meals = kept_meals
        day.total_calories = round(sum((getattr(m, "calories", None) or 0) for m in kept_meals), 1)
        day.total_protein_g = round(sum((getattr(m, "protein_g", None) or 0) for m in kept_meals), 1)
        day.total_carbs_g = round(sum((getattr(m, "carbs_g", None) or 0) for m in kept_meals), 1)
        day.total_fat_g = round(sum((getattr(m, "fat_g", None) or 0) for m in kept_meals), 1)
    if getattr(merged, "days", None):
        merged.average_daily_calories = round(
            sum((getattr(d, "total_calories", None) or 0) for d in merged.days) / len(merged.days), 1
        )
    logger.info(f"Surgical edit merge: kept {kept_count} regenerated meal(s), restored {restored_count} untouched meal(s).")
    return merged

async def run_meal_planner(    user_id: int,
    user_message: str,
    user_profile: Dict[str, Any],
    session: Optional[AsyncSession] = None,
    task_id: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    pending_workflow: Optional[Any] = None,
    just_created_plan_id: Optional[int] = None
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
    # was_resume_request preserves the LLM's verdict for the interruption gate below.
    was_resume_request = (
        intent_data.intent == "resume_request"
        and pending_workflow is not None
        and bool(getattr(pending_workflow, "original_intent", None))
    )
    if was_resume_request:
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
        # For plan updates with no explicitly named target, never silently fall
        # back to the latest plan when several exist: history references still
        # resolve continuations, and a lone plan still resolves to itself.
        _explicit_target = bool(intent_data.target_plan_id or intent_data.target_plan_name)
        matched_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name,
            history=history,
            fallback_to_first=(
                _explicit_target or intent_data.intent != "edit_meal" or len(all_plans) == 1
            ),
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
        cuisine=getattr(intent_data, "requested_cuisine", None),
        current_plan=existing_plan if intent_data.intent == "edit_meal" else None,
        plan_version=target_version if (existing_plan and intent_data.intent == "edit_meal") else 1,
        round=1,
        max_rounds=3,
        status="drafting",
        pending_workflow=pending_workflow,
        include_snack=getattr(intent_data, "include_snack", False)
    )

    # Supervisor is needed by the resume/cancel branches below, so instantiate it
    # before the pending-workflow handling.
    supervisor = SupervisorAgent()

    # Resume/cancel with no in-progress workflow: the LLM understood an intent
    # to resume/cancel, but there is nothing to act on. Say so conversationally
    # instead of letting the request fall through to the planning graph.
    if intent_data.intent in ("resume_request", "cancel_request") and not state.pending_workflow:
        action = "resume" if intent_data.intent == "resume_request" else "cancel"
        context_prompt = (
            f"The user said '{user_message}', which sounds like they want to {action} a meal-plan workflow, "
            f"but there is no in-progress meal plan creation or update right now. "
            f"In warm, natural conversational English (2 sentences), let them know there's nothing in progress "
            f"to {action}, and offer to create a new meal plan or update an existing one instead."
        )
        state.system_message = await supervisor.generate_conversational_response(
            user_message=user_message,
            context=context_prompt,
            history=history,
            user_name=user_profile.get("name"),
        )
        state.status = "conversational"
        return state

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
        # Defence in depth against the classifier reading a plain "create me a
        # plan" as "start a new plan INSTEAD". Losing half-finished work to a
        # misread intent is unrecoverable, while asking continue-or-restart
        # costs one turn, so only take the cancel path when the message
        # actually carries an abandonment signal.
        _msg_l = user_message.lower()
        _abandon_signals = (
            "instead", "start over", "start fresh", "forget", "never mind",
            "nevermind", "cancel", "abort", "stop", "discard", "drop it",
            "quit", "leave it", "not now",
        )
        _looks_like_abandon = any(sig in _msg_l for sig in _abandon_signals)
        _looks_like_new_request = bool(
            intent_data.requested_days
            or intent_data.requested_calories
            or intent_data.requested_diet_type
            or intent_data.chosen_diet_type
            or intent_data.target_plan_name
        )

        if not _looks_like_abandon and _looks_like_new_request:
            # Treat it as a fresh request and let the interruption gate offer
            # continue-or-switch, which is what the user actually needs.
            logger.info(
                f"[{tid}] '{user_message[:60]}' classified cancel_request but "
                f"carries no abandonment signal and does carry a new request; "
                f"offering continue-or-restart instead of discarding the work."
            )
            intent_data.intent = "new_plan"
            # Fall through to the interruption gate below; do NOT run the
            # cancel confirmation copy.
            state.pending_workflow = pending_workflow
            _build_cancel_reply = False
        else:
            # Clear the pending workflow
            state.pending_workflow = None
            _build_cancel_reply = True

        if _build_cancel_reply:
            if pending_workflow.original_intent == "delete_plan":
                context_prompt = (
                    "The user cancelled the deletion of their meal plan. "
                    "In friendly, polite English, reassure them that their meal plan is kept safely and nothing was deleted, "
                    "and ask if they would like to make any other adjustments instead."
                )
            elif pending_workflow.original_intent == "approve_plan":
                context_prompt = (
                    "The user cancelled the approval of their meal plan. "
                    "In friendly, polite English, confirm that nothing was approved, their plan is still sitting in "
                    "draft and unchanged, and ask what they'd like to do instead."
                )
            elif pending_workflow.original_intent == "edit_meal":
                context_prompt = (
                    "The user cancelled the in-progress update of their meal plan. "
                    "In friendly, polite English, confirm that the update was dropped and their saved plan is unchanged, "
                    "and ask what they'd like to do instead (create a new plan, modify existing, etc.)."
                )
            else:
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
            return state

    # =========================================================================
    # Plan Listing: the LLM classified this message as a request to view saved
    # plans. Facts come from the database; the wording is composed by the LLM.
    # If a workflow is pending, an LLM-composed bridge follows the list.
    # =========================================================================
    if intent_data.intent == "list_plans":
        if not all_plans:
            facts = "The user has 0 saved meal plans in their account."
        else:
            status_lbl = {
                "approved": "Approved", "draft": "Draft", "reviewing": "Reviewing",
                "escalated": "Escalated", "failed": "Failed",
            }
            plan_facts = []
            plan_link_lines = []
            for i, p in enumerate(all_plans, start=1):
                p_name = getattr(p, "name", None) or f"Plan #{p.id}"
                p_days = getattr(p, "days", 0)
                p_cal = getattr(p, "calorie_target", None)
                p_cal_txt = f"{int(p_cal):,} kcal" if p_cal else "calories not set"
                p_diet = str(getattr(p, "diet_type", "") or "").lower() or "diet not set"
                p_status = status_lbl.get(getattr(p, "status", ""), "Draft")
                plan_facts.append(f"- {p_name}: {p_days} days, {p_cal_txt}, {p_diet}, status {p_status}")
                plan_link_lines.append(
                    f"{i}. [{p_name}](plan://{p.id}) — {p_days} days · {p_cal_txt} · {p_diet} ({p_status})"
                )
            facts = "The user's saved meal plans:\n" + "\n".join(plan_facts)
            # No "create" button here: options must stay relevant to the
            # list that was asked for (plans open inline via tappable links).
        list_context = (
            f"{facts}\n\n"
            f"In warm, natural conversational English (1-2 sentences), introduce the user's saved plans below. "
            f"Do NOT list the plans yourself in prose — the numbered tappable list follows your message. "
            f"When there are no plans, let them know that warmly and invite them to create one "
            f"by sharing days, calorie target, and diet preference."
        )
        list_text = await supervisor.generate_conversational_response(
            user_message=user_message,
            context=list_context,
            history=history,
            user_name=user_profile.get("name"),
        )
        # The intro must stay an intro: strip any enumeration the model added
        # itself so plans render exactly once (as the tappable list below).
        list_text = _strip_enumeration(list_text)
        if all_plans:
            # Deterministic numbered list with inline tappable plan links that
            # jump straight to the plan in the Plans tab.
            list_text = f"{list_text}\n" + "\n".join(plan_link_lines)

        if state.pending_workflow:
            await append_resume_prompt(
                supervisor, state, list_text, state.pending_workflow,
                user_message, history, user_profile.get("name"),
            )
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
    # PENDING-WORKFLOW INTERRUPTION GATE (LLM-first)
    # Routing uses the LLM's structured verdict (intent + confirmation flags +
    # extracted params) together with the pending state — never keyword matching
    # on the raw message. Reply wording is composed by the supervisor LLM.
    # =========================================================================
    pending_workflow = state.pending_workflow
    if pending_workflow:
        pinfo = dict(pending_workflow.extracted_info or {})
        awaiting_choice = bool(pinfo.get("awaiting_interruption_choice"))

        # --- Specs choice: the user was offered profile-defaults vs. custom
        # specs for a new plan. Route on the LLM's verdict, not keywords.
        # (cancel_request never reaches here: the shared cancel handler above
        # catches it first whenever a pending workflow exists.)
        if pinfo.get("awaiting_specs_choice"):
            if intent_data.intent in ("rename_plan", "delete_plan", "approve_plan", "edit_meal"):
                # Explicit pivot to another action while a plan is still being
                # agreed: ASK whether to finish this plan first or switch over.
                # Dropping the offer silently loses the half-built workflow and
                # leaves the user unsure which action the next "yes" refers to.
                state.system_message = await _compose_interruption_message(
                    supervisor,
                    user_message=user_message,
                    user_name=user_profile.get("name"),
                    pending=pending_workflow,
                    competing_intent=intent_data.intent,
                    history=history,
                )
                state.status = "conversational"
                # awaiting_specs_choice is cleared so the awaiting_choice branch
                # owns this turn; prompt_text is kept so "continue" restores the
                # exact offer the user already saw.
                new_info = {
                    k: v for k, v in pinfo.items()
                    if k not in ("awaiting_interruption_choice", "interrupted_by", "interrupted_intent")
                }
                new_info.update({
                    "awaiting_specs_choice": False,
                    "awaiting_interruption_choice": True,
                    "interrupted_by": user_message,
                    "interrupted_intent": intent_data.intent,
                })
                state.pending_workflow = pending_workflow.model_copy(update={"extracted_info": new_info})
                await supervisor.log_step(
                    state=state, from_agent="supervisor", to_agent="user",
                    msg_type="feedback", content=str(state.system_message)[:120] + "...",
                    payload={"action": "interruption_prompt", "competing_intent": intent_data.intent}
                )
                return state
            elif intent_data.intent == "out_of_scope":
                await _decline_and_represent_pending(
                    supervisor, state, pending_workflow,
                    user_message, history, user_profile.get("name"),
                )
                return state
            elif intent_data.intent in ("chat", "app_info", "explain", "profile_info", "list_plans"):
                # Conversational question: answer it, then bridge back to the
                # workflow that is still open (never silently drop it).
                answer = await _answer_conversational_with_plans(
                    supervisor, state, intent_data, all_plans, user_message,
                    history, user_profile.get("name"),
                )
                await append_resume_prompt(
                    supervisor, state, answer, pending_workflow,
                    user_message, history, user_profile.get("name"),
                )
                return state
            elif getattr(intent_data, "use_profile_defaults", False):
                # Agreed to profile defaults: drop the offer; the new-plan
                # branch below fills the missing specs from the profile.
                state.pending_workflow = None
            elif intent_data.intent == "new_plan":
                sig = _llm_answer_signals(intent_data)
                if sig["fresh_diet"] or sig["fresh_cal"] or intent_data.requested_days:
                    # Custom specifications given: drop the offer, proceed.
                    state.pending_workflow = None
                else:
                    # The user answered the offer itself ("I have my own
                    # specifications...") but gave no numbers yet. Ask WHAT they
                    # want — replaying the stored offer here is what made the
                    # bot answer the same sentence twice in a row.
                    await ask_for_custom_specs(
                        supervisor, state, user_profile, user_message, history,
                        pending_workflow,
                    )
                    await supervisor.log_step(
                        state=state, from_agent="supervisor", to_agent="user",
                        msg_type="feedback", content=str(state.system_message)[:120] + "...",
                        payload={"action": "new_plan_custom_specs_request"}
                    )
                    return state
            else:
                # Anything else: re-present the stored specs offer.
                state.system_message = pinfo.get("prompt_text") or "Would you like me to create the plan with your profile preferences, or do you have your own specifications?"
                state.status = "conversational"
                return state

        elif pinfo.get("awaiting_custom_specs"):
            # We already asked "what are your specifications?". Keep steering
            # toward an actual answer instead of replaying that question, but
            # let a concrete answer through to the new-plan branch below.
            if intent_data.intent in ("rename_plan", "delete_plan", "approve_plan", "edit_meal"):
                state.system_message = await _compose_interruption_message(
                    supervisor,
                    user_message=user_message,
                    user_name=user_profile.get("name"),
                    pending=pending_workflow,
                    competing_intent=intent_data.intent,
                    history=history,
                )
                state.status = "conversational"
                new_info = {k: v for k, v in pinfo.items()
                            if k not in ("awaiting_interruption_choice", "interrupted_by", "interrupted_intent")}
                new_info.update({
                    "awaiting_custom_specs": False,
                    "awaiting_interruption_choice": True,
                    "interrupted_by": user_message,
                    "interrupted_intent": intent_data.intent,
                })
                state.pending_workflow = pending_workflow.model_copy(update={"extracted_info": new_info})
                return state
            if intent_data.intent == "out_of_scope":
                await _decline_and_represent_pending(
                    supervisor, state, pending_workflow,
                    user_message, history, user_profile.get("name"),
                )
                return state
            if intent_data.intent in ("chat", "app_info", "explain", "profile_info", "list_plans"):
                answer = await _answer_conversational_with_plans(
                    supervisor, state, intent_data, all_plans, user_message,
                    history, user_profile.get("name"),
                )
                state.system_message = f"{answer}\n\n{pinfo.get('prompt_text') or ''}".strip()
                state.status = "conversational"
                return state
            if getattr(intent_data, "use_profile_defaults", False):
                state.pending_workflow = None
            else:
                _cs_sig = _llm_answer_signals(intent_data)
                if _cs_sig["fresh_diet"] or _cs_sig["fresh_cal"] or intent_data.requested_days:
                    state.pending_workflow = None  # real specs -> new-plan branch
                else:
                    # Still no numbers. Re-ask, but freshly composed from THIS
                    # message so it never reads as a copy of the last turn.
                    await ask_for_custom_specs(
                        supervisor, state, user_profile, user_message, history,
                        pending_workflow,
                    )
                    return state

        elif awaiting_choice:
            if was_resume_request:
                # Re-present the pending question(s) exactly as the user saw them.
                if pending_workflow.workflow_type == "edit_meal":
                    text = pinfo.get("reprompt_text") or "Where would you like to continue with your update?"
                elif pending_workflow.workflow_type == "delete_plan":
                    text = pinfo.get("reprompt_text") or "Which meal plan would you like to delete?"
                else:
                    text = pinfo.get("prompt_text") or "Let's continue creating your meal plan."
                new_info = {k: v for k, v in pinfo.items()
                            if k not in ("awaiting_interruption_choice", "interrupted_by", "interrupted_intent")}
                state.pending_workflow = pending_workflow.model_copy(update={"extracted_info": new_info})
                state.system_message = text
                state.status = "conversational"
                await supervisor.log_step(
                    state=state, from_agent="supervisor", to_agent="user",
                    msg_type="feedback", content=str(text)[:120] + "...",
                    payload={"action": "pending_resume"}
                )
                return state
            if intent_data.intent == "out_of_scope":
                # Out-of-scope mid-choice: decline WITHOUT answering, then
                # re-present only the pending question (never echo the answer).
                await _decline_and_represent_pending(
                    supervisor, state, pending_workflow,
                    user_message, history, user_profile.get("name"),
                )
                return state
            if intent_data.intent in ("chat", "app_info", "explain", "profile_info", "list_plans"):
                # The LLM understood this as a question, not the competing action:
                # answer it, then keep the interruption choice alive.
                answer = await _answer_conversational_with_plans(
                    supervisor, state, intent_data, all_plans, user_message,
                    history, user_profile.get("name"),
                )
                await append_resume_prompt(
                    supervisor, state, answer, pending_workflow,
                    user_message, history, user_profile.get("name"),
                )
                return state
            # Anything else while awaiting = the LLM classified this as the
            # competing action: drop the pending workflow and handle it fresh.
            sig = _llm_answer_signals(intent_data)
            if (
                intent_data.intent == "new_plan"
                and sig["answered_any"]
                and not _contradicts_pending_answers(intent_data, pinfo)
            ):
                # The user answered the pending question(s) instead of choosing:
                # resume the pending flow (choice flags dropped) and resolve below.
                new_info = {k: v for k, v in pinfo.items()
                            if k not in ("awaiting_interruption_choice", "interrupted_by", "interrupted_intent")}
                state.pending_workflow = pending_workflow.model_copy(update={"extracted_info": new_info})
                # Fall through to the normal branches with the pending restored.
            else:
                state.pending_workflow = None
                if intent_data.intent == "new_plan" and not (sig["fresh_diet"] or sig["fresh_cal"]):
                    # Awaiting a choice and the user sent a bare new-plan message:
                    # abandon the pending flow and ask for the new specs.
                    await ask_for_new_plan_specs(supervisor, state, user_profile, user_message, history)
                    await supervisor.log_step(
                        state=state, from_agent="supervisor", to_agent="user",
                        msg_type="feedback", content=str(state.system_message)[:120] + "...",
                        payload={"action": "new_plan_specs_request"}
                    )
                    return state
                # A concrete competing request (carries params, or a rename/delete/
                # approve/edit action) continues below the gate with no pending workflow.
        elif was_resume_request and pending_workflow.workflow_type == "edit_meal":
            # Resuming an in-progress update: re-present the stored question.
            text = pinfo.get("reprompt_text") or "Where would you like to continue with your update?"
            state.pending_workflow = pending_workflow
            state.system_message = text
            state.status = "conversational"
            await supervisor.log_step(
                state=state, from_agent="supervisor", to_agent="user",
                msg_type="feedback", content=str(text)[:120] + "...",
                payload={"action": "edit_pending_resume"}
            )
            return state
        elif not was_resume_request:
            if intent_data.intent == "approve_plan":
                # Approving is only a competing action when the pending work is
                # something else; when an approval confirmation is already open,
                # this message is the answer to it.
                competing = pending_workflow.workflow_type != "approve_plan"
            elif intent_data.intent in ("rename_plan", "delete_plan"):
                # The LLM classified an explicit plan action: never an answer to
                # diet/calorie confirmations, so it interrupts the pending work.
                competing = True
            elif intent_data.intent == "edit_meal":
                # A fresh update request interrupts plan CREATION; a follow-up to an
                # in-progress update is a continuation handled by the edit flow.
                competing = pending_workflow.workflow_type != "edit_meal"
            elif intent_data.intent == "new_plan":
                if pending_workflow.workflow_type == "edit_meal":
                    # Any new-plan request interrupts an in-progress update.
                    competing = True
                else:
                    sig = _llm_answer_signals(intent_data)
                    # An explicit confirmation is an answer to the open
                    # conflict, full stop. It must never be read as a request
                    # to switch tasks - that produced "would you like me to
                    # switch?" right after the user had confirmed the diet and
                    # the calories, and deadlocked the flow. The guardrails
                    # downstream still re-check whatever they were asked to
                    # check, so nothing unsafe slips through by trusting it.
                    competing = not sig["answered_any"]
            else:
                competing = False

            if competing:
                state.system_message = await _compose_interruption_message(
                    supervisor,
                    user_message=user_message,
                    user_name=user_profile.get("name"),
                    pending=pending_workflow,
                    competing_intent=intent_data.intent,
                    history=history,
                )
                state.status = "conversational"
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
        # No quick options here: they must stay relevant to the profile answer,
        # not pitch a new plan under every response.
        # A pending workflow is not discarded by a profile inquiry: answer it and
        # ask whether to continue the pending work or start a new plan.
        if state.pending_workflow:
            await append_resume_prompt(
                supervisor, state, response_text, state.pending_workflow,
                user_message, history, user_profile.get("name"),
            )
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
            return state

        # Only auto-resolve when the user actually identified a plan (by name,
        # id, day count, diet or history). Guessing "the most recent one" would
        # rename the wrong plan without ever asking — a destructive misfire.
        # history is deliberately not passed: a prior turn that listed every plan
        # would match here and silently pick one.
        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name,
            fallback_to_first=len(all_plans) == 1,
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

            old_title = target_plan.name
            context_prompt = (
                f"Successfully renamed the user's meal plan from '{old_title}' to '{new_title}'. "
                f"In polite, natural English, confirm that their plan '{old_title}' has been renamed to '{new_title}' — "
                f"state both the old and the new name explicitly."
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
            context_prompt = (
                f"The user wants to rename a meal plan, and they have {len(all_plans)} saved plans: {', '.join(p.name for p in all_plans)}. "
                f"In polite English, list each plan with its length and daily calories so they can tell them apart, "
                f"then ask which one they would like to rename and what new name they want."
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

    # =========================================================================
    # Step 4: Handle Plan Deletion Flow
    # =========================================================================
    if intent_data.intent == "delete_plan":
        # A delete confirmation in progress is tracked as a PendingWorkflow, so
        # Cancel/answers resolve against structured state — not history keyword
        # matching.
        del_pending = None
        if state.pending_workflow and state.pending_workflow.original_intent == "delete_plan":
            del_pending = state.pending_workflow
        is_pending_confirmation = del_pending is not None

        cleaned_user_msg = user_message.strip().lower()
        is_cancelling = is_pending_confirmation and any(w in cleaned_user_msg for w in ["no", "cancel", "don't", "keep it", "stop"])

        if is_pending_confirmation and is_cancelling:
            context_prompt = (
                "The user cancelled the deletion of their meal plan. "
                "Warmly and politely reassure them that their meal plan is kept safely and nothing was deleted, "
                "and ask if they would like to make any other adjustments instead."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            # Deletion abandoned - drop the delete confirmation state.
            state.pending_workflow = None
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
                # Fall back to the plan stored in the delete confirmation state.
                prev_name = ((del_pending.extracted_info or {}).get("target_plan_name") or "").strip().lower()
                if prev_name:
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
                # Deletion completed - drop the delete confirmation state.
                state.pending_workflow = None
                # Options stay relevant: with no plans left, offer creation;
                # otherwise point at the remaining plans.
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
            state.pending_workflow = make_delete_pending(
                response_text, user_message, user_profile,
                target_plan_name=target_plan.name,
            )
            return state

        # Case 2: Multiple plans exist and user did not name one
        context_prompt = (
            f"The user wants to delete a meal plan, and they have multiple plans: {', '.join(p.name for p in all_plans)}. "
            f"In polite, natural English, list each plan with its length and daily calories so they can tell them apart, "
            f"then ask which meal plan they would like to delete."
        )
        response_text = await supervisor.generate_conversational_response(
            user_message=user_message,
            context=context_prompt,
            history=history,
            user_name=user_profile.get("name")
        )
        state.system_message = response_text
        state.status = "conversational"
        state.pending_workflow = make_delete_pending(
            response_text, options, user_message, user_profile,
        )
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

        # If no draft plans exist, nothing can be approved.
        if len(draft_plans) == 0:
            context_prompt = (
                "The user asked to approve a meal plan, but none of their plans are in draft state — every plan they "
                "have is already approved, with its grocery list ready in the Grocery tab. "
                "In friendly, natural English (1-2 sentences), let them know they don't have any plans pending "
                "approval right now, and offer to create a brand new plan for them if they'd like."
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

        # An approval we already asked about: resolve the follow-up ("yes", or a
        # bare plan name) against the plan we named, not whichever sorts first.
        approve_pending = None
        if state.pending_workflow and state.pending_workflow.original_intent == "approve_plan":
            approve_pending = state.pending_workflow
        pending_name = (approve_pending.extracted_info or {}).get("target_plan_name") if approve_pending else None
        if pending_name and not (intent_data.target_plan_name or intent_data.target_plan_id):
            intent_data.target_plan_name = pending_name

        # Approving the plan that was built one turn ago is the obvious next
        # step and needs no confirmation - the user just watched it appear.
        # `just_created_plan_id` is the plan id the previous reply returned, so
        # this can only fire on a fresh referent; a loose "approve it" with no
        # plan just created still falls through to the questions below.
        if not intent_data.target_plan_id and not intent_data.target_plan_name:
            fresh = next((p for p in all_plans if p.id == just_created_plan_id), None)
            if fresh is not None and fresh.status == "draft":
                intent_data.target_plan_id = fresh.id
                logger.info(
                    f"[{tid}] Approving plan #{fresh.id} directly: it was created "
                    f"in the previous turn."
                )

        named_target = bool(intent_data.target_plan_name or intent_data.target_plan_id)

        # A bare "approve my plan" must never approve silently: confirm which
        # plan first — or ask which one when several are pending.
        if not named_target:
            if len(draft_plans) == 1:
                only_draft = draft_plans[0]
                context_prompt = (
                    f"The user asked to approve a meal plan without naming one. They have exactly ONE plan waiting "
                    f"for approval: '{only_draft.name}' ({only_draft.days} days, "
                    f"{int(only_draft.calorie_target):,} kcal/day). "
                    f"In warm, natural conversational English (1-2 sentences), name that plan, say it is sitting in "
                    f"draft in their Plans tab, and ask whether they want to approve it now. Mention that approving "
                    f"will build its grocery list with estimated costs. End with the question. "
                    f"IMPORTANT: do NOT approve anything yet — only ask."
                )
                response_text = await _ask_without_claiming(
                    supervisor,
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name"),
                )
                state.system_message = response_text
                state.status = "conversational"
                state.pending_workflow = make_approve_pending(
                    response_text, user_message, user_profile,
                    target_plan_name=only_draft.name,
                )
                return state

            names = ", ".join(f"'{p.name}'" for p in draft_plans)
            context_prompt = (
                f"The user asked to approve a meal plan without saying which one, and they have {len(draft_plans)} "
                f"plans in draft state: {names}. "
                f"In friendly, natural conversational English, tell them how many plans are waiting for approval and "
                f"ask which one they would like you to approve. Mention that approving builds that plan's grocery "
                f"list with estimated costs. End with the question. "
                f"IMPORTANT: do NOT approve any of them yet."
            )
            response_text = await _ask_without_claiming(
                supervisor,
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name"),
            )
            state.system_message = response_text
            state.status = "conversational"
            state.pending_workflow = make_approve_pending(
                response_text, user_message, user_profile,
            )
            return state

        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name
        )
        if not target_plan:
            if not named_target:
                # Nothing named and nothing resolvable: the generic asks above
                # already handled every real draft count, so this is only
                # reachable in a state we have no wording for. Fall back to the
                # first draft rather than claim an action.
                target_plan = draft_plans[0]
            else:
                # The user named a plan that is not in their account. Never
                # substitute an unrelated one: doing so handed the model a plan
                # they never asked about, and it described it back as if they
                # had - inventing a "keto" plan that did not exist.
                # find_referenced_plan's day/diet keyword heuristics can also
                # match on a word like "keto", so the name the model quotes may
                # not even be the one the user typed.
                wanted = (intent_data.target_plan_name or "").strip()
                saved_names = ", ".join(f"'{p.name}'" for p in all_plans)
                context_prompt = (
                    f"The user asked to approve a meal plan called {wanted!r}, but no plan by that name exists in "
                    f"their account. They currently have {len(all_plans)} saved plan(s): {saved_names}. "
                    f"Each name above is the literal, exact name of a real saved plan - quote them only, and never "
                    f"invent, add or repeat any other name, cuisine or diet word from the user's message. "
                    f"In friendly, natural English (1-2 sentences), tell them you could not find a plan by that name, "
                    f"list what they do have saved, and offer to create a new plan for them instead. "
                    f"IMPORTANT: do NOT approve anything — the named plan was not found."
                )
                response_text = await _ask_without_claiming(
                    supervisor,
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name"),
                )
                state.system_message = response_text
                state.status = "conversational"
                # No pending approval to carry: the plan they named is not
                # here, so a bare "yes" must not resolve to some other draft.
                state.pending_workflow = None
                return state

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
            # The approve question has been answered. Leaving the snapshot on
            # the state meant the next message re-presented it — including
            # after an out-of-scope question, which appended a stale
            # "shall I approve this for you?" to the refusal.
            state.pending_workflow = None
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
            # Same reason as the already-approved branch above: approval
            # succeeded, so the stored approve offer is spent. Without this it
            # survived into the next turn and was re-presented as if the plan
            # were still a draft.
            state.pending_workflow = None
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
            return state

    # =========================================================================
    # Step 6: Handle Edit / Update Plan Request
    # =========================================================================
    if intent_data.intent == "edit_meal":
        # The LLM already decided this message is a plan-update request
        # (is_update_request / edit_details). Trust that verdict; no keyword veto.
        if len(all_plans) == 0:
            if not intent_data.is_update_request and not intent_data.edit_details:
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
            return state

        # The user asked to update a plan they named, and no plan by that name
        # exists. Never substitute a different one: the resolver below falls
        # back to the first (or only) saved plan, so "update my keto plan" came
        # back as "what would you like to update in '3-Day Veg Plan'?" - a plan
        # the user never mentioned. Report the miss and offer to create one.
        # find_referenced_plan is called strictly here: no history, because a
        # recent message listing every plan would resolve any invented name,
        # and no fallback, because a fallback is the substitution itself.
        _want = (intent_data.target_plan_name or "").strip()
        _want_id = intent_data.target_plan_id
        if _want or _want_id:
            # Quote normalisation lives inside find_referenced_plan, so the
            # extracted '"Good" plan' resolves to the real plan here too.
            _strict = find_referenced_plan(
                user_message,
                all_plans,
                target_id=_want_id,
                target_name=intent_data.target_plan_name,
                fallback_to_first=False,
            )
            _claiming_a_specific_plan = _want_id is not None or (
                bool(_want) and not _is_generic_plan_ref(_want)
            )
            if _strict is None and _claiming_a_specific_plan:
                wanted = _want or f"#{_want_id}"
                saved_names = ", ".join(f"'{p.name}'" for p in all_plans)
                context_prompt = (
                    f"The user asked to update a meal plan called {wanted!r}, but no plan by that name exists in "
                    f"their account. They currently have {len(all_plans)} saved plan(s): {saved_names}. "
                    f"Each name above is the literal, exact name of a real saved plan - quote them only, and never "
                    f"invent, add or repeat any other name, cuisine or diet word from the user's message. "
                    f"In friendly, natural English (1-2 sentences), tell them the plan they want to update is not in "
                    f"their Plans tab, list what they do have saved, and offer to create a new plan for them instead. "
                    f"IMPORTANT: do NOT update, edit or regenerate anything - the named plan was not found."
                )
                response_text = await _ask_without_claiming(
                    supervisor,
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name"),
                )
                state.system_message = response_text
                state.status = "conversational"
                return state

        # Whether the LLM extracted actionable edit specifics (slot, dish,
        # calories, add/remove operation, plan-wide scope) or exclusions.
        has_excl = bool(intent_data.requested_exclusions and len(intent_data.requested_exclusions) > 0)
        ed = intent_data.edit_details
        if ed is not None:
            # Structural validation of the LLM's extraction (domain rules, not
            # keyword matching): drop values outside the known enums/ranges so a
            # vague request can never look like a specific slot edit.
            if getattr(ed, "meal_type", None) not in (None, "breakfast", "lunch", "dinner", "snack"):
                ed.meal_type = None
            _day = getattr(ed, "day", None)
            if _day is not None and (not isinstance(_day, int) or _day < 1):
                ed.day = None
            if getattr(ed, "operation", None) not in (None, "add", "remove", "replace"):
                ed.operation = None
            if getattr(ed, "edit_scope", None) not in (None, "slot", "day", "plan"):
                ed.edit_scope = None
        # A scope alone is not an instruction. "update the 7-Day plan" yields
        # edit_scope="plan" but names no change, and trusting that scope sent
        # the whole plan through a silent regeneration. Scope only counts when
        # something is named to apply it to — or when the classifier itself
        # did not flag the message as a vague update request.
        _named_change = bool(
            ed and (
                getattr(ed, "day", None)
                or getattr(ed, "meal_type", None)
                or getattr(ed, "dish_to_replace", None)
                or getattr(ed, "target_meal_calories", None)
                or getattr(ed, "operation", None) in ("add", "remove")
            )
        )
        _scoped_change = bool(
            ed and getattr(ed, "edit_scope", None) in ("day", "plan")
            and not intent_data.is_update_request
        )
        has_specific_instructions = bool(has_excl or _named_change or _scoped_change)

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
                state.pending_workflow = make_edit_pending(
                    response_text, "which_meal",
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
                state.pending_workflow = make_edit_pending(
                    response_text, "which_plan",
                    "your meal plan", user_message, user_profile
                )
                return state

        # If user provided meal edit instructions, check if essential parameters are missing or ambiguous
        if existing_plan and intent_data.edit_details:
            # Scope comes from the LLM's structured verdict: plan-wide work
            # (exclusions, whole-plan scope) runs the swarm; a whole-day scope
            # skips slot clarification; otherwise resolve the missing slot/day.
            ed_scope = getattr(intent_data.edit_details, "edit_scope", None)
            _slot_named = bool(getattr(intent_data.edit_details, "meal_type", None))
            _op = getattr(intent_data.edit_details, "operation", None) or "replace"
            _is_all_days = bool(
                ed_scope == "plan"
                or any(k in user_message.lower() for k in ["all days", "every day", "all meals", "each day", "whole plan", "entire plan", "all the days"])
            )
            # A wide scope is only honoured when the message actually covers
            # everything inside it. "Update day 2 with a different meal" names
            # one meal, and "update my 7-day plan" names no change at all —
            # regenerating a whole day or plan there would be a silent,
            # unrecoverable rewrite of work the user never asked to redo.
            is_plan_wide = bool(has_excl or (ed_scope == "plan" and not _slot_named) or (_is_all_days and _op in ("add", "remove")))
            _day_is_whole = bool(ed_scope == "day" and not _slot_named)

            if not is_plan_wide:
                target_meal_type = getattr(intent_data.edit_details, "meal_type", None)

                target_day = getattr(intent_data.edit_details, "day", None)

                # Smart snack day resolution: If user is referring to a snack and day is omitted
                if target_meal_type == "snack" and target_day is None and not _is_all_days:
                    snack_days = [d.day_number for d in existing_plan.days if any(m.meal_type.lower() == "snack" for m in d.meals)]
                    if len(snack_days) == 1:
                        target_day = snack_days[0]
                        intent_data.edit_details.day = target_day

                is_entire_day = False

                # Check if meal slot is specified, but day is missing/ambiguous across a multi-day plan
                if target_meal_type and target_day is None and len(existing_plan.days) > 1 and not _is_all_days:
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
                    state.pending_workflow = make_edit_pending(
                        response_text, "which_day",
                        plan_name_disp, user_message, user_profile
                    )
                    return state

                # Check if day is specified, but meal slot is missing/ambiguous
                if target_day and not target_meal_type:
                    plan_name_disp = getattr(existing_plan, "name", None) or getattr(matched_plan, "name", "your meal plan")
                    # "Update day 2 with a different meal" and "redo day 2" are
                    # indistinguishable from the extracted fields alone, so ask
                    # instead of guessing — and keep a whole-day option so the
                    # second meaning is still one click away.
                    _whole_day_intent = _day_is_whole
                    if _whole_day_intent:
                        context_prompt = (
                            f"The user wants to change Day {target_day} of '{plan_name_disp}', but it is not clear whether they want "
                            f"one meal of that day replaced or the whole day rebuilt. "
                            f"In polite, natural, friendly English, ask whether they want to change one specific meal of Day {target_day} "
                            f"or rebuild the entire day."
                        )
                    else:
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
                    state.pending_workflow = make_edit_pending(
                        response_text, "which_slot",
                        plan_name_disp, user_message, user_profile
                    )
                    return state

                # Slot-existence safety net (EVERY meal slot, not just snacks):
                # a named day/slot must actually exist before the swarm spends
                # minutes on it. A day number past the plan used to silently
                # retarget day 1, and a missing slot silently overwrote the
                # day's last meal. Both now get a polite, LLM-composed
                # question instead. Whole-day and whole-plan scopes name no
                # single slot, so they pass through untouched.
                _sn_ed = intent_data.edit_details
                if _sn_ed is not None and existing_plan and getattr(existing_plan, "days", None):
                    _sn_day = getattr(_sn_ed, "day", None)
                    if _sn_day is not None:
                        _sn_day_numbers = [d.day_number for d in existing_plan.days]
                        if _sn_day not in _sn_day_numbers:
                            plan_name_disp = getattr(existing_plan, "name", None) or getattr(matched_plan, "name", "your meal plan")
                            _sn_valid = ", ".join(f"Day {n}" for n in sorted(_sn_day_numbers))
                            context_prompt = (
                                f"The user wants to change Day {_sn_day} of '{plan_name_disp}', but that plan "
                                f"only has {len(_sn_day_numbers)} day(s): {_sn_valid}. "
                                f"In polite, natural, friendly English, say Day {_sn_day} doesn't exist in that plan "
                                f"and ask which day they meant. Nothing has been changed."
                            )
                            response_text = await supervisor.generate_conversational_response(
                                user_message=user_message,
                                context=context_prompt,
                                history=history,
                                user_name=user_profile.get("name")
                            )
                            state.system_message = response_text
                            state.status = "conversational"
                            state.pending_workflow = make_edit_pending(
                                response_text, "which_day",
                                plan_name_disp, user_message, user_profile
                            )
                            return state
                    _sn_meal = ((getattr(_sn_ed, "meal_type", None) or "").lower()) or None
                    _sn_op = ((getattr(_sn_ed, "operation", None) or "").lower()) or None
                    if _sn_day is not None and _sn_meal and _sn_op in ("replace", "remove"):
                        _sn_day_plan = next((d for d in existing_plan.days if d.day_number == _sn_day), None)
                        _sn_slots = sorted({(m.meal_type or "").lower() for m in (_sn_day_plan.meals if _sn_day_plan else []) if (m.meal_type or "").strip()})
                        if _sn_meal not in _sn_slots:
                            plan_name_disp = getattr(existing_plan, "name", None) or getattr(matched_plan, "name", "your meal plan")
                            _sn_have = ", ".join(_sn_slots) if _sn_slots else "no meals listed"
                            context_prompt = (
                                f"The user wants to {_sn_op} the {_sn_meal} on Day {_sn_day} of '{plan_name_disp}', "
                                f"but Day {_sn_day} has no {_sn_meal} (it has: {_sn_have}). "
                                f"In polite, natural, friendly English, say that meal isn't there, then ask: "
                                f"would they like to add a {_sn_meal} to Day {_sn_day}, or did they mean a different meal or day? "
                                f"Nothing has been changed or added."
                            )
                            response_text = await supervisor.generate_conversational_response(
                                user_message=user_message,
                                context=context_prompt,
                                history=history,
                                user_name=user_profile.get("name")
                            )
                            state.system_message = response_text
                            state.status = "conversational"
                            state.pending_workflow = make_edit_pending(
                                response_text, "missing_slot",
                                plan_name_disp, user_message, user_profile
                            )
                            return state

        # The LLM extracted actionable edit specifics, but no existing plan could
        # be matched (no plan named, or several to choose from): ask which plan
        # to update instead of letting the request fall through to the graph.
        if intent_data.edit_details and not existing_plan and len(all_plans) > 1:
            context_prompt = (
                f"The user wants to update a meal plan ({user_message}), and they have "
                f"{len(all_plans)} saved plans: {', '.join(p.name for p in all_plans)}. "
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
            state.pending_workflow = make_edit_pending(
                response_text, "which_plan",
                "your meal plan", user_message, user_profile
            )
            return state

    # =========================================================================
    # Step 6b: Grocery-list / price requests
    #
    # This intent used to have no handler at all, so "show my grocery list"
    # fell straight through to the planning graph and built a whole new meal
    # plan - minutes of agent swarm for a lookup. Read-only from here on.
    # =========================================================================
    if intent_data.intent == "grocery_only":
        approved_with_grocery = [p for p in all_plans if p.status == "approved" and p.grocery_list]
        asked_to_approve = False

        if approved_with_grocery:
            target = approved_with_grocery[0]
            cost = 0
            try:
                gl = target.grocery_list or {}
                if isinstance(gl, dict):
                    cost = int(float(gl.get("total_estimated_cost") or 0))
            except (TypeError, ValueError):
                cost = 0
            items = 0
            try:
                gl = target.grocery_list or {}
                items = len(gl.get("items", [])) if isinstance(gl, dict) else 0
            except (AttributeError, TypeError):
                items = 0
            context_prompt = (
                f"The user asked to see their grocery list. Their plan '{target.name}' is already approved, "
                f"so a grocery list of {items} items (about Rs {cost:,} estimated) is ready for them. "
                f"In warm, natural conversational English (1-2 sentences), tell them their grocery list is ready "
                f"and point them to the Grocery tab to see the full itemised list and current mandi rates. "
                f"Do NOT create, generate or change any meal plan."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name"),
            )
            state.system_message = response_text
            state.status = "approved"
            state.plan_id = target.id
            state.grocery_list = target.grocery_list
        else:
            drafts = [p for p in all_plans if p.status == "draft"]
            if drafts:
                context_prompt = (
                    f"The user asked to see their grocery list, but a grocery list is only built once a plan is "
                    f"approved. They currently have {len(drafts)} plan(s) sitting in draft: "
                    f"{', '.join(p.name for p in drafts)}. "
                    f"In warm, natural English (1-2 sentences), explain that approving a plan builds its grocery "
                    f"list with prices, and ask whether they would like to approve one of them now. "
                    f"Their saved profile has NOT been changed and nothing has been approved yet - never say or imply "
                    f"that you updated their profile, preferences or settings."
                )
            else:
                context_prompt = (
                    "The user asked to see their grocery list, but they have no approved meal plan yet, and a "
                    "grocery list is only built when a plan is approved. "
                    "In warm, natural English (1-2 sentences), let them know that and offer to create a meal plan "
                    "for them, which they can then approve to get its grocery list."
                )
            response_text = await _ask_without_claiming(
                supervisor,
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name"),
            )
            state.system_message = response_text
            state.status = "conversational"
            asked_to_approve = bool(drafts)
            if drafts:
                # We just asked whether to approve a named plan, so that
                # question has to survive into the next turn. Without a stored
                # target the user's "Yes, approve it" has nothing to resolve
                # against and the assistant narrates an approval that never ran.
                state.pending_workflow = make_approve_pending(
                    response_text, user_message, user_profile,
                    target_plan_name=drafts[0].name,
                )
        # A pending workflow we did not inherit (the user switched topics
        # mid-flow) still needs the continue-or-start-fresh bridge. The approve
        # offer we just made is itself the question, so it does not.
        if state.pending_workflow and not asked_to_approve:
            await append_resume_prompt(
                supervisor, state, response_text, state.pending_workflow,
                user_message, history, user_profile.get("name"),
            )
        return state

    # =========================================================================
    # Step 7: Handle General Conversational Intents (chat, app_info, out_of_scope, explain)
    # =========================================================================
    if intent_data.intent == "out_of_scope" and state.pending_workflow:
        # Out-of-scope question asked mid-workflow: decline WITHOUT answering
        # it, then re-present only the pending question (never echo an answer).
        await _decline_and_represent_pending(
            supervisor, state, state.pending_workflow,
            user_message, history, user_profile.get("name"),
        )
        return state
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
            await append_resume_prompt(
                supervisor, state, response_text, state.pending_workflow,
                user_message, history, user_profile.get("name"),
            )
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

        # The user agreed to build the plan with their saved profile defaults:
        # fill only the missing slots from the profile (explicitly mentioned
        # values always win).
        if getattr(intent_data, "use_profile_defaults", False):
            if not intent_data.requested_diet_type and not intent_data.chosen_diet_type:
                intent_data.requested_diet_type = profile_diet
            if intent_data.requested_calories is None and intent_data.chosen_calorie_target is None:
                try:
                    intent_data.requested_calories = float(user_profile.get("suggested_target_calories") or 2100)
                except (TypeError, ValueError):
                    pass
            if not intent_data.requested_days:
                intent_data.requested_days = settings.default_plan_days

        # A bare create request (the LLM extracted no specs at all and nothing
        # is pending): offer profile defaults vs. custom specs conversationally
        # instead of silently generating a default plan.
        has_any_spec = bool(
            intent_data.requested_calories
            or intent_data.requested_diet_type
            or intent_data.chosen_diet_type
            or intent_data.chosen_calorie_target is not None
            or intent_data.fixed_meals
            or intent_data.requested_exclusions
            or intent_data.new_plan_name
            or getattr(intent_data, "include_snack", False)
        )
        if not has_any_spec and not state.pending_workflow:
            await ask_for_new_plan_specs(supervisor, state, user_profile, user_message, history)
            await supervisor.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="user",
                msg_type="feedback",
                content=str(state.system_message or "")[:120] + "...",
                payload={"action": "new_plan_specs_request"}
            )
            return state

        # ------------------------------------------------------------------
        # Resolve requested parameters, carrying forward anything the user has
        # already told us while resolving a pending conflict question. This is
        # what lets a partial answer (e.g. "I prefer vegetarian") be honoured
        # without re-asking the question the user just answered.
        # ------------------------------------------------------------------
        pending = state.pending_workflow
        pending_info = dict(pending.extracted_info) if (pending and pending.extracted_info) else {}

        # Resolve diet/calorie values from the LLM's structured verdict (see
        # _pending_answer_values): confirmation-backed values while a question
        # is open, extracted request values for a fresh request.
        user_diet, user_cal = _pending_answer_values(intent_data, pending_info)

        req_diet = str(user_diet or pending_info.get("requested_diet") or "").lower() or None
        req_cal = user_cal if user_cal is not None else pending_info.get("cal_val")
        req_days = intent_data.requested_days or pending_info.get("days_val") or settings.default_plan_days

        # A conflict question that was already asked is considered resolved when
        # the user supplies the corresponding detail in this message.
        diet_pending = bool(pending_info.get("diet_pending"))
        cal_pending = bool(pending_info.get("cal_pending"))
        diet_resolved = bool(pending_info.get("diet_resolved")) or (diet_pending and user_diet is not None)
        cal_resolved = bool(pending_info.get("cal_resolved"))

        # A confirmation the user gave in this very message settles the
        # conflict even when no pending workflow survives to say so. The gate
        # drops the pending when it resumes a flow, and without this the diet
        # question was asked a second time for an answer already given.
        if intent_data.diet_conflict_confirmed or intent_data.chosen_diet_type:
            diet_resolved = True
        # Calories are only "resolved" when the user explicitly confirms a
        # guardrail prompt. A bare calorie number or chosen target does not
        # clear the question - the value must pass the safety check first.
        if intent_data.calorie_guardrail_confirmed:
            cal_resolved = True

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
                    # The safe range is what the user was actually offered in
                    # this prompt. Without it stored, answering with the
                    # recommended value looked like a contradiction and the
                    # flow deadlocked on "would you like me to switch?".
                    "suggested": meta.get("suggested_target"),
                    "min_safe": meta.get("min_safe"),
                    "max_safe": meta.get("max_safe"),
                    "is_calorie_unsafe": is_calorie_unsafe,
                    "diet_pending": True,
                    "cal_pending": bool(is_calorie_unsafe),
                    "diet_resolved": False,
                    "cal_resolved": False,
                    # Snapshot of the exact prompt shown, so resuming after an
                    # interruption re-presents the same question to the user.
                    "prompt_text": conflict_prompt,
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

        # Plan length guardrail. A plan longer than the supported maximum cannot
        # be built reliably in one run, so offer the longest supported plan
        # rather than silently generating something other than what was asked.
        max_days = settings.max_plan_days
        if req_days and req_days > max_days:
            # Pure arithmetic, no model call - reused only for the calorie
            # figures stored on the pending workflow below.
            _ok, _msg, _meta = supervisor.check_input_guardrail(
                user_profile=user_profile,
                requested_calories=req_cal,
                requested_days=req_days,
                fixed_meals=fixed,
                requested_diet_type=None,
            )
            suggested_target = _meta.get("suggested_target", 2350.0)
            min_safe = _meta.get("min_safe", 2250.0)
            max_safe = _meta.get("max_safe", 2450.0)
            diet_display = {"veg": "vegetarian", "non-veg": "non-vegetarian", "vegan": "vegan"}.get(effective_diet, effective_diet)
            days_context = (
                f"The user asked for a {req_days}-day {diet_display} meal plan. Plans are limited to "
                f"{max_days} days, because a longer one is built across several passes and the "
                f"portions and totals drift apart. In 2-3 warm, natural sentences explain that "
                f"{req_days} days is longer than we can do well in a single plan, and ask whether "
                f"they would like a {max_days}-day {diet_display} plan instead. Do not use technical "
                f"jargon, do not over-apologise, do not use bullet points, no greeting."
            )
            msg = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=days_context,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = msg
            state.status = "conversational"
            state.days = max_days
            await supervisor.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="user",
                msg_type="feedback",
                content=msg,
                payload={"action": "guardrail_block", "requested_days": req_days, "max_days": max_days}
            )
            # Stored so the follow-up answer ("yes please") is understood as
            # agreeing to the shorter plan rather than re-triggering the question.
            state.pending_workflow = PendingWorkflow(
                workflow_type="new_plan",
                pending_conflicts=["days_limit"],
                asked_questions=[msg],
                extracted_info={
                    "effective_diet": effective_diet,
                    "requested_diet": req_diet,
                    "diet_resolved": True,
                    "cal_pending": False,
                    "cal_resolved": False,
                    "cal_val": req_cal,
                    "days_val": max_days,
                    "requested_days": req_days,
                    "days_pending": True,
                    "days_resolved": False,
                    "suggested": suggested_target,
                    "min_safe": min_safe,
                    "max_safe": max_safe,
                    "req_days": max_days,
                    "prompt_text": msg,
                },
                next_expected_decision="days_limit_resolution",
                original_intent="new_plan",
                original_message=user_message,
                user_profile_at_start=user_profile.copy(),
                created_at=datetime.utcnow().isoformat()
            )
            return state

        # Calorie Guardrail Check: always validate before proceeding. Never let
        # an unsafe target slip through just because a question was "answered".
        is_safe, err_msg, meta = supervisor.check_input_guardrail(
            user_profile=user_profile,
            requested_calories=req_cal,
            requested_days=req_days,
            fixed_meals=fixed,
            requested_diet_type=None
        )
        if not is_safe:
            suggested = meta.get("suggested_target", 2350.0)
            min_safe = meta.get("min_safe", 2250.0)
            max_safe = meta.get("max_safe", 2450.0)
            diet_display = {"veg": "vegetarian", "non-veg": "non-vegetarian", "vegan": "vegan"}.get(effective_diet, effective_diet)
            cal_display = int(req_cal) if req_cal is not None else int(suggested)
            cal_pending = bool(pending_info.get("cal_pending"))

            # Use the guardrail's own message so the goal (lose/maintain/gain)
            # and direction (below/above) are accurate.
            goal = user_profile.get("goal", "maintain")
            relation = "below" if (req_cal or 0) < min_safe else "above"
            if goal == "lose":
                goal_guidance = (
                    f"Since your goal is to lose weight, your daily target should stay within "
                    f"{int(min_safe):,}–{int(max_safe):,} kcal to support sustainable fat loss "
                    f"without muscle wasting."
                )
            elif goal == "gain":
                goal_guidance = (
                    f"Since your goal is to gain weight, your daily target should be at or above "
                    f"{int(min_safe):,}–{int(max_safe):,} kcal to support healthy weight gain."
                )
            else:
                goal_guidance = (
                    f"Since your goal is to maintain weight, your daily target should be within "
                    f"{int(min_safe):,}–{int(max_safe):,} kcal. Planning below this range can "
                    f"lead to nutritional deficiencies and fatigue."
                )

            if cal_pending:
                # User is insisting on an unsafe value after already being warned.
                # Strictly refuse - do not ask again.
                guardrail_context = (
                    f"The user confirmed they want {diet_display} for this {req_days}-day meal plan, "
                    f"but they are insisting on {cal_display:,} kcal/day which is outside their safe "
                    f"range of {int(min_safe):,}–{int(max_safe):,} kcal for their {goal} goal. "
                    f"{goal_guidance} The recommended target is {int(suggested):,} kcal. "
                    f"Politely and firmly refuse to create a plan at {cal_display:,} kcal. "
                    f"Offer to create one at {int(suggested):,} kcal instead or another value within "
                    f"the safe range. Do NOT offer to proceed at the unsafe value. Keep it warm but "
                    f"firm, 2-3 sentences, no greeting."
                )
                msg = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=guardrail_context,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = msg
                state.status = "conversational"
                state.pending_workflow = None
                await supervisor.log_step(
                    state=state,
                    from_agent="supervisor",
                    to_agent="user",
                    msg_type="feedback",
                    content=msg,
                    payload={"action": "calorie_refused", "calories": req_cal, "suggested": suggested}
                )
                return state

            # First time hitting unsafe - ask confirmation with safe options only.
            guardrail_context = (
                f"The user just confirmed they want {diet_display} for this {req_days}-day meal plan. "
                f"First acknowledge that choice warmly. Then explain their requested target of "
                f"{cal_display:,} kcal/day is {relation} their safe range of "
                f"{int(min_safe):,}–{int(max_safe):,} kcal for their {goal} goal. "
                f"{goal_guidance} Ask them to confirm the recommended {int(suggested):,} kcal "
                f"or pick another safe value within the range. Do NOT offer to proceed at the "
                f"unsafe value. Keep it natural and conversational, 2-4 sentences, no greeting, "
                f"no bullet points. CRITICAL: their saved profile has NOT been changed. The diet "
                f"they just chose applies to THIS plan only."
            )
            msg = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=guardrail_context,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = msg
            state.status = "conversational"
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
                    "prompt_text": msg,
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
    # For edits, fold dish_to_replace terms into requested_exclusions up front
    # so the planner swarm and the surgical merge share one exclusion set.
    if intent_data.intent == "edit_meal":
        merged_excl = _edit_exclusion_terms(intent_data)
        try:
            intent_data.requested_exclusions = merged_excl
        except Exception:
            pass
    state.intent_data = intent_data

    # =========================================================================
    # Step 9a: Swarm gate - the single place that decides to spend minutes of
    # agent time.
    #
    # Every branch above returns on its own, so reaching here means the intent
    # fell through. That is exactly how "show my grocery list" once started a
    # full plan build. Only an explicit create/update may cross this line;
    # anything else is answered conversationally instead of being planned.
    # =========================================================================
    SWARM_INTENTS = ("new_plan", "edit_meal")
    if intent_data.intent not in SWARM_INTENTS:
        logger.info(
            f"[{tid}] Intent '{intent_data.intent}' reached the swarm gate; "
            f"answering conversationally instead of planning."
        )
        response_text = await supervisor.generate_conversational_response(
            user_message=user_message,
            context=(
                f"The user sent: '{user_message}'. This was understood as '{intent_data.intent}', which does not "
                f"need a new meal plan. Reply conversationally to what they actually asked, in warm natural "
                f"English. Do NOT create, generate or change any meal plan, and do NOT claim to have done so."
            ),
            history=history,
            user_name=user_profile.get("name"),
        )
        state.system_message = response_text
        state.status = "conversational"
        if state.pending_workflow:
            await append_resume_prompt(
                supervisor, state, response_text, state.pending_workflow,
                user_message, history, user_profile.get("name"),
            )
        return state

    graph = create_meal_planner_graph()
    result = await graph.ainvoke(state)

    if isinstance(result, PlanState):
        final_state = result
    else:
        final_state = PlanState.model_validate(result)

    # Clear pending workflow on successful workflow completion
    if final_state.current_plan and final_state.status in ("reviewing", "approved", "draft"):
        final_state.pending_workflow = None

    # Surgical edit merge: only the user-targeted slots may change; every other
    # meal is restored byte-identical from the previous version so unrelated
    # dishes (and their calories) never drift between versions.
    if intent_data.intent == "edit_meal" and existing_plan and final_state.current_plan:
        try:
            final_state.current_plan = _surgically_merge_edit(
                existing_plan, final_state.current_plan, intent_data
            )
        except Exception as e:
            logger.warning(f"[{tid}] Surgical merge failed, keeping generated plan: {e}")

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
                        f"In natural, polite, friendly conversational English, let the user know their modification has been applied to '{plan_title}' and that they can review it in the Plans tab and approve it whenever ready. "
                        f"Word it as an UPDATE: the plan already existed and their changes were applied to it. Do NOT say the plan was created, and do NOT claim their saved profile was changed (it was not)."
                    )
                    final_state.system_message = await supervisor.generate_conversational_response(
                        user_message=user_message,
                        context=context_prompt,
                        history=history,
                        user_name=user_profile.get("name")
                    )
                    # The update completed - drop any lingering in-progress update state.
                    final_state.pending_workflow = None
                    logger.info(f"[{tid}] Updated existing plan '{plan_title}' to Version {new_version} Draft (incremented={is_edit_previously_approved}).")
                else:
                    persist_status = final_state.status if final_state.status in ("escalated", "failed") else "draft"
                    plan_diet_str = getattr(final_state.current_plan, "diet_type", None) or user_profile.get("diet_type", "veg")
                    final_state.current_plan.diet_type = plan_diet_str
                    display_diet = "Non-Veg" if plan_diet_str == "non-veg" else plan_diet_str.capitalize()
                    p_name = getattr(final_state, "plan_name", None) or getattr(final_state.current_plan, "name", None) or f"{len(final_state.current_plan.days)}-Day {display_diet} Plan"
                    p_name = await unique_plan_name(repo, user_id, p_name)
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
                        f"In natural, polite, friendly conversational English, let the user know their meal plan has been CREATED and is ready to review in the Plans tab, and invite them to approve it or make adjustments whenever ready. "
                        f"CRITICAL: this is a brand-new plan, so it was CREATED, never updated. Do not use the word 'updated' and do not say an existing plan was changed. "
                        f"Do NOT claim their saved profile was changed (it was not) - a diet choice here applies to this plan only."
                    )
                    final_state.system_message = await supervisor.generate_conversational_response(
                        user_message=user_message,
                        context=context_prompt,
                        history=history,
                        user_name=user_profile.get("name")
                    )
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
                            f"In natural, polite, friendly conversational English, let the user know their modification has been applied to '{plan_title}' and that they can review it in the Plans tab and approve it whenever ready. "
                            f"Word it as an UPDATE: the plan already existed and their changes were applied to it. Do NOT say the plan was created, and do NOT claim their saved profile was changed (it was not)."
                        )
                        final_state.system_message = await supervisor.generate_conversational_response(
                            user_message=user_message,
                            context=context_prompt,
                            history=history,
                            user_name=user_profile.get("name")
                        )
                        logger.info(f"[{tid}] Updated existing plan '{plan_title}' to Version {new_version} Draft (incremented={is_edit_previously_approved}).")
                    else:
                        plan_diet_str = getattr(final_state.current_plan, "diet_type", None) or user_profile.get("diet_type", "veg")
                        final_state.current_plan.diet_type = plan_diet_str
                        display_diet = "Non-Veg" if plan_diet_str == "non-veg" else plan_diet_str.capitalize()
                        p_name = getattr(final_state, "plan_name", None) or getattr(final_state.current_plan, "name", None) or f"{len(final_state.current_plan.days)}-Day {display_diet} Plan"
                        p_name = await unique_plan_name(repo, user_id, p_name)
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
                            f"In natural, polite, friendly conversational English, let the user know their meal plan has been CREATED and is ready to review in the Plans tab, and invite them to approve it or make adjustments whenever ready. "
                            f"CRITICAL: this is a brand-new plan, so it was CREATED, never updated. Do not use the word 'updated' and do not say an existing plan was changed. "
                            f"Do NOT claim their saved profile was changed (it was not) - a diet choice here applies to this plan only."
                        )
                        final_state.system_message = await supervisor.generate_conversational_response(
                            user_message=user_message,
                            context=context_prompt,
                            history=history,
                            user_name=user_profile.get("name")
                        )
        except Exception as e:
            logger.error(f"[{tid}] Failed to persist plan to database: {e}", exc_info=True)

    return final_state
