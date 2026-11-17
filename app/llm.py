"""LLM provider abstraction. Provider-specific code stays here. Without a key the system uses the
deterministic local heuristics (clearly reported as engine 'local-heuristic')."""
import json
import re
from typing import Type

import httpx
from pydantic import BaseModel, ValidationError

from .config import settings
from .errors import AEOPError, PermanentError, RetryableError


class LLMError(AEOPError):
    pass


def llm_enabled() -> bool:
    return settings.LLM_PROVIDER == "gemini" and bool(settings.GEMINI_API_KEY)


def engine_name() -> str:
    return f"gemini:{settings.GEMINI_MODEL}" if llm_enabled() else "local-heuristic"


def _strip(txt: str) -> str:
    txt = txt.strip()
    txt = re.sub(r"^```(?:json)?", "", txt).strip()
    return re.sub(r"```$", "", txt).strip()


def generate_json(system: str, user: str, model_cls: Type[BaseModel], retries: int = 1):
    """Returns (validated_pydantic_object, usage). Output is ALWAYS validated against model_cls."""
    if not llm_enabled():
        raise LLMError("LLM provider not configured")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent"
    schema_hint = json.dumps(model_cls.model_json_schema())
    last = None
    for _ in range(retries + 1):
        body = {
            "systemInstruction": {"parts": [{"text": system + "\nReturn ONLY JSON matching this schema:\n" + schema_hint}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        try:
            r = httpx.post(url, headers={"x-goog-api-key": settings.GEMINI_API_KEY}, json=body, timeout=settings.LLM_TIMEOUT)
        except httpx.TimeoutException as e:
            raise RetryableError(f"LLM timeout: {e}")
        except httpx.HTTPError as e:
            raise RetryableError(f"LLM network error: {e}")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"LLM provider status {r.status_code}")
        if r.status_code >= 400:
            raise PermanentError(f"LLM provider rejected request ({r.status_code})")
        data = r.json()
        usage = {"in": data.get("usageMetadata", {}).get("promptTokenCount", 0),
                 "out": data.get("usageMetadata", {}).get("candidatesTokenCount", 0)}
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return model_cls.model_validate_json(_strip(text)), usage
        except (KeyError, IndexError, ValidationError, ValueError) as e:
            last = e
            user += "\n\nYour previous output was invalid. Return strictly valid JSON for the schema."
    raise LLMError(f"LLM returned invalid structured output: {str(last)[:200]}")
