import re
from typing import Optional

# Standard physical unit conversions to grams
UNIT_CONVERSIONS = {
    # Weight
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
    "oz": 28.35,
    "ounce": 28.35,
    "ounces": 28.35,
    "lb": 453.59,
    "lbs": 453.59,
    "pound": 453.59,
    "pounds": 453.59,

    # Volume (standard metric/culinary approximation: 1ml ~ 1g)
    "ml": 1.0,
    "l": 1000.0,
    "liter": 1000.0,
    "liters": 1000.0,
    "cup": 240.0,
    "cups": 240.0,
    "tbsp": 15.0,
    "tablespoon": 15.0,
    "tablespoons": 15.0,
    "tsp": 5.0,
    "teaspoon": 5.0,
    "teaspoons": 5.0,

    # Count / Items (standard average)
    "piece": 50.0,
    "pieces": 50.0,
    "slice": 30.0,
    "slices": 30.0,
    "pinch": 1.0,
    "dash": 1.0,
}

def parse_fraction(s: str) -> float:
    """Parse string representations like '1 1/2' or '1/2' into float."""
    s = s.strip()
    if not s:
        return 0.0
    parts = s.split()
    total = 0.0
    for part in parts:
        if "/" in part:
            num, denom = part.split("/")
            try:
                total += float(num) / float(denom)
            except (ValueError, ZeroDivisionError):
                pass
        else:
            try:
                total += float(part)
            except ValueError:
                pass
    return total


# Average weight of ONE countable food item, in grams.
#
# TheMealDB states quantities as pieces far more often than in grams - "2
# eggs", "1 large onion", "3 cloves garlic". None of those are units, so the
# parser used to fall through and return the bare count: "2 eggs" parsed to
# 2.0, which then got divided by 4 and floored to 5g for a whole meal.
#
# These are typical edible weights (a medium egg is ~50g, a medium onion
# ~110g). They are estimates for a single person, which is what this app
# plans for.
PIECE_WEIGHTS_G = {
    # Eggs & dairy
    "egg": 50.0, "eggs": 50.0, "egg yolk": 17.0, "egg white": 33.0,
    # Produce
    "tomato": 120.0, "tomatoes": 120.0,
    "onion": 110.0, "onions": 110.0,
    "clove": 3.0, "cloves": 3.0, "garlic clove": 3.0, "garlic cloves": 3.0,
    "banana": 118.0, "bananas": 118.0,
    "apple": 180.0, "apples": 180.0,
    "lemon": 100.0, "lemons": 100.0, "lime": 60.0, "limes": 60.0,
    "potato": 150.0, "potatoes": 150.0,
    "carrot": 60.0, "carrots": 60.0,
    "capsicum": 120.0, "bell pepper": 120.0, "peppers": 120.0,
    "cucumber": 300.0, "cucumbers": 300.0,
    "spring onion": 15.0, "scallion": 15.0,
    "mushroom": 18.0, "mushrooms": 18.0,
    "bread": 30.0, "bread slice": 30.0, "slice bread": 30.0,
    "bun": 60.0, "buns": 60.0,
    "roti": 45.0, "rotis": 45.0, "chapati": 45.0, "chapatis": 45.0,
    "paneer": 100.0,
    "chicken breast": 175.0, "chicken thighs": 175.0, "chicken": 175.0,
    "chicken breast fillet": 175.0,
    "salmon fillet": 150.0, "fish fillet": 150.0, "fish": 150.0,
    "prawn": 15.0, "prawns": 15.0, "shrimp": 15.0,
    "beef": 150.0, "steak": 200.0,
    "tortilla": 45.0, "tortillas": 45.0, "wrap": 60.0, "wraps": 60.0,
    "samosa": 60.0, "samosas": 60.0,
}

# Size adjectives that may sit between the count and the noun
# ("1 large egg" -> "large" dropped, "egg" looked up).
_SIZE_WORDS = {
    "large", "small", "medium", "big", "extra", "ripe", "fresh",
    "whole", "diced", "chopped", "sliced", "minced", "grated", "shredded",
}


