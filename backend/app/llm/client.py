import asyncio
import logging
import re
import time
from contextlib import contextmanager
from google import genai
from google.genai import types
from typing import Optional, Dict, Any, Type
from pydantic import BaseModel
from backend.app.config import settings

logger = logging.getLogger("llm.client")


class LLMUnavailableError(Exception):
    """Raised when the LLM backend is unusable (quota exhausted / outage).

    Callers MUST let this propagate instead of masking it with a canned
    fallback reply, so the API layer returns an honest service-trouble
    message rather than pretending to converse.
    """

    def __init__(self, message="LLM service unavailable", retry_after_s=0.0):
        super().__init__(message)
        self.retry_after_s = float(retry_after_s or 0.0)


# Fail-fast circuit breaker: after repeated consecutive LLM failures for a
# model, stop issuing network calls for a cooldown so turns fail in
# milliseconds (instead of hanging on retries) and quota isn't burned.
_CIRCUIT_THRESHOLD = 3
_CIRCUIT_COOLDOWN_S = 300.0
_circuit_state: Dict[str, Dict[str, float]] = {}

# A quota retry-delay longer than this means "exhausted for hours" — fail fast
# instead of sleeping through pointless retries.
_QUOTA_FAST_FAIL_S = 120.0

# Tried in order when the configured model is unavailable. Google fronts these
# endpoints for capacity independently, so when one returns 503 (high demand)
# the next is usually healthy — this keeps a single flaky model from degrading
# every conversational turn into a canned fallback.
_FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-2.5-flash")


def _is_retryable(err_str: str) -> bool:
    """Transient failures worth another attempt.

    Covers quota (429) and capacity/server errors (500/502/503/504,
    UNAVAILABLE, overloaded). These routinely clear in seconds, so retrying is
    correct; any other 4xx is a real request problem and is raised at once.
    """
    return (
        "429" in err_str
        or "RESOURCE_EXHAUSTED" in err_str
        or "UNAVAILABLE" in err_str
        or "InternalServerError" in err_str
        or "overloaded" in err_str.lower()
        or bool(re.search(r"\b50[0234]\b", err_str))
    )


def _parse_retry_delay_s(err_str: str) -> Optional[float]:
    match = re.search(r"retryDelay[\"']:\s*[\"'](\d+)s[\"']", err_str)
    if match:
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


def _circuit_check(model: str) -> None:
    st = _circuit_state.get(model)
    if st and st.get("open_until", 0.0) > time.monotonic():
        raise LLMUnavailableError(
            f"LLM model {model} is cooling down after repeated failures",
            retry_after_s=max(0.0, st["open_until"] - time.monotonic()),
        )


def _circuit_record(model: str, ok: bool) -> None:
    st = _circuit_state.setdefault(model, {"fails": 0.0, "open_until": 0.0})
    if ok:
        st["fails"] = 0.0
        st["open_until"] = 0.0
    else:
        st["fails"] = float(st.get("fails", 0.0)) + 1.0
        if st["fails"] >= _CIRCUIT_THRESHOLD:
            st["open_until"] = time.monotonic() + _CIRCUIT_COOLDOWN_S

