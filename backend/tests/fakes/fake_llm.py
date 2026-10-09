from typing import Optional, Dict, Any, Type, List
from pydantic import BaseModel

class FakeLLMClient:
    """
    Fake LLM client for testing agents and supervisor offline
    without calling real external APIs or incurring latency/cost.
    """
    def __init__(self, default_text: str = "Fake LLM text response"):
        self.default_text = default_text
        self.text_responses: List[str] = []
        self.structured_responses: List[BaseModel] = []
        self.call_history: List[Dict[str, Any]] = []

    def queue_text_response(self, text: str):
        self.text_responses.append(text)

    def queue_structured_response(self, response: BaseModel):
        self.structured_responses.append(response)

    async def generate_text(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        self.call_history.append({
            "type": "text",
            "prompt": prompt,
            "system_instruction": system_instruction
        })
        if self.text_responses:
            return self.text_responses.pop(0)
        return self.default_text

    async def generate_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        system_instruction: Optional[str] = None
    ) -> BaseModel:
        self.call_history.append({
            "type": "structured",
            "prompt": prompt,
            "response_schema": response_schema,
            "system_instruction": system_instruction
        })
        # 1. Search for matching response by schema type
        for i, res in enumerate(self.structured_responses):
            if isinstance(res, response_schema):
                return self.structured_responses.pop(i)
            if isinstance(res, dict):
                try:
                    validated = response_schema.model_validate(res)
                    self.structured_responses.pop(i)
                    return validated
                except Exception:
                    pass

        # 2. If asking for AgentStepAction and none queued, default to finish action
        if response_schema.__name__ == "AgentStepAction":
            from backend.app.agents.base import AgentStepAction
            return AgentStepAction(
                thought="Sufficient context gathered. Ready to finalize.",
                action="finish",
                finish_summary="Finalizing output."
            )

        # 3. If there are any responses left, try converting the first one
        if self.structured_responses:
            res = self.structured_responses.pop(0)
            if isinstance(res, response_schema):
                return res
            if isinstance(res, dict):
                return response_schema.model_validate(res)
            return response_schema.model_validate(res.model_dump())
        
        # Return a dummy instance if possible
        try:
            return response_schema()
        except Exception:
            raise ValueError(f"No queued structured response for schema {response_schema.__name__}")
