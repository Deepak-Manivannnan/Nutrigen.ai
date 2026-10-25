"""The Supervisor turns the two review reports into a precise to-do list for the Planner."""
from backend.app.orchestration.supervisor.routing import decide_next_step, find_repair_targets
from backend.app.schemas.domain import (
    AllergyReport, AllergyViolation, DayPlan, Ingredient, Meal, MealPlan,
    NutritionIssue, NutritionReport,
)


def make_plan(days=3):
    def meal(t, d):
        return Meal(meal_type=t, recipe_name=f"{t} {d}", ingredients=[Ingredient(name="rice", amount_grams=100)], calories=600)
    return MealPlan(
        days=[DayPlan(day_number=d, meals=[meal("breakfast", d), meal("lunch", d), meal("dinner", d)]) for d in range(1, days + 1)],
        target_calories=1800.0,
    )


def violation(day, meal_type, ingredient="peanut butter"):
    return AllergyReport(passed=False, violations=[AllergyViolation(
        day_number=day, meal_type=meal_type, ingredient=ingredient, allergen="peanut", reason="contains peanuts")])


def too_many_calories(day):
    return NutritionReport(passed=False, issues=[NutritionIssue(
        day_number=day, meal_type=None, message=f"Day {day} is 400 kcal over target.", diff_calories=400, suggested_action="Reduce portions")])


def shape(targets):
    return sorted((i.day_number, i.meal_type) for i in targets.items)


def test_one_allergy_meal_flags_only_that_meal():
    t = find_repair_targets(make_plan(), violation(2, "lunch"), None)
    assert shape(t) == [(2, "lunch")]
    assert "peanut butter" in t.items[0].reasons[0]


def test_six_meals_one_bad_flags_exactly_one():
    t = find_repair_targets(make_plan(2), violation(1, "dinner"), NutritionReport(passed=True))
    assert shape(t) == [(1, "dinner")]


def test_calorie_problem_flags_the_whole_day_only():
    t = find_repair_targets(make_plan(), None, too_many_calories(3))
    assert shape(t) == [(3, None)]


def test_unknown_meal_name_falls_back_to_that_day_not_the_plan():
    t = find_repair_targets(make_plan(), violation(2, "brunch"), None)
    assert shape(t) == [(2, None)]


def test_meal_and_calorie_problem_on_same_day_merge_into_the_day():
    t = find_repair_targets(make_plan(), violation(2, "lunch"), too_many_calories(2))
    assert shape(t) == [(2, None)]
    assert len(t.items[0].reasons) == 2


def test_two_separate_problems_stay_separate():
    t = find_repair_targets(make_plan(), violation(1, "lunch"), too_many_calories(3))
    assert shape(t) == [(1, "lunch"), (3, None)]


def test_passing_reports_and_unknown_days_flag_nothing():
    plan = make_plan()
    assert find_repair_targets(plan, AllergyReport(passed=True), NutritionReport(passed=True)).is_empty
    assert find_repair_targets(plan, violation(9, "lunch"), None).is_empty
    assert find_repair_targets(plan, None, None).is_empty


def test_locked_meal_is_never_a_target():
    plan = make_plan()
    plan.days[1].meals[1].locked = True
    t = find_repair_targets(plan, violation(2, "lunch"), None)
    assert t.is_empty and t.locked_skipped == ["day 2 lunch"]


def test_retry_decision_carries_the_instruction_for_the_planner():
    d = decide_next_step(1, None, violation(2, "lunch"), plan=make_plan())
    assert d.action == "retry_planner"
    assert shape(d.repair) == [(2, "lunch")]
    assert d.feedback and "ALLERGY VETO" in d.feedback[0]      # the written feedback is still produced


def test_without_a_plan_the_decision_has_no_instruction_so_the_whole_plan_is_redone():
    d = decide_next_step(1, None, violation(2, "lunch"))
    assert d.action == "retry_planner" and d.repair is None


def test_passing_and_escalating_decisions_carry_no_instruction():
    assert decide_next_step(1, NutritionReport(passed=True), AllergyReport(passed=True), plan=make_plan()).repair is None
    assert decide_next_step(3, None, violation(2, "lunch"), plan=make_plan()).action == "escalate"
