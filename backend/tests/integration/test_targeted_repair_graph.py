"""The Supervisor's retry round sends the Planner to ONLY the flagged meals (fake agents, no AI)."""
from backend.app.orchestration.graph import create_meal_planner_graph
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.schemas.domain import (
    RepairTargets,
    AllergyReport, AllergyViolation, DayPlan, GroceryItem, GroceryList, Ingredient, Meal,
    MealPlan, NutritionReport,
)
from backend.app.schemas.state import PlanState

# 2550 kcal is the recommended target for this profile, so the input guardrail lets it through.
PROFILE = {
    "name": "Jordan", "age": 28, "sex": "male", "height_cm": 175.0, "weight_kg": 70.0,
    "activity_level": "medium", "goal": "maintain", "diet_type": "non-veg", "allergies": ["peanut"],
}


def meal(meal_type, name, ingredient="rice"):
    return Meal(
        meal_type=meal_type, recipe_name=name, diet_type="non-veg", source="ai_generated",
        calories=600.0, ingredients=[Ingredient(name=ingredient, amount_grams=200.0)],
        instructions="1. Prep.\n2. Cook.\n3. Serve warm.",
    )


def make_plan(bad_lunch_on_day=None):
    days = []
    for d in range(1, 3):
        lunch = meal("lunch", f"Lunch {d}", "peanut butter" if d == bad_lunch_on_day else "rice")
        days.append(DayPlan(day_number=d, meals=[meal("breakfast", f"Breakfast {d}"), lunch, meal("dinner", f"Dinner {d}")],
                            total_calories=1800.0))
    return MealPlan(name="P", days=days, target_calories=2550.0, diet_type="non-veg", average_daily_calories=1800.0)


class FakePlanner:
    def __init__(self):
        self.generate_calls = 0
        self.repair_targets = []

    async def generate_plan(self, **kwargs):
        self.generate_calls += 1
        plan = make_plan(bad_lunch_on_day=2)
        plan.version = kwargs.get("version", 1)
        return plan

    async def repair_plan(self, current_plan, targets, calorie_target, user_profile, version=1, **kwargs):
        self.repair_targets.append(targets)
        fixed = current_plan.model_copy(deep=True)
        fixed.days[1].meals[1] = meal("lunch", "Chicken Rice", "chicken")
        fixed.version = version
        return fixed


class FakeNutrition:
    async def review_plan(self, **kwargs):
        return NutritionReport(passed=True, summary="ok")


class FakeAllergy:
    """Vetoes the peanut butter lunch on day 2, passes once it is gone."""
    async def review_plan(self, plan, user_allergies, task_id=None):
        for day in plan.days:
            for m in day.meals:
                if any("peanut" in i.name for i in m.ingredients):
                    return AllergyReport(passed=False, summary="veto", violations=[AllergyViolation(
                        day_number=day.day_number, meal_type=m.meal_type, ingredient="peanut butter",
                        allergen="peanut", reason="contains peanuts")])
        return AllergyReport(passed=True, summary="clean")




async def test_retry_round_repairs_only_the_flagged_meal_instead_of_regenerating():
    planner = FakePlanner()
    graph = create_meal_planner_graph(
        planner=planner, nutrition=FakeNutrition(), allergy=FakeAllergy(),
        supervisor=SupervisorAgent(),
    )
    state = PlanState(task_id="t-repair-1", user_id=1, user_profile=PROFILE, days=2, calorie_target=2550.0)

    result = PlanState.model_validate(await graph.ainvoke(state))

    # Round 1 wrote the plan once; round 2 repaired it instead of writing it again.
    assert planner.generate_calls == 1
    assert len(planner.repair_targets) == 1
    targets: RepairTargets = planner.repair_targets[0]
    # The Supervisor worked the instruction out from the Allergy report: one meal.
    assert [(i.day_number, i.meal_type) for i in targets.items] == [(2, "lunch")]
    assert "peanut butter" in targets.items[0].reasons[0]

    assert result.round == 2
    assert result.current_plan.days[1].meals[1].recipe_name == "Chicken Rice"
    assert result.allergy_report.passed
    assert result.status != "escalated"

    # The supervisor's trace says what happened.
    repair_messages = [m for m in result.trace if m.payload.get("repair") is True]
    assert len(repair_messages) == 1
    # ...and the Supervisor's own retry message says what it asked for.
    asked = [m for m in result.trace if m.type == "feedback" and m.payload.get("repair")]
    assert asked and asked[0].payload["repair"] == "meal(s) day 2 lunch"
    assert repair_messages[0].payload["meals"] == ["day 2 lunch"]

    # Hub and spoke: every message in the run is to or from the supervisor.
    for msg in result.trace:
        assert msg.from_agent == "supervisor" or msg.to_agent == "supervisor"

    # The instruction was cleared once the Planner used it.
    assert result.repair_targets is None


async def test_failed_repair_falls_back_to_writing_the_whole_plan():
    class BrokenRepairPlanner(FakePlanner):
        async def repair_plan(self, *args, **kwargs):
            raise RuntimeError("repair blew up")

        async def generate_plan(self, **kwargs):
            self.generate_calls += 1
            return make_plan(bad_lunch_on_day=2 if self.generate_calls == 1 else None)

    planner = BrokenRepairPlanner()
    graph = create_meal_planner_graph(
        planner=planner, nutrition=FakeNutrition(), allergy=FakeAllergy(),
        supervisor=SupervisorAgent(),
    )
    state = PlanState(task_id="t-repair-2", user_id=1, user_profile=PROFILE, days=2, calorie_target=2550.0)

    result = PlanState.model_validate(await graph.ainvoke(state))

    assert planner.generate_calls == 2          # full regeneration was the fallback
    assert result.allergy_report.passed


# --- Editing one meal keeps working exactly as before ----------------------
from types import SimpleNamespace


class EditPlanner(FakePlanner):
    def __init__(self):
        super().__init__()
        self.modify_calls = []

    async def modify_meal(self, current_plan, edit_details, user_profile, task_id=None, exclusions=None, **kwargs):
        self.modify_calls.append(edit_details)
        updated = current_plan.model_copy(deep=True)
        updated.days[0].meals[1] = meal("lunch", "Paneer Wrap", "paneer")
        return updated


async def test_editing_a_meal_uses_the_edit_path_and_never_the_repair_or_full_rewrite():
    planner = EditPlanner()
    graph = create_meal_planner_graph(
        planner=planner, nutrition=FakeNutrition(), allergy=FakeAllergy(),
        supervisor=SupervisorAgent(),
    )
    state = PlanState(
        task_id="t-edit-1", user_id=1, user_profile=PROFILE, intent="edit_meal",
        days=2, calorie_target=2550.0, current_plan=make_plan(),
        edit_details=SimpleNamespace(day=1, meal_type="lunch", instructions="make lunch a paneer wrap"),
    )

    result = PlanState.model_validate(await graph.ainvoke(state))

    assert len(planner.modify_calls) == 1
    assert planner.generate_calls == 0 and planner.repair_targets == []
    assert result.current_plan.days[0].meals[1].recipe_name == "Paneer Wrap"
    # Every other meal of the existing plan is still there, unchanged.
    original = make_plan()
    for d_old, d_new in zip(original.days, result.current_plan.days):
        for m_old, m_new in zip(d_old.meals, d_new.meals):
            if (d_old.day_number, m_old.meal_type) != (1, "lunch"):
                assert m_old.model_dump() == m_new.model_dump()
