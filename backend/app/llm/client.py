import asyncio
import re
from google import genai
from google.genai import types
from typing import Optional, Dict, Any, Type
from pydantic import BaseModel
from backend.app.config import settings

class LLMClient:
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or settings.gemini_api_key_1
        self.model = model
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY is not configured in .env")
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def generate_text(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """
        Generates text using the Gemini API with rate-limit retry support.
        """
        config = types.GenerateContentConfig()
        if system_instruction:
            config.system_instruction = system_instruction

        for attempt in range(3):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config
                )
                return response.text or ""
            except Exception as e:
                err_str = str(e)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < 2:
                    match = re.search(r"retryDelay[\"']:\s*[\"'](\d+)s[\"']", err_str)
                    delay = int(match.group(1)) + 1 if match else (attempt + 1) * 3
                    await asyncio.sleep(min(delay, 15))
                    continue
                raise

    async def generate_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        system_instruction: Optional[str] = None
    ) -> BaseModel:
        """
        Forces Gemini to return JSON strictly matching a Pydantic schema with rate-limit retry support.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema
        )
        if system_instruction:
            config.system_instruction = system_instruction

        for attempt in range(3):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config
                )
                # Parse into Pydantic model
                if hasattr(response, "parsed") and response.parsed:
                    return response.parsed
                return response_schema.model_validate_json(response.text)
            except Exception as e:
                err_str = str(e)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < 2:
                    match = re.search(r"retryDelay[\"']:\s*[\"'](\d+)s[\"']", err_str)
                    delay = int(match.group(1)) + 1 if match else (attempt + 1) * 3
                    await asyncio.sleep(min(delay, 15))
                    continue
                raise


llm_client = LLMClient()

def get_llm_client(agent_name: str) -> LLMClient:
    """
    Factory that returns an LLMClient configured with the specific Gemini API key
    and model tailored to the agent's role, isolating rate limits:

    Group 1 (API Key 1):
      - Supervisor: settings.supervisor_model (gemini-2.5-flash)
      - Planner: settings.planner_model (gemini-3.5-flash-lite)

    Group 2 (API Key 2):
      - Nutrition: settings.nutrition_model (gemini-3.5-flash-lite)
      - Allergy: settings.allergy_model (gemini-3.5-flash)
      - Shopper: settings.shopper_model (gemini-3.1-flash-lite)
    """
    name = (agent_name or "").lower().replace("_agent", "")
    key1 = settings.gemini_api_key_1
    key2 = settings.gemini_api_key_2 or key1

    if "supervisor" in name:
        return LLMClient(api_key=key1, model=settings.supervisor_model)
    elif "planner" in name:
        return LLMClient(api_key=key1, model=settings.planner_model)
    elif "nutrition" in name:
        return LLMClient(api_key=key2, model=settings.nutrition_model)
    elif "allergy" in name:
        return LLMClient(api_key=key2, model=settings.allergy_model)
    elif "shopper" in name:
        return LLMClient(api_key=key2, model=settings.shopper_model)

    return LLMClient(api_key=key1, model="gemini-2.5-flash")
    return LLMClient(api_key=key1, model="gemini-3.5-flash-lite")

