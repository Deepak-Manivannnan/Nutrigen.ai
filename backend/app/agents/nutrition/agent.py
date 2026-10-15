import os
import asyncio
import logging
from typing import Dict, Any, Optional, List, Literal
from pydantic import BaseModel, Field, field_validator
from backend.app.agents.base import BaseAgent
from backend.app.agents.nutrition.tools import NUTRITION_TOOLS
from backend.app.schemas.domain import MealPlan, NutritionReport, NutritionIssue
from backend.app.tools.nutrition.calculator import audit_meal_plan_nutrition
from backend.app.tools.nutrition.staples import get_staple_nutrition
from backend.app.tools.nutrition.usda import (
    lookup_nutrition,
    set_ingredient_aliases,
    set_ingredient_estimates,
    clear_ingredient_aliases,
)
from backend.app.config import settings
from backend.app.llm.client import LLMClient

logger = logging.getLogger("agents.nutrition")

# How many unresolved ingredient names are sent to the model in one go.
MAX_RESOLUTIONS_PER_CALL = 40

# Minimum confidence in a USDA match before the agent is asked to re-search it.
# 'black olives' -> 'Olives, black' scores 1.0 and is trusted with no model call.
# 'beetroot poriyal' -> 'ROASTED BEETROOT SEEDED SNACKERS' shares only one word
# and names a packaged snack, so it scores 0.2 and is escalated.
MIN_MATCH_QUALITY = 0.6


class IngredientResolution(BaseModel):
    """One dish name rewritten into a term a nutrition database can resolve."""
    ingredient: str = Field(
        ...,
        description="The ingredient name exactly as it appears in the plan."
    )
    resolved_query: str = Field(
        ...,
        description=(
            "A single, specific, searchable food term describing the MAIN "
            "ingredient of this dish in its cooked/plain form. Examples: "
            "'masala poha' -> 'poha'; 'chickpea chaat' -> 'chickpeas'; "
            "'beetroot poriyal' -> 'beets'. Never a brand, never a packaged "
            "snack, never a whole prepared recipe."
        )
    )
    reason: str = Field(
        "",
        description="Short justification, under 15 words."
    )


class IngredientResolutionSet(BaseModel):
    resolutions: List[IngredientResolution] = Field(default_factory=list)


class CandidateVerdict(BaseModel):
    """The model's judgement about one poorly-matched ingredient."""
    ingredient: str = Field(..., description="The ingredient name as given.")
    verdict: Literal["pick", "research"] = Field(
        ...,
        description="'pick' if one of the listed records IS the right food, "
                    "otherwise 'research'."
    )
    search_term: str = Field(
        ...,
        description="MUST differ from the ingredient name. A simpler, plainer "
                    "food word to search for instead."
    )
    reason: str = Field("", description="Under 20 words.")

    @field_validator("search_term", mode="after")
    @classmethod
    def must_be_a_different_term(cls, v, info):
        """Reject an echo of the input.

        Without this the model happily says 'none of these are right' and then
        returns the original ingredient unchanged as its search term, which
        re-runs the identical bad lookup and achieves nothing.
        """
        ingredient = (info.data.get("ingredient") or "").strip().lower()
        term = (v or "").strip()
        if ingredient and term.lower() == ingredient:
            raise ValueError(
                "search_term must be a DIFFERENT, simpler food term than the ingredient"
            )
        return term


class CandidateVerdictSet(BaseModel):
    verdicts: List[CandidateVerdict] = Field(default_factory=list)


class MacroEstimate(BaseModel):
    """Agent-supplied macros for an ingredient the database cannot supply."""
    ingredient: str = Field(..., description="The ingredient name as given.")
    calories: float = Field(..., description="kcal per 100 g. Must be > 0.")
    protein_g: float = Field(..., ge=0.0, description="Grams per 100 g.")
    carbs_g: float = Field(..., ge=0.0, description="Grams per 100 g.")
    fat_g: float = Field(..., ge=0.0, description="Grams per 100 g.")
    basis: str = Field(
        "",
        description="The state the values describe, e.g. 'raw' or 'cooked'.",
    )

    @field_validator("calories", mode="after")
    @classmethod
    def calories_must_be_real(cls, v):
        if not (0.0 < float(v) <= 900.0):
            raise ValueError("calories per 100 g must be between 0 and 900")
        return v


