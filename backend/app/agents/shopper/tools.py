from typing import Dict, Callable, List, Any
from backend.app.tools.shopping import merge_ingredients as _base_merge
from backend.app.tools.prices.mandi import get_price
from backend.app.schemas.domain import Meal

def merge_ingredients_tool(meals: Any = None, **kwargs) -> List[Dict[str, Any]]:
    """
    Consolidates identical ingredients across meals into a single list with total grams.
    """
    raw_meals = meals or kwargs.get("ingredients") or kwargs.get("data") or kwargs.get("items") or []
    if raw_meals and isinstance(raw_meals, list) and len(raw_meals) > 0:
        first = raw_meals[0]
        if isinstance(first, dict) and ("amount_grams" in first or "grams" in first) and "ingredients" not in first:
            from collections import defaultdict
            totals_grams = defaultdict(float)
            for item in raw_meals:
                name = (item.get("name") or "").strip().lower()
                grams = float(item.get("amount_grams") or item.get("grams") or 0.0)
                if name:
                    totals_grams[name] += grams
            return [{"name": n, "total_grams": round(g, 1), "display_unit": "kg" if g >= 1000 else "g"} for n, g in sorted(totals_grams.items())]

    meal_objs: List[Meal] = []
    for m in raw_meals:
        if isinstance(m, Meal):
            meal_objs.append(m)
        elif isinstance(m, dict):
            try:
                meal_objs.append(Meal.model_validate(m))
            except Exception:
                pass
    return _base_merge(meal_objs)

async def get_price_tool(item_name: str) -> Dict[str, Any]:
    """
    Queries wholesale commodity Mandi / CEDA prices and converts to retail price per kg.
    """
    return await get_price(item_name)

SHOPPER_TOOLS: Dict[str, Callable] = {
    "merge_ingredients": merge_ingredients_tool,
    "get_price": get_price_tool,
}
