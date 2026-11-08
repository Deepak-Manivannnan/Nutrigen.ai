from typing import List, Dict, Any
from collections import defaultdict
from backend.app.schemas.domain import Meal


def merge_key(name: str) -> str:
    """Key that treats singular and plural as the same item.

    "bell peppers" and "bell pepper" (or "tomatoes" and "tomato") merge;
    different foods ("green bell pepper" vs "bell pepper") stay separate.
    Only the LAST word is singularised, and only by safe rules.
    """
    words = name.strip().lower().split()
    if not words:
        return ""
    w = words[-1]
    if len(w) > 3 and not w.endswith(("ss", "us", "is")):
        if w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "y"
        elif w.endswith("oes"):
            w = w[:-2]
        elif w.endswith(("ches", "shes", "xes")):
            w = w[:-2]
        elif w.endswith("s"):
            w = w[:-1]
    words[-1] = w
    return " ".join(words)


def merge_named_amounts(pairs) -> List[Dict[str, Any]]:
    """(name, grams) pairs -> one line per item, plurals merged.

    The name shown is the one used most often (first seen on a tie).
    """
    totals: Dict[str, float] = defaultdict(float)
    names: Dict[str, Dict[str, int]] = defaultdict(dict)
    for raw_name, grams in pairs:
        clean = (raw_name or "").strip().lower()
        if not clean:
            continue
        key = merge_key(clean)
        totals[key] += float(grams or 0.0)
        names[key][clean] = names[key].get(clean, 0) + 1
    merged: List[Dict[str, Any]] = []
    for key in sorted(totals):
        counts = names[key]
        shown = max(counts, key=lambda n: counts[n])
        grams = totals[key]
        merged.append({"name": shown, "total_grams": round(grams, 1), "display_unit": "kg" if grams >= 1000 else "g"})
    return merged


def merge_ingredients(meals: List[Meal]) -> List[Dict[str, Any]]:
    """
    Code tool that merges identical ingredients across all meals and days
    (singular and plural count as one), summing the quantities in grams.
    """
    return merge_named_amounts(
        (ing.name, ing.amount_grams) for meal in meals for ing in meal.ingredients
    )
