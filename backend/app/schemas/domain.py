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
    cuisine: Optional[str] = None
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

class RepairItem(BaseModel):
    """One piece of a draft plan the Supervisor wants the Planner to redo."""
    day_number: int
    # None means "every unlocked meal of that day" (a calorie problem is about
    # the whole day); a meal type means exactly that one meal.
    meal_type: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)

class RepairTargets(BaseModel):
    """The Supervisor's instruction to the Planner for a retry round.

    Built by the Supervisor from the Nutrition and Allergy reports and handed
    to the Planner as plain data - the Planner never sees those reports.
    """
    items: List[RepairItem] = Field(default_factory=list)
    # Flagged slots that cannot be redone because the user locked them.
    locked_skipped: List[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def whole_days(self) -> List[RepairItem]:
        return [i for i in self.items if i.meal_type is None]

    def single_meals(self) -> List[RepairItem]:
        return [i for i in self.items if i.meal_type is not None]

    def describe(self) -> str:
        parts = []
        days = sorted(i.day_number for i in self.whole_days())
        if days:
            parts.append("day(s) " + ", ".join(str(d) for d in days))
        meals = sorted((i.day_number, i.meal_type) for i in self.single_meals())
        if meals:
            parts.append("meal(s) " + ", ".join(f"day {d} {m}" for d, m in meals))
        return "; ".join(parts) if parts else "nothing"

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
