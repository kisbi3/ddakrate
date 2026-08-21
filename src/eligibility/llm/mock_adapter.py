from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from eligibility.llm.client import LLMProviderError, LLMStructuredOutputError
from eligibility.llm.models import (
    LLMHealthStatus,
    LLMPurpose,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TextGenerationRequest,
    TextGenerationResponse,
)


T = TypeVar("T", bound=BaseModel)


class MockLLMAdapter:
    provider = "MOCK"

    def __init__(
        self,
        responses: Mapping[LLMPurpose | str, Any] | None = None,
        *,
        model: str = "mock-model",
    ) -> None:
        self.model = model
        self._responses: dict[str, deque[Any]] = defaultdict(deque)
        for purpose, configured in (responses or {}).items():
            key = purpose.value if isinstance(purpose, LLMPurpose) else str(purpose)
            if isinstance(configured, list):
                self._responses[key].extend(configured)
            else:
                self._responses[key].append(configured)
        self.call_history: list[TextGenerationRequest | StructuredGenerationRequest] = []

    def enqueue(self, purpose: LLMPurpose | str, response: Any) -> None:
        key = purpose.value if isinstance(purpose, LLMPurpose) else str(purpose)
        self._responses[key].append(response)

    def _next(self, purpose: LLMPurpose) -> Any:
        queue = self._responses[purpose.value]
        if not queue:
            raise LLMProviderError(f"No mock response configured for {purpose.value}")
        item = queue.popleft()
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return item

    def generate_text(self, request: TextGenerationRequest) -> TextGenerationResponse:
        self.call_history.append(request)
        started = time.perf_counter()
        response = self._next(request.purpose)
        if isinstance(response, BaseModel):
            response = response.model_dump(mode="json")
        text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        return TextGenerationResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
        response_model: type[T],
    ) -> StructuredGenerationResponse[T]:
        self.call_history.append(request)
        started = time.perf_counter()
        response = self._next(request.purpose)
        if isinstance(response, BaseModel):
            response = response.model_dump(mode="json")
        raw_text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        try:
            raw = json.loads(response) if isinstance(response, str) else response
            data = response_model.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise LLMStructuredOutputError(
                f"Mock structured response failed validation: {exc}"
            ) from exc
        return StructuredGenerationResponse[T](
            data=data,
            raw_text=raw_text,
            provider=self.provider,
            model=self.model,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )

    def health_check(self) -> LLMHealthStatus:
        return LLMHealthStatus(
            healthy=True,
            provider=self.provider,
            model=self.model,
            detail="Mock adapter is available",
            latency_ms=0,
        )
