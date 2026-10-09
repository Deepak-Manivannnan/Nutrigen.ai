import asyncio
import inspect
from typing import Dict, Any, List, Optional, Type, Callable, TypeVar, Generic
from pydantic import BaseModel, Field
from backend.app.config import settings
from backend.app.llm.client import llm_client, LLMClient, get_llm_client
from backend.app.observability.logging import get_logger

logger = get_logger("agents.base")

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T", bound=BaseModel)

class ToolArg(BaseModel):
    key: str = Field(..., description="Parameter name")
    value: str = Field(..., description="Parameter value")

class AgentStepAction(BaseModel):
    thought: str = Field(..., description="Reasoning about the current state, progress towards goal, and what needs to be done next.")
    action: str = Field(..., description="Action to take: 'call_tool' to invoke an available tool, or 'finish' if sufficient data has been gathered to produce the final output.")
    tool_name: Optional[str] = Field(None, description="Name of the tool to execute if action is 'call_tool'.")
    tool_args: List[ToolArg] = Field(default_factory=list, description="List of arguments for the tool.")
    finish_summary: Optional[str] = Field(None, description="Summary of final conclusions if action is 'finish'.")

    @field_validator("tool_args", mode="before")
    @classmethod
    def coerce_tool_args(cls, v):
        if isinstance(v, dict):
            return [ToolArg(key=str(k), value=str(val)) for k, val in v.items()]
        return v

    def get_args_dict(self) -> Dict[str, Any]:
        return {arg.key: arg.value for arg in self.tool_args}

