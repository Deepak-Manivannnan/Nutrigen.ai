from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime
from backend.app.config import settings

# Sensible limits for body measurements, shared by creating and updating a
# profile. The frontend mirrors these in src/utils/profileLimits.js - change
# both together. Age follows the app-wide adult minimum.
AGE_MIN = settings.min_age_allowed
AGE_MAX = 120
HEIGHT_MIN_CM = 100
HEIGHT_MAX_CM = 250
WEIGHT_MIN_KG = 30
WEIGHT_MAX_KG = 300

class ProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    age: int = Field(..., ge=AGE_MIN, le=AGE_MAX)
    sex: Literal["male", "female", "other"]
    height_cm: float = Field(..., ge=HEIGHT_MIN_CM, le=HEIGHT_MAX_CM)
    weight_kg: float = Field(..., ge=WEIGHT_MIN_KG, le=WEIGHT_MAX_KG)
    activity_level: Literal["low", "medium", "high"]
    goal: Optional[Literal["lose", "maintain", "gain"]] = "maintain"
    diet_type: Literal["veg", "non-veg", "vegan"]
    dislikes: List[str] = Field(default_factory=list)
    allergies: List[str] = Field(default_factory=list)

    @field_validator("dislikes", "allergies", mode="before")
    def clean_string_list(cls, v):
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        return []

class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    age: Optional[int] = Field(None, ge=AGE_MIN, le=AGE_MAX)
    sex: Optional[Literal["male", "female", "other"]] = None
    height_cm: Optional[float] = Field(None, ge=HEIGHT_MIN_CM, le=HEIGHT_MAX_CM)
    weight_kg: Optional[float] = Field(None, ge=WEIGHT_MIN_KG, le=WEIGHT_MAX_KG)
    activity_level: Optional[Literal["low", "medium", "high"]] = None
    goal: Optional[Literal["lose", "maintain", "gain"]] = None
    diet_type: Optional[Literal["veg", "non-veg", "vegan"]] = None
    dislikes: Optional[List[str]] = None
    allergies: Optional[List[str]] = None

class ProfileResponse(BaseModel):
    # These are Optional because a user row legitimately exists before
    # onboarding finishes: /api/auth/register returns has_profile=False for that
    # state. Declaring them required made every profile endpoint raise a
    # response-validation error (HTTP 500) for a brand-new account.
    id: int
    name: str
    age: Optional[int] = None
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    activity_level: Optional[str] = None
    goal: Optional[str] = None
    diet_type: Optional[str] = None
    dislikes: List[str]
    allergies: List[str]
    min_safe_calories: Optional[float] = None
    max_safe_calories: Optional[float] = None
    suggested_target_calories: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class CalorieRangeResponse(BaseModel):
    bmr: float
    tdee: float
    min_safe_calories: float
    max_safe_calories: float
    suggested_target: float
    is_eligible: bool = True
    message: Optional[str] = None

class ChatRequest(BaseModel):
    user_id: int
    message: str
    task_id: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None
    pending_workflow: Optional[Dict[str, Any]] = None
    # Plan id from the assistant reply immediately before this one. Lets the
    # backend tell "approve the plan you just made" apart from a loose "approve
    # it" with no fresh referent, without matching on the message text.
    just_created_plan_id: Optional[int] = None

class ChatResponse(BaseModel):
    response: str
    task_id: Optional[str] = None
    status: str = "completed"
    plan_id: Optional[int] = None
    intent: Optional[str] = None
    is_workflow: bool = False
    profile_updated: bool = False
    pending_workflow: Optional[Dict[str, Any]] = None
    # No quick_options: the chat is typing-only. Every reply is answered by
    # typing, so the API deliberately ships no tappable reply buttons.
