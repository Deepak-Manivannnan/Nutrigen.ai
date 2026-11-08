"""
Live progress card: the run_started event comes first and at once, the
opening line is the Supervisor's own (fallback only if that fails), events
are replayed to a late listener, and approval sends its three steps.
"""
import asyncio
from types import SimpleNamespace

from backend.app.observability.trace import TraceHub
from backend.app.orchestration import progress


def _state(**kw):
    base = dict(task_id="t-run", days=5, calorie_target=1800.0, user_profile={"diet_type": "veg", "name": "Asha"},
                cuisine=None, intent="new_plan", main_ingredients=[], style_notes=[],
                fixed_meals={"breakfast": "eggs, bread and milk"}, include_snack=False,
                intent_data=SimpleNamespace(requested_exclusions=["paneer"]))
    base.update(kw)
    return SimpleNamespace(**base)


class Sup:
    def __init__(self, reply=None, fail=False, delay=0.0):
        self.reply, self.fail, self.delay, self.contexts = reply, fail, delay, []

    async def generate_conversational_response(self, user_message, context, history=None, user_name=None):
        self.contexts.append(context)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("model down")
        return self.reply


def _capture(monkeypatch):
    hub = TraceHub()
    monkeypatch.setattr(progress, "trace_hub", hub)
    return hub


async def _events(hub, task_id="t-run"):
    q = hub.subscribe(task_id)
    out = []
    while not q.empty():
        out.append(q.get_nowait().payload)
    return out


def test_the_opening_line_uses_the_confirmed_details():
    facts = progress.plan_facts(_state())
    assert "5 days" in facts and "about 1,800 kcal per day" in facts and "vegetarian" in facts
    assert "without paneer" in facts and "eggs, bread and milk for breakfast" in facts


async def test_card_first_then_the_supervisors_own_opening_line(monkeypatch):
    hub = _capture(monkeypatch)
    sup = Sup(reply="Got it! A 5-day vegetarian plan at about 1,800 kcal coming up. Running our agents now.")
    await progress.announce_plan_run(sup, _state(), "plan 5 days", [])
    events = await _events(hub)
    assert events[0]["event"] == "run_started" and events[0]["kind"] == "plan"
    assert events[1] == {"event": "intro", "text": sup.reply}
    assert "5 days" in sup.contexts[0] and "without paneer" in sup.contexts[0]


async def test_fallback_only_when_the_model_fails(monkeypatch):
    hub = _capture(monkeypatch)
    await progress.announce_plan_run(Sup(fail=True), _state(), "plan", [])
    events = await _events(hub)
    assert events[1]["text"] == progress.PLAN_FALLBACK


async def test_fallback_when_the_model_is_too_slow(monkeypatch):
    hub = _capture(monkeypatch)
    monkeypatch.setattr(progress, "INTRO_TIMEOUT_SECONDS", 0.05)
    await progress.announce_plan_run(Sup(reply="late", delay=0.5), _state(), "plan", [])
    assert (await _events(hub))[1]["text"] == progress.PLAN_FALLBACK


async def test_an_update_gets_the_update_wording(monkeypatch):
    hub = _capture(monkeypatch)
    await progress.announce_plan_run(Sup(fail=True), _state(intent="edit_meal"), "change lunch", [])
    events = await _events(hub)
    assert events[0]["kind"] == "edit" and events[1]["text"] == progress.EDIT_FALLBACK


async def test_a_listener_that_connects_late_still_gets_every_event(monkeypatch):
    hub = _capture(monkeypatch)
    await progress.approval_event("t-late", "run_started", "running", intro=progress.APPROVE_INTRO)
    await progress.approval_event("t-late", "grocery", "running")
    q = hub.subscribe("t-late")                       # browser connects now
    await progress.approval_event("t-late", "grocery", "done", items=12, cost=840)
    steps = []
    while not q.empty():
        steps.append(q.get_nowait().payload["step"])
    assert steps == ["run_started", "grocery", "grocery"]


async def test_approval_sends_grocery_safety_and_approved(monkeypatch):
    hub = _capture(monkeypatch)
    from backend.app.orchestration import approval
    from backend.tests.unit.test_approval_final_check import _fake_shopper, _meal_plan
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["rice"]))
    await approval.build_and_verify_grocery(_meal_plan(), [], session=None, task_id="a", progress_task_id="t-ap")
    got = [(e["step"], e["status"]) for e in await _events(hub, "t-ap")]
    assert got == [("grocery", "running"), ("grocery", "done"), ("safety", "running"),
                   ("safety", "done"), ("approved", "done")]


async def test_a_blocked_approval_shows_the_failed_safety_check(monkeypatch):
    hub = _capture(monkeypatch)
    import pytest
    from backend.app.orchestration import approval
    from backend.tests.unit.test_approval_final_check import _fake_shopper, _meal_plan
    monkeypatch.setattr(approval, "ShopperAgent", _fake_shopper(["peanut butter"]))
    with pytest.raises(approval.ApprovalBlocked):
        await approval.build_and_verify_grocery(_meal_plan(), ["peanut"], session=None, task_id="a", progress_task_id="t-bl")
    got = [(e["step"], e["status"]) for e in await _events(hub, "t-bl")]
    assert got[-1] == ("safety", "failed") and ("approved", "done") not in got
