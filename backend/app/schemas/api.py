from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime

class ProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    age: int = Field(..., ge=1, le=120)
    sex: Literal["male", "female", "other"]
    height_cm: float = Field(..., ge=50, le=250)
    weight_kg: float = Field(..., ge=20, le=300)
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
    name: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[Literal["male", "female", "other"]] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    activity_level: Optional[Literal["low", "medium", "high"]] = None
    goal: Optional[Literal["lose", "maintain", "gain"]] = None
    diet_type: Optional[Literal["veg", "non-veg", "vegan"]] = None
    dislikes: Optional[List[str]] = None
    allergies: Optional[List[str]] = None

class ProfileResponse(BaseModel):
    id: int
    name: str
    age: int
    sex: str
    height_cm: float
    weight_kg: float
    activity_level: str
    goal: Optional[str]
    diet_type: str
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

class ChatResponse(BaseModel):
    response: str
    task_id: Optional[str] = None
    status: str = "completed"
    plan_id: Optional[int] = None
    intent: Optional[str] = None
    is_workflow: bool = False
