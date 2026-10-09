from typing import List, Dict, Any
from collections import defaultdict
from backend.app.schemas.domain import Meal

def merge_ingredients(meals: List[Meal]) -> List[Dict[str, Any]]:
    """
    Code tool that merges identical ingredients across all meals and days,
    summing up the total quantities in grams.
    Returns: List of dicts with ingredient name and total_grams.
    The Shopper Agent subsequently loops through these items to query prices
    and compute costs.
    """
    totals_grams: Dict[str, float] = defaultdict(float)

    for meal in meals:
        for ing in meal.ingredients:
            clean_name = ing.name.strip().lower()
            if clean_name:
                totals_grams[clean_name] += ing.amount_grams

    merged_items: List[Dict[str, Any]] = []
    for name, grams in sorted(totals_grams.items()):
        merged_items.append({
            "name": name,
            "total_grams": round(grams, 1),
            "display_unit": "kg" if grams >= 1000 else "g"
        })

    return merged_items