class MacroEstimateSet(BaseModel):
    estimates: List[MacroEstimate] = Field(default_factory=list)


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
            # Hardcoded rather than inherited from AGENT_TIMEOUT_SECONDS.
            # This agent's only clocked work is the prose summary - the calorie
            # maths and the pass/fail verdict are deterministic and get
            # overwritten from code afterwards regardless of what the model
            # says. A longer budget therefore buys words, not accuracy, and when
            # it does expire the base class makes one more direct call to finish
            # cleanly rather than returning anything unfinished.
            timeout_seconds=timeout_seconds or 15
        )

    async def _probe_ingredients(self, plan: MealPlan) -> Dict[str, Dict[str, Any]]:
        """Look up every ingredient the staple table cannot answer, in parallel.

        Populates the runtime cache, so the audit that follows does not repeat
        these calls. Each result carries both the confidence of the chosen
        record and the alternatives the database actually offered.
        """
        names = sorted({
            ing.name.strip().lower()
            for day in plan.days
            for meal in day.meals
            for ing in meal.ingredients
            if ing.name and ing.name.strip()
            and not get_staple_nutrition(ing.name.strip().lower())
        })
        if not names:
            return {}
        results = await asyncio.gather(
            *(lookup_nutrition(n) for n in names),
            return_exceptions=True,
        )
        out: Dict[str, Dict[str, Any]] = {}
        for name, res in zip(names, results):
            out[name] = {} if isinstance(res, Exception) else res
        return out

    @staticmethod
    def _is_confident(res: Dict[str, Any]) -> bool:
        if not res or not res.get("found"):
            return False
        return float(res.get("match_quality") or 0.0) >= MIN_MATCH_QUALITY

    async def _ask_for_better_search_terms(
        self, uncertain: List[str], probe: Dict[str, Dict[str, Any]]
    ) -> Dict[str, str]:
        """Step 3: show the model what the database actually offered and let it judge.

        The earlier version asked the model to invent a search term with no idea
        what the database contained, so it proposed "mint" for mint chutney and
        the database returned a mint julep. Showing the candidate list lets it
        say "none of these, search rice white cooked instead".
        """
        candidates = uncertain[:MAX_RESOLUTIONS_PER_CALL]
        blocks = []
        for name in candidates:
            offered = (probe.get(name) or {}).get("candidates") or []
            listed = "\n".join(f"      - {d}" for d in offered[:5]) or "      (nothing)"
            blocks.append(f"  ingredient: {name}\n    the database offers:\n{listed}")

        prompt = (
            "A nutrition database matched these ingredients badly. For EACH one:\n"
            "1. Read what the database actually offers.\n"
            "2. If one offered item is genuinely the same food -> verdict 'pick'.\n"
            "3. Otherwise -> verdict 'research', and give a SIMPLER, PLAINER food "
            "word as search_term.\n"
            "   The search_term MUST be different from the ingredient name. Drop "
            "regional words, qualifiers and dish names; keep the base food.\n"
            "   e.g. 'basmati rice' -> 'rice, white, cooked'; "
            "'nutritional yeast' -> 'yeast'; 'crusty bread' -> 'bread, white'.\n\n"
            + "\n\n".join(blocks)
        )

        # Mechanical lookup, not reasoning: temporarily route to the cheap
        # model. `using_model` restores the agent's configured model on the way
        # out, including if this raises.
        try:
            with self.llm.using_model(settings.ingredient_lookup_model):
                result: CandidateVerdictSet = await self.llm.generate_structured(
                    prompt=prompt, response_schema=CandidateVerdictSet
                )
        except Exception as e:
            logger.warning(f"[NutritionAgent] Search-term step skipped ({e}).")
            return {}

        aliases: Dict[str, str] = {}
        valid = set(candidates)
        for v in (result.verdicts if result else []):
            key = (v.ingredient or "").strip().lower()
            value = (v.search_term or "").strip().lower()
            if key in valid and value and value != key and len(value) <= 60:
                aliases[key] = value
        return aliases

    async def _ask_for_macro_estimates(
        self, unresolvable: List[str], basis: str
    ) -> Dict[str, Dict[str, Any]]:
        """Step 4: no usable record anywhere, so the model supplies the macros.

        Genuinely a last resort - these are the only figures in a plan with no
        verifiable source. They are installed with an `estimated` flag so the
        plan can show which ingredients are measured and which are inferred.
        """
        if not unresolvable:
            return {}
        names = unresolvable[:MAX_RESOLUTIONS_PER_CALL]

        prompt = (
            "A nutrition database has no suitable record for these foods. "
            f"Give the nutrition PER 100 GRAMS in the '{basis}' state as listed.\n"
            "Be accurate - these are used in a clinical meal planner. Calories must "
            "be above zero.\n\n" + "\n".join(f"- {n}" for n in names)
        )

        try:
            with self.llm.using_model(settings.ingredient_lookup_model):
                result: MacroEstimateSet = await self.llm.generate_structured(
                    prompt=prompt, response_schema=MacroEstimateSet
                )
        except Exception as e:
            logger.warning(f"[NutritionAgent] Macro estimation skipped ({e}).")
            return {}

        estimates: Dict[str, Dict[str, Any]] = {}
        valid = set(names)
        for est in (result.estimates if result else []):
            key = (est.ingredient or "").strip().lower()
            if key not in valid:
                continue
            estimates[key] = {
                "calories": est.calories,
                "protein_g": est.protein_g,
                "carbs_g": est.carbs_g,
                "fat_g": est.fat_g,
                "basis": est.basis or basis,
            }
        return estimates

    async def review_plan(
        self,
        plan: MealPlan,
        calorie_target: float,
        user_profile: Dict[str, Any],
        task_id: Optional[str] = None
    ) -> NutritionReport:
        # Agent-assisted ingredient resolution, before any math. Improves which
        # record each food is matched to; the math and the verdict stay
        # deterministic and the agent can never override them.
        aliases: Dict[str, str] = {}
        estimates: Dict[str, Dict[str, Any]] = {}
        try:
            probe = await self._probe_ingredients(plan)
            uncertain = [n for n, r in probe.items() if not self._is_confident(r)]

            if uncertain:
                logger.info(
                    f"[NutritionAgent] {len(uncertain)}/{len(probe)} ingredients "
                    f"matched poorly; showing the model the alternatives."
                )
                aliases = await self._ask_for_better_search_terms(uncertain, probe)

            if aliases:
                set_ingredient_aliases(aliases)
                # Re-probe only the rewritten names; everything else is cached.
                recheck = await asyncio.gather(
                    *(lookup_nutrition(n) for n in aliases),
                    return_exceptions=True,
                )
                still_bad = [
                    n for n, r in zip(aliases, recheck)
                    if isinstance(r, Exception) or not self._is_confident(r)
                ]
                if still_bad:
                    basis = "raw"
                    if str(user_profile.get("goal") or "") == "maintain":
                        basis = "cooked"
                    estimates = await self._ask_for_macro_estimates(still_bad, basis)
                    if estimates:
                        logger.info(
                            f"[NutritionAgent] No database record for "
                            f"{len(estimates)} ingredient(s); using model estimates."
                        )
        except Exception as e:
            logger.warning(f"[NutritionAgent] Ingredient resolution error ({e}).")
            aliases, estimates = {}, {}

        set_ingredient_aliases(aliases)
        set_ingredient_estimates(estimates)
        try:
            # 1. Deterministic Math Engine (Plan section 7: 'Code does the math')
            code_passed, code_issues, code_daily_totals = await audit_meal_plan_nutrition(plan, calorie_target)
        finally:
            # Never leak overrides into an unrelated plan.
            clear_ingredient_aliases()

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
