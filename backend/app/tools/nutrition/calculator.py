import asyncio
import re
from typing import Dict, Any, List, Tuple
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, NutritionIssue, DailyNutritionTotal
from backend.app.tools.nutrition.usda import lookup_nutrition
from backend.app.tools.diet_rules import is_liquid_ingredient, get_ingredient_unit, check_diet_compliance

# Clean vegetarian/vegan replacement mapping for accidental meat intrusions
VEG_REPLACEMENTS = {
    "turkey": "tofu",
    "ground turkey": "firm tofu",
    "turkey bacon": "smoked tempeh",
    "chicken": "paneer",
    "ground chicken": "crumbled paneer",
    "chicken breast": "paneer",
    "beef": "black beans",
    "ground beef": "red kidney beans (rajma)",
    "steak": "marinated portobello mushrooms",
    "pork": "tempeh",
    "bacon": "crispy tempeh strips",
    "ham": "smoked tofu",
    "fish": "grilled paneer",
    "salmon": "tofu steak",
    "tuna": "mashed chickpeas",
    "shrimp": "button mushrooms",
    "prawn": "mushrooms",
    "crab": "hearts of palm",
    "meat": "soya chunks"
}

def clean_dish_name(dish_name: str, diet_type: str) -> str:
    """Replaces meat names in dish titles with vegetarian counterparts."""
    dt = (diet_type or "").lower()
    if dt not in ("veg", "vegetarian", "vegan"):
        return dish_name

    cleaned = dish_name
    for meat, veg in [
        ("Turkey", "Tofu" if dt == "vegan" else "Paneer"),
        ("Ground Turkey", "Crumbled Tofu"),
        ("Chicken", "Paneer" if dt != "vegan" else "Tofu"),
        ("Beef", "Kidney Bean"),
        ("Pork", "Tempeh"),
        ("Bacon", "Tempeh"),
        ("Fish", "Tofu" if dt == "vegan" else "Paneer"),
        ("Salmon", "Paneer"),
        ("Shrimp", "Mushroom"),
        ("Meat", "Soya Chunks")
    ]:
        cleaned = re.sub(rf"\b{re.escape(meat)}\b", veg, cleaned, flags=re.IGNORECASE)
    return cleaned

