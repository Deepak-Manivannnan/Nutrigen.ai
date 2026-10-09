import re

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

    # Direct numeric match
    try:
        val = float(clean)
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
        return round(quantity, 1)

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

    # If unit unrecognized, return numeric quantity directly
    return round(quantity, 1)
