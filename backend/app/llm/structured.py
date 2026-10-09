from typing import Type, TypeVar, Any
from pydantic import BaseModel, ValidationError
import json

T = TypeVar("T", bound=BaseModel)

def parse_and_validate_json(raw_text: str, schema: Type[T]) -> T:
    """
    Parses a JSON string and validates it against a Pydantic schema.
    Strips markdown code blocks if present (e.g. ```json ... ```).
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    data = json.loads(cleaned)
    return schema.model_validate(data)
