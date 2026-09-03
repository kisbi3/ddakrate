from __future__ import annotations

import os
from enum import StrEnum
from typing import Any, Generic, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from eligibility.audit.models import LLMPayloadMode
from eligibility.llm.model_registry import (
    DEFAULT_OPENAI_MODEL,
    resolve_openai_responses_model_profile,
)


class LLMPurpose(StrEnum):
    RULE_EXTRACTION = "RULE_EXTRACTION"
    SERVICE_EXTRACTION = "SERVICE_EXTRACTION"
    INTENT_PARSING = "INTENT_PARSING"
    CONVERSATION_ORCHESTRATION = "CONVERSATION_ORCHESTRATION"
    INTENT_CLARIFICATION = "INTENT_CLARIFICATION"
    QUESTION_GENERATION = "QUESTION_GENERATION"
    RESULT_EXPLANATION = "RESULT_EXPLANATION"
    ELIGIBILITY_TEXT_REVIEW = "ELIGIBILITY_TEXT_REVIEW"


class LLMRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: LLMRole
    content: str


class LLMProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: LLMPurpose
    prompt_template_id: str
    prompt_template_version: str
    response_schema_version: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30.0, gt=0)


class TextGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: LLMPurpose
    messages: list[LLMMessage]
    prompt_template_id: str
    prompt_template_version: str
    temperature: float = Field(ge=0.0, le=2.0)
    timeout_seconds: float = Field(gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredGenerationRequest(TextGenerationRequest):
    response_schema_version: str
    response_schema_name: str
    response_json_schema: dict[str, Any]


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class TextGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    provider: str
    model: str
    model_version: str | None = None
    latency_ms: int = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    token_usage: TokenUsage | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


T = TypeVar("T")


class StructuredGenerationResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: T
    raw_text: str
    provider: str
    model: str
    model_version: str | None = None
    latency_ms: int = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    token_usage: TokenUsage | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class LLMHealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    healthy: bool
    provider: str
    model: str
    detail: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = "MOCK"
    base_url: str = "http://localhost:8000/v1"
    model: str = "mock-model"
    api_key: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    log_payload_mode: LLMPayloadMode = LLMPayloadMode.REDACTED
    reasoning_effort: str | None = None

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().upper().replace("-", "_")

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("LLM_BASE_URL must not be empty")
        return value

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "LLMSettings":
        env = os.environ if environ is None else environ
        provider = env.get("LLM_PROVIDER", "MOCK").strip().upper().replace("-", "_")
        if provider == "OPENAI":
            default_base_url = "https://api.openai.com/v1"
            default_model = DEFAULT_OPENAI_MODEL
            api_key = env.get("LLM_API_KEY") or env.get("OPENAI_API_KEY") or None
        elif provider == "MOCK":
            default_base_url = "http://localhost:8000/v1"
            default_model = "mock-model"
            api_key = env.get("LLM_API_KEY") or None
        else:
            default_base_url = "http://localhost:8000/v1"
            default_model = "mock-model"
            api_key = env.get("LLM_API_KEY") or None

        base_url = cls._env_value_or_default(env, "LLM_BASE_URL", default_base_url)
        model = cls._env_value_or_default(env, "LLM_MODEL", default_model)
        reasoning_raw = env.get("LLM_REASONING_EFFORT")
        if provider == "OPENAI":
            model_profile = resolve_openai_responses_model_profile(model)
            reasoning_effort = model_profile.resolve_reasoning_effort(reasoning_raw)
        else:
            reasoning_effort = None
        return cls(
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS", "30")),
            max_retries=int(env.get("LLM_MAX_RETRIES", "2")),
            temperature=float(env.get("LLM_TEMPERATURE", "0")),
            log_payload_mode=LLMPayloadMode(
                env.get("LLM_LOG_PAYLOAD_MODE", "REDACTED").upper()
            ),
            reasoning_effort=reasoning_effort,
        )

    @staticmethod
    def _env_value_or_default(
        env: Mapping[str, str],
        name: str,
        default: str,
    ) -> str:
        value = env.get(name)
        if value is None or value.strip().lower() in {"", "auto"}:
            return default
        return value.strip()
