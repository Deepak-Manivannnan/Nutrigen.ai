import asyncio
import json
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse
from backend.app.observability.trace import trace_hub
from backend.app.observability.logging import get_logger

logger = get_logger("api.streaming")
router = APIRouter(prefix="/streaming", tags=["Streaming Timeline (SSE)"])

@router.get("/{task_id}")
async def stream_task_timeline(task_id: str, request: Request):
    """
    Server-Sent Events (SSE) endpoint streaming real-time agent messages,
    vetoes, tool calls, and decisions directly to the React timeline UI.
    """
    queue = trace_hub.subscribe(task_id)

    async def event_generator():
        try:
            while True:
                # Disconnect check
                if await request.is_disconnected():
                    logger.debug(f"Client disconnected from SSE stream for {task_id}")
                    break

                try:
                    # Wait up to 15s for next event or send heartbeat
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    data = message.model_dump(by_alias=True, mode="json")
                    yield {
                        "event": "agent_message",
                        "data": json.dumps(data)
                    }
                except asyncio.TimeoutError:
                    # Heartbeat comment to keep SSE connection alive through proxies
                    yield {
                        "event": "ping",
                        "data": json.dumps({"ping": "keep-alive"})
                    }
        finally:
            trace_hub.unsubscribe(task_id, queue)

    return EventSourceResponse(event_generator())
