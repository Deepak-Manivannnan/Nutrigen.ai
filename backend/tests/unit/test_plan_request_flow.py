"""Plan requests: the number of days is asked, the cuisine is kept, new exclusions can be remembered (no database, no live AI)."""
from types import SimpleNamespace

import pytest

from backend.app.orchestration import runner
from backend.app.orchestration.supervisor.intent import UserIntentExtraction
from backend.app.schemas.domain import DayPlan, Ingredient, Meal, MealPlan
from backend.app.schemas.state import PlanState
# pytest fixtures and helpers shared with the sibling module
from backend.tests.unit.test_llm_first_understanding import FakeRepo, PROFILE, run, world  # noqa: F401


class CreatingRepo(FakeRepo):
    """FakeRepo that also 'saves' a new plan."""
    created = []

    async def create_plan(self, **kwargs):
        CreatingRepo.created.append(kwargs)
        return SimpleNamespace(id=99)


def finished_plan():
    meal = lambda t, n: Meal(meal_type=t, recipe_name=n, ingredients=[Ingredient(name="rice", amount_grams=100)], calories=600)
    day = DayPlan(day_number=1, meals=[meal("breakfast", "Idli"), meal("lunch", "Sambar Rice"), meal("dinner", "Dosa")], total_calories=1800)
    return MealPlan(days=[day], target_calories=2050.0, diet_type="veg", name="Plan")


class FakeGraph:
    """Stands in for the agent graph; records the state it was started with."""
    def __init__(self, produce_plan=False):
        self.started = []
        self.produce_plan = produce_plan

    async def ainvoke(self, state):
        self.started.append(state)
        update = {"status": "reviewing" if self.produce_plan else "failed"}
        if self.produce_plan:
            update["current_plan"] = finished_plan()
        return state.model_copy(update=update)


@pytest.fixture
def graph(monkeypatch, world):  # noqa: F811
    CreatingRepo.plans = list(FakeRepo.plans)
    CreatingRepo.created = []
    monkeypatch.setattr(runner, "PlanRepository", CreatingRepo)
    fake = FakeGraph()
    monkeypatch.setattr(runner, "create_meal_planner_graph", lambda *a, **k: fake)
    return fake


def new_plan(**kw):
    kw.setdefault("requested_calories", 2050.0)      # a safe target for the test profile
    return UserIntentExtraction(intent="new_plan", **kw)


# ------------------------------------------------------------------ number of days
async def test_missing_days_are_asked_for_never_assumed(world, graph):
    state = await run(world, "make me a South Indian plan", new_plan(requested_cuisine="South Indian"))

    assert graph.started == []                                   # nothing was built
    assert state.status == "conversational"
    pw = state.pending_workflow
    assert pw.pending_conflicts == ["days_missing"]
    ctx = world.replies[-1]["context"]
    assert "has not said how many days" in ctx and "at most 7 days" in ctx
    assert "do NOT start creating the plan" in ctx
    assert "South Indian" in ctx                                 # what they already said is acknowledged
    assert pw.extracted_info["carry"]["cuisine"] == "South Indian"


async def test_the_days_answer_completes_the_original_request(world, graph):
    first = await run(world, "make me a South Indian plan", new_plan(requested_cuisine="South Indian", requested_exclusions=["paneer"]))
    pending = first.pending_workflow

    # "3 days" - the model returns only the days; the cuisine is not repeated.
    second = await run(world, "3 days", new_plan(requested_days=3, requested_calories=None), pending=pending)

    assert len(graph.started) == 1
    started = graph.started[0]
    assert started.days == 3
    assert started.cuisine == "South Indian"                     # remembered from the first message
    assert started.intent_data.requested_exclusions == ["paneer"]
    assert "in the middle of" not in " ".join(r["context"] for r in world.replies)   # not treated as an interruption


async def test_a_request_with_days_and_cuisine_goes_straight_to_planning(world, graph):
    await run(world, "3 day South Indian plan", new_plan(requested_days=3, requested_cuisine="South Indian", requested_calories=None))

    assert len(graph.started) == 1
    assert graph.started[0].days == 3 and graph.started[0].cuisine == "South Indian"


