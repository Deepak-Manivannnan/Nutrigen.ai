from typing import Dict, Any, Tuple
from backend.app.config import settings

def calculate_mifflin_st_jeor(
    age: int,
    sex: str,
    height_cm: float,
    weight_kg: float,
    activity_level: str,
    goal: str = "maintain"
) -> Dict[str, Any]:
    """
    Computes BMR and TDEE using the Mifflin-St Jeor equation and calculates healthy calorie boundaries.
    """
    # BMR calculation
    clean_sex = sex.lower()
    if clean_sex == "male":
        bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age + 5.0
    elif clean_sex == "female":
        bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age - 161.0
    else:
        # Neutral midpoint
        bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age - 78.0

    # Activity multiplier
    activity_multipliers = {
        "low": 1.375,      # Lightly active / sedentary
        "medium": 1.55,    # Moderately active (moderate exercise 3-5 days/week)
        "high": 1.725      # Very active (hard exercise 6-7 days/week)
    }
    mult = activity_multipliers.get((activity_level or "").lower(), 1.55)
    tdee = bmr * mult

    # Goal adjustment
    clean_goal = (goal or "maintain").lower()
    if clean_goal == "lose":
        target = tdee - 300.0
    elif clean_goal == "gain":
        target = tdee + 300.0
    else:
        target = tdee

    # Round target to nearest 50 kcal
    suggested_target = round(target / 50.0) * 50.0

    # Clinically recommended 200 kcal span (±100 kcal around target)
    min_safe = suggested_target - 100.0
    max_safe = suggested_target + 100.0

    # Ensure boundaries respect adult minimum and maximum guidelines
    min_safe = max(float(settings.min_calories_adult), min_safe)
    max_safe = min(float(settings.max_calories_adult), max_safe)

    return {
        "is_eligible": True,
        "bmr": round(bmr, 0),
        "tdee": round(tdee, 0),
        "min_safe_calories": round(min_safe, 0),
        "max_safe_calories": round(max_safe, 0),
        "suggested_target": round(suggested_target, 0),
        "message": None
    }
