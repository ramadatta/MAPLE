"""LLM service with OpenAI and GitLab Duo backends."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, Protocol, Type, TypeVar, runtime_checkable

from openai import OpenAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# LLM response caching (opt-out via MAPLE_ENABLE_LLM_CACHE=false). Identical
# (model, prompt, schema) requests are served from disk, so re-running the same
# marker query is near-instant and costs no tokens.
_LLM_CACHE_ENABLED = os.getenv("MAPLE_ENABLE_LLM_CACHE", "true").lower() not in ("0", "false", "no")


def _llm_cache_key(model: str, full_system: str, user: str, schema_name: str) -> str:
    return json.dumps(
        {"model": model, "system": full_system, "user": user, "schema": schema_name},
        sort_keys=True,
    )

SCIENTIFIC_SYSTEM_INSTRUCTION = (
    "You are a careful computational biologist. You must distinguish marker-based "
    "inference from literature-supported evidence. Never invent PMIDs, paper titles, "
    "or evidence. If evidence is weak or absent, say so explicitly."
)


@runtime_checkable
class LLMServiceBase(Protocol):
    """Common interface for LLM backends."""

    def complete_json(
        self,
        system: str,
        user: str,
        schema: Type[T],
        extra_system: Optional[str] = None,
    ) -> T: ...

    def complete_text(
        self,
        system: str,
        user: str,
        extra_system: Optional[str] = None,
    ) -> str: ...


def fallback_instance(schema: Type[T]) -> T:
    """Create a minimal valid instance when LLM parsing fails."""
    try:
        fields = schema.model_fields
        defaults = {}
        for name, field_info in fields.items():
            if field_info.is_required():
                annotation = field_info.annotation
                if annotation is str:
                    defaults[name] = ""
                elif annotation is float:
                    defaults[name] = 0.0
                elif annotation is int:
                    defaults[name] = 0
                elif hasattr(annotation, "__origin__"):
                    defaults[name] = []
                else:
                    defaults[name] = None
        return schema.model_validate(defaults)
    except Exception:
        return schema.model_construct()


def is_blablador_config() -> bool:
    """True when OPENAI_BASE_URL points at the Blablador API."""
    return "blablador" in os.getenv("OPENAI_BASE_URL", "").lower()


def llm_provider_label(api_key: str, provider: Optional[str] = None) -> str:
    """Human-readable provider name for UI messages."""
    selected = detect_llm_provider(api_key, provider)
    if selected == "gitlab":
        return "GitLab Duo"
    if is_blablador_config():
        return "Blablador"
    return "OpenAI"


def detect_llm_provider(api_key: str, provider: Optional[str] = None) -> str:
    """Auto-detect LLM provider from token prefix or env."""
    if provider and provider != "auto":
        return provider.lower()
    env_provider = os.getenv("LLM_PROVIDER", "auto").lower()
    if env_provider != "auto":
        return env_provider
    # Helmholtz Codebase glpat tokens used with Blablador are OpenAI-compatible.
    if os.getenv("OPENAI_BASE_URL", "").strip():
        return "openai"
    if api_key.startswith(("glpat-", "glptt-", "glpat_")):
        return "gitlab"
    return "openai"


def create_llm_service(api_key: str, provider: Optional[str] = None) -> LLMServiceBase:
    """
    Create an LLM service for OpenAI or GitLab Duo.

    GitLab tokens (glpat-...) route to GitLabLLMService automatically.
    Set LLM_PROVIDER=gitlab|openai to override auto-detection.
    """
    selected = detect_llm_provider(api_key, provider)
    if selected == "gitlab":
        from services.gitlab_llm_service import GitLabLLMService

        return GitLabLLMService(api_key=api_key)
    return OpenAILLMService(api_key=api_key)


class OpenAILLMService:
    """OpenAI Responses API backend (also used for Blablador via OPENAI_BASE_URL)."""

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        resolved_base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "").strip() or None
        self.client = OpenAI(api_key=api_key, base_url=resolved_base_url)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: Type[T],
        extra_system: Optional[str] = None,
    ) -> T:
        full_system = SCIENTIFIC_SYSTEM_INSTRUCTION
        if extra_system:
            full_system = f"{full_system}\n\n{extra_system}"
        if system:
            full_system = f"{full_system}\n\n{system}"

        json_schema = schema.model_json_schema()
        schema_instruction = (
            f"\n\nReturn ONLY valid JSON matching this schema:\n{json.dumps(json_schema, indent=2)}"
        )

        # ── Cache lookup ──────────────────────────────────────────────────────
        cache = None
        cache_key = ""
        if _LLM_CACHE_ENABLED:
            try:
                from services.cache_service import get_cache

                cache = get_cache()
                cache_key = _llm_cache_key(
                    self.model, full_system + schema_instruction, user, schema.__name__
                )
                cached = cache.get("llm_json", cache_key)
                if cached is not None:
                    return schema.model_validate(cached)
            except Exception as exc:  # cache must never break the call path
                logger.debug("LLM cache lookup skipped: %s", exc)
                cache = None

        for attempt in range(2):
            try:
                prompt = user
                if attempt == 1:
                    prompt = (
                        f"{user}\n\nYour previous response was not valid JSON. "
                        "Return ONLY valid JSON — no markdown, no explanation."
                    )

                messages = [
                    {"role": "system", "content": full_system + schema_instruction},
                    {"role": "user", "content": prompt},
                ]

                # Prefer json_object mode (supported by most providers); fall back to plain.
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        response_format={"type": "json_object"},
                    )
                except Exception:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                    )

                text = response.choices[0].message.content or ""
                data = json.loads(text)
                validated = schema.model_validate(data)
                # Cache only successful parses (never the empty fallback).
                if cache is not None:
                    try:
                        cache.set("llm_json", cache_key, validated.model_dump(mode="json"))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("LLM cache store skipped: %s", exc)
                return validated
            except (json.JSONDecodeError, ValidationError, Exception) as exc:
                logger.warning("LLM JSON attempt %d failed: %s", attempt + 1, exc)
                if attempt == 1:
                    return fallback_instance(schema)

        return fallback_instance(schema)

    def complete_text(
        self,
        system: str,
        user: str,
        extra_system: Optional[str] = None,
    ) -> str:
        full_system = SCIENTIFIC_SYSTEM_INSTRUCTION
        if extra_system:
            full_system = f"{full_system}\n\n{extra_system}"
        if system:
            full_system = f"{full_system}\n\n{system}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("LLM text call failed: %s", exc)
            return ""


# Backward-compatible alias
LLMService = OpenAILLMService
