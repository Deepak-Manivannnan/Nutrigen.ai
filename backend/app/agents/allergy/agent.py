import os
from typing import Dict, Any, Optional, List
from backend.app.agents.base import BaseAgent
from backend.app.agents.allergy.tools import ALLERGY_TOOLS, check_allergens_tool
from backend.app.schemas.domain import MealPlan, AllergyReport, AllergyViolation
from backend.app.llm.client import LLMClient

def _load_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are the Allergy Agent. Enforce zero-tolerance allergen safety and issue hard vetoes."

class AllergyAgent(BaseAgent[AllergyReport]):
    """
    Allergy Agent: Enforces zero-tolerance safety checks for user allergies.
    Investigates compound ingredients with LLM reasoning, while deterministic
    database code guarantees a hard veto for any verified allergen.
    """
    def __init__(
        self,
        client: Optional[LLMClient] = None,
        max_steps: Optional[int] = None,
        timeout_seconds: Optional[int] = None
    ):
        super().__init__(
            name="AllergyAgent",
            system_prompt=_load_prompt(),
            output_schema=AllergyReport,
            tools=ALLERGY_TOOLS,
            client=client,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds
        )

    async def review_plan(
        self,
        plan: MealPlan,
        user_allergies: List[str],
        task_id: Optional[str] = None
    ) -> AllergyReport:
        clean_allergies = [a.strip().lower() for a in user_allergies if a.strip()]
        
        plan_diet = getattr(plan, "diet_type", "veg") or "veg"
        from backend.app.tools.diet_rules import check_diet_compliance

        # Check for immediate deterministic diet violations across all days and meals
        deterministic_diet_violations = []
        for day in plan.days:
            for meal in day.meals:
                ing_names = [ing.name for ing in meal.ingredients]
                names_to_check = ing_names + [meal.recipe_name]
                d_viols = check_diet_compliance(plan_diet, names_to_check)
                for dv in d_viols:
                    deterministic_diet_violations.append(AllergyViolation(
                        day_number=day.day_number,
                        meal_type=meal.meal_type,
                        ingredient=dv["ingredient"],
                        allergen=f"Dietary Violation ({plan_diet.capitalize()})",
                        reason=dv["violation"]
                    ))

        # If user has no allergies and no diet violations, return clean report
        if not clean_allergies and not deterministic_diet_violations:
            return AllergyReport(
                passed=True,
                violations=[],
                investigated_items=[],
                summary="User profile specifies zero allergies and plan complies with diet constraints."
            )

        task_instruction = (
            f"Review this {len(plan.days)}-day meal plan for allergic hazards and dietary violations.\n"
            f"User's allergies: {clean_allergies}\n"
            f"Plan diet type: {plan_diet}\n"
            f"Use `check_allergens` for direct allergen verification, investigate compound, derivative, or unclear items using culinary intelligence, "
            f"and issue a hard veto (`passed: false`) if ANY allergen or diet-violating ingredient is present."
        )

        context = {
            "task_id": task_id,
            "user_allergies": clean_allergies,
            "plan_diet": plan_diet,
            "plan": plan.model_dump()
        }

        report: AllergyReport
        if clean_allergies:
            report = await self.run(task_instruction, context)
        else:
            report = AllergyReport(passed=True, violations=[], investigated_items=[], summary="Allergies clear.")

        # Hybrid Architecture Layer 1: Deterministic Code Guarantee
        # Append deterministic diet violations
        for dv in deterministic_diet_violations:
            existing = any(
                viol.day_number == dv.day_number and viol.ingredient.lower() == dv.ingredient.lower()
                for viol in report.violations
            )
            if not existing:
                report.violations.append(dv)

        # Enforce hard veto for any directly matched or compound allergen regardless of LLM reasoning
        for day in plan.days:
            for meal in day.meals:
                ing_names = [ing.name for ing in meal.ingredients]
                db_violations = await check_allergens_tool(clean_allergies, ing_names)
                for v in db_violations:
                    existing = any(
                        viol.day_number == day.day_number and viol.ingredient.lower() == v["ingredient"].lower()
                        for viol in report.violations
                    )
                    if not existing:
                        report.violations.append(AllergyViolation(
                            day_number=day.day_number,
                            meal_type=meal.meal_type,
                            ingredient=v["ingredient"],
                            allergen=v["allergen"],
                            reason=v["reason"]
                        ))

        if report.violations:
            report.passed = False
            report.summary = f"Safety check failed with {len(report.violations)} violation(s). Hard veto enforced."

        return report
