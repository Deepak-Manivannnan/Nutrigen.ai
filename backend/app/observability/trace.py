import asyncio
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict
from backend.app.schemas.messages import AgentMessage
from backend.app.observability.logging import get_logger

logger = get_logger("agent.trace")

# How long a run's events are kept for replay, and how many per run.
_REPLAY_SECONDS = 15 * 60
_REPLAY_MAX_EVENTS = 200
_REPLAY_MAX_TASKS = 500


class TraceHub:
    """
    Central hub that logs agent trace messages and broadcasts them in real
    time to the chat's live progress card (SSE). Nothing goes to the database.

    Each run's recent events are also kept in memory for a short while and
    replayed to a listener that connects late: the run can emit its first
    event before the browser's stream is open, and a page reload mid-run
    reconnects to the same run.
    """
    def __init__(self):
        # Maps task_id -> set of asyncio.Queue for live UI listeners
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        # task_id -> (last activity time, events)
        self._recent: Dict[str, Tuple[float, List[AgentMessage]]] = {}

    def _prune(self, now: float) -> None:
        stale = [t for t, (ts, _) in self._recent.items() if now - ts > _REPLAY_SECONDS]
        for t in stale:
            self._recent.pop(t, None)
        while len(self._recent) > _REPLAY_MAX_TASKS:
            oldest = min(self._recent, key=lambda t: self._recent[t][0])
            self._recent.pop(oldest, None)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        for message in self._recent.get(task_id, (0, []))[1]:
            queue.put_nowait(message)
        self._subscribers[task_id].add(queue)
        logger.debug(f"New live timeline subscriber for task {task_id}")
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        if task_id in self._subscribers and queue in self._subscribers[task_id]:
            self._subscribers[task_id].remove(queue)
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]
        logger.debug(f"Subscriber removed for task {task_id}")

    async def log_event(self, message: AgentMessage, session=None) -> None:
        """Log an agent event and broadcast it to any live UI streams.

        `session` is accepted for older callers and ignored.
        """
        logger.info(
            f"[{message.from_agent} -> {message.to_agent}] "
            f"type={message.type} task_id={message.task_id} v={message.plan_version}"
        )
        now = time.time()
        self._prune(now)
        events = self._recent.get(message.task_id, (now, []))[1]
        if len(events) < _REPLAY_MAX_EVENTS:
            events.append(message)
        self._recent[message.task_id] = (now, events)

        # Broadcast event to live SSE listeners
        listeners = list(self._subscribers.get(message.task_id, set()))
        for q in listeners:
            await q.put(message)

    async def emit(self, task_id: Optional[str], from_agent: str, msg_type: str,
                   payload: Dict[str, Any], to_agent: str = "user", content: str = "") -> None:
        """Send a progress event for the chat's live card. Never raises."""
        if not task_id:
            return
        try:
            await self.log_event(AgentMessage(
                task_id=task_id, from_agent=from_agent, to_agent=to_agent,
                type=msg_type, payload=payload, content=content or None,
            ))
        except Exception as e:  # progress display must never break a run
            logger.warning(f"Progress event failed for {task_id}: {e}")


trace_hub = TraceHub()
