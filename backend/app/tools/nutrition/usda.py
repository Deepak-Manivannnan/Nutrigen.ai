import httpx
import re
import logging
from typing import Dict, Any, Optional, List
from backend.app.config import settings
from backend.app.tools.nutrition.staples import get_staple_nutrition

logger = logging.getLogger("tools.nutrition.usda")

# In-memory runtime session cache for identical USDA API queries during execution
_runtime_cache: Dict[str, Dict[str, Any]] = {}

# Search-term overrides supplied by the Nutrition Agent for the current audit.
#
# The agent looks at the ingredient list and rewrites compound or regional dish
# names into a single term USDA can actually resolve ("masala poha" ->
# "poha", not "cape-gooseberries"). Applied only AFTER the staple table has
# missed, so the curated high-fidelity entries always win, and cleared as soon
# as the audit finishes so it can never leak into another plan.
_ingredient_aliases: Dict[str, str] = {}

# Per-100g macro values supplied by the Nutrition Agent for ingredients that
# USDA cannot supply at all. Last resort only: checked after the staple table
# and after any search-term rewrite, and always flagged with `estimated: True`
# so the plan can show which figures are measured and which are inferred.
_ingredient_estimates: Dict[str, Dict[str, Any]] = {}


def set_ingredient_aliases(aliases: Dict[str, str]) -> None:
    """Install search-term overrides for the duration of one nutrition audit."""
    _ingredient_aliases.clear()
    for key, value in (aliases or {}).items():
        k = str(key).strip().lower()
        v = str(value).strip().lower()
        if k and v and k != v:
            _ingredient_aliases[k] = v


def set_ingredient_estimates(estimates: Dict[str, Dict[str, Any]]) -> None:
    """Install agent-supplied macros for ingredients with no usable USDA record."""
    _ingredient_estimates.clear()
    for key, value in (estimates or {}).items():
        k = str(key).strip().lower()
        if not k or not value:
            continue
        try:
            cal = float(value.get("calories", 0.0))
            pro = float(value.get("protein_g", 0.0))
            carb = float(value.get("carbs_g", 0.0))
            fat = float(value.get("fat_g", 0.0))
        except (TypeError, ValueError):
            continue
        # A food with no calories is not a food. Reject rather than silently
        # registering a zero that would make the meal look free.
        if cal <= 0 or cal > 900:
            continue
        _ingredient_estimates[k] = {
            "calories": round(cal, 1),
            "protein_g": round(max(pro, 0.0), 1),
            "carbs_g": round(max(carb, 0.0), 1),
            "fat_g": round(max(fat, 0.0), 1),
            "basis": str(value.get("basis") or "as listed"),
        }


def clear_ingredient_aliases() -> None:
    _ingredient_aliases.clear()
    _ingredient_estimates.clear()


def get_ingredient_aliases() -> Dict[str, str]:
    """Current overrides, for logging and tests."""
    return dict(_ingredient_aliases)


PENALTY_MODIFIERS = [
    "spread", "flour", "powder", "seasoning", "flavor", "babyfood", 
    "infant", "candy", "sweetened", "beverage", "soup", "fast foods", 
    "fat, ", "substitute", "imitation", "white only", "yolk only"
]

# Words that name a food the query never asked for. A record carrying one of
# these is a different food even when it shares vocabulary with the query -
# 'black olives' was matching 'Olive loaf, pork', and SR Legacy's data-type
# bonus was enough to win it.
FOREIGN_FOOD_WORDS = {
    "pork", "beef", "chicken", "turkey", "lamb", "veal", "bacon", "ham",
    "sausage", "salami", "pepperoni", "prosciutto", "hotdog", "frankfurter",
    "candy", "syrup", "soda", "cola", "coke", "fizzy", "fudge", "caramel",
    "loaf", "sandwich", "burger", "doughnut", "donut", "cake", "cookie",
    "biscuit", "brownie", "muffin", "pie", "cupcake", "tart", "pudding",
    "juice", "drink", "shake", "sherbet", "popsicle", "crock", "stew", "casserole",
    "cream filling", "jelly filling", "filling",
    # clearly packaged / confectionery, which is what a dish name most often
    # collides with: 'beetroot poriyal' -> 'ROASTED BEETROOT SEEDED SNACKERS'
    "snacker", "cracker", "chip", "crisp", "pretzel", "fudge", "caramel",
    "marshmallow", "lollipop", "gummi", "waffle cone", "nougat", "toffee",
}

# Words too generic to identify a food on their own; they only count toward
# overlap when the query also contains them.
STOPWORDS = {"with", "and", "the", "for", "from", "raw", "cooked", "roasted",
             "boiled", "fresh", "whole", "dried", "canned", "frozen", "mixed",
             "without", "made", "including", "plus", "other", "all"}

