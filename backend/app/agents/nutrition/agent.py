import os
import logging
from typing import Dict, Any, Optional
from backend.app.agents.base import BaseAgent
from backend.app.agents.nutrition.tools import NUTRITION_TOOLS
from backend.app.schemas.domain import MealPlan, NutritionReport, NutritionIssue
from backend.app.tools.nutrition.calculator import audit_meal_plan_nutrition
from backend.app.llm.client import LLMClient

logger = logging.getLogger("agents.nutrition")


def _load_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are the Nutrition Agent. Verify calorie and macro balance per meal and day."

class NutritionAgent(BaseAgent[NutritionReport]):
    """
    Nutrition Agent: Verifies meal calories and macronutrients against USDA data.
    Architecture:
    - Code does the exact math (audit_meal_plan_nutrition): calories, protein, carbs, fat, ±5% tolerance.
    - LLM provides clinical analysis, macro balance evaluation, and actionable portion adjustments for the Planner.
    """
    def __init__(
        self,
        client: Optional[LLMClient] = None,
        max_steps: Optional[int] = None,
        timeout_seconds: Optional[int] = None
    ):
        super().__init__(
            name="NutritionAgent",
            system_prompt=_load_prompt(),
            output_schema=NutritionReport,
            tools=NUTRITION_TOOLS,
            client=client,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds
        )

    async def review_plan(
        self,
        plan: MealPlan,
        calorie_target: float,
        user_profile: Dict[str, Any],
        task_id: Optional[str] = None
    ) -> NutritionReport:
        # 1. Deterministic Math Engine (Plan section 7: 'Code does the math')
        code_passed, code_issues, code_daily_totals = await audit_meal_plan_nutrition(plan, calorie_target)

        # 2. Contextual instructions for the LLM
        issue_descriptions = [f"Day {i.day_number}: diff {i.diff_calories:+.0f} kcal ({i.message})" for i in code_issues]
        issues_summary = "\n".join(issue_descriptions) if issue_descriptions else "All days are within ±5% of calorie target."

        task_instruction = (
            f"Review the nutritional profile of the proposed {len(plan.days)}-day meal plan.\n"
            f"Target daily calories: {calorie_target:.0f} kcal (±5% tolerance).\n"
            f"Deterministic Code Math Results:\n"
            f"- Pass Status: {'PASSED' if code_passed else 'CALORIE MISMATCH'}\n"
            f"- Issues Detected:\n{issues_summary}\n\n"
            f"Your clinical role: Analyze macro distribution (protein/carbs/fat for user goal '{user_profile.get('goal', 'maintain')}'), "
            f"and provide concrete portion adjustment recommendations for any flagged days."
        )

        context = {
            "task_id": task_id,
            "plan": plan.model_dump(),
            "calorie_target": calorie_target,
            "calculated_daily_totals": [d.model_dump() for d in code_daily_totals],
            "calculated_issues": [i.model_dump() for i in code_issues],
            "code_passed": code_passed,
            "user_profile": {
                "activity_level": user_profile.get("activity_level"),
                "goal": user_profile.get("goal")
            }
        }

        # 3. Run LLM reasoning loop
        try:
            report: NutritionReport = await self.run(task_instruction, context)
        except Exception as e:
            logger.warning(f"NutritionAgent LLM review failed ({e}); falling back to deterministic calculations.")
            report = NutritionReport(
                daily_totals=code_daily_totals,
                passed=code_passed,
                issues=code_issues,
                summary=f"Nutritional profile verified deterministically: all days meet {calorie_target:.0f} kcal/day target." if code_passed else f"Calorie target mismatch on {len(code_issues)} day(s)."
            )

        # 4. Enforce Deterministic Code Math Guarantee (Overrule LLM arithmetic errors)
        report.daily_totals = code_daily_totals
        report.passed = code_passed

        # Merge code-calculated issues with LLM recommendations
        if not code_passed:
            # Map LLM suggestions to code issues if available
            llm_suggestions = {iss.day_number: iss.suggested_action for iss in report.issues if iss.suggested_action}
            final_issues = []
            for c_issue in code_issues:
                action = llm_suggestions.get(c_issue.day_number) or c_issue.suggested_action
                final_issues.append(NutritionIssue(
                    day_number=c_issue.day_number,
                    meal_type=c_issue.meal_type,
                    message=c_issue.message,
                    diff_calories=c_issue.diff_calories,
                    suggested_action=action
                ))
            report.issues = final_issues
            if not report.summary:
                report.summary = f"Calorie target mismatch on {len(code_issues)} day(s). Adjustments required."
        else:
            report.issues = []
            if not report.summary:
                report.summary = f"Nutritional profile verified: all days meet {calorie_target:.0f} kcal/day target within ±5%."

        return report
