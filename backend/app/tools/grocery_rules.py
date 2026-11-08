"""
Plain rules for the grocery list (no AI): which group an item belongs to,
the smallest amount a shop sells, and which items are pantry staples.

Matching is on whole words, checked from the most specific rule down, so
"green bell pepper" is produce while "black pepper" is a spice.
"""
import re
from typing import Optional

# Most specific first. Each entry: (category, words or phrases).
_CATEGORY_RULES = [
    ("spices", ["black pepper", "white pepper", "pepper powder", "chilli powder", "chili powder", "red chilli powder",
                "garam masala", "turmeric", "cumin", "jeera", "coriander powder", "coriander seeds", "mustard seeds",
                "fenugreek seeds", "cardamom", "cinnamon", "clove", "cloves", "bay leaf", "asafoetida", "hing",
                "paprika", "oregano", "chaat masala", "sambar powder", "curry powder", "nutmeg", "saffron",
                "fennel seeds", "ajwain", "salt", "black salt", "spice", "masala"]),
    ("produce", ["bell pepper", "capsicum", "green chilli", "green chili", "chilli", "chili", "garlic", "ginger",
                 "onion", "tomato", "potato", "sweet potato", "carrot", "spinach", "palak", "broccoli", "cauliflower",
                 "cabbage", "peas", "beans", "okra", "bhindi", "brinjal", "eggplant", "cucumber", "zucchini",
                 "mushroom", "lettuce", "kale", "beetroot", "radish", "pumpkin", "gourd", "lemon", "lime",
                 "coriander leaves", "cilantro", "mint", "curry leaves", "spring onion", "apple", "banana", "mango",
                 "orange", "papaya", "berries", "strawberry", "grapes", "pomegranate", "guava", "pineapple",
                 "watermelon", "avocado", "vegetable", "fruit", "sprouts"]),
    ("dairy", ["milk", "paneer", "curd", "yogurt", "yoghurt", "cheese", "butter", "ghee", "cream", "buttermilk"]),
    ("proteins", ["chicken", "fish", "egg", "eggs", "tofu", "soya", "soy chunks", "tempeh", "mutton", "meat",
                  "prawn", "shrimp", "peanut butter", "protein powder", "whey", "seeds", "nuts", "almond", "peanut", "walnut"]),
    ("grains", ["rice", "wheat", "oats", "flour", "atta", "roti", "bread", "dal", "lentil", "lentils", "rajma",
                "chana", "chickpeas", "chickpea", "quinoa", "gram", "poha", "semolina", "rava", "suji", "millet", "ragi", "pasta", "noodles",
                "vermicelli", "cornflakes", "muesli"]),
    ("pantry", ["oil", "sugar", "jaggery", "honey", "vinegar", "sauce", "syrup", "dressing", "ketchup", "jam",
                "baking", "cocoa", "coconut milk", "stock", "broth"]),
]

# Smallest amount you can usually buy (grams). Used only when the plan needs
# less; the cost is then for the pack. Keyed like the category rules.
_PACK_GRAMS = [
    (["salt", "black salt"], 500),
    (["oil", "ghee"], 500),
    (["ginger", "garlic", "green chilli", "green chili", "chilli", "chili", "lemon", "lime"], 100),
    (["coriander leaves", "cilantro", "mint", "curry leaves", "spring onion"], 100),
    (["spice", "masala", "turmeric", "cumin", "jeera", "powder", "seeds", "cardamom", "cinnamon", "clove",
      "cloves", "bay leaf", "asafoetida", "hing", "paprika", "oregano", "nutmeg", "ajwain"], 50),
    (["sauce", "vinegar", "honey", "syrup", "ketchup", "jam"], 200),
]

# Most kitchens already have these: listed, but kept out of the main total.
_STAPLES = ["salt", "black salt", "oil", "ghee", "sugar", "jaggery", "vinegar", "sauce", "black pepper",
            "garam masala", "turmeric", "cumin", "jeera", "chilli powder", "chili powder", "coriander powder",
            "mustard seeds", "cardamom", "cinnamon", "clove", "cloves", "bay leaf", "asafoetida", "hing",
            "paprika", "oregano", "chaat masala", "curry powder", "masala"]


def _has(name: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", name) is not None


def _first_match(name: str, phrases) -> Optional[str]:
    # Longest phrase first, so "black pepper" wins over "pepper".
    for p in sorted(phrases, key=len, reverse=True):
        if _has(name, p):
            return p
    return None


def category_for(name: str) -> Optional[str]:
    """Group for a known item, or None to let the estimate decide."""
    n = (name or "").lower()
    best = None  # (phrase length, category) - the most specific phrase wins
    for cat, phrases in _CATEGORY_RULES:
        hit = _first_match(n, phrases)
        if hit and (best is None or len(hit) > best[0]):
            best = (len(hit), cat)
    return best[1] if best else None


def pack_grams(name: str) -> Optional[float]:
    n = (name or "").lower()
    for phrases, grams in _PACK_GRAMS:
        if _first_match(n, phrases):
            return float(grams)
    return None


def is_staple(name: str) -> bool:
    n = (name or "").lower()
    if "oil" in n.split() or n.endswith(" oil"):
        return True
    return _first_match(n, _STAPLES) is not None


def buy_amount(name: str, needed_grams: float) -> Optional[float]:
    """Grams to buy when the plan needs less than the smallest pack, else None."""
    pack = pack_grams(name)
    if pack and needed_grams < pack:
        return pack
    return None


def item_cost(grams: float, price_per_kg: float) -> float:
    """Cost of what is bought, never shown as a misleading ₹0."""
    cost = (grams / 1000.0) * price_per_kg
    if 0 < cost < 1:
        return 1.0  # minimum of ₹1 for anything you actually pay for
    return round(cost, 2)
