import os
from typing import Dict, Any, Optional
from backend.app.agents.base import BaseAgent
from backend.app.agents.shopper.tools import SHOPPER_TOOLS
from backend.app.schemas.domain import MealPlan, GroceryList
from backend.app.llm.client import LLMClient

def _load_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are the Shopper Agent. Consolidate grocery items and estimate total cost using mandi prices."

class ShopperAgent(BaseAgent[GroceryList]):
    """
    Shopper Agent: Consolidates meal plan ingredients into a single grocery list,
    retrieves mandi prices, applies AI estimates for missing pantry items,
    and calculates the total estimated cost in INR.
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

        task_instruction = (
            f"Build a consolidated grocery shopping list for this {len(plan.days)}-day meal plan.\n"
            f"1. Use `merge_ingredients` to aggregate identical ingredients and sum total grams.\n"
            f"2. Use `get_price` to fetch Mandi / CEDA prices for the merged commodities.\n"
            f"3. For items not found in mandi data, supply a reasonable AI price estimate tagged 'ai_estimate'.\n"
            f"4. Calculate total estimated cost in INR."
        )

        context = {
            "task_id": task_id,
            "meals_count": len(all_meals),
            "meals": all_meals
        }

        grocery_list: GroceryList = await self.run(task_instruction, context)
        
        # Verify total cost calculation
        total = sum(item.estimated_cost for item in grocery_list.items)
        if grocery_list.total_estimated_cost <= 0.0 or abs(grocery_list.total_estimated_cost - total) > 1.0:
            grocery_list.total_estimated_cost = round(total, 2)
        grocery_list.currency = "INR"

        return grocery_list
