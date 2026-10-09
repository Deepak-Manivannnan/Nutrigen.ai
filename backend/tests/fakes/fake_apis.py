from typing import Dict, Any, List

FAKE_USDA_DATA: Dict[str, Dict[str, Any]] = {
    "apple": {"food_name": "apple", "found": True, "source": "fake_usda", "calories": 52.0, "protein_g": 0.3, "carbs_g": 14.0, "fat_g": 0.2},
    "rice": {"food_name": "rice", "found": True, "source": "fake_usda", "calories": 130.0, "protein_g": 2.7, "carbs_g": 28.0, "fat_g": 0.3},
    "egg": {"food_name": "egg", "found": True, "source": "fake_usda", "calories": 143.0, "protein_g": 12.6, "carbs_g": 0.7, "fat_g": 9.5},
    "chicken": {"food_name": "chicken", "found": True, "source": "fake_usda", "calories": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
    "milk": {"food_name": "milk", "found": True, "source": "fake_usda", "calories": 42.0, "protein_g": 3.4, "carbs_g": 5.0, "fat_g": 1.0}
}

FAKE_MEALDB_RECIPES: Dict[str, List[Dict[str, Any]]] = {
    "chicken": [
        {
            "recipe_name": "Grilled Chicken Salad",
            "cuisine": "American",
            "source": "api",
            "ingredients": [
                {"name": "chicken breast", "amount_grams": 200.0},
                {"name": "lettuce", "amount_grams": 100.0},
                {"name": "olive oil", "amount_grams": 15.0}
            ],
            "instructions": "Grill chicken and serve over chopped lettuce."
        }
    ],
    "veg": [
        {
            "recipe_name": "Lentil Soup",
            "cuisine": "Indian",
            "source": "api",
            "ingredients": [
                {"name": "lentils", "amount_grams": 100.0},
                {"name": "tomato", "amount_grams": 80.0},
                {"name": "onion", "amount_grams": 50.0}
            ],
            "instructions": "Boil lentils with vegetables and spices."
        }
    ]
}

FAKE_MANDI_PRICES: Dict[str, Dict[str, Any]] = {
    "wheat": {"item": "wheat", "matched_commodity": "Wheat", "found": True, "price_per_kg": 30.0, "price_source": "mandi_api"},
    "rice": {"item": "rice", "matched_commodity": "Rice", "found": True, "price_per_kg": 45.0, "price_source": "mandi_api"},
    "tomato": {"item": "tomato", "matched_commodity": "Tomato", "found": True, "price_per_kg": 25.0, "price_source": "mandi_api"},
    "onion": {"item": "onion", "matched_commodity": "Onion", "found": True, "price_per_kg": 28.0, "price_source": "mandi_api"}
}

async def fake_lookup_nutrition(food_name: str) -> Dict[str, Any]:
    clean = food_name.strip().lower()
    return FAKE_USDA_DATA.get(clean, {
        "food_name": food_name,
        "found": False,
        "source": "fake_usda",
        "calories": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0
    })

async def fake_search_recipes(query: str) -> List[Dict[str, Any]]:
    clean = query.strip().lower()
    for k, v in FAKE_MEALDB_RECIPES.items():
        if k in clean:
            return v
    return []

async def fake_get_price(item_name: str) -> Dict[str, Any]:
    clean = item_name.strip().lower()
    return FAKE_MANDI_PRICES.get(clean, {
        "item": item_name,
        "found": False,
        "price_per_kg": 0.0,
        "price_source": None
    })
