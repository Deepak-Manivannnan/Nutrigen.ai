import re
from typing import List, Dict, Set, Tuple

# Comprehensive list of non-vegetarian items (meats, poultry, fish, seafood, and derivatives)
NON_VEG_KEYWORDS: Set[str] = {
    "turkey", "ground turkey", "turkey bacon", "turkey breast",
    "chicken", "ground chicken", "chicken breast", "chicken thigh", "poultry", "fowl",
    "beef", "ground beef", "steak", "veal", "sirloin", "brisket",
    "pork", "bacon", "ham", "pork chop", "prosciutto", "pancetta", "lard", "pork belly", "ribs",
    "mutton", "lamb", "goat meat", "goat",
    "fish", "salmon", "tuna", "cod", "tilapia", "trout", "halibut", "sardine", "sardines",
    "anchovy", "anchovies", "mackerel", "bass", "snapper", "catfish", "haddock", "swordfish",
    "shrimp", "prawn", "prawns", "crab", "lobster", "clam", "clams", "mussel", "mussels",
    "oyster", "oysters", "scallop", "scallops", "squid", "calamari", "octopus", "crawfish", "crayfish",
    "seafood", "meat", "gelatin", "duck", "goose", "quail", "venison", "rabbit",
    "sausage", "pepperoni", "salami", "chorizo", "hot dog", "hotdog", "prosciutto"
}

# Non-vegan items (meats + dairy + eggs + honey)
NON_VEGAN_ADDITIONAL: Set[str] = {
    "egg", "eggs", "egg white", "egg yolk", "mayonnaise", "mayo",
    "milk", "cow milk", "cheese", "cheddar", "mozzarella", "parmesan", "feta", "ricotta",
    "butter", "cream", "heavy cream", "sour cream", "yogurt", "curd", "paneer", "ghee",
    "casein", "whey", "honey"
}

# Known false positive exclusions where the ingredient is vegetarian/vegan despite word overlap
FALSE_POSITIVE_EXCLUSIONS: Set[str] = {
    "eggplant", "eggplants", "aubergine",
    "butternut squash", "butternut",
    "nutmeg",
    "peanut butter", "cacao butter", "cocoa butter", "apple butter", "shea butter",
    "coconut milk", "almond milk", "soy milk", "soya milk", "oat milk", "cashew milk", "rice milk",
    "jackfruit", "dragon fruit", "passion fruit",
    "water chestnut", "water chestnuts"
}

# Liquid ingredients that should be measured in milliliters (ml) instead of grams (g)
LIQUID_KEYWORDS: Set[str] = {
    "oil", "olive oil", "vegetable oil", "mustard oil", "sesame oil", "canola oil", "sunflower oil",
    "coconut oil", "avocado oil", "ghee",
    "milk", "cow milk", "soya milk", "soy milk", "almond milk", "oat milk", "coconut milk", "cashew milk",
    "water", "juice", "lemon juice", "lime juice", "orange juice", "apple juice",
    "broth", "vegetable broth", "stock", "vegetable stock",
    "vinegar", "apple cider vinegar", "white vinegar", "balsamic vinegar",
    "sauce", "soy sauce", "tamari", "hot sauce", "tomato sauce", "sriracha",
    "syrup", "maple syrup", "honey",
    "buttermilk", "dressing", "salad dressing"
}

def is_liquid_ingredient(name: str) -> bool:
    """
    Returns True if the ingredient is primarily a liquid (e.g. oils, milks, juices, sauces),
    which should be measured in milliliters (ml) rather than grams (g).
    """
    clean = name.strip().lower()
    # Check exact match
    if clean in LIQUID_KEYWORDS:
        return True
    # Check word match
    words = re.findall(r'\b[a-z]+\b', clean)
    for w in words:
        if w in {"oil", "milk", "juice", "broth", "stock", "vinegar", "syrup", "water", "buttermilk"}:
            return True
    return False

def get_ingredient_unit(name: str) -> str:
    """
    Returns 'ml' for liquids, 'g' for solids.
    """
    return "ml" if is_liquid_ingredient(name) else "g"

def check_diet_compliance(diet_type: str, ingredients: List[str]) -> List[Dict[str, str]]:
    """
    Verifies that a list of ingredient names strictly complies with the requested diet_type.
    Returns a list of violations (empty if 100% compliant).
    """
    dt = (diet_type or "").strip().lower()
    if dt in ("non-veg", "non-vegetarian", "omnivore"):
        return []

    is_vegan = dt in ("vegan",)
    is_eggetarian = dt in ("eggetarian",)
    is_veg = dt in ("veg", "vegetarian") or is_vegan or is_eggetarian

    EGG_KEYWORDS: Set[str] = {
        "egg", "eggs", "scrambled egg", "scrambled eggs", "boiled egg", "boiled eggs",
        "egg white", "egg whites", "egg yolk", "egg yolks", "mayonnaise", "mayo", "omelet", "omelette"
    }

    violations = []
    for ing in ingredients:
        clean = ing.strip().lower()
        if not clean:
            continue

        # Check false-positive exemptions first (e.g. eggplant, butternut squash, etc.)
        if any(exempt in clean for exempt in FALSE_POSITIVE_EXCLUSIONS):
            continue

        # Check non-veg keywords (meat, poultry, fish, seafood)
        matched_non_veg = None
        for term in NON_VEG_KEYWORDS:
            pattern = rf"\b{re.escape(term)}\b"
            if re.search(pattern, clean):
                matched_non_veg = term
                break

        if matched_non_veg and is_veg:
            violations.append({
                "ingredient": ing,
                "violation": f"Non-vegetarian ingredient '{matched_non_veg}' found in {dt} plan",
                "diet_type": dt
            })
            continue

        # Check eggs for standard vegetarian plans (unless explicitly eggetarian)
        if is_veg and not is_eggetarian and not is_vegan:
            matched_egg = None
            for term in EGG_KEYWORDS:
                pattern = rf"\b{re.escape(term)}\b"
                if re.search(pattern, clean):
                    matched_egg = term
                    break
            if matched_egg:
                violations.append({
                    "ingredient": ing,
                    "violation": f"Egg product '{matched_egg}' found in vegetarian plan",
                    "diet_type": dt
                })
                continue

        # Check non-vegan keywords if diet is vegan
        if is_vegan:
            matched_non_vegan = None
            for term in NON_VEGAN_ADDITIONAL:
                pattern = rf"\b{re.escape(term)}\b"
                if re.search(pattern, clean):
                    matched_non_vegan = term
                    break

            if matched_non_vegan:
                violations.append({
                    "ingredient": ing,
                    "violation": f"Animal derivative/dairy '{matched_non_vegan}' found in vegan plan",
                    "diet_type": dt
                })

    return violations
