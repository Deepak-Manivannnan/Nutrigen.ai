import os
import re
from typing import Dict, Any, Optional, List
from backend.app.agents.base import BaseAgent
from backend.app.agents.planner.tools import PLANNER_TOOLS
from backend.app.schemas.domain import MealPlan, Meal, DayPlan, Ingredient
from backend.app.llm.client import LLMClient

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
    Planner Agent: Creates a complete multi-day meal plan.
    Slices calories across meals, locks fixed user meals, utilizes recipe APIs
    with authentic AI fallbacks, and reformulates plans upon Supervisor feedback.
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
            max_steps=max_steps or 2,
            timeout_seconds=timeout_seconds or 120
        )

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
        exclusions: Optional[List[str]] = None
    ) -> MealPlan:
        plan_diet = getattr(current_plan, "diet_type", None) or user_profile.get("diet_type") or "veg"
        plan_name = plan_name or getattr(current_plan, "name", None) or f"{days}-Day {plan_diet.capitalize()} Plan"

        task_instruction = (
            f"Generate a complete {days}-day meal plan targeting {calorie_target:.0f} calories per day (±5%).\n"
            f"Plan version: {version}.\n"
            f"Plan diet type: {plan_diet}.\n"
            f"Provide exactly {days} days, with 3 meals (breakfast, lunch, dinner) per day, ingredients in grams, and accurate calories.\n"
        )
        if feedback:
            task_instruction += (
                f"\nCRITICAL REVISION FEEDBACK FROM PREVIOUS ROUND (Must be addressed):\n"
                f"- " + "\n- ".join(feedback) + "\n"
            )

        context = {
            "task_id": task_id,
            "days": days,
            "calorie_target": calorie_target,
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
        # Combine allergies and exclusions for all diet types
        allergies_list = [str(a).lower() for a in user_profile.get("allergies", [])]
        exclusions_list = [str(e).lower() for e in (exclusions or [])]
        all_exclusions = list(set(allergies_list + exclusions_list))

        if plan_diet in ("veg", "vegetarian"):
            diet_rules_prompt = (
                "- STRICT DIETARY RULE: 100% VEGETARIAN.\n"
                "  * Absolutely NO meat, poultry (NO chicken, turkey), fish (NO salmon, tuna, seafood), beef, pork, or animal gelatin.\n"
                "  * Absolutely NO eggs unless specifically eggetarian.\n"
                "  * Every single meal (breakfast, lunch, dinner) MUST contain ONLY vegetarian plant-based foods, lentils/dal, chickpeas/chana, beans/rajma, paneer, tofu, vegetables, grains (quinoa, brown rice, oats, whole wheat roti), nuts, and seeds.\n"
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

        meal_plan = None
        if days > 1:
            logger.info(f"[PlannerAgent] Generating {days}-day plan directly via structured output.")
            plan_prompt = (
                f"You are the Meal Planner Agent. Generate a complete {days}-day clinical meal plan for {user_profile.get('name', 'user')}.\n"
                f"- Target calories: {calorie_target:.0f} kcal per day (±5%)\n"
                f"- Diet type: {plan_diet}\n"
                f"{diet_rules_prompt}"
                f"- Exclude allergies: {user_profile.get('allergies', [])}\n"
                f"- Exclude ingredients: {exclusions or []}\n"
                f"- Avoid dislikes: {user_profile.get('dislikes', [])}\n"
                f"- Provide EXACTLY {days} DayPlan items (day_number 1 to {days}).\n"
                f"- Each day must have breakfast (~30% cal), lunch (~40% cal), and dinner (~30% cal) with realistic ingredients (amount_grams).\n"
                f"- For EACH meal, provide detailed step-by-step 'instructions' (3 to 4 numbered steps detailing prep, cooking, seasoning, and serving warm). NEVER output generic 1-liners.\n"
            )
            if feedback:
                plan_prompt += f"\n- Address this clinical review feedback from previous round:\n" + "\n".join(f"  * {fb}" for fb in feedback)
            if fixed_meals:
                plan_prompt += f"\n- Fixed meal constraints:\n" + "\n".join(f"  * {k}: {v}" for k, v in fixed_meals.items())

            try:
                meal_plan = await self.llm.generate_structured(
                    prompt=plan_prompt,
                    response_schema=MealPlan
                )
            except Exception as e:
                logger.warning(f"[PlannerAgent] Direct structured generation error: {e}")
        else:
            try:
                meal_plan = await self.run(task_instruction, context)
            except Exception as e:
                logger.warning(f"[PlannerAgent] Run encountered error: {e}. Executing direct generation fallback.")
                meal_plan = MealPlan(days=[], target_calories=calorie_target, version=version)

        # Fallback safeguard: If loop returned empty or incomplete days, generate directly
        if not meal_plan or not meal_plan.days or len(meal_plan.days) < days:
            logger.info(f"[PlannerAgent] Plan had {len(meal_plan.days) if meal_plan else 0}/{days} days. Running direct structured generation fallback.")
            fallback_prompt = (
                f"You are the Meal Planner Agent. Generate a complete {days}-day clinical meal plan for {user_profile.get('name', 'user')}.\n"
                f"- Target calories: {calorie_target:.0f} kcal per day (±5%)\n"
                f"- Diet type: {plan_diet}\n"
                f"{diet_rules_prompt}"
                f"- Exclude allergies: {user_profile.get('allergies', [])}\n"
                f"- Exclude ingredients: {exclusions or []}\n"
                f"- Avoid dislikes: {user_profile.get('dislikes', [])}\n"
                f"- Provide EXACTLY {days} DayPlan items (day_number 1 to {days}).\n"
                f"- Each day must have breakfast (~30% cal), lunch (~40% cal), and dinner (~30% cal) with realistic ingredients (amount_grams).\n"
                f"- For EACH meal, provide detailed step-by-step 'instructions' (3 to 4 numbered steps detailing prep, cooking, seasoning, and serving warm). NEVER output generic 1-liners.\n"
            )
            if feedback:
                fallback_prompt += f"\n- Address this clinical review feedback:\n" + "\n".join(f"  * {fb}" for fb in feedback)
            try:
                direct_plan = await self.llm.generate_structured(
                    prompt=fallback_prompt,
                    response_schema=MealPlan
                )
                if direct_plan and direct_plan.days:
                    meal_plan = direct_plan
            except Exception as direct_err:
                logger.error(f"[PlannerAgent] Direct generation error: {direct_err}")

        meal_plan.target_calories = calorie_target
        meal_plan.version = version
        meal_plan.diet_type = plan_diet
        meal_plan.name = plan_name

        # Strict Diet Compliance Verification and Sanitization Pass
        self._sanitize_diet_compliance(meal_plan, plan_diet, calorie_target, user_profile)

        from backend.app.tools.nutrition.calculator import normalize_single_person_plan
        await normalize_single_person_plan(meal_plan, calorie_target)

        return meal_plan

    async def modify_meal(
        self,
        current_plan: MealPlan,
        edit_details: Any,
        user_profile: Dict[str, Any],
        task_id: Optional[str] = None,
        exclusions: Optional[List[str]] = None
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

        cl_inst = instructions.lower()

        # Meat/pork-family words so "ham", "bacon", "sausage" style requests are
        # caught even when phrased without the parent ingredient name.
        meat_words = ["pork", "beef", "turkey", "meat", "chicken", "fish", "dairy", "allerg",
                      "ham", "bacon", "sausage", "pepperoni", "salami", "prosciutto", "pancetta", "lard"]

        # Check if the instruction asks to replace recipes across the plan containing specific excluded ingredients
        is_exclusion_scrub = (
            any(w in cl_inst for w in meat_words)
            and any(w in cl_inst for w in ["replace", "remove", "exclude", "without", "free", "scrub", "different recipes", "swap", "swap out"])
        ) or (bool(user_profile.get("allergies")) and any(w in cl_inst for w in ["exclude", "remove", "without", "replace recipes", "replace the recipes"])) \
            or (bool(edit_exclusions) and (edit_scope == "plan" or (
                getattr(edit_details, "day", None) is None and getattr(edit_details, "meal_type", None) is None)))

        if is_exclusion_scrub:
            allergies_list = [str(a).lower() for a in user_profile.get("allergies", [])]
            for exc in ["pork", "beef", "turkey", "dairy", "ham", "bacon", "sausage", "chicken", "fish"]:
                if exc in cl_inst and exc not in allergies_list:
                    allergies_list.append(exc)
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

        target_meal_type = getattr(edit_details, "meal_type", None)
        if not target_meal_type:
            for mt in ["snack", "breakfast", "lunch", "dinner"]:
                if mt in cl_inst:
                    target_meal_type = mt
                    break

        # Check if the instruction asks to update an entire day's meals
        is_entire_day = (
            (not target_meal_type and any(k in cl_inst for k in ["entire day", "all meals", "whole day", "all 3 meals", "all 4 meals", "day meals"]))
            or (not target_meal_type and bool(re.search(r'\bday\s*\d+\b', cl_inst)) and not any(m in cl_inst for m in ["breakfast", "lunch", "dinner", "snack"]))
        )

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

        if is_entire_day:
            # Regenerate ONLY this day's meals
            day_prompt = (
                f"You are the Meal Planner Agent. Generate a replacement DayPlan for Day {target_day} of a {plan_diet} meal plan.\n"
                f"- Daily calorie target: ~{int(current_plan.target_calories)} kcal (±5%)\n"
                f"- User request: '{instructions}'\n"
                f"- Diet type: {plan_diet} (FOR VEGETARIAN: ZERO MEAT/CHICKEN/FISH/SEAFOOD, FOR VEGAN: ZERO ANIMAL PRODUCTS)\n"
                f"- Exclude allergies: {user_profile.get('allergies', [])}\n"
                f"- Also strictly exclude these user-rejected ingredients: {edit_exclusions if edit_exclusions else 'none'}. Do NOT include any of them.\n"
                f"- Avoid dislikes: {user_profile.get('dislikes', [])}\n"
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

            # Get operation type from edit_details (default: replace)
            operation = getattr(edit_details, "operation", None) or "replace"
            
            # Handle remove operation
            if operation == "remove" or any(k in cl_inst for k in ["remove", "delete", "drop"]):
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
                slot_cal = target_meal_cal or (
                    old_meal.calories if old_meal and old_meal.calories > 100
                    else (current_plan.target_calories * (0.3 if target_meal_type in ("breakfast", "dinner") else 0.4))
                )

                meal_prompt = (
                    f"You are the Meal Planner Agent. Generate a single replacement {target_meal_type} meal for Day {target_day}.\n"
                    f"- Meal type: {target_meal_type}\n"
                    f"- Replaces previous dish: '{old_dish}'\n"
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
                        if any(k in cl_inst for k in ["add", "include", "put"]) or operation == "add":
                            target_day_plan.meals.append(replacement_meal)
                        elif target_day_plan.meals:
                            target_day_plan.meals[-1] = replacement_meal

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
        from backend.app.tools.diet_rules import check_diet_compliance
        from backend.app.schemas.domain import Ingredient
        from backend.app.tools.allergens import ALLERGEN_CANONICAL_TARGETS

        dt = (plan_diet or "").strip().lower()
        is_veg = dt in ("veg", "vegetarian", "vegan")
        is_vegan = dt == "vegan"
        allergies = [str(a).lower() for a in (user_profile.get("allergies", []) if user_profile else [])]
        exclusions = [str(e).lower() for e in (exclusions or [])]
        # Combine allergies and exclusions for violation checking
        all_exclusions = list(set(allergies + exclusions))

        if not is_veg and not all_exclusions:
            return

        fallback_veg_options = {
            "breakfast": [
                {
                    "name": "Tofu Scramble with Whole Wheat Toast and Avocado" if is_vegan else "Paneer & Vegetable Bhurji with Multigrain Toast",
                    "cals": 0.30 * calorie_target,
                    "protein": 28.0 if is_vegan else 30.0,
                    "carbs": 42.0,
                    "fat": 16.0,
                    "ingredients": [
                        Ingredient(name="Firm Tofu" if is_vegan else "Fresh Paneer", amount_grams=150.0),
                        Ingredient(name="Whole Wheat Bread" if is_vegan else "Multigrain Bread", amount_grams=60.0),
                        Ingredient(name="Avocado" if is_vegan else "Green Bell Pepper", amount_grams=50.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0),
                        Ingredient(name="Spinach", amount_grams=50.0)
                    ],
                    "instructions": [
                        "1. Heat olive oil in a skillet over medium heat.",
                        "2. Crumble " + ("firm tofu" if is_vegan else "fresh paneer") + " into the skillet with turmeric, sea salt, and black pepper.",
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
                    "name": "Spiced Chickpea & Spinach Bowl with Quinoa" if is_vegan else "Paneer Tikka with Roasted Broccoli and Brown Rice",
                    "cals": 0.40 * calorie_target,
                    "protein": 26.0 if is_vegan else 36.0,
                    "carbs": 68.0,
                    "fat": 18.0,
                    "ingredients": [
                        Ingredient(name="Boiled Chickpeas" if is_vegan else "Paneer Cubes", amount_grams=160.0),
                        Ingredient(name="Cooked Quinoa" if is_vegan else "Steamed Brown Rice", amount_grams=140.0),
                        Ingredient(name="Baby Spinach" if is_vegan else "Broccoli Florets", amount_grams=100.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0),
                        Ingredient(name="Lemon Juice", amount_grams=15.0)
                    ],
                    "instructions": [
                        "1. Heat olive oil in a pan and lightly bloom ground cumin and turmeric.",
                        "2. Add " + ("chickpeas" if is_vegan else "paneer cubes") + " and sauté on medium heat until golden and fragrant.",
                        "3. Toss in " + ("baby spinach" if is_vegan else "broccoli florets") + " and cook for 3 minutes until tender-crisp.",
                        "4. Plate over a generous portion of warm " + ("quinoa" if is_vegan else "brown rice") + " and finish with a squeeze of fresh lemon."
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
                    "name": "Grilled Tofu with Roasted Sweet Potato and Asparagus" if is_vegan else "Palak Paneer with Whole Wheat Roti and Roasted Asparagus",
                    "cals": 0.30 * calorie_target,
                    "protein": 25.0 if is_vegan else 32.0,
                    "carbs": 48.0,
                    "fat": 16.0,
                    "ingredients": [
                        Ingredient(name="Firm Tofu" if is_vegan else "Paneer Cubes", amount_grams=140.0),
                        Ingredient(name="Roasted Sweet Potato" if is_vegan else "Spinach Purée", amount_grams=140.0),
                        Ingredient(name="Asparagus", amount_grams=90.0),
                        Ingredient(name="Whole Wheat Roti", amount_grams=60.0),
                        Ingredient(name="Olive Oil", amount_grams=10.0)
                    ],
                    "instructions": [
                        "1. Sear " + ("cubed tofu" if is_vegan else "paneer cubes") + " in olive oil until golden on all sides.",
                        "2. Prepare the " + ("roasted sweet potato" if is_vegan else "seasoned spinach sauce with cumin and garam masala") + ".",
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
                for exc in all_exclusions:
                    targets = ALLERGEN_CANONICAL_TARGETS.get(exc, [exc])
                    if any(t in meal_full_text for t in targets):
                        violates_allergy = True
                        break

                if violates_diet or violates_allergy:
                    logger.warning(
                        f"[PlannerAgent] Compliance violation in '{meal.recipe_name}' (diet: {violates_diet}, allergy: {violates_allergy}). "
                        f"Replacing with certified compliant dish."
                    )
                    slot_key = "breakfast" if "breakfast" in m_type else ("lunch" if "lunch" in m_type else "dinner")

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