_DATATYPE_BONUS = {"Foundation": 12.0, "SR Legacy": 8.0, "Survey (FNDDS)": 6.0}


def _content_tokens(text: str) -> set:
    """Lower-cased, de-pluralised, stopword-free word set."""
    cleaned = re.sub(r"[^a-z ]", " ", (text or "").lower())
    out = set()
    for word in cleaned.split():
        if word in STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        if len(word) > 2:
            out.add(word)
    return out


def _token_overlap(query: str, desc: str) -> float:
    """Fraction of the query's meaningful words present in the description.

    Word-set based, so word order and USDA's commas do not matter:
    'black olives' matches 'Olives, black' fully.
    """
    q = _content_tokens(query)
    if not q:
        return 0.0
    return len(q & _content_tokens(desc)) / len(q)


def _score_food_candidate(query: str, desc: str, data_type: str) -> float:
    """
    Ranks USDA candidates for one ingredient.

    Relevance leads: how much of what was asked for actually appears in the
    candidate. The data type only breaks ties, because a laboratory-verified
    record about the wrong food is still the wrong food. Foreign food words
    subtract heavily, which is what stops a vegetarian plan from matching a
    pork product.

    Returned alongside every lookup as `match_quality`, so a caller can tell a
    confident match from a guess and escalate only the guesses.
    """
    q_lower = query.lower().strip()
    d_lower = desc.lower().strip()

    score = 100.0 + 60.0 * _token_overlap(query, desc)

    if d_lower == q_lower:
        score += 40.0
    elif d_lower.startswith(q_lower):
        score += 15.0

    # Laboratory-verified data is preferred, but only among comparable matches.
    score += _DATATYPE_BONUS.get(data_type, 4.0)

    # Penalize unwanted modifiers UNLESS explicitly queried
    for mod in PENALTY_MODIFIERS:
        if mod in d_lower and mod not in q_lower:
            score -= 50.0

    # A candidate that names a food the query never mentioned is a different
    # food, however well it shares vocabulary.
    asked = _content_tokens(query)
    for word in _content_tokens(desc) - asked:
        if word in FOREIGN_FOOD_WORDS:
            score -= 45.0

    # Reward whole food descriptors
    for positive in ["raw", "cooked", "roasted", "boiled", "fresh", "whole"]:
        if positive in d_lower:
            score += 10.0

    return score


def match_quality(query: str, desc: str) -> float:
    """Public confidence for a lookup: 0.0 (unrelated) .. 1.0 (exact words).

    Used to decide which ingredients are worth sending to the agent for a
    rewritten search term. A record that names a food the query never mentioned
    scores far lower even when it shares vocabulary: 'beetroot poriyal' shares
    the word 'beetroot' with 'ROASTED BEETROOT SEEDED SNACKERS', which is one
    word out of two, but the record is a packaged snack rather than the dish.
    """
    q = _content_tokens(query)
    if not q:
        return 0.0
    d = _content_tokens(desc)
    quality = len(q & d) / len(q)
    if (d - q) & FOREIGN_FOOD_WORDS:
        quality *= 0.4
    return quality


def _has_nutrients(result: Dict[str, Any]) -> bool:
    """True when a lookup actually returned usable macro values.

    A rewritten search term can land on a USDA record whose nutrient names do
    not match the parser (observed: 'tortilla wrap' -> a corn tortilla record
    that yielded 0 kcal). Silently returning 0 would make the meal look free
    and understate the plan's calories, so an empty result is discarded.
    """
    return any(
        float(result.get(k) or 0.0) > 0
        for k in ("calories", "protein_g", "carbs_g", "fat_g")
    )