class LLMClient:
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or settings.gemini_api_key_1
        self.model = model
        self._client: Optional[genai.Client] = None

    @contextmanager
    def using_model(self, model: str):
        """Route calls to `model` for the duration of one block, then restore.

        Used for short, mechanical calls - matching an ingredient to a plain
        food word - where a reasoning-tier model adds latency without adding
        accuracy. The agent's own configured model is always put back on the
        way out, including when the block raises, so no later step can inherit
        the override.
        """
        previous = self.model
        if model and model != previous:
            self.model = model
        try:
            yield self
        finally:
            self.model = previous

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
        Raises LLMUnavailableError (fast, no retries) when the backend reports
        an hours-long quota exhaustion or the circuit breaker is open.
        """
        config = types.GenerateContentConfig()
        if system_instruction:
            config.system_instruction = system_instruction

        _circuit_check(self.model)
        models = [self.model] + [m for m in _FALLBACK_MODELS if m != self.model]
        last_err: Optional[Exception] = None

        for model_index, model in enumerate(models):
            if model_index > 0:
                logger.warning(
                    f"[LLM] {self.model} unavailable; retrying this call on {model}"
                )
            for attempt in range(3):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config
                    )
                    _circuit_record(self.model, True)
                    return response.text or ""
                except LLMUnavailableError:
                    raise
                except Exception as e:
                    err_str = str(e)
                    last_err = e
                    if not _is_retryable(err_str):
                        # Real request problem (bad key, bad schema, 4xx) —
                        # retrying or switching models will not help.
                        if model_index == 0:
                            _circuit_record(self.model, False)
                        raise
                    delay = _parse_retry_delay_s(err_str)
                    if delay is not None and delay > _QUOTA_FAST_FAIL_S:
                        # "Exhausted for hours" — fail fast, don't burn time.
                        if model_index == 0:
                            _circuit_record(self.model, False)
                        hours, remainder = divmod(int(delay), 3600)
                        if model_index == len(models) - 1:
                            raise LLMUnavailableError(
                                f"LLM quota exhausted for model {model} "
                                f"(retry in ~{hours}h{remainder // 60:02d}m)",
                                retry_after_s=delay,
                            )
                        break
                    if attempt < 2:
                        await asyncio.sleep(min(delay if delay else (attempt + 1) * 3, 15))
                        continue
                    break  # this model is unusable — try the next one
            if model_index == 0:
                _circuit_record(self.model, False)

        if last_err is not None:
            raise last_err
        raise LLMUnavailableError(f"No usable model available (tried {', '.join(models)})")

    async def generate_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        system_instruction: Optional[str] = None
    ) -> BaseModel:
        """
        Forces Gemini to return JSON strictly matching a Pydantic schema with rate-limit retry support.
        Uses temperature 0 so intent/parameter extraction is deterministic —
        the same message must always produce the same structured verdict.
        Raises LLMUnavailableError (fast, no retries) when the backend reports
        an hours-long quota exhaustion or the circuit breaker is open.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.0
        )
        if system_instruction:
            config.system_instruction = system_instruction

        _circuit_check(self.model)
        models = [self.model] + [m for m in _FALLBACK_MODELS if m != self.model]
        last_err: Optional[Exception] = None

        for model_index, model in enumerate(models):
            if model_index > 0:
                logger.warning(
                    f"[LLM] {self.model} unavailable; retrying this call on {model}"
                )
            for attempt in range(3):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config
                    )
                except LLMUnavailableError:
                    raise
                except Exception as e:
                    err_str = str(e)
                    last_err = e
                    if not _is_retryable(err_str):
                        if model_index == 0:
                            _circuit_record(self.model, False)
                        raise
                    delay = _parse_retry_delay_s(err_str)
                    if delay is not None and delay > _QUOTA_FAST_FAIL_S:
                        if model_index == 0:
                            _circuit_record(self.model, False)
                        hours, remainder = divmod(int(delay), 3600)
                        if model_index == len(models) - 1:
                            raise LLMUnavailableError(
                                f"LLM quota exhausted for model {model} "
                                f"(retry in ~{hours}h{remainder // 60:02d}m)",
                                retry_after_s=delay,
                            )
                        break
                    if attempt < 2:
                        await asyncio.sleep(min(delay if delay else (attempt + 1) * 3, 15))
                        continue
                    break
                else:
                    _circuit_record(self.model, True)
                    # Parse into Pydantic model (local step; failures here are
                    # not backend outages and must not trip the breaker).
                    if hasattr(response, "parsed") and response.parsed:
                        return response.parsed
                    return response_schema.model_validate_json(response.text)
            if model_index == 0:
                _circuit_record(self.model, False)

        if last_err is not None:
            raise last_err
        raise LLMUnavailableError(f"No usable model available (tried {', '.join(models)})")


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

