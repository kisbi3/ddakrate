from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from eligibility.llm.models import (
    LLMHealthStatus,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TextGenerationRequest,
    TextGenerationResponse,
)


class LLMError(RuntimeError):
    error_code = "LLM_ERROR"


class LLMConfigurationError(LLMError):
    error_code = "LLM_CONFIGURATION_ERROR"


class LLMTimeoutError(LLMError):
    error_code = "LLM_TIMEOUT"


class LLMProviderError(LLMError):
    error_code = "LLM_PROVIDER_ERROR"

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMStructuredOutputError(LLMError):
    error_code = "INVALID_STRUCTURED_OUTPUT"


T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    provider: str
    model: str

    def generate_text(
        self,
        request: TextGenerationRequest,
    ) -> TextGenerationResponse:
        ...

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
        response_model: type[T],
    ) -> StructuredGenerationResponse[T]:
        ...

    def health_check(self) -> LLMHealthStatus:
        ...
