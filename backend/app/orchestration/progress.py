"""
The chat's live progress card: the first message of a run and its start event.

When a plan build starts, the browser immediately gets a "run_started"
event (the card appears at once). The Supervisor then writes a short,
personal opening line from the details the user confirmed and sends it as
an "intro" event; if that fails or is slow, a plain fallback line is sent
instead. None of this blocks or slows the agents.
"""
import asyncio
import re
from typing import Any, Dict, List

from backend.app.observability.logging import get_logger
from backend.app.observability.trace import trace_hub

logger = get_logger("orchestration.progress")

PLAN_FALLBACK = "Got it! I'll create your meal plan now. Let me run our agents to prepare it for you…"
EDIT_FALLBACK = "Got it! I'll update your plan now. Let me run our agents on it…"
APPROVE_INTRO = "Got it! I'll go ahead and build your grocery list and cost estimate."
INTRO_TIMEOUT_SECONDS = 6.0


def plan_facts(state) -> List[str]:
    """The confirmed request, in plain words, for the opening line."""
    facts: List[str] = []
    if state.days:
        facts.append(f"{state.days} day{'s' if state.days != 1 else ''}")
    if state.calorie_target:
        facts.append(f"about {int(round(state.calorie_target)):,} kcal per day")
    diet = (state.user_profile or {}).get("diet_type")
    if diet:
        facts.append({"veg": "vegetarian", "non-veg": "non-vegetarian"}.get(diet, diet))
    if state.cuisine:
        facts.append(f"{state.cuisine} cuisine")
    intent = state.intent_data
    excl = list(getattr(intent, "requested_exclusions", None) or [])
    if excl:
        facts.append("without " + ", ".join(excl))
    if state.main_ingredients:
        facts.append("built around " + ", ".join(state.main_ingredients))
    if state.style_notes:
        facts.append(", ".join(state.style_notes))
    for slot, meal in (state.fixed_meals or {}).items():
        if meal:
            facts.append(f"{meal} for {slot}")
    if state.include_snack:
        facts.append("with a daily snack")
    return facts


def _edit_change(state) -> str:
    """The change the user asked for, in their own words where available."""
    details = getattr(state, "edit_details", None) or getattr(state.intent_data, "edit_details", None)
    text = getattr(details, "instructions", None) if details is not None else None
    if text is None and isinstance(details, dict):
        text = details.get("instructions")
    return str(text or "").strip()


async def _write_intro(supervisor, state, user_message: str, history, kind: str) -> str:
    if kind == "edit":
        change = _edit_change(state) or user_message
        context = (
            f"The user asked to change their existing meal plan: \"{change}\". NOTHING has been changed yet; "
            "you are about to start. Write ONE or TWO short, warm sentences that say what you are about to "
            "change (in plain words) and that you're running your agents on it now. Use future or present "
            "tense only (\"I'll...\", \"I'm...\"), NEVER \"I have\" or \"I've\". Do not mention the grocery "
            "list. Do not ask a question. No emojis, no markdown."
        )
    else:
        facts = plan_facts(state)
        context = (
            "The user has confirmed everything and you are about to create their meal plan. NOTHING has been "
            f"made yet. Confirmed details: {'; '.join(facts) if facts else 'as discussed'}. "
            "Write ONE or TWO short, warm sentences that restate the plan they asked for using these details "
            "(naturally, not as a list), then say you're running your agents to prepare it now. Use future or "
            "present tense only, NEVER \"I have\" or \"I've\". Do not say the plan is ready, do not mention a "
            "grocery list, do not ask a question. No emojis, no markdown."
        )
    text = await supervisor.generate_conversational_response(
        user_message=user_message, context=context, history=history,
        user_name=(state.user_profile or {}).get("name"),
    )
    # Claimed the work was already done: don't show it, use the plain line.
    from backend.app.orchestration.runner import _DONE_CLAIM_RE
    if _DONE_CLAIM_RE.search(text or "") or re.search(r"\b(I have|I've|I’ve)\b", text or "", re.I):
        logger.info(f"[{state.task_id}] Opening line claimed finished work; using the default line.")
        return ""
    return text


async def emit_run_started(state) -> None:
    """Sent before the agents start, so the card is open for their first event."""
    kind = "edit" if state.intent == "edit_meal" else "plan"
    await trace_hub.emit(state.task_id, "supervisor", "status",
                         {"event": "run_started", "kind": kind, "days": state.days})


async def announce_plan_run(supervisor, state, user_message: str, history, started: bool = False) -> None:
    """Card first (instantly), then the personal opening line."""
    kind = "edit" if state.intent == "edit_meal" else "plan"
    if not started:
        await emit_run_started(state)
    fallback = EDIT_FALLBACK if kind == "edit" else PLAN_FALLBACK
    try:
        text = await asyncio.wait_for(_write_intro(supervisor, state, user_message, history, kind),
                                      timeout=INTRO_TIMEOUT_SECONDS)
        text = (text or "").strip() or fallback
    except Exception as e:
        logger.info(f"[{state.task_id}] Opening line fell back to the default: {type(e).__name__}")
        text = fallback
    await trace_hub.emit(state.task_id, "supervisor", "status", {"event": "intro", "text": text})


async def start_plan_announcement(supervisor, state, user_message: str, history) -> "asyncio.Task":
    """Open the card now; write the opening line next to the agents."""
    await emit_run_started(state)
    task = asyncio.create_task(announce_plan_run(supervisor, state, user_message, history, started=True))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return task


async def approval_event(task_id: str, step: str, status: str, **extra: Any) -> None:
    """Approval card steps: run_started, grocery, safety, approved."""
    payload: Dict[str, Any] = {"event": "approval", "step": step, "status": status, **extra}
    await trace_hub.emit(task_id, "supervisor", "status", payload)
