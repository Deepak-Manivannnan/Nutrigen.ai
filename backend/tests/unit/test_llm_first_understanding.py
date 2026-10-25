"""The model decides what the user wants; code validates and executes (no database, no live AI).

Covers: unclear messages keep the conversation going, saved plans are shown to the
intent step, a plan is deleted only on a pending + model-confirmed yes (and never by
a word match), and the planner no longer reads edit scope/operation out of the text.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.app.db.models import Plan
from backend.app.orchestration import runner
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.orchestration.supervisor.intent import UserIntentExtraction, extract_user_intent
from backend.tests.fakes.fake_llm import FakeLLMClient

PROFILE = {
    "name": "Asha", "age": 30, "sex": "female", "height_cm": 165, "weight_kg": 60,
    "activity_level": "medium", "goal": "maintain", "diet_type": "veg", "allergies": [], "dislikes": [],
}


def make_plan(pid, name, days=5, diet="veg"):
    return Plan(id=pid, user_id=1, name=name, days=days, calorie_target=1800.0, diet_type=diet,
                status="draft", version=1, meals=[])


class FakeRepo(runner.PlanRepository):
    """In-memory stand-in: the real class's pure helpers stay, the database calls do not."""
    plans = []
    deleted = []

    def __init__(self, session=None):
        pass

    async def get_all_by_user(self, user_id):
        return list(FakeRepo.plans)

    async def delete_plan(self, plan_id):
        FakeRepo.deleted.append(plan_id)
        return True


@asynccontextmanager
async def _no_session():
    yield None


@pytest.fixture
def world(monkeypatch):
    """Two saved plans, a recording reply writer, and a scripted intent step."""
    FakeRepo.plans = [make_plan(1, "Good Meal"), make_plan(2, "Veg Week", days=7)]
    FakeRepo.deleted = []
    replies = []

    async def fake_reply(self, user_message, context, history=None, user_name=None):
        replies.append({"message": user_message, "context": context})
        return "(reply)"

    scripted = {}
    seen = {}

    async def fake_intent(user_message, client=None, history=None, pending_workflow=None, plans=None, just_created_plan_id=None):
        seen["plans"] = plans
        seen["just_created_plan_id"] = just_created_plan_id
        return scripted["intent"]

    monkeypatch.setattr(runner, "PlanRepository", FakeRepo)
    monkeypatch.setattr(runner, "AsyncSessionLocal", _no_session)
    monkeypatch.setattr(SupervisorAgent, "generate_conversational_response", fake_reply)
    monkeypatch.setattr(runner, "extract_user_intent", fake_intent)
    return SimpleNamespace(replies=replies, scripted=scripted, seen=seen)


async def run(world, message, intent, pending=None, history=None, **kw):
    world.scripted["intent"] = intent
    return await runner.run_meal_planner(
        user_id=1, user_message=message, user_profile=dict(PROFILE),
        history=history, pending_workflow=pending, **kw,
    )


def delete_pending(name):
    return runner.make_delete_pending(f"Are you sure you want to delete '{name}'?", f"delete {name}", PROFILE, target_plan_name=name)


# ------------------------------------------------------------------ unclear input
async def test_unclear_message_keeps_the_open_question_and_does_nothing(world):
    pending = delete_pending("Veg Week")
    state = await run(world, "ksdjf83", UserIntentExtraction(intent="unclear", unclear_reason="random characters"), pending=pending)

    assert state.status == "conversational"
    assert state.pending_workflow is pending            # the open question is still open
    assert FakeRepo.deleted == []                       # nothing was done
    ctx = world.replies[-1]["context"]
    assert "deleting one of your meal plans" in ctx     # names the thread we were in
    assert "Are you sure you want to delete 'Veg Week'?" in ctx   # and the question that is still open
    assert "Do NOT pretend you understood" in ctx


async def test_unclear_message_without_pending_work_picks_up_the_last_thing_said(world):
    history = [
        {"role": "user", "content": "make me a plan"},
        {"role": "assistant", "content": "Sure - how many days, and what calorie target?"},
    ]
    state = await run(world, ".", UserIntentExtraction(intent="unclear"), history=history)

    assert state.status == "conversational" and state.pending_workflow is None
    assert "how many days, and what calorie target?" in world.replies[-1]["context"]


