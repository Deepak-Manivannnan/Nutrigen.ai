from typing import List
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import AgentTrace
from backend.app.schemas.messages import AgentMessage
from backend.app.account_context import current_user_id

class TraceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_event(self, message: AgentMessage) -> AgentTrace:
        payload = dict(message.payload) if message.payload else {}
        if message.content and "message" not in payload:
            payload["message"] = message.content
        trace = AgentTrace(
            task_id=message.task_id,
            user_id=current_user_id.get(),
            from_agent=message.from_agent,
            to_agent=message.to_agent,
            type=message.type,
            payload=payload,
            timestamp=message.timestamp
        )
        self.session.add(trace)
        await self.session.commit()
        await self.session.refresh(trace)
        return trace

    async def get_by_task_id(self, task_id: str) -> List[AgentTrace]:
        stmt = (
            select(AgentTrace)
            .where(AgentTrace.task_id == task_id)
            .order_by(AgentTrace.timestamp.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
