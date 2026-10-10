from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class Ingredient(BaseModel):
    name: str
    amount_grams: float
    unit: Optional[str] = "g"
    notes: Optional[str] = None

class Meal(BaseModel):
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"]
    recipe_name: str
    diet_type: Optional[str] = None
    locked: bool = False
    source: Literal["api", "ai_generated", "user_specified"] = "api"
    ingredients: List[Ingredient] = Field(default_factory=list)
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    instructions: Optional[str] = None

class DayPlan(BaseModel):
    day_number: int = Field(..., description="Sequential day number (1, 2, ...)")
    meals: List[Meal] = Field(..., description="List of meals for the day (breakfast, lunch, dinner)")
    total_calories: float = 0.0
    total_protein_g: float = 0.0
    total_carbs_g: float = 0.0
    total_fat_g: float = 0.0

class MealPlan(BaseModel):
    name: Optional[str] = None
    days: List[DayPlan] = Field(..., description="List of daily meal plans for all requested days. MUST NOT BE EMPTY.")
    target_calories: float
    diet_type: Optional[str] = "veg"
    average_daily_calories: float = 0.0
    version: int = 1

class NutritionIssue(BaseModel):
    day_number: int
    meal_type: Optional[str] = None
    message: str
    diff_calories: float
    suggested_action: str

class DailyNutritionTotal(BaseModel):
    day: Optional[int] = None
    day_number: Optional[int] = None
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0

class NutritionReport(BaseModel):
    passed: bool
    issues: List[NutritionIssue] = Field(default_factory=list)
    summary: str = ""
    daily_totals: List[DailyNutritionTotal] = Field(default_factory=list)

class AllergyViolation(BaseModel):
    day_number: int
    meal_type: str
    ingredient: str
    allergen: str
    reason: str

class InvestigatedItem(BaseModel):
    ingredient: str
    status: str
    notes: Optional[str] = None

class AllergyReport(BaseModel):
    passed: bool
    violations: List[AllergyViolation] = Field(default_factory=list)
    investigated_items: List[InvestigatedItem] = Field(default_factory=list)
    summary: str = ""

class GroceryItem(BaseModel):
    name: str
    category: str = "produce"
    total_grams: float
    display_unit: str = "kg"
    price_per_kg: float = 0.0
    estimated_cost: float = 0.0
    price_source: Literal["mandi_api", "baseline_cache", "ai_estimate"] = "baseline_cache"

class GroceryList(BaseModel):
    items: List[GroceryItem] = Field(default_factory=list)
    total_estimated_cost: float = 0.0
    currency: str = "INR"
