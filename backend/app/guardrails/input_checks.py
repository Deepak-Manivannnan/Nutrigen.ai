from typing import Dict, Any, List, Optional, Tuple
from backend.app.guardrails.calorie_rules import calculate_mifflin_st_jeor
from backend.app.config import settings

def validate_input_request(
    user_profile: Dict[str, Any],
    requested_calories: Optional[float],
    requested_days: Optional[int],
    fixed_meals: Optional[Dict[str, Any]] = None,
    requested_diet_type: Optional[str] = None
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    Input guardrail:
    1. Verifies adult age requirement (> 18).
    2. Validates days (1 to the configured maximum). A missing number of days is never
       replaced by a default: the caller asks the user.
    3. Compares requested calories with Mifflin-St Jeor safe range.
       If out of range, blocks and provides the clinically safe range.
    4. Checks fixed meals against diet type and allergies.
    5. Checks requested diet type vs profile diet type.
    """
    required_fields = ["age", "sex", "height_cm", "weight_kg", "activity_level", "diet_type"]
    for field in required_fields:
        if field not in user_profile:
            return False, f"User profile is missing required field: '{field}'", {}

    age = int(user_profile["age"])
    if age < settings.min_age_allowed:
        return (
            False,
            f"Automated meal planning is only permitted for adults ({settings.min_age_allowed}+). For individuals under 18, please consult a qualified physician or registered dietitian.",
            {}
        )

    sex = str(user_profile["sex"])
    height = float(user_profile["height_cm"])
    weight = float(user_profile["weight_kg"])
    activity = str(user_profile["activity_level"])
    goal = user_profile.get("goal") or "maintain"
    diet_type = str(user_profile["diet_type"]).lower()
    allergies = [str(a).lower() for a in user_profile.get("allergies", [])]

    # Calculate calorie info first (needed for diet conflict response)
    min_safe = user_profile.get("min_safe_calories")
    max_safe = user_profile.get("max_safe_calories")
    suggested_target = user_profile.get("suggested_target_calories")

    if min_safe is None or max_safe is None or suggested_target is None:
        cal_info = calculate_mifflin_st_jeor(age, sex, height, weight, activity, goal)
        min_safe = cal_info["min_safe_calories"]
        max_safe = cal_info["max_safe_calories"]
        suggested_target = cal_info["suggested_target"]
    else:
        cal_info = {
            "min_safe_calories": min_safe,
            "max_safe_calories": max_safe,
            "suggested_target": suggested_target
        }

    # 1. Days check
    days = requested_days if (requested_days and 1 <= requested_days <= settings.max_plan_days) else None

    # 3. Calorie range check (to get target_cal)
    target_cal = requested_calories if requested_calories is not None else suggested_target

    # Plan length check. Handled before the calorie check because there is no
    # point negotiating a daily target for a plan that will not be built.
    if requested_days and requested_days > settings.max_plan_days:
        return (
            False,
            f"Plan length: {requested_days} days exceeds the {settings.max_plan_days}-day maximum.",
            {
                "action": "days_too_many",
                "requested_days": requested_days,
                "max_days": settings.max_plan_days,
                "cal_info": cal_info,
                "days": settings.max_plan_days,
                "calorie_target": target_cal,
                "suggested_target": suggested_target,
                "min_safe": min_safe,
                "max_safe": max_safe
            }
        )

    # 2. Diet conflict check: requested diet vs profile diet
    if requested_diet_type:
        req_diet = str(requested_diet_type).lower()
        valid_diets = {"veg", "non-veg", "vegan", "vegetarian"}
        if req_diet in valid_diets and req_diet != diet_type:
            profile_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(diet_type, diet_type.capitalize())
            requested_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(req_diet, req_diet.capitalize())
            return (
                False,
                f"Diet conflict: your profile preference is **{profile_display}**, but you requested a **{requested_display}** meal plan.",
                {
                    "action": "diet_conflict",
                    "profile_diet": diet_type,
                    "requested_diet": req_diet,
                    "cal_info": cal_info,
                    "days": days,
                    "calorie_target": target_cal,
                    "suggested_target": suggested_target,
                    "min_safe": min_safe,
                    "max_safe": max_safe
                }
            )

    if target_cal < min_safe or target_cal > max_safe:
        relation = "below" if target_cal < min_safe else "above"
        if goal == "maintain":
            goal_guidance = (
                f"Since your goal is to maintain weight, your daily target should be within your maintenance range of "
                f"{int(min_safe):,} – {int(max_safe):,} kcal. Planning {relation} this threshold can "
                f"{'lead to nutritional deficiencies and fatigue' if relation == 'below' else 'lead to unintended weight gain'}."
            )
        elif goal == "gain":
            goal_guidance = (
                f"Since your goal is to gain weight, your daily target should be at or above your recommended surplus range of "
                f"{int(min_safe):,} – {int(max_safe):,} kcal to support healthy weight gain."
            )
        else: # lose
            goal_guidance = (
                f"Since your goal is to lose weight safely, your daily target should remain within {int(min_safe):,} – {int(max_safe):,} kcal "
                f"to support sustainable fat loss without muscle wasting."
            )

        return (
            False,
            f"Your requested target of {int(target_cal):,} kcal/day is not within the safe range for your goal. {goal_guidance}\n\n"
            f"Would you like me to create a balanced plan at your recommended target of {int(suggested_target):,} kcal instead?",
            {
                "cal_info": cal_info,
                "days": days,
                "calorie_target": target_cal,
                "suggested_target": suggested_target,
                "min_safe": min_safe,
                "max_safe": max_safe
            }
        )

    # 4. Check fixed meals for conflicts
    if fixed_meals:
        for meal_slot, desc in fixed_meals.items():
            desc_lower = str(desc).lower()
            # Vegan conflict check
            if diet_type == "vegan":
                if any(k in desc_lower for k in ["egg", "milk", "cheese", "paneer", "butter", "ghee", "curd", "yogurt", "chicken", "meat", "fish"]):
                    return (
                        False,
                        f"Fixed meal '{meal_slot}: {desc}' conflicts with your vegan diet preference.",
                        {"cal_info": cal_info, "days": days, "calorie_target": target_cal}
                    )
            # Veg conflict check
            elif diet_type == "veg":
                if any(k in desc_lower for k in ["chicken", "meat", "fish", "beef", "pork", "seafood", "prawn"]):
                    return (
                        False,
                        f"Fixed meal '{meal_slot}: {desc}' conflicts with your vegetarian diet preference.",
                        {"cal_info": cal_info, "days": days, "calorie_target": target_cal}
                    )

            # Allergy conflict check
            for allergen in allergies:
                if allergen in desc_lower:
                    return (
                        False,
                        f"Fixed meal '{meal_slot}: {desc}' contains '{allergen}', which is listed in your profile allergies!",
                        {"cal_info": cal_info, "days": days, "calorie_target": target_cal}
                    )

    return True, None, {
        "cal_info": cal_info,
        "days": days,
        "calorie_target": target_cal
    }

async def check_out_of_scope_query(user_message: str) -> Tuple[bool, Optional[str]]:
    """
    Semantic domain guardrail checking whether a user message is out of scope.
    Uses Supervisor LLM semantic classification instead of hardcoded keyword lists,
    ensuring arbitrary out-of-scope queries (e.g. iPhone pricing, Apple CEO, politics,
    medicine, coding, etc.) are reliably intercepted.
    """
    from backend.app.orchestration.supervisor.intent import extract_user_intent
    extraction = await extract_user_intent(user_message)
    if extraction.intent == "out_of_scope":
        return True, f"Query '{user_message}' is outside the domain of meal planning and nutrition."
    return False, None