async def test_unclear_message_at_the_very_start_offers_help(world):
    await run(world, "??", UserIntentExtraction(intent="unclear"))
    ctx = world.replies[-1]["context"]
    assert "Ask warmly what they would like to do" in ctx
    assert "already have saved meal plans" in ctx          # the world fixture has two plans


async def test_unclear_first_message_from_a_user_with_no_plans_offers_to_create_one(world):
    FakeRepo.plans = []
    await run(world, "??", UserIntentExtraction(intent="unclear"))
    ctx = world.replies[-1]["context"]
    assert "NO saved meal plans" in ctx
    assert "Offer to create their first meal plan" in ctx
    assert "already have saved meal plans" not in ctx


async def test_reply_that_claims_an_action_is_rewritten_for_unclear_too(monkeypatch, world):
    calls = []

    async def claims_then_asks(self, user_message, context, history=None, user_name=None):
        calls.append(context)
        return "I've deleted it for you." if len(calls) == 1 else "Sorry, I missed that - what did you mean?"
    monkeypatch.setattr(SupervisorAgent, "generate_conversational_response", claims_then_asks)

    state = await run(world, "xx", UserIntentExtraction(intent="unclear"))
    assert state.system_message == "Sorry, I missed that - what did you mean?"
    assert len(calls) == 2 and "IMPORTANT CORRECTION" in calls[1]


# ------------------------------------------------------------------ the intent step
async def test_message_with_no_letters_or_digits_never_reaches_the_model():
    class Exploding(FakeLLMClient):
        async def generate_structured(self, *a, **k):
            raise AssertionError("the model must not be called for a lone dot")

    for text in [".", "  ", "?!...", "", "-_-"]:
        out = await extract_user_intent(text, client=Exploding())
        assert out.intent == "unclear" and out.unclear_reason


async def test_digits_and_letters_are_left_to_the_model():
    llm = FakeLLMClient()
    llm.queue_structured_response(UserIntentExtraction(intent="unclear", unclear_reason="just digits"))
    out = await extract_user_intent("48392", client=llm)
    assert out.intent == "unclear" and len(llm.call_history) == 1


async def test_unreadable_model_answer_becomes_a_question_not_small_talk():
    class Broken(FakeLLMClient):
        async def generate_structured(self, *a, **k):
            raise ValueError("bad json")
    out = await extract_user_intent("make a plan", client=Broken())
    assert out.intent == "unclear"


async def test_saved_plans_are_shown_to_the_model_with_their_ids():
    llm = FakeLLMClient()
    llm.queue_structured_response(UserIntentExtraction(intent="approve_plan", target_plan_id=7))
    plans = [make_plan(7, "Keto Week", days=7, diet="non-veg"), make_plan(8, "Good Meal")]
    await extract_user_intent("approve it", client=llm, plans=plans, just_created_plan_id=7)

    prompt = llm.call_history[0]["prompt"]
    assert 'ID 7: "Keto Week" - 7 days' in prompt and 'ID 8: "Good Meal"' in prompt
    assert "created in the assistant's previous reply is ID 7" in prompt


async def test_runner_loads_plans_before_asking_the_model_to_interpret(world):
    await run(world, "show my plans", UserIntentExtraction(intent="chat"), just_created_plan_id=2)
    assert [p.id for p in world.seen["plans"]] == [1, 2]
    assert world.seen["just_created_plan_id"] == 2


# ------------------------------------------------------------------ deleting a plan
async def test_a_confirmed_delete_removes_the_plan_that_was_asked_about_not_the_first_one(world):
    intent = UserIntentExtraction(intent="delete_plan", is_delete_confirmation=True)
    state = await run(world, "yes please", intent, pending=delete_pending("Veg Week"))

    assert FakeRepo.deleted == [2]
    assert state.status == "deleted" and state.deleted_plan_id == 2 and state.pending_workflow is None


