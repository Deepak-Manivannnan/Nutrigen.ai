import os
import asyncio
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from backend.app.agents.base import BaseAgent
from backend.app.agents.shopper.tools import SHOPPER_TOOLS, merge_ingredients_tool, get_price_tool
from backend.app.schemas.domain import MealPlan, GroceryList, GroceryItem
from backend.app.llm.client import LLMClient
from backend.app.observability.logging import get_logger

logger = get_logger("agents.shopper")

def _load_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are the Shopper Agent. Consolidate grocery items and estimate total cost using mandi prices."

def _classify_category(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ["milk", "paneer", "curd", "yogurt", "cheese", "butter", "ghee", "cream"]):
        return "dairy"
    if any(k in n for k in ["chicken", "fish", "egg", "tofu", "soya", "mutton", "meat", "prawn", "shrimp"]):
        return "proteins"
    if any(k in n for k in ["rice", "wheat", "oats", "flour", "atta", "roti", "bread", "dal", "lentil", "rajma", "chana", "quinoa", "gram"]):
        return "grains"
    if any(k in n for k in ["turmeric", "cumin", "mustard seed", "pepper", "chilli powder", "garam masala", "salt", "coriander powder", "cardamom", "cinnamon", "clove"]):
        return "spices"
    if any(k in n for k in ["oil", "sugar", "honey", "vinegar", "sauce", "syrup", "dressing"]):
        return "pantry"
    return "produce"

class MissingItemEstimate(BaseModel):
    name: str
    price_per_kg: float = Field(..., description="Retail price per kg/liter in INR")
    category: str = Field(default="pantry", description="produce, dairy, grains, proteins, spices, or pantry")

class MissingItemsResponse(BaseModel):
    items: List[MissingItemEstimate] = Field(default_factory=list)

class ShopperAgent(BaseAgent[GroceryList]):
    """
    Shopper Agent: Consolidates meal plan ingredients into a single grocery list,
    retrieves mandi prices following strict hierarchy (data.gov.in -> CEDA -> AI estimate),
    applies AI estimates for missing pantry items, and calculates the total estimated cost in INR.
    """
    def __init__(
        self,
        client: Optional[LLMClient] = None,
        max_steps: Optional[int] = None,
        timeout_seconds: Optional[int] = None
    ):
        super().__init__(
            name="ShopperAgent",
            system_prompt=_load_prompt(),
            output_schema=GroceryList,
            tools=SHOPPER_TOOLS,
            client=client,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds
        )

    async def build_grocery_list(self, plan: MealPlan, task_id: Optional[str] = None) -> GroceryList:
        all_meals = []
        for day in plan.days:
            for meal in day.meals:
                all_meals.append(meal.model_dump())

        # 1. Aggregate ingredients across all meals and calculate total grams
        merged_items = merge_ingredients_tool(meals=all_meals)
        logger.info(f"[{self.name}] Merged {len(merged_items)} distinct grocery items across {len(plan.days)} days.")

        # 2. Concurrently query Mandi & CEDA prices for each merged ingredient
        pricing_tasks = [get_price_tool(item["name"]) for item in merged_items]
        pricing_results = await asyncio.gather(*pricing_tasks, return_exceptions=True)

        grocery_items: List[GroceryItem] = []
        missing_items: List[Dict[str, Any]] = []

        for item, p_res in zip(merged_items, pricing_results):
            name = item["name"]
            total_grams = float(item.get("total_grams", 0.0))
            display_unit = item.get("display_unit", "kg" if total_grams >= 1000 else "g")
            initial_cat = _classify_category(name)

            if isinstance(p_res, dict) and p_res.get("found"):
                price_per_kg = float(p_res.get("price_per_kg", 0.0))
                estimated_cost = round((total_grams / 1000.0) * price_per_kg, 2)
                grocery_items.append(
                    GroceryItem(
                        name=name,
                        category=initial_cat,
                        total_grams=total_grams,
                        display_unit=display_unit,
                        price_per_kg=price_per_kg,
                        estimated_cost=estimated_cost,
                        price_source="mandi_api"
                    )
                )
            else:
                missing_items.append({
                    "name": name,
                    "total_grams": total_grams,
                    "display_unit": display_unit,
                    "category": initial_cat
                })

        # 3. For non-mandi items (dairy, spices, specialty pantry), use AI price estimation
        if missing_items:
            missing_names = [m["name"] for m in missing_items]
            logger.info(f"[{self.name}] Querying AI estimation for {len(missing_items)} non-mandi items: {missing_names}")
            try:
                estimate_prompt = (
                    "You are the Shopper Agent pricing assistant for an Indian clinical diet application.\n"
                    "Provide realistic Indian supermarket retail prices (in INR per kg or per liter) for these "
                    f"packaged/dairy/spice items not found on wholesale agricultural mandis: {missing_names}.\n\n"
                    "Typical ranges: paneer ~400-450/kg, milk ~60-70/L, curd ~80-100/kg, spices ~400-800/kg, ghee ~600-700/kg.\n"
                    "Return realistic price_per_kg and category ('dairy', 'spices', 'pantry', 'proteins', 'produce', 'grains')."
                )
                est_resp: MissingItemsResponse = await self.llm.generate_structured(
                    prompt=estimate_prompt,
                    response_schema=MissingItemsResponse
                )
                price_map = {item.name.lower(): item for item in est_resp.items}
            except Exception as e:
                logger.warning(f"[{self.name}] AI estimation failed: {e}. Using standard retail baselines.")
                price_map = {}

            for m in missing_items:
                m_name = m["name"]
                matched_est = price_map.get(m_name.lower())
                if matched_est and matched_est.price_per_kg > 0:
                    p_kg = round(matched_est.price_per_kg, 2)
                    cat = matched_est.category or m["category"]
                else:
                    # Robust fallback baselines
                    cat = m["category"]
                    if cat == "dairy":
                        p_kg = 350.0
                    elif cat == "spices":
                        p_kg = 500.0
                    elif cat == "proteins":
                        p_kg = 280.0
                    elif cat == "pantry":
                        p_kg = 180.0
                    else:
                        p_kg = 60.0

                cost = round((m["total_grams"] / 1000.0) * p_kg, 2)
                grocery_items.append(
                    GroceryItem(
                        name=m_name,
                        category=cat,
                        total_grams=m["total_grams"],
                        display_unit=m["display_unit"],
                        price_per_kg=p_kg,
                        estimated_cost=cost,
                        price_source="ai_estimate"
                    )
                )

        total_cost = round(sum(i.estimated_cost for i in grocery_items), 2)
        grocery_list = GroceryList(
            items=grocery_items,
            total_estimated_cost=total_cost,
            currency="INR"
        )
        logger.info(f"[{self.name}] Compiled grocery list: {len(grocery_items)} items, total INR {total_cost:.2f}.")
        return grocery_list

    async def generate_grocery_list(
        self,
        meal_plan: Optional[MealPlan] = None,
        plan: Optional[MealPlan] = None,
        task_id: Optional[str] = None
    ) -> GroceryList:
        """Alias ensuring compatibility with routes/plans.py approve_plan."""
        target = meal_plan or plan
        if not target:
            raise ValueError("No meal plan provided for grocery list generation.")
        return await self.build_grocery_list(target, task_id=task_id)

