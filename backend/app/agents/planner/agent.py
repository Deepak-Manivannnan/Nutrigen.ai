import os
import re
import asyncio
from typing import Dict, Any, Optional, List
from backend.app.agents.base import BaseAgent
from backend.app.agents.planner.tools import PLANNER_TOOLS
from backend.app.schemas.domain import MealPlan, Meal, DayPlan, Ingredient, RepairTargets
from backend.app.llm.client import LLMClient
from backend.app.tools.diet_rules import expand_exclusions

from backend.app.observability.logging import get_logger

logger = get_logger("agents.planner")

def _load_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are the Planner Agent. Plan daily meals adhering to calorie targets and dietary constraints."

class PlannerAgent(BaseAgent[MealPlan]):
    """
    Planner Agent: Creates a complete meal plan of any length.
    Slices calories across meals, locks fixed user meals, looks up real recipes
    via TheMealDB with authentic AI fallbacks, and reformulates plans upon
    Supervisor feedback.

    Every plan - one day or fourteen - goes through the same agent loop. The
    planner decides for itself whether to call `search_recipes`, how many
    lookups to make, and whether what came back was worth using.
    """
    def __init__(
        self,
        client: Optional[LLMClient] = None,
        max_steps: Optional[int] = None,
        timeout_seconds: Optional[int] = None
    ):
        super().__init__(
            name="PlannerAgent",
            system_prompt=_load_prompt(),
            output_schema=MealPlan,
            tools=PLANNER_TOOLS,
            client=client,
            max_steps=max_steps or 4,
            timeout_seconds=timeout_seconds or 600
        )

    @staticmethod
    def _cuisine_rule(cuisine: Optional[str]) -> str:
        """The instruction that keeps every meal inside the cuisine the user asked for."""
        if not cuisine or not str(cuisine).strip():
            return ""
        c = str(cuisine).strip()
        return (
            f"CUISINE RULE: the user asked for {c} food. Every breakfast, lunch, dinner and snack must be a genuine "
            f"{c} dish made the {c} way, with ingredients typical of {c} cooking. Do NOT include dishes from other "
            f"cuisines. Create variety by using different {c} dishes. Meals the user locked stay exactly as they wrote them."
        )

    @staticmethod
    def _preference_rules(style_notes: Optional[List[str]], main_ingredients: Optional[List[str]]) -> str:
        """What the user wants the food to be like, and the foods to build meals around."""
        parts = []
        if style_notes:
            parts.append(
                f"STYLE: the user wants the food to be {', '.join(style_notes)}. Lean every meal that way where it makes sense."
            )
        if main_ingredients:
            parts.append(
                f"MAIN INGREDIENTS: the user has or wants {', '.join(main_ingredients)}. Build meals around them and use each one "
                f"in at least one meal across the plan (more often if it suits). You may add other ingredients to make complete meals."
            )
        return " ".join(parts)

    async def generate_plan(
        self,
        days: int,
        calorie_target: float,
        user_profile: Dict[str, Any],
        fixed_meals: Optional[Dict[str, Any]] = None,
        feedback: Optional[List[str]] = None,
        version: int = 1,
        task_id: Optional[str] = None,
        current_plan: Optional[MealPlan] = None,
        plan_name: Optional[str] = None,
        exclusions: Optional[List[str]] = None,
        cuisine: Optional[str] = None,
        include_snack: bool = False,
        style_notes: Optional[List[str]] = None,
        main_ingredients: Optional[List[str]] = None
    ) -> MealPlan:
        plan_diet = getattr(current_plan, "diet_type", None) or user_profile.get("diet_type") or "veg"
        plan_name = plan_name or getattr(current_plan, "name", None) or f"{days}-Day {plan_diet.capitalize()} Plan"

        meals_spec = (
            "4 meals (breakfast, lunch, dinner, snack) per day (breakfast ~25%, lunch ~35%, dinner ~25%, snack ~15%)"
            if include_snack
            else "3 meals (breakfast, lunch, dinner) per day"
        )
        task_instruction = (
            f"Generate a complete {days}-day meal plan targeting {calorie_target:.0f} calories per day (±5%).\n"
            f"Plan version: {version}.\n"
            f"Plan diet type: {plan_diet}.\n"
            f"Provide exactly {days} days, with {meals_spec}, ingredients in grams, and accurate calories.\n"
        )
        if cuisine:
            task_instruction += self._cuisine_rule(cuisine) + "\n"
        prefs = self._preference_rules(style_notes, main_ingredients)
        if prefs:
            task_instruction += prefs + "\n"
        if feedback:
            task_instruction += (
                f"\nCRITICAL REVISION FEEDBACK FROM PREVIOUS ROUND (Must be addressed):\n"
                f"- " + "\n- ".join(feedback) + "\n"
            )

        context = {
            "task_id": task_id,
            "days": days,
            "calorie_target": calorie_target,
            "include_snack": include_snack,
            "user_profile": {
                "name": user_profile.get("name"),
                "diet_type": plan_diet,
                "dislikes": user_profile.get("dislikes", []),
                "allergies": user_profile.get("allergies", []),
                "exclusions": exclusions or []
            },
            "fixed_meals": fixed_meals or {},
            "version": version
        }
        if current_plan and current_plan.days:
            context["current_plan"] = current_plan.model_dump()

        diet_rules_prompt = ""
        # Combine allergies and exclusions for all diet types. Category words
        # ("dairy", "seafood") are expanded into the ingredients they stand
        # for - the planner has no reliable way to interpret a category on its
        # own, and "no dairy" used to leave milk and butter in the plan.
        allergies_list = [str(a).lower() for a in user_profile.get("allergies", [])]
        exclusions_list = expand_exclusions(exclusions or [])
        all_exclusions = list(set(allergies_list + exclusions_list))

        if plan_diet in ("veg", "vegetarian"):
            diet_rules_prompt = (
                "- STRICT DIETARY RULE: 100% VEGETARIAN.\n"
                "  * Absolutely NO meat, poultry (NO chicken, turkey), fish (NO salmon, tuna, seafood), beef, pork, or animal gelatin.\n"
                "  * Absolutely NO eggs unless specifically eggetarian.\n"
                "  * Every single meal (breakfast, lunch, dinner, snack) MUST contain ONLY vegetarian plant-based foods, lentils/dal, chickpeas/chana, beans/rajma, paneer, tofu, vegetables, grains (quinoa, brown rice, oats, whole wheat roti), nuts, and seeds.\n"
            )
            if all_exclusions:
                diet_rules_prompt += f"\n  * ADDITIONAL EXCLUSIONS: Zero {', '.join(all_exclusions)}. Do NOT include any recipe or ingredient containing {', '.join(all_exclusions)}."
        elif plan_diet == "vegan":
            diet_rules_prompt = (
                "- STRICT DIETARY RULE: 100% VEGAN.\n"
                "  * Absolutely NO animal products: NO meat, poultry, fish, seafood, eggs, dairy (milk, cheese, paneer, butter, ghee, curd, yogurt), or honey. 100% plant-based.\n"
            )
            if all_exclusions:
                diet_rules_prompt += f"\n  * ADDITIONAL EXCLUSIONS: Zero {', '.join(all_exclusions)}. Do NOT include any recipe or ingredient containing {', '.join(all_exclusions)}."
        else:
            # Non-vegetarian: build allowed proteins based on exclusions
            allowed_proteins = ["eggs"]
            if "chicken" not in all_exclusions and "poultry" not in all_exclusions:
                allowed_proteins.append("chicken")
            if "fish" not in all_exclusions and "seafood" not in all_exclusions:
                allowed_proteins.append("fish")
            if "turkey" not in all_exclusions and "poultry" not in all_exclusions:
                allowed_proteins.append("turkey")
            if "beef" not in all_exclusions and "meat" not in all_exclusions:
                allowed_proteins.append("lean beef")
            if "pork" not in all_exclusions and "meat" not in all_exclusions:
                allowed_proteins.append("pork")

            diet_rules_prompt = (
                f"- DIETARY RULE: Non-Vegetarian. Primary allowed protein sources: {', '.join(allowed_proteins)}."
            )
            if all_exclusions:
                diet_rules_prompt += f"\n  * ADDITIONAL EXCLUSIONS: Zero {', '.join(all_exclusions)}. Do NOT include any recipe or ingredient containing {', '.join(all_exclusions)}."

        # Everything the final structured write must see. The agent loop shows
        # this in both the decide step and the finalize step, so the strict
        # diet rules travel with the task rather than being bolted onto a
        # one-off prompt.
        task_instruction += (
            f"Strict dietary rules for this plan:\n{diet_rules_prompt}\n"
            f"Allergies to exclude: {user_profile.get('allergies', [])}.\n"
            f"Ingredients to exclude: {exclusions or []}.\n"
            f"Dislikes to avoid: {user_profile.get('dislikes', [])}."
        )
        if fixed_meals:
            task_instruction += "\nFixed meal constraints:\n- " + "\n- ".join(
                f"{k}: {v}" for k, v in fixed_meals.items()
            )

        # Single agentic path. The planner decides for itself whether to
        # consult TheMealDB, how many lookups to make, and whether a result was
        # worth keeping. Previously multi-day plans bypassed the tool loop and
        # had recipes pre-fetched for them, while 1-day plans went through the
        # loop - two different behaviours for the same job.
        meal_plan = None
        try:
            meal_plan = await self.run(task_instruction, context)
        except Exception as e:
            logger.warning(f"[PlannerAgent] Agent loop error: {e}")

        # Fallback safeguard: If loop returned empty or incomplete days, generate directly
        if not meal_plan or not meal_plan.days or len(meal_plan.days) < days:
            logger.info(f"[PlannerAgent] Plan had {len(meal_plan.days) if meal_plan else 0}/{days} days. Running direct structured generation fallback.")
            meals_desc_fallback = (
                f"- Each day must have 4 balanced meals: breakfast (~25% cal), lunch (~35% cal), dinner (~25% cal), and snack (~15% cal) with realistic ingredients (amount_grams).\n"
                if include_snack
                else f"- Each day must have breakfast (~30% cal), lunch (~40% cal), and dinner (~30% cal) with realistic ingredients (amount_grams).\n"
            )
            fallback_prompt = (
                f"You are the Meal Planner Agent. Generate a complete {days}-day clinical meal plan for {user_profile.get('name', 'user')}.\n"
                f"- Target calories: {calorie_target:.0f} kcal per day (±5%)\n"
                f"- Diet type: {plan_diet}\n"
                f"{diet_rules_prompt}"
                f"- Exclude allergies: {user_profile.get('allergies', [])}\n"
                f"- Exclude ingredients: {exclusions or []}\n"
                f"- Avoid dislikes: {user_profile.get('dislikes', [])}\n"
                + (f"- {self._cuisine_rule(cuisine)}\n" if cuisine else "")
                + (f"- {prefs}\n" if prefs else "")
                + f"- Provide EXACTLY {days} DayPlan items (day_number 1 to {days}).\n"
                + meals_desc_fallback
                + f"- For EACH meal, provide detailed step-by-step 'instructions' (3 to 4 numbered steps detailing prep, cooking, seasoning, and serving warm). NEVER output generic 1-liners.\n"
            )
            if feedback:
                fallback_prompt += f"\n- Address this clinical review feedback:\n" + "\n".join(f"  * {fb}" for fb in feedback)
            if fixed_meals:
                fallback_prompt += f"\n- Fixed meal constraints:\n" + "\n".join(f"  * {k}: {v}" for k, v in fixed_meals.items())
            try:
                direct_plan = await self.llm.generate_structured(
                    prompt=fallback_prompt,
                    response_schema=MealPlan
                )
                if direct_plan and direct_plan.days:
                    meal_plan = direct_plan
            except Exception as direct_err:
                logger.error(f"[PlannerAgent] Direct generation error: {direct_err}")

        if meal_plan is None:
            # The agent loop and the fallback both failed. This used to fall
            # through to `meal_plan.target_calories` and raise AttributeError
            # on None, which surfaced as an opaque 500 with no explanation.
            logger.error("[PlannerAgent] Neither the agent loop nor the fallback produced a plan.")
            raise RuntimeError("Planner failed to produce a meal plan.")

        meal_plan.target_calories = calorie_target
        meal_plan.version = version
        meal_plan.diet_type = plan_diet
        meal_plan.name = plan_name
        # The model may fill in a cuisine it chose itself; only the user's request counts.
        meal_plan.cuisine = cuisine or None

        # Strict Diet Compliance Verification and Sanitization Pass.
        # exclusions must be passed here: without it the check never ran on
        # newly generated plans (only on edits), so "no dairy" was a prompt
        # suggestion the model was free to ignore - and it did.
        self._sanitize_diet_compliance(
            meal_plan, plan_diet, calorie_target, user_profile, exclusions=exclusions)

        from backend.app.tools.nutrition.calculator import normalize_single_person_plan
        await normalize_single_person_plan(meal_plan, calorie_target)

        return meal_plan

    # ------------------------------------------------------------------
    # Targeted repair (used by the Supervisor's retry rounds)
    #
    # generate_plan() rewrites the whole plan. After a review that flagged only
    # some meals or days that is wasteful and risky: every retry could change
    # meals that had already passed. repair_plan() starts from the previous
    # draft and only asks the AI for the flagged parts; every other meal is
    # copied across untouched, so nothing that passed can regress.
    # ------------------------------------------------------------------
    @staticmethod
    def _slot_shares(four_meals: bool) -> Dict[str, float]:
        # Same split the portion normaliser uses, so a repaired meal lands where
        # the audit expects it.
        if four_meals:
            return {"breakfast": 0.25, "lunch": 0.35, "dinner": 0.25, "snack": 0.15}
        return {"breakfast": 0.30, "lunch": 0.40, "dinner": 0.30, "snack": 0.10}

    def _constraint_lines(self, plan_diet: str, user_profile: Dict[str, Any], exclusions: Optional[List[str]]) -> str:
        lines = [f"- Diet type: {plan_diet}."]
        if plan_diet in ("veg", "vegetarian"):
            lines.append("- 100% vegetarian: no meat, poultry, fish, seafood, gelatin or eggs.")
        elif plan_diet == "vegan":
            lines.append("- 100% vegan: no animal products at all (no meat, fish, eggs, dairy, honey).")
        allergies = [str(a) for a in user_profile.get("allergies", [])]
        if allergies:
            lines.append(f"- ALLERGIES (absolute, zero tolerance, including hidden sources): {', '.join(allergies)}.")
        excluded = expand_exclusions(exclusions or [])
        if excluded:
            lines.append(f"- Also exclude completely: {', '.join(excluded)}.")
        dislikes = [str(d) for d in user_profile.get("dislikes", [])]
        if dislikes:
            lines.append(f"- Avoid these disliked foods: {', '.join(dislikes)}.")
        return "\n".join(lines)

    async def _meal_problems(
        self, meal: Meal, plan_diet: str, user_profile: Dict[str, Any], exclusions: Optional[List[str]]
    ) -> List[str]:
        """Deterministic check of one freshly written meal (no AI)."""
        from backend.app.tools.allergens import check_allergens
        from backend.app.tools.diet_rules import check_diet_compliance

        names = [i.name for i in meal.ingredients] + [meal.recipe_name]
        banned = [str(a) for a in user_profile.get("allergies", [])] + expand_exclusions(exclusions or [])
        problems: List[str] = []
        for v in await check_allergens(banned, names):
            problems.append(f"'{v['ingredient']}' is not allowed ({v['allergen']})")
        for dv in check_diet_compliance(plan_diet, names):
            problems.append(f"'{dv['ingredient']}' breaks the {plan_diet} diet")
        return problems

    async def _scale_meal_to_calories(self, meal: Meal, target_cal: float) -> None:
        """Scale one meal's portions so its tool-computed calories hit the target."""
        from backend.app.tools.nutrition import calculator
        from backend.app.tools.diet_rules import is_liquid_ingredient

        nut = await calculator.calculate_meal_nutrition(meal)
        current = nut["calories"]
        if current > 0 and target_cal > 0 and abs(current - target_cal) > 0.05 * target_cal:
            scale = max(0.2, min(3.5, target_cal / current))
            for ing in meal.ingredients:
                floor = 5.0 if is_liquid_ingredient(ing.name) else 10.0
                ing.amount_grams = max(floor, min(280.0, round(ing.amount_grams * scale, 1)))
            nut = await calculator.calculate_meal_nutrition(meal)
        meal.calories = nut["calories"]
        meal.protein_g = nut["protein_g"]
        meal.carbs_g = nut["carbs_g"]
        meal.fat_g = nut["fat_g"]

    async def _replacement_meal(
        self,
        *,
        day_number: int,
        meal_type: str,
        target_cal: float,
        reasons: List[str],
        plan_diet: str,
        user_profile: Dict[str, Any],
        exclusions: Optional[List[str]],
        avoid_names: List[str],
        cuisine: Optional[str] = None,
        style_notes: Optional[List[str]] = None,
        main_ingredients: Optional[List[str]] = None,
    ) -> Meal:
        """Ask the AI for ONE replacement meal; retry once if it breaks a rule."""
        constraints = self._constraint_lines(plan_diet, user_profile, exclusions)
        cuisine_line = f"- {self._cuisine_rule(cuisine)}\n" if cuisine else ""
        _prefs = self._preference_rules(style_notes, main_ingredients)
        if _prefs:
            cuisine_line += f"- {_prefs}\n"
        rejected_note = ""
        meal: Optional[Meal] = None
        for _attempt in range(2):
            prompt = (
                f"You are repairing ONE meal of an existing {plan_diet} meal plan. "
                f"Write a replacement {meal_type} for Day {day_number}.\n"
                f"Why the previous {meal_type} was rejected: {' | '.join(reasons)}\n"
                f"Hard rules:\n{constraints}\n"
                f"{cuisine_line}"
                f"- Target about {int(target_cal)} kcal (±5%) for this single-person meal.\n"
                f"- Ingredients in grams (amount_grams), realistic single-person portions.\n"
                f"- 3 to 4 numbered preparation steps in `instructions`.\n"
                f"- Do NOT reuse any of these dishes, they are already in the plan: {', '.join(avoid_names) or 'none'}.\n"
                f"- Set meal_type to \"{meal_type}\".{rejected_note}"
            )
            try:
                candidate: Meal = await self.llm.generate_structured(prompt=prompt, response_schema=Meal)
            except Exception as e:
                logger.warning(f"[PlannerAgent] Replacement meal call failed (day {day_number} {meal_type}): {e}")
                continue
            candidate.meal_type = meal_type
            candidate.diet_type = plan_diet
            candidate.locked = False
            candidate.source = "ai_generated"
            await self._scale_meal_to_calories(candidate, target_cal)
            meal = candidate
            problems = await self._meal_problems(candidate, plan_diet, user_profile, exclusions)
            if not problems:
                return candidate
            rejected_note = (
                f"\n- Your previous attempt was rejected: {'; '.join(problems)}. "
                f"Choose completely different ingredients."
            )
        if meal is None:
            raise RuntimeError(f"Could not write a replacement {meal_type} for day {day_number}.")
        # Still breaking a rule after two tries: hand it on. The diet/allergy
        # clean-up below and the next review round deal with it.
        return meal

    async def repair_plan(
        self,
        current_plan: MealPlan,
        targets: RepairTargets,
        calorie_target: float,
        user_profile: Dict[str, Any],
        version: int = 1,
        task_id: Optional[str] = None,
        exclusions: Optional[List[str]] = None,
        cuisine: Optional[str] = None,
        style_notes: Optional[List[str]] = None,
        main_ingredients: Optional[List[str]] = None,
    ) -> MealPlan:
        """Redo only what the Supervisor flagged (a RepairTargets instruction).

        The Planner receives plain "redo these slots/days, here is why" data from
        the Supervisor and never sees the Nutrition or Allergy reports.

        - A flagged meal is replaced by one new meal; its calories stay what the
          old meal's were, so the day's total is unchanged.
        - A flagged day has every unlocked meal replaced and the day is then
          portion-balanced to the calorie target.
        - Every other meal is the previous draft's meal, copied across.
        """
        from backend.app.tools.nutrition import calculator

        plan_diet = getattr(current_plan, "diet_type", None) or user_profile.get("diet_type") or "veg"
        repaired = current_plan.model_copy(deep=True)
        day_by_number = {d.day_number: d for d in repaired.days}

        # (day_number, index in day.meals, meal_type, calorie target, reasons)
        jobs: List[tuple] = []
        whole_days: List[int] = []

        for item in targets.whole_days():
            day_no, reasons = item.day_number, item.reasons
            day = day_by_number[day_no]
            shares = self._slot_shares(len(day.meals) >= 4)
            unlocked = [(i, m) for i, m in enumerate(day.meals) if not m.locked]
            share_sum = sum(shares.get(m.meal_type.lower(), 0.3) for _, m in unlocked) or 1.0
            budget = calorie_target - sum(m.calories for m in day.meals if m.locked)
            if budget <= 0:
                budget = calorie_target * share_sum
            for i, m in unlocked:
                cal = budget * shares.get(m.meal_type.lower(), 0.3) / share_sum
                jobs.append((day_no, i, m.meal_type.lower(), cal, reasons))
            whole_days.append(day_no)

        for item in targets.single_meals():
            day_no, meal_type, reasons = item.day_number, item.meal_type.lower(), item.reasons
            if day_no in whole_days:
                continue  # that whole day is already being redone
            day = day_by_number[day_no]
            idx, old = next((i, m) for i, m in enumerate(day.meals) if m.meal_type.lower() == meal_type)
            shares = self._slot_shares(len(day.meals) >= 4)
            cal = old.calories if old.calories and old.calories > 0 else calorie_target * shares.get(meal_type, 0.3)
            jobs.append((day_no, idx, meal_type, cal, reasons))

        replacing = {(d, i) for d, i, *_ in jobs}
        kept_names = sorted({
            m.recipe_name
            for d in repaired.days
            for i, m in enumerate(d.meals)
            if (d.day_number, i) not in replacing and m.recipe_name
        })

        new_meals = await asyncio.gather(*[
            self._replacement_meal(
                day_number=day_no, meal_type=meal_type, target_cal=cal, reasons=reasons,
                plan_diet=plan_diet, user_profile=user_profile, exclusions=exclusions,
                avoid_names=kept_names, cuisine=cuisine,
                style_notes=style_notes, main_ingredients=main_ingredients,
            )
            for day_no, _i, meal_type, cal, reasons in jobs
        ])
        for (day_no, idx, *_rest), new_meal in zip(jobs, new_meals):
            day_by_number[day_no].meals[idx] = new_meal

        # A flagged day is re-balanced as a unit (only that day, on its own).
        for day_no in whole_days:
            position = next(i for i, d in enumerate(repaired.days) if d.day_number == day_no)
            single = MealPlan(days=[repaired.days[position]], target_calories=calorie_target, diet_type=plan_diet)
            await calculator.normalize_single_person_plan(single, calorie_target)
            repaired.days[position] = single.days[0]

        repaired.target_calories = calorie_target
        repaired.version = version
        repaired.diet_type = plan_diet
        repaired.cuisine = cuisine if cuisine else current_plan.cuisine

        # Same deterministic diet/allergy clean-up generate_plan applies. It only
        # touches a meal that actually breaks a rule.
        self._sanitize_diet_compliance(repaired, plan_diet, calorie_target, user_profile, exclusions=exclusions)

        for day_no in {d for d, *_ in jobs}:
            day = next(d for d in repaired.days if d.day_number == day_no)
            day.total_calories = round(sum(m.calories for m in day.meals), 1)
            day.total_protein_g = round(sum(m.protein_g for m in day.meals), 1)
            day.total_carbs_g = round(sum(m.carbs_g for m in day.meals), 1)
            day.total_fat_g = round(sum(m.fat_g for m in day.meals), 1)
        if repaired.days:
            repaired.average_daily_calories = round(
                sum(d.total_calories for d in repaired.days) / len(repaired.days), 1
            )
        return repaired

    async def modify_meal(
        self,
        current_plan: MealPlan,
        edit_details: Any,
        user_profile: Dict[str, Any],
        task_id: Optional[str] = None,
        exclusions: Optional[List[str]] = None,
        cuisine: Optional[str] = None
    ) -> MealPlan:
        """
        Modifies a specific meal slot (or specific day, or scrubbed exclusions) in an existing meal plan
        while keeping all other days and meals strictly unchanged.
        """
        updated_plan = current_plan.model_copy(deep=True)

        instructions = getattr(edit_details, "instructions", "") or "modify meal"
        target_meal_cal = getattr(edit_details, "target_meal_calories", None)
        plan_diet = getattr(current_plan, "diet_type", None) or user_profile.get("diet_type") or "veg"
        edit_scope = getattr(edit_details, "edit_scope", None)
        edit_exclusions = [str(e).strip().lower() for e in (exclusions or []) if str(e or "").strip()]
        # The cuisine of the plan being edited (or the one named for this edit): new
        # meals must stay inside it.
        cuisine_line = f"- {self._cuisine_rule(cuisine)}\n" if cuisine else ""

        # What the edit targets comes from the model's structured verdict
        # (edit_details: day, meal_type, operation, edit_scope) and the
        # supervisor's requested_exclusions. The instruction text is NOT searched
        # for words like "remove", "all meals" or "pork" to work this out.
        target_meal_type = getattr(edit_details, "meal_type", None)

        # A plan-wide scrub of excluded ingredients: the model listed what must
        # go (requested_exclusions) and the edit covers the whole plan.
        is_exclusion_scrub = (not target_meal_type) and bool(edit_exclusions) and (
            edit_scope == "plan" or getattr(edit_details, "day", None) is None
        )

        if is_exclusion_scrub:
            allergies_list = [str(a).lower() for a in user_profile.get("allergies", [])]
            # Structured exclusions from the supervisor always apply (they name
            # exactly what must go, e.g. "ham", "beef").
            for exc in edit_exclusions:
                if exc not in allergies_list:
                    allergies_list.append(exc)
            user_profile["allergies"] = allergies_list

            from backend.app.tools.allergens import ALLERGEN_CANONICAL_TARGETS

            for d in updated_plan.days:
                for idx, m in enumerate(d.meals):
                    meal_text = (m.recipe_name + " " + " ".join(ing.name for ing in m.ingredients)).lower()
                    violates = False
                    for exc in allergies_list:
                        targets = ALLERGEN_CANONICAL_TARGETS.get(exc, [exc])
                        if any(t in meal_text for t in targets):
                            violates = True
                            break
                    if violates:
                        target_cal = m.calories or (updated_plan.target_calories * (0.3 if m.meal_type in ("breakfast", "dinner") else 0.4))
                        allowed_proteins = ["eggs", "chicken", "fish"]
                        for a in allergies_list:
                            if a in allowed_proteins:
                                allowed_proteins.remove(a)
                        p_str = ", ".join(allowed_proteins) or "plant-based proteins"

                        meal_prompt = (
                            f"Generate a replacement {m.meal_type} meal for Day {d.day_number} in a {plan_diet} meal plan.\n"
                            f"- Target calories: ~{int(target_cal)} kcal (±5%)\n"
                            f"- ABSOLUTE HARD CONSTRAINT: ZERO {', '.join(allergies_list)}. Do NOT include any {', '.join(allergies_list)}.\n"
                            f"- Allowed protein sources: {p_str}.\n"
                            f"{cuisine_line}"
                            f"- Provide realistic single-person ingredients with exact amount_grams.\n"
                            f"- Provide 3 to 4 numbered step-by-step instructions.\n"
                        )
                        try:
                            rep: Meal = await self.llm.generate_structured(
                                prompt=meal_prompt,
                                response_schema=Meal
                            )
                            rep.meal_type = m.meal_type
                            rep.diet_type = plan_diet
                            rep.source = "user_specified"
                            d.meals[idx] = rep
                        except Exception as e:
                            logger.error(f"[PlannerAgent] Error replacing violating meal: {e}")

            for d in updated_plan.days:
                d.total_calories = round(sum(m.calories for m in d.meals), 1)
                d.total_protein_g = round(sum(m.protein_g for m in d.meals), 1)
                d.total_carbs_g = round(sum(m.carbs_g for m in d.meals), 1)
                d.total_fat_g = round(sum(m.fat_g for m in d.meals), 1)
            updated_plan.average_daily_calories = round(
                sum(d.total_calories for d in updated_plan.days) / len(updated_plan.days),
                1
            )
            self._sanitize_diet_compliance(updated_plan, plan_diet, current_plan.target_calories, user_profile, exclusions=edit_exclusions)
            return updated_plan

        # Redo a whole day only when the model said so (edit_scope == "day") and
        # named no single meal. Phrases in the text are not searched for.
        is_entire_day = (not target_meal_type) and edit_scope == "day"

        target_day = getattr(edit_details, "day", None)
        if target_day is None:
            if target_meal_type == "snack":
                snack_days = [d.day_number for d in updated_plan.days if any(m.meal_type.lower() == "snack" for m in d.meals)]
                if len(snack_days) == 1:
                    target_day = snack_days[0]
                elif len(snack_days) > 1:
                    target_day = snack_days[-1]
                else:
                    target_day = 1
            else:
                target_day = 1

        target_day_plan = next((d for d in updated_plan.days if d.day_number == target_day), None)
        if not target_day_plan and updated_plan.days:
            target_day_plan = updated_plan.days[0]
            target_day = target_day_plan.day_number

        if not target_day_plan:
            return current_plan

        # The operation is the model's structured verdict (add / remove / replace);
        # when it left it blank the edit is a replace. The instruction text is
        # never searched for "add" or "remove": that is how "added" once turned a
        # replace into a plan-wide add, and a destructive path must never hang on
        # a word match.
        operation = getattr(edit_details, "operation", None) or "replace"

        is_plan_wide_snack = (
            target_meal_type == "snack"
            and (
                edit_scope == "plan"
                or (getattr(edit_details, "day", None) is None and operation in ("add", "remove"))
            )
        )

        if is_plan_wide_snack:
            if operation == "remove":
                for d in updated_plan.days:
                    d.meals = [m for m in d.meals if m.meal_type.lower() != "snack"]
                    d.total_calories = round(sum(m.calories for m in d.meals), 1)
                    d.total_protein_g = round(sum(m.protein_g for m in d.meals), 1)
                    d.total_carbs_g = round(sum(m.carbs_g for m in d.meals), 1)
                    d.total_fat_g = round(sum(m.fat_g for m in d.meals), 1)
            else:
                # A "light" snack arrives as target_meal_calories from the model.
                snack_cal = target_meal_cal or (0.10 * current_plan.target_calories)

                for d in updated_plan.days:
                    existing_snack = next((m for m in d.meals if m.meal_type.lower() == "snack"), None)
                    if existing_snack and operation == "add":
                        continue
                    snack_prompt = (
                        f"You are the Meal Planner Agent. Generate a healthy snack meal for Day {d.day_number} of a {plan_diet} meal plan.\n"
                        f"- Meal type: snack\n"
                        f"- Target calories: ~{int(snack_cal)} kcal (±5%)\n"
                        f"- Diet type: {plan_diet} (FOR VEGETARIAN: ZERO MEAT/CHICKEN/FISH/SEAFOOD, FOR VEGAN: ZERO ANIMAL PRODUCTS)\n"
                        f"- Exclude allergies: {user_profile.get('allergies', [])}\n"
                        f"- Also strictly exclude these user-rejected ingredients: {edit_exclusions if edit_exclusions else 'none'}.\n"
                        f"{cuisine_line}"
                        f"- Avoid dislikes: {user_profile.get('dislikes', [])}\n"
                        f"- Provide realistic single-person ingredients with exact amount_grams.\n"
                        f"- Provide detailed step-by-step 'instructions' (3 to 4 numbered steps detailing prep, cooking, seasoning, and serving).\n"
                    )
                    try:
                        new_snack: Meal = await self.llm.generate_structured(
                            prompt=snack_prompt,
                            response_schema=Meal
                        )
                        new_snack.meal_type = "snack"
                        new_snack.diet_type = plan_diet
                        new_snack.source = "user_specified"
                        if existing_snack:
                            idx = d.meals.index(existing_snack)
                            d.meals[idx] = new_snack
                        else:
                            d.meals.append(new_snack)
                        d.total_calories = round(sum(m.calories for m in d.meals), 1)
                        d.total_protein_g = round(sum(m.protein_g for m in d.meals), 1)
                        d.total_carbs_g = round(sum(m.carbs_g for m in d.meals), 1)
                        d.total_fat_g = round(sum(m.fat_g for m in d.meals), 1)
                    except Exception as e:
                        logger.error(f"[PlannerAgent] Error generating snack for Day {d.day_number}: {e}")
        elif is_entire_day:
            # Regenerate ONLY this day's meals
            day_prompt = (
                f"You are the Meal Planner Agent. Generate a replacement DayPlan for Day {target_day} of a {plan_diet} meal plan.\n"
                f"- Daily calorie target: ~{int(current_plan.target_calories)} kcal (±5%)\n"
                f"- User request: '{instructions}'\n"
                f"- Diet type: {plan_diet} (FOR VEGETARIAN: ZERO MEAT/CHICKEN/FISH/SEAFOOD, FOR VEGAN: ZERO ANIMAL PRODUCTS)\n"
                f"- Exclude allergies: {user_profile.get('allergies', [])}\n"
                f"- Also strictly exclude these user-rejected ingredients: {edit_exclusions if edit_exclusions else 'none'}. Do NOT include any of them.\n"
                f"- Avoid dislikes: {user_profile.get('dislikes', [])}\n"
                f"{cuisine_line}"
                f"- Meals to include: {len(target_day_plan.meals)} balanced meals ({', '.join(m.meal_type for m in target_day_plan.meals)}).\n"
                f"- For EACH meal, provide realistic ingredients (amount_grams) and detailed 3 to 4 numbered step-by-step 'instructions' (prep, cook, season, serve warm).\n"
            )
            try:
                new_day: DayPlan = await self.llm.generate_structured(
                    prompt=day_prompt,
                    response_schema=DayPlan
                )
                if new_day and new_day.meals:
                    new_day.day_number = target_day
                    new_day.total_calories = round(sum(m.calories for m in new_day.meals), 1)
                    new_day.total_protein_g = round(sum(m.protein_g for m in new_day.meals), 1)
                    new_day.total_carbs_g = round(sum(m.carbs_g for m in new_day.meals), 1)
                    new_day.total_fat_g = round(sum(m.fat_g for m in new_day.meals), 1)
                    for idx, d in enumerate(updated_plan.days):
                        if d.day_number == target_day:
                            updated_plan.days[idx] = new_day
                            break
            except Exception as e:
                logger.error(f"[PlannerAgent] Error generating replacement day {target_day}: {e}")
        else:
            if not target_meal_type:
                target_meal_type = "lunch"

            meal_idx = next(
                (i for i, m in enumerate(target_day_plan.meals) if m.meal_type.lower() == target_meal_type.lower()),
                None
            )
            old_meal = target_day_plan.meals[meal_idx] if meal_idx is not None else None
            old_dish = old_meal.recipe_name if old_meal else "existing dish"

            # Handle remove operation (only when the model's verdict is "remove").
            if operation == "remove":
                if meal_idx is not None:
                    removed_meal = target_day_plan.meals.pop(meal_idx)
                    logger.info(f"[PlannerAgent] Removed meal '{removed_meal.recipe_name}' ({target_meal_type}) from Day {target_day}")
                else:
                    logger.warning(f"[PlannerAgent] Remove requested but meal_idx not found for {target_meal_type} on Day {target_day}")
                
                target_day_plan.total_calories = round(sum(m.calories for m in target_day_plan.meals), 1)
                target_day_plan.total_protein_g = round(sum(m.protein_g for m in target_day_plan.meals), 1)
                target_day_plan.total_carbs_g = round(sum(m.carbs_g for m in target_day_plan.meals), 1)
                target_day_plan.total_fat_g = round(sum(m.fat_g for m in target_day_plan.meals), 1)
            else:
                if target_meal_type == "snack":
                    slot_cal = target_meal_cal or (
                        old_meal.calories if old_meal and old_meal.calories > 50
                        else (0.10 * current_plan.target_calories)
                    )
                else:
                    slot_cal = target_meal_cal or (
                        old_meal.calories if old_meal and old_meal.calories > 100
                        else (current_plan.target_calories * (0.3 if target_meal_type in ("breakfast", "dinner") else 0.4))
                    )

                meal_prompt = (
                    f"You are the Meal Planner Agent. Generate a single replacement {target_meal_type} meal for Day {target_day}.\n"
                    f"- Meal type: {target_meal_type}\n"
                    f"- Replaces previous dish: '{old_dish}'\n"
                    f"{cuisine_line}"
                    f"- User modification request: '{instructions}'\n"
                    f"- Target calories: ~{int(slot_cal)} kcal (±5%)\n"
                    f"- Diet type: {plan_diet} (FOR VEGETARIAN: ZERO MEAT/CHICKEN/FISH/SEAFOOD, FOR VEGAN: ZERO ANIMAL PRODUCTS)\n"
                    f"- Exclude allergies: {user_profile.get('allergies', [])}\n"
                    f"- Avoid dislikes: {user_profile.get('dislikes', [])}\n"
                    f"- Provide realistic single-person ingredients with exact amount_grams.\n"
                    f"- Provide detailed step-by-step 'instructions' (3 to 4 numbered steps detailing prep, cooking, seasoning, and serving warm).\n"
                )
                try:
                    replacement_meal: Meal = await self.llm.generate_structured(
                        prompt=meal_prompt,
                        response_schema=Meal
                    )
                    replacement_meal.meal_type = target_meal_type
                    replacement_meal.diet_type = plan_diet
                    replacement_meal.source = "user_specified"

                    if meal_idx is not None:
                        target_day_plan.meals[meal_idx] = replacement_meal
                    else:
                        # The targeted slot does not exist on this day. Append
                        # ONLY for an explicit add (the model's verdict).
                        # Anything else leaves the day untouched: this branch
                        # previously overwrote the day's LAST meal, so "replace
                        # the snack on day 5" (no snack there) silently ate dinner.
                        if operation == "add":
                            target_day_plan.meals.append(replacement_meal)
                        else:
                            logger.warning(
                                f"[PlannerAgent] {target_meal_type} not present on Day {target_day}; "
                                f"leaving the day unchanged instead of guessing."
                            )

                    target_day_plan.total_calories = round(sum(m.calories for m in target_day_plan.meals), 1)
                    target_day_plan.total_protein_g = round(sum(m.protein_g for m in target_day_plan.meals), 1)
                    target_day_plan.total_carbs_g = round(sum(m.carbs_g for m in target_day_plan.meals), 1)
                    target_day_plan.total_fat_g = round(sum(m.fat_g for m in target_day_plan.meals), 1)
                except Exception as e:
                    logger.error(f"[PlannerAgent] Error generating replacement meal: {e}")

        # Recalculate average daily calories across all days
        if updated_plan.days:
            updated_plan.average_daily_calories = round(
                sum(d.total_calories for d in updated_plan.days) / len(updated_plan.days),
                1
            )
        updated_plan.target_calories = current_plan.target_calories
        updated_plan.version = current_plan.version
        updated_plan.diet_type = plan_diet
        updated_plan.name = getattr(current_plan, "name", None) or f"{len(updated_plan.days)}-Day {plan_diet.capitalize()} Plan"

        # Sanitize diet compliance on the plan
        self._sanitize_diet_compliance(updated_plan, plan_diet, current_plan.target_calories, user_profile, exclusions=edit_exclusions)

        return updated_plan


    def _sanitize_diet_compliance(self, meal_plan: MealPlan, plan_diet: str, calorie_target: float, user_profile: Optional[Dict[str, Any]] = None, exclusions: Optional[List[str]] = None):
        """Ensures zero non-compliant dishes exist in vegetarian, vegan, or allergy/exclusion restricted plans."""
        from backend.app.tools.diet_rules import (
    check_diet_compliance, expand_exclusions, DAIRY_TERMS, FALSE_POSITIVE_EXCLUSIONS,
)
        from backend.app.schemas.domain import Ingredient
        from backend.app.tools.allergens import ALLERGEN_CANONICAL_TARGETS

        dt = (plan_diet or "").strip().lower()
        is_veg = dt in ("veg", "vegetarian", "vegan")
        is_vegan = dt == "vegan"
        allergies = [str(a).lower() for a in (user_profile.get("allergies", []) if user_profile else [])]
        # Expanded for the same reason as the prompt: the violation check
        # substring-matches each exclusion against the meal text, so a bare
        # "dairy" only ever looked for the word "dairy" and milk and butter
        # sailed through.
        exclusions = expand_exclusions(exclusions or [])
        # Combine allergies and exclusions for violation checking
        all_exclusions = list(set(allergies + exclusions))

        # The replacement dishes must not reintroduce what was just removed.
        # Paneer, cow's milk and butter are dairy, so a plan that excludes
        # dairy needs the plant version even when the diet is merely "veg" -
        # otherwise the fix swaps a dairy meal for a different dairy meal.
        dairy_excluded = bool(
            DAIRY_TERMS.intersection(set(all_exclusions))
            | DAIRY_TERMS.intersection({str(a).lower() for a in (exclusions or [])})
        )
        dairy_free = is_vegan or dairy_excluded

        if not is_veg and not all_exclusions:
            return

        fallback_veg_options = {
            "breakfast": [
                {
                    "name": "Tofu Scramble with Whole Wheat Toast and Avocado" if dairy_free else "Paneer & Vegetable Bhurji with Multigrain Toast",
                    "cals": 0.30 * calorie_target,
                    "protein": 28.0 if dairy_free else 30.0,
                    "carbs": 42.0,
                    "fat": 16.0,
                    "ingredients": [
                        Ingredient(name="Firm Tofu" if dairy_free else "Fresh Paneer", amount_grams=150.0),
                        Ingredient(name="Whole Wheat Bread" if dairy_free else "Multigrain Bread", amount_grams=60.0),
                        Ingredient(name="Avocado" if dairy_free else "Green Bell Pepper", amount_grams=50.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0),
                        Ingredient(name="Spinach", amount_grams=50.0)
                    ],
                    "instructions": [
                        "1. Heat olive oil in a skillet over medium heat.",
                        "2. Crumble " + ("firm tofu" if dairy_free else "fresh paneer") + " into the skillet with turmeric, sea salt, and black pepper.",
                        "3. Sauté for 4-5 minutes until aromatic and lightly golden, tossing in baby spinach until wilted.",
                        "4. Toast bread slices until crisp and serve warm alongside the scramble."
                    ]
                },
                {
                    "name": "Steel-Cut Oats with Almond Butter, Chia Seeds and Berries",
                    "cals": 0.30 * calorie_target,
                    "protein": 18.0,
                    "carbs": 55.0,
                    "fat": 14.0,
                    "ingredients": [
                        Ingredient(name="Rolled Oats", amount_grams=70.0),
                        Ingredient(name="Almond Milk", amount_grams=200.0),
                        Ingredient(name="Chia Seeds", amount_grams=15.0),
                        Ingredient(name="Almond Butter", amount_grams=20.0),
                        Ingredient(name="Mixed Berries", amount_grams=60.0)
                    ],
                    "instructions": [
                        "1. Simmer rolled oats in almond milk over medium-low heat for 6-8 minutes until creamy.",
                        "2. Stir in chia seeds and a dash of cinnamon.",
                        "3. Transfer to a bowl and swirl in creamy almond butter.",
                        "4. Top with fresh berries and serve warm."
                    ]
                }
            ],
            "lunch": [
                {
                    "name": "Spiced Chickpea & Spinach Bowl with Quinoa" if dairy_free else "Paneer Tikka with Roasted Broccoli and Brown Rice",
                    "cals": 0.40 * calorie_target,
                    "protein": 26.0 if dairy_free else 36.0,
                    "carbs": 68.0,
                    "fat": 18.0,
                    "ingredients": [
                        Ingredient(name="Boiled Chickpeas" if dairy_free else "Paneer Cubes", amount_grams=160.0),
                        Ingredient(name="Cooked Quinoa" if dairy_free else "Steamed Brown Rice", amount_grams=140.0),
                        Ingredient(name="Baby Spinach" if dairy_free else "Broccoli Florets", amount_grams=100.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0),
                        Ingredient(name="Lemon Juice", amount_grams=15.0)
                    ],
                    "instructions": [
                        "1. Heat olive oil in a pan and lightly bloom ground cumin and turmeric.",
                        "2. Add " + ("chickpeas" if dairy_free else "paneer cubes") + " and sauté on medium heat until golden and fragrant.",
                        "3. Toss in " + ("baby spinach" if dairy_free else "broccoli florets") + " and cook for 3 minutes until tender-crisp.",
                        "4. Plate over a generous portion of warm " + ("quinoa" if dairy_free else "brown rice") + " and finish with a squeeze of fresh lemon."
                    ]
                },
                {
                    "name": "Rajma (Red Kidney Bean) Masala with Steamed Brown Rice and Cucumber Salad",
                    "cals": 0.40 * calorie_target,
                    "protein": 24.0,
                    "carbs": 72.0,
                    "fat": 12.0,
                    "ingredients": [
                        Ingredient(name="Kidney Beans (Rajma)", amount_grams=160.0),
                        Ingredient(name="Brown Rice", amount_grams=140.0),
                        Ingredient(name="Tomatoes & Onions", amount_grams=100.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0),
                        Ingredient(name="Cucumber", amount_grams=80.0)
                    ],
                    "instructions": [
                        "1. Sauté diced onions, tomatoes, ginger, and garlic in olive oil until a fragrant masala base forms.",
                        "2. Add cooked kidney beans along with a cup of broth and simmer on low for 10 minutes.",
                        "3. Steam brown rice until fluffy and slice crisp fresh cucumber.",
                        "4. Ladle hot rajma masala over the rice and serve with fresh cucumber salad."
                    ]
                }
            ],
            "dinner": [
                {
                    "name": "Grilled Tofu with Roasted Sweet Potato and Asparagus" if dairy_free else "Palak Paneer with Whole Wheat Roti and Roasted Asparagus",
                    "cals": 0.30 * calorie_target,
                    "protein": 25.0 if dairy_free else 32.0,
                    "carbs": 48.0,
                    "fat": 16.0,
                    "ingredients": [
                        Ingredient(name="Firm Tofu" if dairy_free else "Paneer Cubes", amount_grams=140.0),
                        Ingredient(name="Roasted Sweet Potato" if dairy_free else "Spinach Purée", amount_grams=140.0),
                        Ingredient(name="Asparagus", amount_grams=90.0),
                        Ingredient(name="Whole Wheat Roti", amount_grams=60.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0)
                    ],
                    "instructions": [
                        "1. Sear " + ("cubed tofu" if dairy_free else "paneer cubes") + " in olive oil until golden on all sides.",
                        "2. Prepare the " + ("roasted sweet potato" if dairy_free else "seasoned spinach sauce with cumin and garam masala") + ".",
                        "3. Sauté asparagus in the pan until bright green and tender-crisp.",
                        "4. Plate with warm whole wheat roti and serve immediately."
                    ]
                },
                {
                    "name": "Yellow Moong Dal Khichdi with Sautéed Mixed Vegetables",
                    "cals": 0.30 * calorie_target,
                    "protein": 22.0,
                    "carbs": 56.0,
                    "fat": 12.0,
                    "ingredients": [
                        Ingredient(name="Moong Dal", amount_grams=70.0),
                        Ingredient(name="Brown Rice", amount_grams=60.0),
                        Ingredient(name="Carrots & Green Peas", amount_grams=80.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0),
                        Ingredient(name="Cumin Seeds", amount_grams=5.0)
                    ],
                    "instructions": [
                        "1. Rinse moong dal and brown rice, then cook in water with turmeric and salt until soft and porridge-like.",
                        "2. In a small pan, temper cumin seeds and a pinch of asafoetida in olive oil.",
                        "3. Sauté carrots and green peas for 4 minutes until tender.",
                        "4. Pour the aromatic tempering and vegetables over the hot khichdi, stir gently, and serve."
                    ]
                }
            ],
            "snack": [
                {
                    "name": "Spiced Roasted Chickpeas with Lemon & Cucumber" if dairy_free else "Greek Yogurt with Mixed Berries and Walnuts",
                    "cals": 0.10 * calorie_target,
                    "protein": 10.0 if dairy_free else 14.0,
                    "carbs": 24.0,
                    "fat": 6.0,
                    "ingredients": [
                        Ingredient(name="Boiled Chickpeas" if dairy_free else "Plain Greek Yogurt", amount_grams=80.0 if dairy_free else 120.0),
                        Ingredient(name="Cucumber" if dairy_free else "Mixed Berries", amount_grams=60.0),
                        Ingredient(name="Olive Oil" if dairy_free else "Walnuts", amount_grams=5.0 if dairy_free else 15.0),
                        Ingredient(name="Lemon Juice", amount_grams=10.0) if dairy_free else Ingredient(name="Chia Seeds", amount_grams=10.0),
                    ],
                    "instructions": [
                        "1. Sauté chickpeas in olive oil with cumin, chaat masala, and salt until crisp." if dairy_free else "1. Spoon plain Greek yogurt into a bowl.",
                        "2. Dice fresh cucumber and toss with warm roasted chickpeas." if dairy_free else "2. Wash fresh berries and layer over the yogurt.",
                        "3. Finish with a squeeze of fresh lemon juice and serve warm." if dairy_free else "3. Sprinkle with crushed walnuts and chia seeds.",
                        "4. Enjoy as a nutrient-dense, plant-based wholesome snack." if dairy_free else "4. Serve chilled as a refreshing, high-protein snack."
                    ]
                },
                {
                    "name": "Sliced Apple with Natural Peanut Butter and Cinnamon",
                    "cals": 0.10 * calorie_target,
                    "protein": 7.0,
                    "carbs": 26.0,
                    "fat": 8.0,
                    "ingredients": [
                        Ingredient(name="Fresh Apple", amount_grams=120.0),
                        Ingredient(name="Natural Peanut Butter", amount_grams=20.0),
                        Ingredient(name="Ground Cinnamon", amount_grams=2.0)
                    ],
                    "instructions": [
                        "1. Wash and core a crisp fresh apple.",
                        "2. Slice into even wedges.",
                        "3. Portion creamy natural peanut butter into a dip bowl.",
                        "4. Dust apple slices with ground cinnamon and serve with peanut butter."
                    ]
                }
            ]
        }

        fallback_non_veg_options = {
            "breakfast": [
                {
                    "name": "Poached Eggs on Multigrain Toast with Baby Spinach",
                    "cals": 0.30 * calorie_target,
                    "protein": 28.0,
                    "carbs": 38.0,
                    "fat": 16.0,
                    "ingredients": [
                        Ingredient(name="Whole Eggs", amount_grams=120.0),
                        Ingredient(name="Multigrain Bread", amount_grams=60.0),
                        Ingredient(name="Baby Spinach", amount_grams=50.0),
                        Ingredient(name="Olive Oil", amount_grams=8.0)
                    ],
                    "instructions": [
                        "1. Bring a small pot of water to a gentle simmer with a drop of vinegar.",
                        "2. Crack fresh eggs into simmering water and poach for 3 minutes until whites are set.",
                        "3. Toast multigrain bread until golden and crisp.",
                        "4. Layer fresh baby spinach over toast, top with poached eggs, and season with black pepper."
                    ]
                },
                {
                    "name": "Egg White & Spinach Scramble with Avocado and Rye Toast",
                    "cals": 0.30 * calorie_target,
                    "protein": 30.0,
                    "carbs": 35.0,
                    "fat": 14.0,
                    "ingredients": [
                        Ingredient(name="Egg Whites", amount_grams=150.0),
                        Ingredient(name="Whole Egg", amount_grams=50.0),
                        Ingredient(name="Rye Toast", amount_grams=60.0),
                        Ingredient(name="Avocado", amount_grams=40.0),
                        Ingredient(name="Olive Oil", amount_grams=8.0)
                    ],
                    "instructions": [
                        "1. Whisk egg whites and whole egg together with a pinch of sea salt.",
                        "2. Heat olive oil in a non-stick pan and sauté baby spinach for 1 minute.",
                        "3. Pour eggs into the pan and gently scramble over medium-low heat until soft curds form.",
                        "4. Serve with toasted rye bread and sliced fresh avocado."
                    ]
                }
            ],
            "lunch": [
                {
                    "name": "Lemon Herb Grilled Chicken Breast with Quinoa and Steamed Asparagus",
                    "cals": 0.40 * calorie_target,
                    "protein": 42.0,
                    "carbs": 55.0,
                    "fat": 15.0,
                    "ingredients": [
                        Ingredient(name="Skinless Chicken Breast", amount_grams=180.0),
                        Ingredient(name="Cooked Quinoa", amount_grams=140.0),
                        Ingredient(name="Asparagus", amount_grams=100.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0),
                        Ingredient(name="Lemon Juice", amount_grams=15.0)
                    ],
                    "instructions": [
                        "1. Season chicken breast with olive oil, oregano, garlic powder, and fresh lemon juice.",
                        "2. Grill on medium-high heat for 6-7 minutes per side until thoroughly cooked through.",
                        "3. Steam asparagus spears until bright green and tender-crisp.",
                        "4. Slice chicken breast and serve over warm fluffy quinoa with asparagus."
                    ]
                },
                {
                    "name": "Baked Herb Salmon Fillet with Steamed Brown Rice and Broccoli",
                    "cals": 0.40 * calorie_target,
                    "protein": 38.0,
                    "carbs": 52.0,
                    "fat": 18.0,
                    "ingredients": [
                        Ingredient(name="Salmon Fillet", amount_grams=160.0),
                        Ingredient(name="Brown Rice", amount_grams=140.0),
                        Ingredient(name="Broccoli Florets", amount_grams=100.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0),
                        Ingredient(name="Dill & Lemon", amount_grams=10.0)
                    ],
                    "instructions": [
                        "1. Preheat oven to 200°C (400°F) and place salmon fillet on a lined baking tray.",
                        "2. Brush salmon with olive oil, fresh dill, sea salt, and lemon slices.",
                        "3. Bake for 12-14 minutes until tender and flaky.",
                        "4. Serve with steamed brown rice and tender broccoli florets."
                    ]
                }
            ],
            "dinner": [
                {
                    "name": "Pan-Seared White Fish Fillet with Roasted Sweet Potato and Green Beans",
                    "cals": 0.30 * calorie_target,
                    "protein": 34.0,
                    "carbs": 42.0,
                    "fat": 12.0,
                    "ingredients": [
                        Ingredient(name="White Fish Fillet", amount_grams=160.0),
                        Ingredient(name="Sweet Potato", amount_grams=130.0),
                        Ingredient(name="Green Beans", amount_grams=90.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0)
                    ],
                    "instructions": [
                        "1. Cube sweet potatoes and roast in the oven with olive oil and thyme until fork-tender.",
                        "2. Season fish fillet with paprika, sea salt, and black pepper.",
                        "3. Pan-sear fish in olive oil over medium-high heat for 3-4 minutes per side.",
                        "4. Blanch green beans in boiling water for 3 minutes and plate together with fish and potatoes."
                    ]
                },
                {
                    "name": "Grilled Garlic Herb Chicken Thigh with Jasmine Rice and Sautéed Greens",
                    "cals": 0.30 * calorie_target,
                    "protein": 35.0,
                    "carbs": 44.0,
                    "fat": 14.0,
                    "ingredients": [
                        Ingredient(name="Boneless Chicken Thigh", amount_grams=160.0),
                        Ingredient(name="Jasmine Rice", amount_grams=130.0),
                        Ingredient(name="Baby Spinach", amount_grams=80.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0)
                    ],
                    "instructions": [
                        "1. Marinate chicken thigh with minced garlic, dried herbs, and olive oil.",
                        "2. Sear on a hot grill pan for 5-6 minutes per side until beautifully charred and cooked.",
                        "3. Steam fragrant jasmine rice until light and fluffy.",
                        "4. Quickly wilt spinach in the remaining pan juices and serve warm alongside chicken."
                    ]
                }
            ],
            "snack": [
                {
                    "name": "Hard-Boiled Eggs with Cherry Tomatoes and Sea Salt",
                    "cals": 0.10 * calorie_target,
                    "protein": 13.0,
                    "carbs": 4.0,
                    "fat": 10.0,
                    "ingredients": [
                        Ingredient(name="Whole Eggs", amount_grams=100.0),
                        Ingredient(name="Cherry Tomatoes", amount_grams=60.0),
                        Ingredient(name="Black Pepper & Sea Salt", amount_grams=2.0)
                    ],
                    "instructions": [
                        "1. Boil fresh eggs in simmering water for 9 minutes until hard-boiled.",
                        "2. Cool in an ice bath, peel carefully, and halve lengthwise.",
                        "3. Wash fresh sweet cherry tomatoes.",
                        "4. Season eggs with cracked black pepper and sea salt, and serve with tomatoes."
                    ]
                },
                {
                    "name": "Sliced Roast Chicken Breast with Cucumber and Hummus",
                    "cals": 0.10 * calorie_target,
                    "protein": 22.0,
                    "carbs": 8.0,
                    "fat": 6.0,
                    "ingredients": [
                        Ingredient(name="Skinless Chicken Breast", amount_grams=80.0),
                        Ingredient(name="Cucumber", amount_grams=60.0),
                        Ingredient(name="Hummus", amount_grams=25.0)
                    ],
                    "instructions": [
                        "1. Slice cold cooked roast chicken breast into thin bite-sized strips.",
                        "2. Cut fresh cucumber into crisp dipping batons.",
                        "3. Plate alongside creamy hummus.",
                        "4. Season with a pinch of paprika and enjoy fresh."
                    ]
                }
            ]
        }

        for day in meal_plan.days:
            for meal in day.meals:
                m_type = meal.meal_type.lower()
                ing_names = [ing.name for ing in meal.ingredients]
                names_to_check = ing_names + [meal.recipe_name]
                meal_full_text = " ".join(names_to_check).lower()

                violates_diet = False
                if is_veg:
                    diet_violations = check_diet_compliance(dt, names_to_check)
                    if diet_violations:
                        violates_diet = True

                violates_allergy = False
                # Substring matching alone reads "almond milk" as milk and
                # "almond butter" as butter, which flagged dairy-free recipes
                # and then replaced them with ones that were not. Remove the
                # known lookalikes before matching.
                check_text = meal_full_text
                for lookalike in FALSE_POSITIVE_EXCLUSIONS:
                    if lookalike in check_text:
                        check_text = check_text.replace(lookalike, " ")
                for exc in all_exclusions:
                    targets = ALLERGEN_CANONICAL_TARGETS.get(exc, [exc])
                    if any(t in check_text for t in targets):
                        violates_allergy = True
                        break

                if violates_diet or violates_allergy:
                    logger.warning(
                        f"[PlannerAgent] Compliance violation in '{meal.recipe_name}' (diet: {violates_diet}, allergy: {violates_allergy}). "
                        f"Replacing with certified compliant dish."
                    )
                    slot_key = "snack" if "snack" in m_type else ("breakfast" if "breakfast" in m_type else ("lunch" if "lunch" in m_type else "dinner"))

                    if is_veg:
                        opts = fallback_veg_options.get(slot_key, fallback_veg_options["lunch"])
                    else:
                        # Non-veg: find fallback dishes that don't violate requested allergies/exclusions
                        candidate_opts = fallback_non_veg_options.get(slot_key, fallback_non_veg_options["lunch"])
                        opts = []
                        for opt in candidate_opts:
                            opt_text = (opt["name"] + " " + " ".join(i.name for i in opt["ingredients"])).lower()
                            if not any(t in opt_text for exc in all_exclusions for t in ALLERGEN_CANONICAL_TARGETS.get(exc, [exc])):
                                opts.append(opt)
                        if not opts:
                            opts = fallback_veg_options.get(slot_key, fallback_veg_options["lunch"])

                    chosen = opts[(day.day_number - 1) % len(opts)]
                    meal.recipe_name = chosen["name"]
                    meal.calories = chosen["cals"]
                    meal.protein_g = chosen["protein"]
                    meal.carbs_g = chosen["carbs"]
                    meal.fat_g = chosen["fat"]
                    meal.ingredients = chosen["ingredients"]
                    meal.instructions = "\n".join(chosen["instructions"]) if isinstance(chosen["instructions"], list) else str(chosen["instructions"])
                    meal.source = "ai_generated"