@pytest.mark.parametrize("message", [
    "ok let me cook dinner first",     # "ok" inside another sentence
    "yesterday I liked it",            # "yes" inside a word
    "hmm which one is that?",
])
async def test_words_inside_a_message_never_confirm_a_delete(world, message):
    intent = UserIntentExtraction(intent="delete_plan", is_delete_confirmation=False)
    state = await run(world, message, intent, pending=delete_pending("Veg Week"))

    assert FakeRepo.deleted == []
    assert state.status != "deleted"


@pytest.mark.parametrize("message", ["I know which one", "no idea, it's not important", "stop by later"])
async def test_words_inside_a_message_never_cancel_by_themselves(world, message):
    """The model said this is not a cancel and not a yes: nothing is deleted AND nothing is dropped silently."""
    intent = UserIntentExtraction(intent="delete_plan", is_delete_confirmation=False)
    state = await run(world, message, intent, pending=delete_pending("Veg Week"))

    assert FakeRepo.deleted == []
    assert state.pending_workflow is not None            # still waiting for a real answer
    assert state.status == "conversational"


async def test_a_yes_without_a_pending_question_does_not_delete(world):
    intent = UserIntentExtraction(intent="delete_plan", is_delete_confirmation=True, target_plan_id=1)
    state = await run(world, "yes delete plan 1", intent)

    assert FakeRepo.deleted == []
    assert state.pending_workflow is not None            # it asks to confirm first
    assert state.pending_workflow.extracted_info["target_plan_name"] == "Good Meal"


async def test_a_yes_that_points_at_a_different_plan_is_a_new_request_not_a_confirmation(world):
    intent = UserIntentExtraction(intent="delete_plan", is_delete_confirmation=True, target_plan_id=1)
    state = await run(world, "yes, delete Good Meal", intent, pending=delete_pending("Veg Week"))

    assert FakeRepo.deleted == []
    assert state.pending_workflow.extracted_info["target_plan_name"] == "Good Meal"


async def test_delete_with_several_plans_and_no_name_asks_which_instead_of_crashing(world):
    intent = UserIntentExtraction(intent="delete_plan")
    state = await run(world, "delete a plan", intent)

    assert FakeRepo.deleted == []
    assert state.status == "conversational" and state.pending_workflow is not None
    assert "multiple plans" in world.replies[-1]["context"]


async def test_the_model_names_the_plan_and_the_app_asks_before_deleting(world):
    intent = UserIntentExtraction(intent="delete_plan", target_plan_id=2)
    state = await run(world, "get rid of the week-long one", intent)

    assert FakeRepo.deleted == []
    assert state.pending_workflow.extracted_info["target_plan_name"] == "Veg Week"


# ------------------------------------------------------------------ editing: the model's verdict rules
def _plan_with_snack():
    from backend.app.schemas.domain import DayPlan, Ingredient, Meal, MealPlan

    def m(t, name):
        return Meal(meal_type=t, recipe_name=name, ingredients=[Ingredient(name="rice", amount_grams=100)], calories=500)
    day = DayPlan(day_number=1, meals=[m("breakfast", "Poha"), m("lunch", "Dal Rice"), m("snack", "Fruit"), m("dinner", "Khichdi")])
    return MealPlan(days=[day], target_calories=2000, diet_type="veg")


async def test_words_like_remove_in_the_instruction_do_not_delete_a_meal():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.app.orchestration.supervisor.intent import EditDetails
    from backend.app.schemas.domain import Ingredient, Meal

    llm = FakeLLMClient()
    llm.queue_structured_response(Meal(meal_type="lunch", recipe_name="Paneer Wrap", ingredients=[Ingredient(name="paneer", amount_grams=80)], calories=500))
    edit = EditDetails(day=1, meal_type="lunch", operation=None, instructions="remove the boring lunch and drop in something with paneer")

    out = await PlannerAgent(client=llm, max_steps=1).modify_meal(_plan_with_snack(), edit, dict(PROFILE))

    names = [m.recipe_name for m in out.days[0].meals]
    assert "Paneer Wrap" in names and len(names) == 4        # replaced, not removed
    assert "Dal Rice" not in names