def _piece_weight_grams(unit_part: str) -> Optional[float]:
    """Grams per piece when `unit_part` names a countable food, else None.

    Returns None - meaning "not a piece, not a unit" - when the phrase starts
    with a real unit token. "500g beef" parses with unit_part "g beef"; without
    this guard "beef" would be read as a piece count and 500 pieces of beef
    would come back as 75000 g.
    """
    tokens = [t for t in unit_part.replace("-", " ").replace("/", " ").split() if t]
    if not tokens:
        return None

    # A leading unit token means the number is already a weight/volume.
    head = tokens[0].rstrip(".")
    if head in UNIT_CONVERSIONS or (len(head) >= 2 and head in UNIT_CONVERSIONS):
        return None

    tokens = [t for t in tokens if t not in _SIZE_WORDS]
    if not tokens:
        return None

    # Try the whole phrase, then progressively shorter tails.
    for start in range(len(tokens)):
        candidate = " ".join(tokens[start:])
        if candidate in PIECE_WEIGHTS_G:
            return PIECE_WEIGHTS_G[candidate]
        if candidate.endswith("s"):
            singular = candidate[:-1]
            if singular in PIECE_WEIGHTS_G:
                return PIECE_WEIGHTS_G[singular]

    # Finally any single token, which covers "3 cloves garlic" where the
    # countable word is first and the food name trails it.
    for token in tokens:
        if token in PIECE_WEIGHTS_G:
            return PIECE_WEIGHTS_G[token]
        if token.endswith("s") and token[:-1] in PIECE_WEIGHTS_G:
            return PIECE_WEIGHTS_G[token[:-1]]
    return None

def convert_to_grams(
    raw_amount_str: Optional[str] = None,
    quantity: Optional[str] = None,
    quantity_str: Optional[str] = None,
    amount: Optional[str] = None,
    **kwargs
) -> float:
    """
    Parses a quantity string (e.g. '2 cups', '1 1/2 tbsp', '100g', '2 slices')
    and converts standard units to grams.
    """
    target = raw_amount_str or quantity or quantity_str or amount or (str(kwargs.get("food")) if "food" in kwargs else None) or ""
    if not target or not str(target).strip():
        return 0.0

    clean = str(target).lower().strip()
    food = str(kwargs.get("food") or "").strip().lower()

    # Direct numeric match. A bare number is a count, not a weight, so if the
    # caller told us which food this is, resolve it as countable pieces first -
    # "4" chicken breasts is 700 g, not 4 g.
    try:
        val = float(clean)
        if food:
            piece = _piece_weight_grams(food)
            if piece is not None:
                return round(val * piece, 1)
        return val
    except ValueError:
        pass

    # Extract number/fraction and unit
    match = re.match(r"^([\d\s\.\/]+)\s*([a-zA-Z\s]+)?", clean)
    if not match:
        return 0.0

    num_part = match.group(1).strip()
    unit_part = match.group(2).strip().lower() if match.group(2) else ""

    quantity = parse_fraction(num_part)
    if quantity <= 0:
        quantity = 1.0

    if not unit_part:
        if food:
            piece = _piece_weight_grams(food)
            if piece is not None:
                return round(quantity * piece, 1)
        # No unit and no resolvable food: the number is not a weight.
        return 0.0

    # Exact match first (covers all explicit entries: "g", "gram", "grams", "kg", "l", "liter", etc.)
    if unit_part in UNIT_CONVERSIONS:
        return round(quantity * UNIT_CONVERSIONS[unit_part], 1)

    # Safe prefix match ONLY for multi-character keys (length >= 2) where the remaining
    # suffix is non-alphabetic (e.g. "kgs" → "kg" ✓, but "large" → "l" ✗, "lb" → "l" ✗).
    for unit_key, factor in UNIT_CONVERSIONS.items():
        if len(unit_key) >= 2 and unit_part.startswith(unit_key):
            suffix = unit_part[len(unit_key):]
            if not suffix or not suffix[0].isalpha():
                return round(quantity * factor, 1)

    # "500g beef" parses with unit_part "g beef": a leading unit token means the
    # number is already a weight and the trailing words are just the food name.
    # Handle it here so it never reaches the piece lookup below, where "beef"
    # would be misread as a count of pieces.
    lead = unit_part.split()
    if lead:
        head = lead[0].rstrip(".")
        if head in UNIT_CONVERSIONS:
            return round(quantity * UNIT_CONVERSIONS[head], 1)
        for unit_key, factor in UNIT_CONVERSIONS.items():
            if len(unit_key) >= 2 and head.startswith(unit_key):
                suffix = head[len(unit_key):]
                if not suffix or not suffix[0].isalpha():
                    return round(quantity * factor, 1)

    # Not a known unit, but it may name a countable food ("2 eggs", "1 large
    # onion"). TheMealDB measures things that way constantly, and without this
    # the parser returned the bare count - "2 eggs" became 2.0 "grams".
    piece = _piece_weight_grams(unit_part)
    if piece is not None:
        return round(quantity * piece, 1)

    # Genuinely unrecognised unit. Report 0 rather than the bare number: that
    # number is not grams, and a 0 lets callers skip the ingredient instead of
    # quietly inventing a quantity for it.
    return 0.0
