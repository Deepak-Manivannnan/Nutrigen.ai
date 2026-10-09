import operator
from typing import Annotated, List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from backend.app.schemas.domain import MealPlan, NutritionReport, AllergyReport, GroceryList
from backend.app.schemas.messages import AgentMessage

class PlanState(BaseModel):
    task_id: str
    user_id: int
    user_profile: Dict[str, Any]
    intent: Literal["new_plan", "edit_meal", "grocery_only", "explain", "chat", "app_info", "out_of_scope", "delete_plan"] = "chat"
    days: Optional[int] = None
    calorie_target: Optional[float] = None
    fixed_meals: Dict[str, Any] = Field(default_factory=dict)
    edit_details: Optional[Any] = None
    
    current_plan: Optional[MealPlan] = None
    plan_version: int = 1
    
    nutrition_report: Optional[NutritionReport] = None
    allergy_report: Optional[AllergyReport] = None
    grocery_list: Optional[GroceryList] = None
    
    round: int = 1
    max_rounds: int = 3
    status: Literal["drafting", "reviewing", "approved", "escalated", "failed", "conversational", "out_of_scope", "deleted"] = "drafting"
    
    trace: Annotated[List[AgentMessage], operator.add] = Field(default_factory=list)
    system_message: Optional[str] = None
    deleted_plan_id: Optional[int] = None
    quick_options: Optional[List[Dict[str, str]]] = None
