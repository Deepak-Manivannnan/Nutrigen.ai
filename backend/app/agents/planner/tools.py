from typing import Dict, Callable
from backend.app.tools.recipes.themealdb import search_recipes
from backend.app.tools.units import convert_to_grams

PLANNER_TOOLS: Dict[str, Callable] = {
    "search_recipes": search_recipes,
    "convert_to_grams": convert_to_grams,
}