async def test_a_fresh_complete_request_does_not_inherit_the_old_cuisine(world, graph):
    first = await run(world, "make me a South Indian plan", new_plan(requested_cuisine="South Indian"))
    # The user changes their mind with a complete new specification.
    await run(world, "actually a 4 day veg plan at 2050 kcal", new_plan(requested_days=4, requested_diet_type="veg"),
              pending=first.pending_workflow)
    started = graph.started[-1]
    assert started.days == 4 and started.cuisine is None


async def test_the_bare_create_offer_asks_for_days_and_names_no_default_length(world, graph):
    await run(world, "create a meal plan", UserIntentExtraction(intent="new_plan"))

    ctx = world.replies[-1]["context"]
    assert "how many days" in ctx and "up to 7" in ctx
    assert "-day plan with those profile" not in ctx            # no invented length
    assert graph.started == []


def test_input_check_no_longer_invents_a_length():
    from backend.app.guardrails.input_checks import validate_input_request
    profile = dict(PROFILE, suggested_target_calories=2050, min_safe_calories=1950, max_safe_calories=2150)
    ok, _msg, meta = validate_input_request(profile, 2050.0, None)
    assert ok and meta["days"] is None


async def test_the_graph_refuses_to_plan_without_a_length():
    from backend.app.orchestration.graph import create_meal_planner_graph
    from backend.app.orchestration.supervisor.agent import SupervisorAgent

    class Never:
        async def generate_plan(self, **k):
            raise AssertionError("must not plan without days")
    graph = create_meal_planner_graph(planner=Never(), supervisor=SupervisorAgent())
    profile = dict(PROFILE, suggested_target_calories=2050)
    result = PlanState.model_validate(await graph.ainvoke(PlanState(
        task_id="t-nodays", user_id=1, user_profile=profile, days=None, calorie_target=2050.0)))
    assert result.status == "failed" and "how many days" in result.system_message


# ------------------------------------------------------------------ remembering new exclusions
@pytest.fixture
def creating_graph(monkeypatch, world):  # noqa: F811
    CreatingRepo.plans = []
    CreatingRepo.created = []
    monkeypatch.setattr(runner, "PlanRepository", CreatingRepo)
    fake = FakeGraph(produce_plan=True)
    monkeypatch.setattr(runner, "create_meal_planner_graph", lambda *a, **k: fake)
    return fake


async def test_new_exclusions_are_applied_then_the_user_is_asked_to_remember_them(world, creating_graph):
    intent = new_plan(requested_days=3, requested_exclusions=["paneer", "Mushroom"])
    state = await run(world, "3 day plan without paneer and mushroom", intent)

    assert len(CreatingRepo.created) == 1                        # the plan was made first
    assert state.pending_workflow.workflow_type == "save_dislikes"
    assert sorted(state.pending_workflow.extracted_info["exclusions"]) == ["mushroom", "paneer"]
    assert any("not in their saved disliked foods" in r["context"] for r in world.replies)
    assert any("only asking; nothing has been saved yet" in r["context"] for r in world.replies)
    assert state.profile_updated is False                        # nothing saved yet


async def test_exclusions_the_profile_already_has_are_not_asked_about(world, creating_graph, monkeypatch):
    import backend.app.orchestration.runner as r
    original = r.run_meal_planner
    intent = new_plan(requested_days=3, requested_exclusions=["Pork", "mushrooms"])
    world.scripted["intent"] = intent
    profile = dict(PROFILE, dislikes=["pork"], allergies=["Mushrooms"])
    state = await r.run_meal_planner(user_id=1, user_message="no pork or mushrooms", user_profile=profile)

    assert state.pending_workflow is None                        # both already covered: no question


@pytest.fixture
def saver(monkeypatch, world):  # noqa: F811
    saved = []

    async def fake_save(user_id, items, session=None):
        saved.append((user_id, list(items)))
        return ["pork"] + list(items)
    monkeypatch.setattr(runner, "_save_dislikes", fake_save)
    return saved


def save_pending():
    return runner.PendingWorkflow(
        workflow_type="save_dislikes", pending_conflicts=["save_dislikes"],
        asked_questions=["Want me to remember paneer?"], extracted_info={"exclusions": ["paneer", "mushroom"]},
        next_expected_decision="save_dislikes_answer", original_intent=None, original_message="x",
    )