async def normalize_single_person_plan(
    plan: MealPlan,
    target_daily_calories: float
) -> MealPlan:
    """
    Normalizes a meal plan for an individual user:
    1. Replaces non-veg ingredients and titles in vegetarian/vegan plans with healthy plant/dairy proteins.
    2. Enforces single-person serving portions (scales family/batch portions to single person).
    3. Aligns meal slots to standard splits: Breakfast 30%, Lunch 40%, Dinner 30% (or 25%/35%/25%/15% with snack).
    4. Sets unit to 'ml' for liquids (oils, milks, juices, sauces) and 'g' for solids.
    5. Provides step-by-step cooking instructions on every meal if missing.
    """
    plan_diet = getattr(plan, "diet_type", "veg") or "veg"
    is_veg = plan_diet in ("veg", "vegetarian", "vegan")

    for day in plan.days:
        meal_count = len(day.meals)
        has_snack = any(m.meal_type.lower() == "snack" for m in day.meals)

        if has_snack or meal_count >= 4:
            slot_targets = {
                "breakfast": 0.25 * target_daily_calories,
                "lunch": 0.35 * target_daily_calories,
                "dinner": 0.25 * target_daily_calories,
                "snack": 0.15 * target_daily_calories
            }
        else:
            slot_targets = {
                "breakfast": 0.30 * target_daily_calories,
                "lunch": 0.40 * target_daily_calories,
                "dinner": 0.30 * target_daily_calories,
                "snack": 0.10 * target_daily_calories
            }

        for meal in day.meals:
            slot_key = meal.meal_type.lower()
            slot_target_cal = slot_targets.get(slot_key, target_daily_calories / max(1, meal_count))

            # 1. Sanitize Diet Violations for Veg/Vegan
            if is_veg:
                meal.recipe_name = clean_dish_name(meal.recipe_name, plan_diet)
                for ing in meal.ingredients:
                    ing_clean = ing.name.strip().lower()
                    for meat_term, sub_term in VEG_REPLACEMENTS.items():
                        if re.search(rf"\b{re.escape(meat_term)}\b", ing_clean):
                            ing.name = re.sub(rf"\b{re.escape(meat_term)}\b", sub_term, ing_clean, flags=re.IGNORECASE)
                            break

            # 2. Assign Correct Physical Units (ml for liquids, g for solids)
            for ing in meal.ingredients:
                ing.unit = get_ingredient_unit(ing.name)
                # Cap excessive single-person oil/fat portions
                if is_liquid_ingredient(ing.name) and "oil" in ing.name.lower():
                    if ing.amount_grams > 15.0:
                        ing.amount_grams = 10.0

            # 3. Scale Portion Sizes to Single-Person Calories
            current_nut = await calculate_meal_nutrition(meal)
            curr_cal = current_nut["calories"]

            # If meal is severely out of single-person proportion (> 1.25x or < 0.75x of slot target)
            if curr_cal > 0 and (curr_cal > 1.25 * slot_target_cal or curr_cal < 0.75 * slot_target_cal):
                scale = slot_target_cal / curr_cal
                # Bound scaling factor reasonably
                scale = max(0.20, min(3.5, scale))
                for ing in meal.ingredients:
                    scaled_amt = round(ing.amount_grams * scale, 1)
                    # Allow realistic portions to achieve calorie target
                    if not is_liquid_ingredient(ing.name):
                        ing.amount_grams = max(10.0, min(280.0, scaled_amt))
                    else:
                        ing.amount_grams = max(5.0, min(280.0, scaled_amt))

            # 4. Recompute exact meal nutrition
            updated_nut = await calculate_meal_nutrition(meal)
            meal.calories = updated_nut["calories"]
            meal.protein_g = updated_nut["protein_g"]
            meal.carbs_g = updated_nut["carbs_g"]
            meal.fat_g = updated_nut["fat_g"]
            meal.diet_type = plan_diet

            # 5. Ensure Detailed Step-by-Step Cooking Instructions
            ins = (meal.instructions or "").strip()
            dish_lower = (meal.recipe_name or "").lower()
            m_type = (meal.meal_type or "").lower()
            is_generic = (
                not ins
                or len(ins) < 35
                or "consume as" in ins.lower()
                or "eat as" in ins.lower()
                or "snack on" in ins.lower()
            )
            if is_generic:
                prep_ings = ", ".join(i.name for i in meal.ingredients[:3]) if meal.ingredients else "fresh ingredients"
                if any(w in dish_lower for w in ["nut", "almond", "walnut", "cashew", "seed", "trail mix"]):
                    meal.instructions = (
                        f"1. Measure out portioned {prep_ings} into a small bowl.\n"
                        f"2. (Optional) Heat a dry skillet over low heat and lightly toast for 2–3 minutes until fragrant and crisp.\n"
                        f"3. Sprinkle lightly with a pinch of rock salt or cinnamon if desired.\n"
                        f"4. Let cool to room temperature and enjoy as a wholesome, nutrient-dense snack."
                    )
                elif any(w in dish_lower for w in ["salad", "sprout", "chaat", "fruit", "cucumber"]):
                    meal.instructions = (
                        f"1. Thoroughly wash, peel, and chop {prep_ings} into bite-sized pieces.\n"
                        f"2. Transfer to a mixing bowl and toss gently.\n"
                        f"3. Season with fresh lemon juice, black salt, roasted cumin powder, and herbs.\n"
                        f"4. Toss to coat evenly and serve immediately chilled or at room temperature."
                    )
                elif any(w in dish_lower for w in ["oats", "oatmeal", "porridge", "smoothie"]):
                    meal.instructions = (
                        f"1. Measure {prep_ings} and bring water or milk to a gentle simmer in a saucepan.\n"
                        f"2. Stir in oats/grains and cook over low heat for 5–7 minutes, stirring occasionally.\n"
                        f"3. Remove from heat once thickened to a creamy consistency.\n"
                        f"4. Garnish with portioned nuts, seeds, or sliced fruit and serve warm."
                    )
                else:
                    meal.instructions = (
                        f"1. Rinse and prep all fresh ingredients ({prep_ings}) by dicing evenly.\n"
                        f"2. Heat cooking oil in a pan over medium heat, add aromatic spices, and sauté until fragrant.\n"
                        f"3. Add main ingredients and cook for 10–12 minutes with a splash of water until tender and cooked through.\n"
                        f"4. Season with salt and spices to taste. Garnish with fresh herbs and serve warm."
                    )

        # Update day totals
        day_cals = sum(m.calories for m in day.meals)

        # Ensure the whole day matches target_daily_calories within ±5%
        if day_cals > 0 and abs(day_cals - target_daily_calories) > 0.05 * target_daily_calories:
            day_scale = target_daily_calories / day_cals
            day_scale = max(0.5, min(3.5, day_scale))
            for m in day.meals:
                for ing in m.ingredients:
                    scaled = ing.amount_grams * day_scale
                    if not is_liquid_ingredient(ing.name):
                        ing.amount_grams = round(max(10.0, min(320.0, scaled)), 1)
                    else:
                        ing.amount_grams = round(max(5.0, min(320.0, scaled)), 1)
                re_nut = await calculate_meal_nutrition(m)
                m.calories = re_nut["calories"]
                m.protein_g = re_nut["protein_g"]
                m.carbs_g = re_nut["carbs_g"]
                m.fat_g = re_nut["fat_g"]
            day_cals = sum(m.calories for m in day.meals)

        day.total_calories = round(day_cals, 1)
        day.total_protein_g = round(sum(m.protein_g for m in day.meals), 1)
        day.total_carbs_g = round(sum(m.carbs_g for m in day.meals), 1)
        day.total_fat_g = round(sum(m.fat_g for m in day.meals), 1)

    if plan.days:
        plan.average_daily_calories = round(sum(d.total_calories for d in plan.days) / len(plan.days), 1)

    return plan

