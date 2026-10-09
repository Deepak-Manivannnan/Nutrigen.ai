import re
import json
from typing import List, Dict, Any, Optional, Set

# Canonical allergen mappings for direct Layer 1 deterministic safety guard
ALLERGEN_CANONICAL_TARGETS: Dict[str, List[str]] = {
    "peanut": ["peanut", "groundnut", "arachis"],
    "peanuts": ["peanut", "groundnut", "arachis"],
    "tree nut": ["tree nut", "almond", "walnut", "cashew", "pistachio", "pecan", "hazelnut", "macadamia", "brazil nut", "pine nut", "chestnut"],
    "tree nuts": ["tree nut", "almond", "walnut", "cashew", "pistachio", "pecan", "hazelnut", "macadamia", "brazil nut", "pine nut", "chestnut"],
    "nut": ["nut", "almond", "walnut", "cashew", "pistachio", "pecan", "hazelnut", "macadamia", "brazil nut", "pine nut"],
    "nuts": ["nut", "almond", "walnut", "cashew", "pistachio", "pecan", "hazelnut", "macadamia", "brazil nut", "pine nut"],
    "dairy": ["dairy", "cow milk", "cheese", "butter", "cream", "yogurt", "curd", "paneer", "ghee", "casein", "whey"],
    "milk": ["milk", "dairy", "cheese", "butter", "cream", "yogurt", "curd", "paneer", "ghee", "casein", "whey"],
    "egg": ["egg", "eggs", "mayo", "mayonnaise", "albumen", "albumin", "meringue"],
    "eggs": ["egg", "eggs", "mayo", "mayonnaise", "albumen", "albumin", "meringue"],
    "gluten": ["gluten", "wheat", "barley", "rye", "spelt", "seitan", "semolina", "couscous", "maida", "atta"],
    "wheat": ["wheat", "gluten", "semolina", "couscous", "maida", "atta", "spelt", "seitan", "bulgur", "farina"],
    "soy": ["soy", "soya", "soybean", "edamame", "tofu", "tempeh", "miso", "tamari"],
    "soya": ["soy", "soya", "soybean", "edamame", "tofu", "tempeh", "miso", "tamari"],
    "soybeans": ["soy", "soya", "soybean", "edamame", "tofu", "tempeh", "miso", "tamari"],
    "shellfish": ["shellfish", "shrimp", "prawn", "crab", "lobster", "clam", "mussel", "oyster", "scallop", "squid", "calamari", "octopus", "crawfish"],
    "fish": ["fish", "salmon", "tuna", "cod", "tilapia", "trout", "halibut", "sardine", "anchovy", "mackerel", "bass", "snapper", "dashi"],
    "sesame": ["sesame", "tahini", "halva"],
    "mustard": ["mustard"],
    "coconut": ["coconut"],
}

# Known false-positive exemptions for substring / linguistic overlaps
FALSE_POSITIVE_EXCLUSIONS: Dict[str, Set[str]] = {
    "egg": {"eggplant", "eggplants", "aubergine"},
    "eggs": {"eggplant", "eggplants", "aubergine"},
    "nut": {"butternut", "butternut squash", "nutmeg", "water chestnut", "water chestnuts", "donut", "doughnut"},
    "nuts": {"butternut", "butternut squash", "nutmeg", "water chestnut", "water chestnuts", "donut", "doughnut"},
    "tree nut": {"butternut", "butternut squash", "nutmeg", "water chestnut", "water chestnuts"},
    "tree nuts": {"butternut", "butternut squash", "nutmeg", "water chestnut", "water chestnuts"},
    "pea": {"peach", "peaches", "pear", "pears"},
    "dairy": {"coconut milk", "almond milk", "oat milk", "soy milk", "soya milk", "cashew milk", "rice milk", "hemp milk", "pea milk", "peanut butter", "cacao butter", "cocoa butter", "apple butter", "shea butter"},
    "milk": {"coconut milk", "almond milk", "oat milk", "soy milk", "soya milk", "cashew milk", "rice milk", "hemp milk", "pea milk", "peanut butter", "cacao butter", "cocoa butter", "apple butter"},
}

def normalize_text(text: str) -> str:
    """Lowercases, normalizes whitespace and removes special characters."""
    return re.sub(r'[\s\-_]+', ' ', text.strip().lower())

def collapse_text(text: str) -> str:
    """Removes all spaces, hyphens, and underscores for compound/agglutinated matching."""
    return re.sub(r'[\s\-_]+', '', text.strip().lower())

