from typing import Dict, Callable, List, Any
from backend.app.tools.allergens import check_allergens, lookup_ingredient
async def check_allergens_tool(user_allergies: Any, ingredients: Any) -> List[Dict[str, Any]]:
    """
    Checks ingredients against user's allergies using Layer 1 deterministic matching.
    Returns any violations detected.
    """
    return await check_allergens(user_allergies, ingredients)

async def lookup_ingredient_tool(ingredient_name: str) -> Dict[str, Any]:
    """
    Inspects an unclear or compound ingredient to determine common culinary components.
    """
    return await lookup_ingredient(ingredient_name)

ALLERGY_TOOLS: Dict[str, Callable] = {
    "check_allergens": check_allergens_tool,
    "lookup_ingredient": lookup_ingredient_tool,
}
