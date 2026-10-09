import httpx
from typing import List, Dict, Any, Optional
from backend.app.tools.units import convert_to_grams

async def search_recipes(query: str) -> List[Dict[str, Any]]:
    """
    Searches TheMealDB API for recipes matching the query.
    Extracts ingredients and converts quantities to grams using convert_to_grams.
    If no recipes are found, returns an empty list so the Planner Agent can observe
    and generate AI recipes as planned in the architecture.
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
                    ingredients = []
                    for i in range(1, 21):
                        ing_name = m.get(f"strIngredient{i}")
                        measure = m.get(f"strMeasure{i}")
                        if ing_name and ing_name.strip():
                            grams = convert_to_grams(measure or "")
                            ingredients.append({
                                "name": ing_name.strip().lower(),
                                "amount_grams": grams
                            })

                    results.append({
                        "recipe_name": m.get("strMeal"),
                        "cuisine": m.get("strArea", "General"),
                        "source": "api",
                        "ingredients": ingredients,
                        "instructions": m.get("strInstructions", "")
                    })
    except Exception:
        pass

    return results