async def calculate_ingredient_nutrition(name: str, amount_grams: float) -> Dict[str, float]:
    """
    Computes exact nutrient values for a specific ingredient amount in grams.
    Formula: (amount_grams / 100.0) * nutrient_per_100g
    """
    data = await lookup_nutrition(name)
    scale = max(0.0, float(amount_grams)) / 100.0
    return {
        "calories": round(data["calories"] * scale, 1),
        "protein_g": round(data["protein_g"] * scale, 1),
        "carbs_g": round(data["carbs_g"] * scale, 1),
        "fat_g": round(data["fat_g"] * scale, 1),
        "found": data["found"]
    }

async def calculate_meal_nutrition(meal: Meal) -> Dict[str, float]:
    """
    Computes exact calories and macros for a meal by summing its ingredients.
    """
    tot_cal, tot_prot, tot_carbs, tot_fat = 0.0, 0.0, 0.0, 0.0

    if meal.ingredients:
        tasks = [calculate_ingredient_nutrition(ing.name, ing.amount_grams) for ing in meal.ingredients]
        results = await asyncio.gather(*tasks)
        for nut in results:
            tot_cal += nut["calories"]
            tot_prot += nut["protein_g"]
            tot_carbs += nut["carbs_g"]
            tot_fat += nut["fat_g"]
    else:
        tot_cal = meal.calories
        tot_prot = meal.protein_g
        tot_carbs = meal.carbs_g
        tot_fat = meal.fat_g

    return {
        "calories": round(tot_cal, 1),
        "protein_g": round(tot_prot, 1),
        "carbs_g": round(tot_carbs, 1),
        "fat_g": round(tot_fat, 1)
    }

async def audit_meal_plan_nutrition(
    plan: MealPlan,
    target_calories: float,
    tolerance_pct: float = 0.05
) -> Tuple[bool, List[NutritionIssue], List[DailyNutritionTotal]]:
    """
    Pure Python Deterministic Math Engine (Plan section 7: 'Code does the math').
    1. Normalizes portions for single person and ensures liquid/solid units and instructions.
    2. Computes exact day-by-day calories and macros from ingredients.
    3. Evaluates tolerance (±5%), generating issues for any discrepancies.
    """
    # Run deterministic portion and single-person normalization first
    await normalize_single_person_plan(plan, target_calories)

    daily_totals: List[DailyNutritionTotal] = []
    issues: List[NutritionIssue] = []

    for day in plan.days:
        day_cal, day_prot, day_carbs, day_fat = 0.0, 0.0, 0.0, 0.0

        meal_tasks = [calculate_meal_nutrition(meal) for meal in day.meals]
        meal_results = await asyncio.gather(*meal_tasks)

        for meal, meal_nut in zip(day.meals, meal_results):
            meal.calories = meal_nut["calories"]
            meal.protein_g = meal_nut["protein_g"]
            meal.carbs_g = meal_nut["carbs_g"]
            meal.fat_g = meal_nut["fat_g"]

            day_cal += meal_nut["calories"]
            day_prot += meal_nut["protein_g"]
            day_carbs += meal_nut["carbs_g"]
            day_fat += meal_nut["fat_g"]

        day_total = DailyNutritionTotal(
            day=day.day_number,
            day_number=day.day_number,
            calories=round(day_cal, 1),
            protein_g=round(day_prot, 1),
            carbs_g=round(day_carbs, 1),
            fat_g=round(day_fat, 1)
        )
        daily_totals.append(day_total)

        # Update day in plan
        day.total_calories = day_total.calories
        day.total_protein_g = day_total.protein_g
        day.total_carbs_g = day_total.carbs_g
        day.total_fat_g = day_total.fat_g

        # Check tolerance (±5%)
        diff = round(day_total.calories - target_calories, 1)
        allowed_delta = target_calories * tolerance_pct

        if abs(diff) > allowed_delta:
            action = "Reduce portion sizes for dense carbs/fats" if diff > 0 else "Increase portion sizes or add a healthy snack"
            issues.append(NutritionIssue(
                day_number=day.day_number,
                meal_type=None,
                diff_calories=diff,
                message=f"Day {day.day_number} is {abs(diff):.0f} kcal {'over' if diff > 0 else 'under'} target ({day_total.calories:.0f} vs {target_calories:.0f} kcal).",
                suggested_action=action
            ))

    # Update plan average
    if daily_totals:
        plan.average_daily_calories = round(sum(d.calories for d in daily_totals) / len(daily_totals), 1)

    passed = len(issues) == 0
    return passed, issues, daily_totals

