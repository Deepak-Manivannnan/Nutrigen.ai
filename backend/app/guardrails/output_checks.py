from typing import List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.schemas.domain import MealPlan, GroceryList
from backend.app.tools.allergens import check_allergens

async def verify_output_safety(
    plan: MealPlan,
    grocery_list: GroceryList,
    user_allergies: List[str],
    session: AsyncSession
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Output guardrail:
    1. Re-runs final allergy check across all meals and grocery items.
    2. Validates diet compliance against plan's declared diet type.
    3. Appends mandatory medical disclaimer.
    """
    warnings: List[str] = []

    # 1. Collect all ingredient names from all meals
    all_ingredients = []
    for day in plan.days:
        for meal in day.meals:
            for ing in meal.ingredients:
                all_ingredients.append(ing.name)

    # Also collect grocery list names
    for item in (grocery_list.items if grocery_list else []):
        all_ingredients.append(item.name)

    violations = await check_allergens(user_allergies, all_ingredients, session)
    if violations:
        reasons = [f"{v['ingredient']} ({v['reason']})" for v in violations]
        return False, [f"Critical safety failure in output guardrail: Allergen detected: {', '.join(reasons)}"], {}

    # Check diet compliance (e.g. Vegetarian/Vegan)
    plan_diet = getattr(plan, "diet_type", "veg") or "veg"
    from backend.app.tools.diet_rules import check_diet_compliance
    diet_violations = check_diet_compliance(plan_diet, all_ingredients)
    if diet_violations:
        d_reasons = [f"{dv['ingredient']} ({dv['violation']})" for dv in diet_violations]
        return False, [f"Critical safety failure in output guardrail: Dietary violation detected: {', '.join(d_reasons)}"], {}

    # 2. Check for AI fallbacks that need transparency tags
    ai_recipes_count = 0
    for day in plan.days:
        for meal in day.meals:
            if meal.source == "ai_generated":
                ai_recipes_count += 1

    ai_prices_count = sum(1 for item in grocery_list.items if item.price_source == "ai_estimate") if (grocery_list and grocery_list.items) else 0

    metadata = {
        "disclaimer": "This meal plan is AI-generated for nutritional planning purposes and does not constitute medical advice. Consult a healthcare professional or registered dietitian for specific clinical dietary requirements.",
        "ai_recipes_count": ai_recipes_count,
        "ai_prices_count": ai_prices_count
    }

    return True, warnings, metadata
