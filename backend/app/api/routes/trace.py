from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from datetime import datetime
from backend.app.db.repositories.trace import TraceRepository
from backend.app.api.deps import get_trace_repository

router = APIRouter(prefix="/trace", tags=["Observability & Trace"])

class TraceEventResponse(BaseModel):
    id: int
    task_id: str
    from_agent: str
    to_agent: str
    type: str
    payload: Dict[str, Any]
    timestamp: datetime

@router.get("/{task_id}", response_model=List[TraceEventResponse])
async def get_task_trace(
    task_id: str,
    trace_repo: TraceRepository = Depends(get_trace_repository)
):
    """
    Returns the complete audit timeline of agent messages, vetoes, and tool calls for a task.
    """
    records = await trace_repo.get_by_task_id(task_id)
    return [
        TraceEventResponse(
            id=r.id,
            task_id=r.task_id,
            from_agent=r.from_agent,
            to_agent=r.to_agent,
            type=r.type,
            payload=r.payload or {},
            timestamp=r.timestamp
        )
        for r in records
    ]
