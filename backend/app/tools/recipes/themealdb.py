import httpx
from typing import List, Dict, Any, Optional
from backend.app.tools.units import convert_to_grams

import httpx
from typing import List, Dict, Any, Optional
from backend.app.tools.units import convert_to_grams
from backend.app.tools.diet_rules import is_liquid_ingredient, get_ingredient_unit, check_diet_compliance

async def search_recipes(query: str, diet_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Searches TheMealDB API for recipes matching the query.
    1. Extracts ingredients, scales batch recipes to 1 single-person serving (divided by 4).
    2. Tags liquid items with 'ml' and solid items with 'g'.
    3. Filters out non-vegetarian recipes if diet_type is veg or vegan.
    4. Extracts clean cooking instructions.
    """
    clean_query = query.strip().lower()
    if not clean_query:
        return []

    url = f"https://www.themealdb.com/api/json/v1/1/search.php?s={clean_query}"
    results: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                meals = data.get("meals") or []
                for m in meals:
                    recipe_name = m.get("strMeal", "Recipe")
                    ingredients = []
                    ing_names = []
                    for i in range(1, 21):
                        ing_name = m.get(f"strIngredient{i}")
                        measure = m.get(f"strMeasure{i}")
                        if ing_name and ing_name.strip():
                            raw_name = ing_name.strip().lower()
                            raw_grams = convert_to_grams(measure or "")
                            # Scale 4-serving batch recipes down to 1 single-person portion
                            single_serving_grams = max(5.0, round(raw_grams / 4.0, 1)) if raw_grams > 0 else 50.0
                            unit = get_ingredient_unit(raw_name)

                            ingredients.append({
                                "name": raw_name,
                                "amount_grams": single_serving_grams,
                                "unit": unit
                            })
                            ing_names.append(raw_name)

                    # Filter out non-compliant recipes if diet_type is vegetarian or vegan
                    if diet_type:
                        violations = check_diet_compliance(diet_type, ing_names + [recipe_name.lower()])
                        if violations:
                            continue

                    raw_instructions = m.get("strInstructions") or ""
                    clean_instructions = raw_instructions.strip()

                    results.append({
                        "recipe_name": recipe_name,
                        "cuisine": m.get("strArea", "General"),
                        "source": "api",
                        "ingredients": ingredients,
                        "instructions": clean_instructions
                    })
    except Exception:
        pass

    return results