async def test_the_models_remove_verdict_is_what_removes_a_meal():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.app.orchestration.supervisor.intent import EditDetails

    edit = EditDetails(day=1, meal_type="snack", operation="remove", edit_scope="slot", instructions="skip the snack")
    out = await PlannerAgent(client=FakeLLMClient(), max_steps=1).modify_meal(_plan_with_snack(), edit, dict(PROFILE))
    assert [m.meal_type for m in out.days[0].meals] == ["breakfast", "lunch", "dinner"]


async def test_phrases_like_all_days_in_the_instruction_do_not_widen_the_edit():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.app.orchestration.supervisor.intent import EditDetails
    from backend.app.schemas.domain import Ingredient, Meal

    llm = FakeLLMClient()
    llm.queue_structured_response(Meal(meal_type="snack", recipe_name="Nuts Mix", ingredients=[Ingredient(name="almonds", amount_grams=30)], calories=150))
    edit = EditDetails(day=1, meal_type="snack", operation="replace", edit_scope="slot",
                       instructions="swap this snack, I do not want it in all days, only this one")
    out = await PlannerAgent(client=llm, max_steps=1).modify_meal(_plan_with_snack(), edit, dict(PROFILE))

    assert len(llm.call_history) == 1                         # one meal written, not a plan-wide rewrite
    assert [m.recipe_name for m in out.days[0].meals if m.meal_type == "snack"] == ["Nuts Mix"]


async def test_a_bare_yes_the_model_reads_as_continue_confirms_the_pending_delete(world):
    """The model labels a lone "yes, go ahead" as resume_request; with a delete pending that IS the yes."""
    state = await run(world, "yes go ahead", UserIntentExtraction(intent="resume_request"), pending=delete_pending("Good Meal"))

    assert FakeRepo.deleted == [1]
    assert state.status == "deleted"


async def test_a_delete_request_while_something_else_is_pending_still_interrupts(world):
    pending = runner.make_edit_pending("Which meal should change?", "ask_slot", "Good Meal", "update my plan", PROFILE)
    state = await run(world, "delete Veg Week", UserIntentExtraction(intent="delete_plan", target_plan_id=2), pending=pending)

    assert FakeRepo.deleted == []
    assert "in the middle of" in world.replies[-1]["context"]


# ------------------------------------------------------------------ pausing is not cancelling
@pytest.mark.parametrize("make_pending", [
    lambda: delete_pending("Veg Week"),
    lambda: runner.make_approve_pending("Approve Veg Week?", "approve", PROFILE, target_plan_name="Veg Week"),
    lambda: runner.make_edit_pending("Which meal?", "ask_slot", "Veg Week", "update", PROFILE),
])
async def test_pausing_keeps_the_open_question_for_every_kind_of_action(world, make_pending):
    pending = make_pending()
    # Even if the model also labels it a cancel, the pause wins: keeping work is the safe side.
    state = await run(world, "let me think about it", UserIntentExtraction(intent="cancel_request", defer_pending=True), pending=pending)

    assert state.pending_workflow is pending
    assert FakeRepo.deleted == []
    ctx = world.replies[-1]["context"]
    assert "question is still open" in ctx and "nothing has been changed" in ctx


async def test_after_a_pause_a_plain_yes_deletes_without_asking_again(world):
    first = await run(world, "ok let me cook dinner first", UserIntentExtraction(intent="chat", defer_pending=True),
                      pending=delete_pending("Veg Week"))
    second = await run(world, "ok I'm back, yes delete it", UserIntentExtraction(intent="delete_plan", is_delete_confirmation=True),
                       pending=first.pending_workflow)

    assert FakeRepo.deleted == [2]
    assert second.status == "deleted"


async def test_a_real_no_still_cancels(world):
    state = await run(world, "no, keep it", UserIntentExtraction(intent="cancel_request", abandon_pending_work=True),
                      pending=delete_pending("Veg Week"))
    assert state.pending_workflow is None and FakeRepo.deleted == []


async def test_a_pause_with_nothing_open_is_just_chat(world):
    state = await run(world, "let me think", UserIntentExtraction(intent="chat", defer_pending=True))
    assert state.status == "conversational" and state.pending_workflow is None
    assert not any("question is still open" in r["context"] for r in world.replies)