async def lookup_nutrition(food_name: str, _use_alias: bool = True) -> Dict[str, Any]:
    """
    Looks up calories, protein, carbs, and fat per 100g.
    First checks the verified standard staple baseline (preventing ambiguities like
    'chicken' resolving to chicken spread, or 'egg' resolving to egg white).
    Falls back to a skeptically ranked USDA FoodData Central query.
    """
    clean_name = food_name.strip().lower()
    if not clean_name:
        return {"food_name": food_name, "found": False, "calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}

    # 1. High-Fidelity Staple Baseline Check.
    #    Checked first and never overridden: these entries are curated exactly,
    #    so a model rewrite must not be allowed to downgrade one.
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
            "fat_g": staple["fat_g"],
            "match_quality": 1.0
        }
        _runtime_cache[clean_name] = result
        return result

    # 2. Agent-supplied search term for a dish-style name.
    #    Checked BEFORE the literal-name cache: the first, unresolved lookup for
    #    a dish name is cached under its own key and is frequently a bad match
    #    (a packaged snack, or an unrelated plant), so serving it from cache
    #    would make the agent's rewrite pointless for the rest of the run.
    # 3. Skeptical USDA Search via POST API
    search_term = clean_name
    query_name = _ingredient_aliases.get(clean_name) if _use_alias else None
    if query_name and query_name != clean_name:
        candidate: Optional[Dict[str, Any]] = None
        if query_name in _runtime_cache:
            candidate = dict(_runtime_cache[query_name])
            candidate["resolved_from"] = clean_name
        else:
            staple = get_staple_nutrition(query_name)
            if staple:
                candidate = {
                    "food_name": food_name,
                    "matched_description": query_name,
                    "resolved_from": clean_name,
                    "found": True,
                    "source": "usda_staple_baseline",
                    "calories": staple["calories"],
                    "protein_g": staple["protein_g"],
                    "carbs_g": staple["carbs_g"],
                    "fat_g": staple["fat_g"],
                    "match_quality": 1.0
                }
                _runtime_cache[query_name] = candidate

        # Only trust the rewrite if it actually carries macros. Otherwise fall
        # through and use the literal name, which is imperfect but real.
        if candidate is not None:
            if _has_nutrients(candidate):
                return candidate
            logger.warning(
                "[usda] alias '%s' -> '%s' returned no nutrients; using the "
                "literal name instead.", clean_name, query_name,
            )
        else:
            search_term = query_name

    # Skipped whenever a rewrite is in play: the literal name was cached by an
    # earlier lookup in this run, and that entry is precisely the bad match the
    # rewrite exists to correct.
    if search_term == clean_name and clean_name in _runtime_cache:
        return _runtime_cache[clean_name]

    # 3. Agent-supplied macros. Only reached for an ingredient the staple table
    #    cannot answer and whose USDA search produced nothing usable, so this is
    #    strictly the last resort and always carries `estimated: True`.
    estimate = _ingredient_estimates.get(clean_name)
    if estimate:
        result = {
            "food_name": food_name,
            "matched_description": estimate.get("basis", "agent estimate"),
            "found": True,
            "source": "agent_estimate",
            "estimated": True,
            "calories": estimate["calories"],
            "protein_g": estimate["protein_g"],
            "carbs_g": estimate["carbs_g"],
            "fat_g": estimate["fat_g"],
            "match_quality": 0.0,
        }
        _runtime_cache[clean_name] = result
        return result

    # 4. Skeptical USDA Search via POST API
    api_key = settings.usda_api_key or "DEMO_KEY"
    url = f"https://api.nal.usda.gov/fdc/v1/foods/search?api_key={api_key}"

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            # Query Foundation and SR Legacy first
            payload = {
                "query": search_term,
                "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)"],
                "pageSize": 10
            }
            resp = await client.post(url, json=payload)
            data = resp.json() if resp.status_code == 200 else {}
            foods: List[Dict[str, Any]] = data.get("foods", [])

            # Fallback to general search if Foundation/SR Legacy has no results
            if not foods:
                fallback_payload = {
                    "query": search_term,
                    "pageSize": 10
                }
                resp_fb = await client.post(url, json=fallback_payload)
                if resp_fb.status_code == 200:
                    foods = resp_fb.json().get("foods", [])

            if foods:
                # Rank candidates skeptically
                scored_foods = [
                    (_score_food_candidate(search_term, f.get("description", ""), f.get("dataType", "")), f)
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
                    "fat_g": round(fat, 1),
                    "match_quality": round(
                        match_quality(search_term, best_food.get("description", "")), 3
                    ),
                    # What else was on offer. The Nutrition Agent shows this to
                    # the model so it can judge whether the top-ranked record is
                    # genuinely the right food, instead of guessing blind.
                    "candidates": [
                        f.get("description", "") for _s, f in scored_foods[:5]
                    ],
                }
                # A rewritten term can land on a record whose nutrient names the
                # parser does not recognise (observed: 'tortilla wrap' -> a corn
                # tortilla record yielding 0 kcal). Returning that would make the
                # meal look free, so retry once with the literal name.
                if query_name and not _has_nutrients(result):
                    logger.warning(
                        "[usda] alias '%s' -> '%s' returned no nutrients (%s); "
                        "retrying with the literal name.",
                        clean_name, query_name, result.get("matched_description"),
                    )
                    _runtime_cache.pop(search_term, None)
                    return await lookup_nutrition(food_name, _use_alias=False)

                # Cached under the resolved term: the next ingredient that
                # resolves to the same food then costs nothing.
                if query_name and query_name != clean_name:
                    result["resolved_from"] = clean_name
                _runtime_cache[search_term] = result
                return result

    except Exception:
        pass

    # No match found from USDA API
    not_found_result = {
        "match_quality": 0.0,
        "food_name": food_name,
        "found": False,
        "source": "usda_api",
        "calories": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0
    }
    return not_found_result