class BaseAgent(Generic[T]):
    """
    Base class providing the shared Think-Act-Observe loop for all worker agents.
    Enforces:
    1. Step limits (default 6 steps).
    2. Timeout boundaries (default 30 seconds).
    3. Tool permission checks (agents can only invoke their explicitly bound tools).
    4. Structured Pydantic validation for the final output artifact.
    """
    def __init__(
        self,
        name: str,
        system_prompt: str,
        output_schema: Type[T],
        tools: Optional[Dict[str, Callable]] = None,
        client: Optional[LLMClient] = None,
        max_steps: Optional[int] = None,
        timeout_seconds: Optional[int] = None
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.output_schema = output_schema
        self.tools: Dict[str, Callable] = tools or {}
        self.llm = client or get_llm_client(self.name)
        self.max_steps = max_steps or settings.max_agent_steps
        self.timeout_seconds = timeout_seconds or settings.agent_timeout_seconds

    def _format_tools_description(self) -> str:
        if not self.tools:
            return "No external tools available. Rely on instructions and domain reasoning."
        lines = ["Available tools you are authorized to call:"]
        for tool_name, func in self.tools.items():
            doc = inspect.getdoc(func) or "No description provided."
            first_line = doc.splitlines()[0] if doc else ""
            lines.append(f"- `{tool_name}`: {first_line}")
        return "\n".join(lines)

    async def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if tool_name not in self.tools:
            return f"Error: Tool '{tool_name}' is not in your authorized tools list. Allowed: {list(self.tools.keys())}"

        func = self.tools[tool_name]
        try:
            if inspect.iscoroutinefunction(func):
                return await func(**args)
            return func(**args)
        except Exception as e:
            logger.error(f"[{self.name}] Error executing tool {tool_name}: {e}")
            return f"Error executing tool '{tool_name}': {str(e)}"

    async def run(self, task_instruction: str, context: Optional[Dict[str, Any]] = None) -> T:
        """
        Executes the Think-Act-Observe autonomous loop with timeout protection.
        """
        try:
            return await asyncio.wait_for(
                self._run_loop(task_instruction, context),
                timeout=float(self.timeout_seconds)
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{self.name}] Timed out after {self.timeout_seconds}s. Forcing finalization.")
            return await self._force_finalize(task_instruction, context, ["Agent reached execution timeout."])

    async def _run_loop(self, task_instruction: str, context: Optional[Dict[str, Any]] = None) -> T:
        history: List[str] = []
        tools_desc = self._format_tools_description()
        step = 0

        while step < self.max_steps:
            step += 1
            logger.debug(f"[{self.name}] Step {step}/{self.max_steps}")

            # Think & Decide
            history_text = "\n".join(history) if history else "No actions taken yet."
            prompt = (
                f"You are the {self.name}.\n\n"
                f"{self.system_prompt}\n\n"
                f"{tools_desc}\n\n"
                f"--- TASK ---\n{task_instruction}\n\n"
                f"--- CONTEXT ---\n{context or {}}\n\n"
                f"--- HISTORY & OBSERVATIONS ---\n{history_text}\n\n"
                f"Step {step} of {self.max_steps}: Decide whether to call a tool ('call_tool') or finish ('finish')."
            )

            try:
                decision: AgentStepAction = await self.llm.generate_structured(
                    prompt=prompt,
                    response_schema=AgentStepAction
                )
            except Exception as e:
                logger.error(f"[{self.name}] Error in step decision: {e}")
                history.append(f"Step {step} observation: Failed to parse action decision: {str(e)}. Proceeding to finish.")
                break

            logger.info(f"[{self.name}] Step {step} Think: {decision.thought} | Action: {decision.action}")

            task_id = (context or {}).get("task_id")
            if task_id:
                try:
                    from backend.app.observability.trace import trace_hub
                    from backend.app.schemas.messages import AgentMessage
                    sender = self.name.lower().replace("agent", "_agent")
                    if not sender.endswith("_agent"):
                        sender = f"{sender}_agent"
                    await trace_hub.log_event(
                        AgentMessage(
                            task_id=task_id,
                            from_agent=sender,
                            to_agent="supervisor",
                            type="thought",
                            content=decision.thought or f"{self.name} step {step}...",
                            payload={"step": step, "action": decision.action}
                        )
                    )
                except Exception as log_err:
                    logger.debug(f"Failed to stream step thought: {log_err}")

            if decision.action.lower() == "finish":
                if task_id:
                    try:
                        from backend.app.observability.trace import trace_hub
                        from backend.app.schemas.messages import AgentMessage
                        sender = self.name.lower().replace("agent", "_agent")
                        if not sender.endswith("_agent"):
                            sender = f"{sender}_agent"
                        await trace_hub.log_event(
                            AgentMessage(
                                task_id=task_id,
                                from_agent=sender,
                                to_agent="supervisor",
                                type="status",
                                content=decision.finish_summary or f"{self.name} completed analysis.",
                                payload={"summary": decision.finish_summary}
                            )
                        )
                    except Exception as log_err:
                        logger.debug(f"Failed to stream finish: {log_err}")
                history.append(f"Step {step} conclusion: {decision.finish_summary or 'Completed task.'}")
                break

            # Act
            tool_name = decision.tool_name or ""
            tool_args = decision.get_args_dict()

            if task_id:
                try:
                    from backend.app.observability.trace import trace_hub
                    from backend.app.schemas.messages import AgentMessage
                    sender = self.name.lower().replace("agent", "_agent")
                    if not sender.endswith("_agent"):
                        sender = f"{sender}_agent"
                    tool_desc = f"{tool_name}({', '.join(f'{k}={v}' for k, v in tool_args.items())})" if tool_name else "acting"
                    await trace_hub.log_event(
                        AgentMessage(
                            task_id=task_id,
                            from_agent=sender,
                            to_agent="supervisor",
                            type="tool_call",
                            content=f"Invoking tool: {tool_desc}",
                            payload={"tool": tool_name, "args": tool_args}
                        )
                    )
                except Exception as log_err:
                    logger.debug(f"Failed to stream tool call: {log_err}")

            observation = await self._execute_tool(tool_name, tool_args)

            # Observe
            obs_str = str(observation)
            if len(obs_str) > 1500:
                obs_str = obs_str[:1500] + "... [truncated]"
            history.append(
                f"Step {step}: Called `{tool_name}` with args {tool_args}\nObservation: {obs_str}"
            )

        # Generate final structured output matching output_schema
        return await self._finalize(task_instruction, context, history)

    async def _finalize(self, task_instruction: str, context: Optional[Dict[str, Any]], history: List[str]) -> T:
        history_text = "\n".join(history)
        finalize_prompt = (
            f"You are the {self.name}.\n\n"
            f"{self.system_prompt}\n\n"
            f"--- ORIGINAL TASK ---\n{task_instruction}\n\n"
            f"--- CONTEXT ---\n{context or {}}\n\n"
            f"--- OBSERVATIONS & TOOL RESULTS GATHERED ---\n{history_text}\n\n"
            f"Now generate your final complete structured artifact strictly conforming to the requested schema. "
            f"Ensure all calculations, portions, and safety criteria match domain rules."
        )

        return await self.llm.generate_structured(
            prompt=finalize_prompt,
            response_schema=self.output_schema
        )

    async def _force_finalize(self, task_instruction: str, context: Optional[Dict[str, Any]], history: List[str]) -> T:
        return await self._finalize(task_instruction, context, history)
