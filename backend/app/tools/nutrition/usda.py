import httpx
import re
from typing import Dict, Any, Optional, List
from backend.app.config import settings
from backend.app.tools.nutrition.staples import get_staple_nutrition

# In-memory runtime session cache for identical USDA API queries during execution
_runtime_cache: Dict[str, Dict[str, Any]] = {}

PENALTY_MODIFIERS = [
    "spread", "flour", "powder", "seasoning", "flavor", "babyfood", 
    "infant", "candy", "sweetened", "beverage", "soup", "fast foods", 
    "fat, ", "substitute", "imitation", "white only", "yolk only"
]

def _score_food_candidate(query: str, desc: str, data_type: str) -> float:
    """
    Skeptically scores a USDA candidate to select the most authentic whole/cooked food,
    penalizing processed products, spreads, flours, and seasonings when searching for whole items.
    """
    q_lower = query.lower().strip()
    d_lower = desc.lower().strip()
    score = 100.0

    # Strong bonus for exact or starting match
    if d_lower == q_lower:
        score += 80.0
    elif d_lower.startswith(q_lower):
        score += 40.0

    # Foundation and SR Legacy foods are laboratory verified whole foods
    if data_type == "Foundation":
        score += 30.0
    elif data_type == "SR Legacy":
        score += 25.0
    elif data_type == "Survey (FNDDS)":
        score += 15.0

    # Penalize unwanted modifiers UNLESS explicitly queried
    for mod in PENALTY_MODIFIERS:
        if mod in d_lower and mod not in q_lower:
            score -= 50.0

    # Reward whole food descriptors
    for positive in ["raw", "cooked", "roasted", "boiled", "fresh", "whole"]:
        if positive in d_lower:
            score += 10.0

    return score

async def lookup_nutrition(food_name: str) -> Dict[str, Any]:
    """
    Looks up calories, protein, carbs, and fat per 100g.
    First checks the verified standard staple baseline (preventing ambiguities like
    'chicken' resolving to chicken spread, or 'egg' resolving to egg white).
    Falls back to a skeptically ranked USDA FoodData Central query.
    """
    clean_name = food_name.strip().lower()
    if not clean_name:
        return {"food_name": food_name, "found": False, "calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}

    if clean_name in _runtime_cache:
        return _runtime_cache[clean_name]

    # 1. High-Fidelity Staple Baseline Check
    staple = get_staple_nutrition(clean_name)
    if staple:
        result = {
            "food_name": food_name,
            "matched_description": clean_name,
            "found": True,
            "source": "usda_staple_baseline",
            "calories": staple["calories"],
            "protein_g": staple["protein_g"],
            "carbs_g": staple["carbs_g"],
            "fat_g": staple["fat_g"]
        }
        _runtime_cache[clean_name] = result
        return result

    # 2. Skeptical USDA Search via POST API
    api_key = settings.usda_api_key or "DEMO_KEY"
    url = f"https://api.nal.usda.gov/fdc/v1/foods/search?api_key={api_key}"

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            # Query Foundation and SR Legacy first
            payload = {
                "query": clean_name,
                "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)"],
                "pageSize": 10
            }
            resp = await client.post(url, json=payload)
            data = resp.json() if resp.status_code == 200 else {}
            foods: List[Dict[str, Any]] = data.get("foods", [])

            # Fallback to general search if Foundation/SR Legacy has no results
            if not foods:
                fallback_payload = {
                    "query": clean_name,
                    "pageSize": 10
                }
                resp_fb = await client.post(url, json=fallback_payload)
                if resp_fb.status_code == 200:
                    foods = resp_fb.json().get("foods", [])

            if foods:
                # Rank candidates skeptically
                scored_foods = [
                    (_score_food_candidate(clean_name, f.get("description", ""), f.get("dataType", "")), f)
                    for f in foods
                ]
                scored_foods.sort(key=lambda x: x[0], reverse=True)
                best_food = scored_foods[0][1]

                nutrients = best_food.get("foodNutrients", [])
                cal, prot, carbs, fat = 0.0, 0.0, 0.0, 0.0
                for n in nutrients:
                    n_name = n.get("nutrientName", "").lower()
                    val = float(n.get("value", 0.0))
                    if "energy" in n_name and (n.get("unitName") == "KCAL" or "kcal" in n_name):
                        cal = val
                    elif "protein" in n_name:
                        prot = val
                    elif "carbohydrate" in n_name:
                        carbs = val
                    elif "total lipid" in n_name or "fat" in n_name:
                        fat = val

                result = {
                    "food_name": food_name,
                    "matched_description": best_food.get("description", ""),
                    "found": True,
                    "source": f"usda_{best_food.get('dataType', 'api').lower().replace(' ', '_')}",
                    "calories": round(cal, 1),
                    "protein_g": round(prot, 1),
                    "carbs_g": round(carbs, 1),
                    "fat_g": round(fat, 1)
                }
                _runtime_cache[clean_name] = result
                return result

    except Exception:
        pass

    # No match found from USDA API
    not_found_result = {
        "food_name": food_name,
        "found": False,
        "source": "usda_api",
        "calories": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0
    }
    return not_found_result
