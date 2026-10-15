import httpx
from typing import List, Dict, Any, Optional
from backend.app.tools.units import convert_to_grams
from backend.app.tools.diet_rules import is_liquid_ingredient, get_ingredient_unit, check_diet_compliance

# TheMealDB's search endpoint publishes no serving count, so a batch recipe is
# assumed to feed four people. Both the raw batch amount and a derived
# single-person estimate are handed to the planner so it can either trust the
# estimate or rescale itself.
ASSUMED_BATCH_SERVINGS = 4

# TheMealDB is inconsistent about cuisine labels: its own area list uses
# adjectives ("Indian", "American") while individual meals are sometimes tagged
# with the country ("India", "United States"). Map the forms users actually
# type - and the country spellings that turn up in the data - onto one key.
_AREA_ALIASES = {
    "south indian": "indian", "north indian": "indian",
    "india": "indian",
    "italy": "italian", "italian food": "italian",
    "usa": "american", "us": "american", "united states": "american",
    "america": "american", "american food": "american",
    "uk": "british", "england": "british", "english": "british",
    "united kingdom": "british", "british food": "british",
    "china": "chinese", "chinese food": "chinese",
    "japan": "japanese", "mexico": "mexican",
    "greece": "greek", "turkey": "turkish", "thailand": "thai",
    "spain": "spanish", "france": "french", "korea": "korean",
    "vietnam": "vietnamese", "indonesia": "indonesian",
    "portugal": "portuguese", "russia": "russian",
    "poland": "polish", "netherlands": "dutch",
    "malaysia": "malaysian", "philippines": "filipino",
    "egypt": "egyptian", "morocco": "moroccan",
    "croatia": "croatian", "ireland": "irish",
    "ukraine": "ukrainian", "jamaica": "jamaican",
    "kenya": "kenyan", "tunisia": "tunisian",
    "uruguay": "uruguayan", "canada": "canadian",
    # Not a TheMealDB area - treated as "no specific area requested".
    "mediterranean": "", "fusion": "",
}


def _normalise_area(name: Optional[str]) -> str:
    n = (name or "").strip().lower()
    return _AREA_ALIASES.get(n, n)


def _area_matches(area: Optional[str], wanted: Optional[str]) -> bool:
    """True when a recipe's area satisfies the requested cuisine."""
    w = _normalise_area(wanted)
    if not w:
        return True
    a = _normalise_area(area)
    if not a:
        return False
    if a == w:
        return True
    # "india" vs "indian", "american" vs "united states"
    return a.startswith(w) or w.startswith(a)

async def _search(
    clean_query: str,
    diet_type: Optional[str],
    cuisine: Optional[str]
) -> List[Dict[str, Any]]:
    """One TheMealDB round trip, filtered and normalised."""
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
                    area = (m.get("strArea") or "").strip()

                    # Cuisine filter: honour the request before doing any work.
                    if not _area_matches(area, cuisine):
                        continue

                    ingredients = []
                    unmeasurable: List[str] = []
                    for i in range(1, 21):
                        ing_name = m.get(f"strIngredient{i}")
                        measure = m.get(f"strMeasure{i}")
                        if not ing_name or not ing_name.strip():
                            continue
                        raw_name = ing_name.strip().lower()
                        # The ingredient name lets a bare count ("4" chicken
                        # breasts, "2" eggs) resolve to a real piece weight.
                        raw_grams = convert_to_grams(measure or "", food=raw_name)

                        if raw_grams <= 0:
                            # The measure cannot be weighed - "to taste", "a
                            # pinch", "1 tin", "6 leaves". The ingredient is
                            # still real and still needed by the recipe, so it is
                            # kept with the original wording rather than dropped.
                            # Dropping it silently made a 8-ingredient recipe
                            # look like a 4-ingredient one and gave the planner
                            # no way to tell the difference.
                            #
                            # amount_grams stays None (not 0) so a consumer can
                            # tell "unmeasurable" apart from "genuinely zero",
                            # and measure_text carries the source wording.
                            ingredients.append({
                                "name": raw_name,
                                "amount_grams": None,
                                "batch_amount_grams": None,
                                "measure_text": (measure or "").strip(),
                                "unit": get_ingredient_unit(raw_name)
                            })
                            unmeasurable.append(raw_name)
                            continue

                        single_serving_grams = max(
                            1.0, round(raw_grams / float(ASSUMED_BATCH_SERVINGS), 1)
                        )
                        unit = get_ingredient_unit(raw_name)

                        ingredients.append({
                            "name": raw_name,
                            "amount_grams": single_serving_grams,
                            "batch_amount_grams": round(raw_grams, 1),
                            "measure_text": (measure or "").strip(),
                            "unit": unit
                        })

                    if not ingredients:
                        # Nothing at all came back; not worth offering.
                        continue

                    ing_names = [i["name"] for i in ingredients]

                    # Filter out non-compliant recipes if diet_type is vegetarian or vegan
                    if diet_type:
                        violations = check_diet_compliance(diet_type, ing_names + [recipe_name.lower()])
                        if violations:
                            continue

                    raw_instructions = m.get("strInstructions") or ""
                    clean_instructions = raw_instructions.strip()

                    results.append({
                        "recipe_name": recipe_name,
                        "cuisine": area or "General",
                        "source": "api",
                        "assumed_batch_servings": ASSUMED_BATCH_SERVINGS,
                        "ingredients": ingredients,
                        "unmeasurable_ingredients": unmeasurable,
                        "instructions": clean_instructions
                    })
    except Exception:
        # TheMealDB is an optional enrichment. If it is unreachable the planner
        # still produces a plan from its own knowledge.
        pass

    return results


async def search_recipes(
    query: str,
    diet_type: Optional[str] = None,
    cuisine: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Searches TheMealDB for recipes matching the query.

    1. Converts measures to grams. An ingredient whose measure cannot be
       resolved keeps its original wording under `measure_text` and a null
       `amount_grams`, rather than being given an invented weight or dropped -
       a wrong number here becomes a wrong calorie count, and a dropped
       ingredient makes the recipe look simpler than it is.
    2. Reports both the raw batch amount and an estimated single-person amount.
    3. Tags liquid items with 'ml' and solid items with 'g'.
    4. Filters out non-vegetarian recipes when diet_type is veg or vegan.
    5. When `cuisine` is given, recipes are matched on area, with a relaxed
       retry if the strict filter matches nothing.

    TheMealDB matches on dish NAME, so a long composed phrase like "Indian
    vegetarian breakfast" finds nothing. There is exactly ONE retry: drop the
    area filter. Earlier versions also re-searched with shorter phrases, but
    each extra round trip cost several seconds and the planner agent now
    decides for itself when a lookup was not useful enough.
    """
    clean_query = (query or "").strip().lower()
    if not clean_query:
        return []

    results = await _search(clean_query, diet_type, cuisine)

    # Strict area filter matched nothing (e.g. "South Indian" has no area).
    # Drop the area filter; the search term still carries the cuisine.
    if not results and _normalise_area(cuisine):
        results = await _search(clean_query, diet_type, None)
        for rec in results:
            rec["cuisine_filter_relaxed"] = True

    return results