async def test_yes_saves_the_foods_as_dislikes_and_refreshes_the_profile(world, saver):
    state = await run(world, "yes please", UserIntentExtraction(intent="chat", save_preference_answer=True), pending=save_pending())

    assert saved_items(saver) == ["paneer", "mushroom"]
    assert state.pending_workflow is None and state.profile_updated is True
    assert "future meal plans will leave" in world.replies[-1]["context"]


def saved_items(saver):
    assert len(saver) == 1
    return saver[0][1]


async def test_no_keeps_the_exclusions_to_this_plan_only(world, saver):
    state = await run(world, "no, just this once", UserIntentExtraction(intent="chat", save_preference_answer=False), pending=save_pending())

    assert saver == []                                            # nothing saved
    assert state.pending_workflow is None and state.profile_updated is False
    assert "that one plan only" in world.replies[-1]["context"]


async def test_the_models_continue_and_cancel_readings_count_as_yes_and_no(world, saver):
    await run(world, "go ahead", UserIntentExtraction(intent="resume_request"), pending=save_pending())
    assert len(saver) == 1
    await run(world, "nah", UserIntentExtraction(intent="cancel_request"), pending=save_pending())
    assert len(saver) == 1                                        # the second one did not save


async def test_ignoring_the_question_drops_it_and_handles_the_new_message(world, saver):
    state = await run(world, "what is protein?", UserIntentExtraction(intent="explain"), pending=save_pending())

    assert saver == []
    assert state.pending_workflow is None                         # let go quietly, never forced
    assert state.status == "conversational"


