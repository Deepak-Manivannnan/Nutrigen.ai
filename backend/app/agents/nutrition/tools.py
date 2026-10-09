from typing import Dict, Callable
from backend.app.tools.nutrition.usda import lookup_nutrition
from backend.app.tools.nutrition.calculator import calculate_ingredient_nutrition, calculate_meal_nutrition

NUTRITION_TOOLS: Dict[str, Callable] = {
    "lookup_nutrition": lookup_nutrition,
    "calculate_ingredient_nutrition": calculate_ingredient_nutrition,
    "calculate_meal_nutrition": calculate_meal_nutrition,
}
