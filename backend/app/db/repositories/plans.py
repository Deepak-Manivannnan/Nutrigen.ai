from typing import Optional, List
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import Plan, PlanMeal
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient, GroceryList

class PlanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_plan(
        self,
        user_id: int,
        days: int,
        calorie_target: float,
        meal_plan: MealPlan,
        grocery_list: Optional[GroceryList] = None,
        status: str = "draft",
        name: Optional[str] = None,
        diet_type: Optional[str] = None,
        version: int = 1,
        cuisine: Optional[str] = None
    ) -> Plan:
        plan_name = name or getattr(meal_plan, "name", None) or f"{days}-Day Meal Plan"
        plan_diet = diet_type or getattr(meal_plan, "diet_type", "veg") or "veg"
        plan = Plan(
            user_id=user_id,
            name=plan_name,
            days=days,
            calorie_target=calorie_target,
            diet_type=plan_diet,
            cuisine=(cuisine or getattr(meal_plan, "cuisine", None) or None),
            status=status,
            version=version,
            grocery_list=grocery_list.model_dump() if grocery_list else None
        )
        self.session.add(plan)
        await self.session.flush()

        # Add meals
        for day in meal_plan.days:
            for meal in day.meals:
                pm = PlanMeal(
                    plan_id=plan.id,
                    day=day.day_number,
                    meal_type=meal.meal_type,
                    diet_type=getattr(meal, "diet_type", None) or plan_diet,
                    locked=meal.locked,
                    recipe_name=meal.recipe_name,
                    source=meal.source,
                    ingredients=[ing.model_dump() for ing in meal.ingredients],
                    calories=meal.calories,
                    instructions=meal.instructions,
                    macros={
                        "protein_g": meal.protein_g,
                        "carbs_g": meal.carbs_g,
                        "fat_g": meal.fat_g
                    }
                )
                self.session.add(pm)

        await self.session.commit()
        await self.session.refresh(plan)
        return await self.get_by_id(plan.id)

    async def update_plan(
        self,
        plan_id: int,
        meal_plan: MealPlan,
        grocery_list: Optional[GroceryList] = None,
        status: str = "draft",
        name: Optional[str] = None,
        diet_type: Optional[str] = None,
        increment_version: bool = True
    ) -> Optional[Plan]:
        plan = await self.get_by_id(plan_id)
        if not plan:
            return None
        plan.days = len(meal_plan.days)
        plan.calorie_target = meal_plan.target_calories
        if name:
            plan.name = name
        if diet_type:
            plan.diet_type = diet_type
        if increment_version:
            plan.version = (plan.version or 1) + 1
        plan.status = status
        plan.grocery_list = grocery_list.model_dump() if grocery_list else None

        for old_m in (plan.meals or []):
            await self.session.delete(old_m)
        await self.session.flush()

        plan_diet = plan.diet_type or "veg"
        for day in meal_plan.days:
            for meal in day.meals:
                pm = PlanMeal(
                    plan_id=plan.id,
                    day=day.day_number,
                    meal_type=meal.meal_type,
                    diet_type=getattr(meal, "diet_type", None) or plan_diet,
                    locked=meal.locked,
                    recipe_name=meal.recipe_name,
                    source=meal.source,
                    ingredients=[ing.model_dump() for ing in meal.ingredients],
                    calories=meal.calories,
                    instructions=meal.instructions,
                    macros={
                        "protein_g": meal.protein_g,
                        "carbs_g": meal.carbs_g,
                        "fat_g": meal.fat_g
                    }
                )
                self.session.add(pm)

        await self.session.commit()
        await self.session.refresh(plan)
        return await self.get_by_id(plan.id)

    async def rename_plan(self, plan_id: int, new_name: str) -> Optional[Plan]:
        plan = await self.get_by_id(plan_id)
        if not plan:
            return None
        plan.name = new_name.strip()
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def get_by_id(self, plan_id: int) -> Optional[Plan]:
        stmt = (
            select(Plan)
            .where(Plan.id == plan_id)
            .options(selectinload(Plan.meals))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_by_user(self, user_id: int) -> Optional[Plan]:
        stmt = (
            select(Plan)
            .where(Plan.user_id == user_id)
            .order_by(Plan.id.desc())
            .options(selectinload(Plan.meals))
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_all_by_user(self, user_id: int) -> List[Plan]:
        stmt = (
            select(Plan)
            .where(Plan.user_id == user_id)
            .order_by(Plan.id.desc())
            .options(selectinload(Plan.meals))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_plan(self, plan_id: int) -> bool:
        plan = await self.get_by_id(plan_id)
        if not plan:
            return False
        await self.session.delete(plan)
        await self.session.commit()
        return True

    @staticmethod
    def to_meal_plan_domain(plan: Plan) -> MealPlan:
        days_map: dict = {}
        for m in (plan.meals or []):
            d_num = m.day
            if d_num not in days_map:
                days_map[d_num] = []
            ings = [
                Ingredient(
                    name=ing.get("name", ""),
                    amount_grams=float(ing.get("amount_grams", 0.0)),
                    notes=ing.get("notes")
                )
                for ing in (m.ingredients or [])
            ]
            macros = m.macros or {}
            meal = Meal(
                meal_type=m.meal_type if m.meal_type in ("breakfast", "lunch", "dinner", "snack") else "lunch",
                recipe_name=m.recipe_name,
                diet_type=getattr(m, "diet_type", None) or plan.diet_type or "veg",
                locked=m.locked,
                source=m.source if m.source in ("api", "ai_generated", "user_specified") else "api",
                ingredients=ings,
                calories=float(m.calories or 0.0),
                instructions=getattr(m, "instructions", None),
                protein_g=float(macros.get("protein_g", 0.0)),
                carbs_g=float(macros.get("carbs_g", 0.0)),
                fat_g=float(macros.get("fat_g", 0.0))
            )
            days_map[d_num].append(meal)

        day_plans = []
        total_all_cals = 0.0
        for d_num in sorted(days_map.keys()):
            meals = days_map[d_num]
            day_cals = sum(meal.calories for meal in meals)
            day_p = sum(meal.protein_g for meal in meals)
            day_c = sum(meal.carbs_g for meal in meals)
            day_f = sum(meal.fat_g for meal in meals)
            total_all_cals += day_cals
            day_plans.append(DayPlan(
                day_number=d_num,
                meals=meals,
                total_calories=round(day_cals, 1),
                total_protein_g=round(day_p, 1),
                total_carbs_g=round(day_c, 1),
                total_fat_g=round(day_f, 1)
            ))

        avg_cals = round(total_all_cals / len(day_plans), 1) if day_plans else plan.calorie_target
        return MealPlan(
            name=plan.name,
            days=day_plans,
            target_calories=plan.calorie_target,
            diet_type=plan.diet_type or "veg",
            cuisine=getattr(plan, "cuisine", None),
            average_daily_calories=avg_cals,
            version=plan.version or 1
        )

