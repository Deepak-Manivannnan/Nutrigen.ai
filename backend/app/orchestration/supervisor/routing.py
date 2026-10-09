from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from backend.app.schemas.domain import NutritionReport, AllergyReport
from backend.app.config import settings

class RouteDecision(BaseModel):
    action: Literal["proceed_shopper", "retry_planner", "escalate"]
    feedback: List[str] = Field(default_factory=list)
    reason: str

def decide_next_step(
    current_round: int,
    nutrition_report: Optional[NutritionReport],
    allergy_report: Optional[AllergyReport],
    max_rounds: Optional[int] = None
) -> RouteDecision:
    """
    Deterministic code routing logic inside Supervisor:
    1. Allergy veto always has priority and triggers replanning.
    2. Nutrition issues (> ±5%) trigger replanning with corrective feedback.
    3. If round limit (default 3) is reached, escalates to user.
    4. If both checks pass, proceeds to Shopper Agent.
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

    return RouteDecision(
        action="retry_planner",
        feedback=feedback_items,
        reason=f"Corrections required for round {current_round + 1}. Feedback provided to Planner."
    )
