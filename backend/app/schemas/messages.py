from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, Literal, Optional
from datetime import datetime, timezone
import uuid

MessageType = Literal[
    "task",       # Supervisor assigns work
    "draft",      # Planner returns meal plan
    "feedback",   # Nutrition requests changes
    "veto",       # Allergy agent blocks a meal
    "approve",    # Checks passed or user approved
    "error",      # Failure or timeout
    "escalate",   # Hit round limit or unresolvable conflict
    "thought",    # Agent reasoning step
    "tool_call",  # Agent invokes tool
    "status"      # Agent status / update
]

class AgentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: f"m-{uuid.uuid4().hex[:8]}")
    task_id: str
    from_agent: str = Field(..., alias="from")
    to_agent: str = Field(..., alias="to")
    type: MessageType
    content: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    plan_version: int = 1
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True)