async def test_a_failed_save_is_reported_honestly(world, monkeypatch):
    async def boom(user_id, items, session=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(runner, "_save_dislikes", boom)
    state = await run(world, "yes", UserIntentExtraction(intent="chat", save_preference_answer=True), pending=save_pending())

    assert state.profile_updated is False
    assert "NOTHING was saved" in world.replies[-1]["context"]


def test_new_exclusions_helper_compares_ignoring_case_and_duplicates():
    out = runner._new_exclusions(["Pork", "paneer", "PANEER", " "], {"dislikes": ["pork"], "allergies": []})
    assert out == ["paneer"]


# ------------------------------------------------------------------ cuisine
async def test_planner_prompts_hold_every_meal_to_the_cuisine():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.tests.fakes.fake_llm import FakeLLMClient

    llm = FakeLLMClient()
    llm.queue_structured_response(finished_plan())
    await PlannerAgent(client=llm, max_steps=1).generate_plan(
        days=1, calorie_target=2050.0, user_profile=dict(PROFILE), cuisine="South Indian")

    prompts = " ".join(c["prompt"] for c in llm.call_history)
    assert "CUISINE RULE" in prompts and "genuine South Indian dish" in prompts
    assert "Do NOT include dishes from other cuisines" in prompts


async def test_repaired_meals_stay_in_the_cuisine():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.tests.fakes.fake_llm import FakeLLMClient
    from backend.app.schemas.domain import RepairItem, RepairTargets
    from backend.tests.agents.test_planner_repair import offline_nutrition  # noqa: F401

    llm = FakeLLMClient()
    llm.queue_structured_response(Meal(meal_type="lunch", recipe_name="Curd Rice", ingredients=[Ingredient(name="rice", amount_grams=150)], calories=500))
    targets = RepairTargets(items=[RepairItem(day_number=1, meal_type="lunch", reasons=["too much salt"])])
    await PlannerAgent(client=llm, max_steps=1).repair_plan(
        finished_plan(), targets, 2050.0, dict(PROFILE), version=2, cuisine="South Indian")

    assert "genuine South Indian dish" in llm.call_history[0]["prompt"]


def test_regional_cuisines_search_the_right_recipe_area():
    from backend.app.tools.recipes.themealdb import _normalise_area
    for regional in ["South Indian", "Punjabi", "kerala", "Tamil"]:
        assert _normalise_area(regional) == "indian"


# ------------------------------------------------------------------ the plan remembers its cuisine
async def test_a_new_plan_is_saved_with_its_cuisine(world, creating_graph):
    await run(world, "3 day South Indian plan", new_plan(requested_days=3, requested_cuisine="South Indian"))
    assert CreatingRepo.created[0]["cuisine"] == "South Indian"


def test_a_saved_plan_carries_its_cuisine_back_into_the_domain_plan():
    from backend.app.db.models import Plan, PlanMeal
    meal = PlanMeal(plan_id=1, day=1, meal_type="lunch", recipe_name="Sambar Rice", ingredients=[], calories=600.0,
                    macros={}, locked=False, source="ai_generated", diet_type="veg")
    plan = Plan(id=1, user_id=1, name="P", days=1, calorie_target=1800.0, diet_type="veg", cuisine="South Indian",
                status="draft", version=1, meals=[meal])
    assert runner.PlanRepository.to_meal_plan_domain(plan).cuisine == "South Indian"
    plan.cuisine = None
    assert runner.PlanRepository.to_meal_plan_domain(plan).cuisine is None


async def test_editing_a_meal_stays_in_the_plans_cuisine_and_asks_nothing_else(world, graph):
    from backend.app.db.models import Plan, PlanMeal
    from backend.app.orchestration.supervisor.intent import EditDetails
    meal = PlanMeal(plan_id=1, day=1, meal_type="lunch", recipe_name="Sambar Rice", ingredients=[], calories=600.0,
                    macros={}, locked=False, source="ai_generated", diet_type="veg")
    FakeRepo.plans = [Plan(id=1, user_id=1, name="Dakshin Week", days=1, calorie_target=1800.0, diet_type="veg",
                           cuisine="South Indian", status="draft", version=1, meals=[meal])]
    CreatingRepo.plans = FakeRepo.plans

    intent = UserIntentExtraction(
        intent="edit_meal", target_plan_id=1,
        edit_details=EditDetails(day=1, meal_type="lunch", operation="replace", edit_scope="slot", instructions="something lighter"),
    )
    await run(world, "make Tuesday lunch lighter", intent)

    assert len(graph.started) == 1                                # straight to the edit: no diet / calorie / cuisine questions
    assert graph.started[0].cuisine == "South Indian"             # taken from the plan itself
    assert graph.started[0].current_plan.cuisine == "South Indian"


async def test_an_edit_that_names_another_cuisine_uses_it_for_that_meal(world, graph):
    from backend.app.db.models import Plan, PlanMeal
    from backend.app.orchestration.supervisor.intent import EditDetails
    meal = PlanMeal(plan_id=1, day=1, meal_type="lunch", recipe_name="Sambar Rice", ingredients=[], calories=600.0,
                    macros={}, locked=False, source="ai_generated", diet_type="veg")
    FakeRepo.plans = [Plan(id=1, user_id=1, name="Dakshin Week", days=1, calorie_target=1800.0, diet_type="veg",
                           cuisine="South Indian", status="draft", version=1, meals=[meal])]
    CreatingRepo.plans = FakeRepo.plans
    intent = UserIntentExtraction(
        intent="edit_meal", target_plan_id=1, requested_cuisine="Italian",
        edit_details=EditDetails(day=1, meal_type="lunch", operation="replace", edit_scope="slot", instructions="an Italian pasta"),
    )
    await run(world, "make day 1 lunch an Italian pasta", intent)
    assert graph.started[0].cuisine == "Italian"


async def test_modify_meal_prompts_hold_the_new_meal_to_the_cuisine():
    from backend.app.agents.planner.agent import PlannerAgent
    from backend.app.orchestration.supervisor.intent import EditDetails
    from backend.tests.fakes.fake_llm import FakeLLMClient
    llm = FakeLLMClient()
    llm.queue_structured_response(Meal(meal_type="lunch", recipe_name="Lemon Rice", ingredients=[Ingredient(name="rice", amount_grams=150)], calories=500))
    edit = EditDetails(day=1, meal_type="lunch", operation="replace", edit_scope="slot", instructions="lighter")
    await PlannerAgent(client=llm, max_steps=1).modify_meal(
        finished_plan(), edit, dict(PROFILE), cuisine="South Indian")
    assert "genuine South Indian dish" in llm.call_history[0]["prompt"]
