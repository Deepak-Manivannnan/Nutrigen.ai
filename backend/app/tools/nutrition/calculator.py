import asyncio
from typing import Dict, Any, List, Tuple
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, NutritionIssue, DailyNutritionTotal
from backend.app.tools.nutrition.usda import lookup_nutrition

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
        # Fallback to meal-level numbers if ingredients list is not detailed
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
    Computes exact day-by-day calories and macros from ingredients, evaluates tolerance (±5%),
    and generates issues with exact diff_calories without relying on LLM mental math.
    """
    daily_totals: List[DailyNutritionTotal] = []
    issues: List[NutritionIssue] = []

    for day in plan.days:
        day_cal, day_prot, day_carbs, day_fat = 0.0, 0.0, 0.0, 0.0

        meal_tasks = [calculate_meal_nutrition(meal) for meal in day.meals]
        meal_results = await asyncio.gather(*meal_tasks)

        for meal, meal_nut in zip(day.meals, meal_results):
            # Update meal object with verified numbers
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

