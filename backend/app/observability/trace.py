import asyncio
from typing import Dict, List, Optional, Set
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.schemas.messages import AgentMessage
from backend.app.db.models import AgentTrace
from backend.app.db.repositories.trace import TraceRepository
from backend.app.observability.logging import get_logger

logger = get_logger("agent.trace")

class TraceHub:
    """
    Central hub for recording agent trace messages to the database
    and broadcasting them in real time to SSE streaming subscribers.
    """
    def __init__(self):
        # Maps task_id -> set of asyncio.Queue for live UI listeners
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self._subscribers[task_id].add(queue)
        logger.debug(f"New live timeline subscriber for task {task_id}")
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        if task_id in self._subscribers and queue in self._subscribers[task_id]:
            self._subscribers[task_id].remove(queue)
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]
        logger.debug(f"Subscriber removed for task {task_id}")

    async def log_event(
        self,
        message: AgentMessage,
        session: Optional[AsyncSession] = None
    ) -> Optional[AgentTrace]:
        """
        Logs an agent event to the database and broadcasts to any live UI streams.
        """
        logger.info(
            f"[{message.from_agent} -> {message.to_agent}] "
            f"type={message.type} task_id={message.task_id} v={message.plan_version}"
        )

        trace_record = None
        if session:
            repo = TraceRepository(session)
            trace_record = await repo.log_event(message)

        # Broadcast event to live SSE listeners
        listeners = list(self._subscribers.get(message.task_id, set()))
        for q in listeners:
            await q.put(message)

        return trace_record

trace_hub = TraceHub()
