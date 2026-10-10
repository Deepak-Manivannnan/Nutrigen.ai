import operator
from typing import Annotated, List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from backend.app.schemas.domain import MealPlan, NutritionReport, AllergyReport, GroceryList
from backend.app.schemas.messages import AgentMessage

IntentType = Literal[
    "chat",
    "app_info",
    "profile_info",
    "out_of_scope",
    "explain",
    "new_plan",
    "edit_meal",
    "approve_plan",
    "grocery_only",
    "delete_plan",
    "rename_plan",
    "add_snack",
]

class PlanState(BaseModel):
    task_id: str
    user_id: int
    user_profile: Dict[str, Any]
    intent: IntentType = "chat"
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
    status: Literal["drafting", "reviewing", "approved", "escalated", "failed", "conversational", "out_of_scope", "deleted", "draft"] = "drafting"
    
    trace: Annotated[List[AgentMessage], operator.add] = Field(default_factory=list)
    system_message: Optional[str] = None
    deleted_plan_id: Optional[int] = None
    quick_options: Optional[List[Dict[str, str]]] = None
