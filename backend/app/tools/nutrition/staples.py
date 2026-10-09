"""
Curated USDA Standard Reference (SR Legacy / Foundation) nutritional baseline
for common culinary staple foods per 100 grams.
Guarantees exact, deterministic nutritional values without ambiguity (e.g. preventing
'chicken' from resolving to chicken spread, or 'egg' resolving to egg white only).
"""

from typing import Dict, Any, Optional

# Nutrient values per 100g: calories (kcal), protein (g), carbs (g), fat (g)
STAPLE_NUTRITION: Dict[str, Dict[str, float]] = {
    # Proteins
    "chicken breast": {"calories": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
    "chicken": {"calories": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
    "chicken breast boneless": {"calories": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
    "chicken thigh": {"calories": 209.0, "protein_g": 24.0, "carbs_g": 0.0, "fat_g": 11.0},
    "chicken tenders": {"calories": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
    "egg": {"calories": 143.0, "protein_g": 12.6, "carbs_g": 0.7, "fat_g": 9.5},
    "eggs": {"calories": 143.0, "protein_g": 12.6, "carbs_g": 0.7, "fat_g": 9.5},
    "whole egg": {"calories": 143.0, "protein_g": 12.6, "carbs_g": 0.7, "fat_g": 9.5},
    "egg white": {"calories": 52.0, "protein_g": 10.9, "carbs_g": 0.7, "fat_g": 0.2},
    "egg yolk": {"calories": 322.0, "protein_g": 15.9, "carbs_g": 3.6, "fat_g": 26.5},
    "salmon": {"calories": 208.0, "protein_g": 20.4, "carbs_g": 0.0, "fat_g": 13.4},
    "tuna": {"calories": 130.0, "protein_g": 28.0, "carbs_g": 0.0, "fat_g": 1.0},
    "shrimp": {"calories": 99.0, "protein_g": 24.0, "carbs_g": 0.2, "fat_g": 0.3},
    "prawn": {"calories": 99.0, "protein_g": 24.0, "carbs_g": 0.2, "fat_g": 0.3},
    "tofu": {"calories": 76.0, "protein_g": 8.0, "carbs_g": 1.9, "fat_g": 4.8},
    "paneer": {"calories": 265.0, "protein_g": 18.0, "carbs_g": 3.5, "fat_g": 20.0},
    "cottage cheese": {"calories": 98.0, "protein_g": 11.0, "carbs_g": 3.4, "fat_g": 4.3},
    "ground beef": {"calories": 250.0, "protein_g": 26.0, "carbs_g": 0.0, "fat_g": 15.0},
    "beef": {"calories": 250.0, "protein_g": 26.0, "carbs_g": 0.0, "fat_g": 15.0},

    # Grains & Carbohydrates
    "rice": {"calories": 130.0, "protein_g": 2.7, "carbs_g": 28.2, "fat_g": 0.3},
    "white rice": {"calories": 130.0, "protein_g": 2.7, "carbs_g": 28.2, "fat_g": 0.3},
    "cooked rice": {"calories": 130.0, "protein_g": 2.7, "carbs_g": 28.2, "fat_g": 0.3},
    "brown rice": {"calories": 112.0, "protein_g": 2.6, "carbs_g": 23.5, "fat_g": 0.9},
    "oats": {"calories": 389.0, "protein_g": 16.9, "carbs_g": 66.3, "fat_g": 6.9},
    "rolled oats": {"calories": 389.0, "protein_g": 16.9, "carbs_g": 66.3, "fat_g": 6.9},
    "oatmeal": {"calories": 68.0, "protein_g": 2.4, "carbs_g": 12.0, "fat_g": 1.4},
    "quinoa": {"calories": 120.0, "protein_g": 4.4, "carbs_g": 21.3, "fat_g": 1.9},
    "bread": {"calories": 265.0, "protein_g": 9.0, "carbs_g": 49.0, "fat_g": 3.2},
    "whole wheat bread": {"calories": 247.0, "protein_g": 13.0, "carbs_g": 41.0, "fat_g": 3.4},
    "roti": {"calories": 264.0, "protein_g": 9.0, "carbs_g": 54.0, "fat_g": 3.0},
    "chapati": {"calories": 264.0, "protein_g": 9.0, "carbs_g": 54.0, "fat_g": 3.0},
    "pasta": {"calories": 131.0, "protein_g": 5.0, "carbs_g": 25.0, "fat_g": 1.1},
    "potato": {"calories": 77.0, "protein_g": 2.0, "carbs_g": 17.5, "fat_g": 0.1},
    "potatoes": {"calories": 77.0, "protein_g": 2.0, "carbs_g": 17.5, "fat_g": 0.1},
    "sweet potato": {"calories": 86.0, "protein_g": 1.6, "carbs_g": 20.1, "fat_g": 0.1},

    # Legumes & Pulses
    "lentils": {"calories": 116.0, "protein_g": 9.0, "carbs_g": 20.0, "fat_g": 0.4},
    "dal": {"calories": 116.0, "protein_g": 9.0, "carbs_g": 20.0, "fat_g": 0.4},
    "chickpeas": {"calories": 164.0, "protein_g": 8.9, "carbs_g": 27.4, "fat_g": 2.6},
    "black beans": {"calories": 132.0, "protein_g": 8.9, "carbs_g": 23.7, "fat_g": 0.5},
    "kidney beans": {"calories": 127.0, "protein_g": 8.7, "carbs_g": 22.8, "fat_g": 0.5},

    # Dairy & Fats
    "milk": {"calories": 61.0, "protein_g": 3.2, "carbs_g": 4.8, "fat_g": 3.3},
    "whole milk": {"calories": 61.0, "protein_g": 3.2, "carbs_g": 4.8, "fat_g": 3.3},
    "skim milk": {"calories": 35.0, "protein_g": 3.4, "carbs_g": 5.0, "fat_g": 0.2},
    "greek yogurt": {"calories": 59.0, "protein_g": 10.0, "carbs_g": 3.6, "fat_g": 0.4},
    "yogurt": {"calories": 61.0, "protein_g": 3.5, "carbs_g": 4.7, "fat_g": 3.3},
    "curd": {"calories": 61.0, "protein_g": 3.5, "carbs_g": 4.7, "fat_g": 3.3},
    "cheddar cheese": {"calories": 403.0, "protein_g": 25.0, "carbs_g": 1.3, "fat_g": 33.0},
    "cheese": {"calories": 403.0, "protein_g": 25.0, "carbs_g": 1.3, "fat_g": 33.0},
    "butter": {"calories": 717.0, "protein_g": 0.9, "carbs_g": 0.1, "fat_g": 81.1},
    "ghee": {"calories": 884.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 99.5},
    "olive oil": {"calories": 884.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 100.0},
    "coconut oil": {"calories": 862.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 100.0},
    "peanut butter": {"calories": 588.0, "protein_g": 25.0, "carbs_g": 20.0, "fat_g": 50.0},
    "almonds": {"calories": 579.0, "protein_g": 21.0, "carbs_g": 22.0, "fat_g": 50.0},
    "walnuts": {"calories": 654.0, "protein_g": 15.0, "carbs_g": 14.0, "fat_g": 65.0},

    # Vegetables & Fruits
    "apple": {"calories": 52.0, "protein_g": 0.3, "carbs_g": 13.8, "fat_g": 0.2},
    "banana": {"calories": 89.0, "protein_g": 1.1, "carbs_g": 22.8, "fat_g": 0.3},
    "orange": {"calories": 47.0, "protein_g": 0.9, "carbs_g": 11.8, "fat_g": 0.1},
    "broccoli": {"calories": 34.0, "protein_g": 2.8, "carbs_g": 6.6, "fat_g": 0.4},
    "spinach": {"calories": 23.0, "protein_g": 2.9, "carbs_g": 3.6, "fat_g": 0.4},
    "carrots": {"calories": 41.0, "protein_g": 0.9, "carbs_g": 9.6, "fat_g": 0.2},
    "onion": {"calories": 40.0, "protein_g": 1.1, "carbs_g": 9.3, "fat_g": 0.1},
    "tomato": {"calories": 18.0, "protein_g": 0.9, "carbs_g": 3.9, "fat_g": 0.2},
    "cucumber": {"calories": 15.0, "protein_g": 0.7, "carbs_g": 3.6, "fat_g": 0.1},
    "bell pepper": {"calories": 20.0, "protein_g": 0.9, "carbs_g": 4.6, "fat_g": 0.2},
}

def get_staple_nutrition(name: str) -> Optional[Dict[str, float]]:
    clean = name.strip().lower()
    if clean in STAPLE_NUTRITION:
        return STAPLE_NUTRITION[clean]
    # Check singular/plural
    if clean.endswith("s") and clean[:-1] in STAPLE_NUTRITION:
        return STAPLE_NUTRITION[clean[:-1]]
    return None
