import pytest
from backend.app.tools.units import convert_to_grams
from backend.app.tools.nutrition.usda import lookup_nutrition
from backend.app.tools.recipes.themealdb import search_recipes
from backend.app.tools.prices.mandi import get_price
from backend.app.tools.allergens import check_allergens
from backend.app.tools.shopping import merge_ingredients
from backend.app.schemas.domain import Meal, Ingredient
from backend.app.db.session import init_db, AsyncSessionLocal
from backend.app.db.seed.seed import seed_allergen_sources

def test_convert_to_grams():
    assert convert_to_grams("100g") == 100.0
    assert convert_to_grams("1 kg") == 1000.0
    assert convert_to_grams("2 tbsp") == 30.0
    assert convert_to_grams("1 cup") == 240.0
    assert convert_to_grams("2 slices") == 60.0

@pytest.mark.asyncio
async def test_nutrition_lookup_usda():
    # Real USDA API search query
    data = await lookup_nutrition("apple")
    assert "found" in data
    if data["found"]:
        assert data["calories"] > 0
        assert data["source"] in ("usda_api", "usda_staple_baseline")

    # Non-existent food item
    missing = await lookup_nutrition("nonexistentunknownfood12345")
    assert missing["found"] is False
    assert missing["calories"] == 0.0

@pytest.mark.asyncio
async def test_search_recipes_themealdb():
    # Real TheMealDB query
    recipes = await search_recipes("chicken")
    assert isinstance(recipes, list)
    if recipes:
        assert recipes[0]["source"] == "api"
        assert len(recipes[0]["ingredients"]) > 0

    # No match query
    empty = await search_recipes("xyzunknownnonexistentquery999")
    assert empty == []

@pytest.mark.asyncio
async def test_mandi_prices_tool():
    # Mandi tool returns found status without hardcoded fallbacks
    price_info = await get_price("wheat")
    assert "found" in price_info
    assert "price_per_kg" in price_info

@pytest.mark.asyncio
async def test_allergen_detection_hybrid():
    # Check allergen detection (peanut butter -> peanuts)
    violations = await check_allergens(["peanuts"], ["peanut butter", "apple"])
    assert len(violations) >= 1
    assert any(v["allergen"] == "peanuts" for v in violations)

    # Safe food
    safe_violations = await check_allergens(["dairy"], ["apple", "rice"])
    assert len(safe_violations) == 0

def test_merge_ingredients_quantities():
    meals = [
        Meal(
            meal_type="breakfast",
            recipe_name="Meal 1",
            ingredients=[
                Ingredient(name="oats", amount_grams=50.0),
                Ingredient(name="milk", amount_grams=200.0)
            ]
        ),
        Meal(
            meal_type="lunch",
            recipe_name="Meal 2",
            ingredients=[
                Ingredient(name="rice", amount_grams=150.0),
                Ingredient(name="milk", amount_grams=100.0)
            ]
        )
    ]

    merged = merge_ingredients(meals)
    # Milk should be merged: 200 + 100 = 300g
    milk_item = next(item for item in merged if item["name"] == "milk")
    assert milk_item["total_grams"] == 300.0
    assert len(merged) == 3
