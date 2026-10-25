from typing import Dict, List, Optional, Literal, Tuple
from pydantic import BaseModel, Field
from backend.app.schemas.domain import (
    NutritionReport, AllergyReport, MealPlan, RepairItem, RepairTargets,
)
from backend.app.config import settings

class RouteDecision(BaseModel):
    action: Literal["proceed_shopper", "retry_planner", "escalate"]
    feedback: List[str] = Field(default_factory=list)
    reason: str
    # On a retry: exactly which meals/days the Planner should redo, worked out
    # from the two reviews. None when the plan has to be rewritten as a whole.
    repair: Optional[RepairTargets] = None


def find_repair_targets(
    plan: MealPlan,
    allergy_report: Optional[AllergyReport],
    nutrition_report: Optional[NutritionReport],
) -> RepairTargets:
    """Turn the two review reports into a precise to-do list for the Planner.

    This is Supervisor logic (it reads other agents' reports), so it lives here
    and the Planner only ever receives the resulting RepairTargets.

    - An allergy or diet veto names a day and a meal -> that one meal is redone.
    - A calorie problem is about a whole day -> that day's unlocked meals are redone.
    - A veto whose meal cannot be matched to a meal in that day falls back to the
      whole day, never to the whole plan.
    - Locked (user-fixed) meals are never targeted.
    """
    slots: Dict[int, Dict[str, bool]] = {
        d.day_number: {m.meal_type.lower(): bool(m.locked) for m in d.meals}
        for d in plan.days
    }
    meal_reasons: Dict[Tuple[int, str], List[str]] = {}
    day_reasons: Dict[int, List[str]] = {}
    locked_skipped: List[str] = []

    def flag_day(day: int, reason: str) -> None:
        if day not in slots:
            return  # a day number that is not in this plan cannot be repaired
        if all(slots[day].values()):
            locked_skipped.extend(f"day {day} {m}" for m in slots[day])
            return  # every meal that day is locked
        day_reasons.setdefault(day, []).append(reason)

    def flag_meal(day: int, meal_type: str, reason: str) -> None:
        if day not in slots:
            return
        if meal_type in slots[day]:
            if slots[day][meal_type]:
                locked_skipped.append(f"day {day} {meal_type}")
                return
            meal_reasons.setdefault((day, meal_type), []).append(reason)
        else:
            # The reviewer named a meal we cannot find: redo that whole day
            # rather than guess, but still only that day.
            flag_day(day, reason)

    if allergy_report is not None and not allergy_report.passed:
        for v in allergy_report.violations:
            flag_meal(
                v.day_number,
                (v.meal_type or "").strip().lower(),
                f"it contained '{v.ingredient}' ({v.reason})",
            )

    if nutrition_report is not None and not nutrition_report.passed:
        for issue in nutrition_report.issues:
            reason = f"{issue.message} Action: {issue.suggested_action}"
            meal_type = (issue.meal_type or "").strip().lower()
            if meal_type:
                flag_meal(issue.day_number, meal_type, reason)
            else:
                flag_day(issue.day_number, reason)

    # A flagged day already covers every meal in it.
    for day in list(day_reasons):
        for key in [k for k in meal_reasons if k[0] == day]:
            day_reasons[day].extend(meal_reasons.pop(key))

    items = [RepairItem(day_number=d, meal_type=None, reasons=r) for d, r in sorted(day_reasons.items())]
    items += [RepairItem(day_number=d, meal_type=m, reasons=r) for (d, m), r in sorted(meal_reasons.items())]
    return RepairTargets(items=items, locked_skipped=locked_skipped)


def decide_next_step(
    current_round: int,
    nutrition_report: Optional[NutritionReport],
    allergy_report: Optional[AllergyReport],
    max_rounds: Optional[int] = None,
    plan: Optional[MealPlan] = None,
) -> RouteDecision:
    """
    Deterministic code routing logic inside Supervisor:
    1. Allergy veto always has priority and triggers replanning.
    2. Nutrition issues (> ±5%) trigger replanning with corrective feedback.
    3. If round limit (default 3) is reached, escalates to user.
    4. If both checks pass, proceeds to Shopper Agent.

    On a retry, when the current plan is supplied, the decision also carries
    `repair`: the exact meals/days to redo, so everything that passed is kept.
    """
    limit = max_rounds or settings.max_rounds

    has_allergy_violation = allergy_report is not None and not allergy_report.passed
    has_nutrition_issues = nutrition_report is not None and not nutrition_report.passed

    # Both passed!
    if not has_allergy_violation and not has_nutrition_issues:
        return RouteDecision(
            action="proceed_shopper",
            feedback=[],
            reason="Both Nutrition and Allergy safety reviews passed."
        )

    # If any problems and round limit reached -> Escalate to user
    if current_round >= limit:
        all_reasons = []
        if has_allergy_violation and allergy_report:
            all_reasons.extend([f"Allergy Veto: {v.reason}" for v in allergy_report.violations])
        if has_nutrition_issues and nutrition_report:
            all_reasons.extend([f"Nutrition Issue: {i.message}" for i in nutrition_report.issues])

        return RouteDecision(
            action="escalate",
            feedback=all_reasons,
            reason=f"Reached round limit ({limit}) with unresolved issues: {'; '.join(all_reasons)}"
        )

    # Build feedback list for Planner replanning
    feedback_items: List[str] = []

    # Priority 1: Allergy Vetoes (Plan Section 14: "Allergy always wins")
    if has_allergy_violation and allergy_report:
        for v in allergy_report.violations:
            feedback_items.append(
                f"[ALLERGY VETO] Day {v.day_number} {v.meal_type} contains '{v.ingredient}' ({v.reason}). "
                f"Replace this ingredient or meal entirely with an allergen-free alternative."
            )

    # Priority 2: Nutrition discrepancies
    if has_nutrition_issues and nutrition_report:
        for issue in nutrition_report.issues:
            feedback_items.append(
                f"[NUTRITION ADJUSTMENT] Day {issue.day_number}: {issue.message}. Action: {issue.suggested_action}"
            )

    repair: Optional[RepairTargets] = None
    if plan is not None:
        found = find_repair_targets(plan, allergy_report, nutrition_report)
        if not found.is_empty:
            repair = found

    return RouteDecision(
        action="retry_planner",
        feedback=feedback_items,
        reason=f"Corrections required for round {current_round + 1}. Feedback provided to Planner.",
        repair=repair,
    )