def _get_targets_for_allergen(allergen: str) -> List[str]:
    clean = normalize_text(allergen)
    if clean in ALLERGEN_CANONICAL_TARGETS:
        return ALLERGEN_CANONICAL_TARGETS[clean]
    
    # Generic singularization fallback for custom user allergens (e.g. strawberries -> strawberry)
    targets = [clean]
    if clean.endswith("ies") and len(clean) > 3:
        targets.append(clean[:-3] + "y")
    elif clean.endswith("s") and len(clean) > 2:
        targets.append(clean[:-1])
    return targets

def check_direct_allergen_match(clean_allergy: str, ingredient_name: str) -> Optional[Dict[str, Any]]:
    """
    Deterministic code check (Layer 1 of Hybrid Architecture).
    Checks if ingredient violates clean_allergy with smart boundary matching,
    compound spacing tolerance ('coconutoil' vs 'coconut oil'), and false-positive exemptions.
    """
    clean_ing = normalize_text(ingredient_name)
    collapsed_ing = collapse_text(ingredient_name)
    targets = _get_targets_for_allergen(clean_allergy)

    # Check false positive exclusions first
    exclusions = FALSE_POSITIVE_EXCLUSIONS.get(clean_allergy, set())
    for exc in exclusions:
        if exc in clean_ing or collapse_text(exc) == collapsed_ing:
            return None

    for target in targets:
        clean_target = normalize_text(target)
        collapsed_target = collapse_text(target)

        # 1. Exact or whole-word token match (e.g., 'peanut', 'roasted peanuts', 'cow milk')
        word_pattern = r'\b' + re.escape(clean_target) + r'(?:s|es)?\b'
        if re.search(word_pattern, clean_ing):
            return {
                "ingredient": ingredient_name,
                "allergen": clean_allergy,
                "reason": f"Deterministic Code Guard: '{ingredient_name}' directly matches allergen target '{clean_target}' for allergy '{clean_allergy}'."
            }

        # 2. Collapsed compound match (e.g., 'coconutoil' -> 'coconut', 'peanutbutter' -> 'peanut')
        # Only apply collapsed matching if target is at least 3 characters to avoid noisy collisions
        if len(collapsed_target) >= 3 and collapsed_target in collapsed_ing:
            # Re-verify that the matched part isn't an excluded token
            is_excluded = any(exc in clean_ing for exc in exclusions)
            if not is_excluded:
                return {
                    "ingredient": ingredient_name,
                    "allergen": clean_allergy,
                    "reason": f"Deterministic Code Guard: '{ingredient_name}' compound term matches allergen '{clean_allergy}' (target '{clean_target}')."
                }

    return None

async def check_allergens(
    user_allergies: List[str] | str,
    ingredients: List[str] | str,
    session: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Hybrid Layer 1 Deterministic Checker.
    Safely coerces input strings or lists, performs zero-maintenance normalized code matching,
    and returns violations without requiring database tables.
    """
    # Coerce user_allergies if passed as JSON string or comma-separated string by LLM tool calls
    if isinstance(user_allergies, str):
        try:
            parsed = json.loads(user_allergies)
            clean_allergies_list = parsed if isinstance(parsed, list) else [user_allergies]
        except Exception:
            clean_allergies_list = [a.strip() for a in user_allergies.split(",") if a.strip()]
    else:
        clean_allergies_list = list(user_allergies or [])

    # Coerce ingredients if passed as string
    if isinstance(ingredients, str):
        try:
            parsed = json.loads(ingredients)
            clean_ingredients_list = parsed if isinstance(parsed, list) else [ingredients]
        except Exception:
            clean_ingredients_list = [i.strip() for i in ingredients.split(",") if i.strip()]
    else:
        clean_ingredients_list = list(ingredients or [])

    if not clean_allergies_list or not clean_ingredients_list:
        return []

    violations: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for ing in clean_ingredients_list:
        if not ing:
            continue
        for allergen in clean_allergies_list:
            clean_a = normalize_text(allergen)
            if not clean_a:
                continue
            violation = check_direct_allergen_match(clean_a, ing)
            if violation:
                key = f"{ing.lower()}::{clean_a}"
                if key not in seen:
                    seen.add(key)
                    violations.append(violation)
                break

    return violations

async def lookup_ingredient(ingredient_name: str) -> Dict[str, Any]:
    """
    Ingredient inspection tool for compound/packaged items.
    Allows the Allergy Agent to inspect ingredient structure and potential culinary components.
    """
    clean = normalize_text(ingredient_name)
    return {
        "ingredient": ingredient_name,
        "normalized": clean,
        "note": f"Inspected '{ingredient_name}'. Use culinary domain reasoning for hidden derivatives (e.g. dashi -> fish, satay -> peanut, ghee -> dairy, semolina -> gluten)."
    }
