import operator
from typing import Annotated, List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from backend.app.schemas.domain import MealPlan, NutritionReport, AllergyReport, GroceryList, RepairTargets
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
    "list_plans",
    "resume_request",
    "cancel_request",
    "unclear",
]

class PendingWorkflow(BaseModel):
    """Stores pending meal plan creation workflow state"""
    workflow_type: Literal["new_plan", "edit_meal", "delete_plan", "approve_plan", "save_dislikes"] = "new_plan"
    pending_conflicts: List[str] = Field(default_factory=list)
    asked_questions: List[str] = Field(default_factory=list)
    extracted_info: Dict[str, Any] = Field(default_factory=dict)
    next_expected_decision: Optional[str] = None
    original_intent: Optional[str] = None
    original_message: Optional[str] = None
    user_profile_at_start: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None

class PlanState(BaseModel):
    task_id: str
    user_id: int
    user_profile: Dict[str, Any]
    intent: IntentType = "chat"
    days: Optional[int] = None
    calorie_target: Optional[float] = None
    cuisine: Optional[str] = None
    # What the user asked the food to be like ("high protein") and the foods the
    # meals should be built around ("chicken"). Both go to the Planner.
    style_notes: List[str] = Field(default_factory=list)
    main_ingredients: List[str] = Field(default_factory=list)
    fixed_meals: Dict[str, Any] = Field(default_factory=dict)
    edit_details: Optional[Any] = None
    plan_name: Optional[str] = None
    include_snack: bool = False
    
    current_plan: Optional[MealPlan] = None
    plan_version: int = 1
    
    nutrition_report: Optional[NutritionReport] = None
    allergy_report: Optional[AllergyReport] = None
    grocery_list: Optional[GroceryList] = None

    # Set by the Supervisor when it sends the plan back for a retry: exactly
    # which meals/days the Planner should redo. Cleared once the Planner has used it.
    repair_targets: Optional[RepairTargets] = None
    
    round: int = 1
    max_rounds: int = 3
    status: Literal["drafting", "reviewing", "approved", "escalated", "failed", "conversational", "out_of_scope", "deleted", "draft"] = "drafting"
    
    trace: Annotated[List[AgentMessage], operator.add] = Field(default_factory=list)
    system_message: Optional[str] = None
    deleted_plan_id: Optional[int] = None
    # No quick_options: the chat is typing-only, so the runner never builds
    # tappable reply buttons.
    plan_id: Optional[int] = None
    # True when this turn changed the user's saved profile (e.g. foods added to
    # their dislikes), so the app can refresh what it shows.
    profile_updated: bool = False
    
    # New field for handling off-topic messages during active meal-planning workflow
    pending_workflow: Optional[PendingWorkflow] = None
    
    # Store intent extraction data for graph nodes (e.g., exclusions)
    intent_data: Optional[Any] = None
