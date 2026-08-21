from __future__ import annotations

import json
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from eligibility.llm.client import (
    LLMProviderError,
    LLMStructuredOutputError,
    LLMTimeoutError,
)
from eligibility.llm.models import (
    LLMHealthStatus,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TextGenerationRequest,
    TextGenerationResponse,
    TokenUsage,
)
from eligibility.llm.transport import HTTPResponse, HTTPTransport, UrllibHTTPTransport


T = TypeVar("T", bound=BaseModel)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", flags=re.DOTALL | re.IGNORECASE)


class OpenAICompatibleAdapter:
    provider = "OPENAI_COMPATIBLE"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        transport: HTTPTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.transport = transport or UrllibHTTPTransport()

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/models"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def generate_text(self, request: TextGenerationRequest) -> TextGenerationResponse:
        payload = self._base_payload(request)
        started = time.perf_counter()
        response, retry_count = self._request_with_retry(
            "POST",
            self.chat_completions_url,
            payload,
            timeout_seconds=request.timeout_seconds,
        )
        parsed = self._parse_completion(response)
        return TextGenerationResponse(
            text=parsed["content"],
            provider=self.provider,
            model=parsed.get("model") or self.model,
            model_version=parsed.get("model_version"),
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            retry_count=retry_count,
            token_usage=parsed.get("token_usage"),
            raw_metadata=parsed.get("metadata", {}),
        )

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
        response_model: type[T],
    ) -> StructuredGenerationResponse[T]:
        payload = self._base_payload(request)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": request.response_schema_name,
                "strict": True,
                "schema": request.response_json_schema,
            },
        }
        started = time.perf_counter()
        response, retry_count = self._request_with_retry(
            "POST",
            self.chat_completions_url,
            payload,
            timeout_seconds=request.timeout_seconds,
        )
        parsed = self._parse_completion(response)
        raw_text = parsed["content"]
        fence_match = _JSON_FENCE_RE.match(raw_text.strip())
        if fence_match:
            raw_text = fence_match.group(1)
        try:
            decoded = json.loads(raw_text)
            data = response_model.model_validate(decoded)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise LLMStructuredOutputError(
                f"Provider structured response failed validation: {exc}"
            ) from exc
        return StructuredGenerationResponse[T](
            data=data,
            raw_text=raw_text,
            provider=self.provider,
            model=parsed.get("model") or self.model,
            model_version=parsed.get("model_version"),
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            retry_count=retry_count,
            token_usage=parsed.get("token_usage"),
            raw_metadata=parsed.get("metadata", {}),
        )

    def health_check(self) -> LLMHealthStatus:
        started = time.perf_counter()
        try:
            response, _ = self._request_with_retry(
                "GET",
                self.models_url,
                None,
                timeout_seconds=self.timeout_seconds,
            )
            healthy = 200 <= response.status_code < 300
            detail = f"HTTP {response.status_code}"
        except Exception as exc:  # Health checks return state instead of raising.
            healthy = False
            detail = f"{type(exc).__name__}: {exc}"
        return LLMHealthStatus(
            healthy=healthy,
            provider=self.provider,
            model=self.model,
            detail=detail,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )

    def _base_payload(self, request: TextGenerationRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [message.model_dump(mode="json") for message in request.messages],
            "temperature": request.temperature,
        }

    def _request_with_retry(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        *,
        timeout_seconds: float,
    ) -> tuple[HTTPResponse, int]:
        retry_count = 0
        while True:
            try:
                response = self.transport.request(
                    method,
                    url,
                    headers=self._headers(),
                    json_body=payload,
                    timeout_seconds=timeout_seconds,
                )
            except LLMTimeoutError:
                if retry_count >= self.max_retries:
                    raise
                retry_count += 1
                continue

            if 200 <= response.status_code < 300:
                return response, retry_count
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and retry_count < self.max_retries:
                retry_count += 1
                continue
            detail = response.body.decode("utf-8", errors="replace")[:500]
            raise LLMProviderError(
                f"LLM provider returned HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
            )

    @staticmethod
    def _parse_completion(response: HTTPResponse) -> dict[str, Any]:
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Provider response has no assistant content") from exc
        if not isinstance(content, str):
            # Some local servers return typed content parts.
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", "")) if isinstance(part, dict) else str(part)
                    for part in content
                )
            else:
                content = str(content)
        usage_raw = body.get("usage") or {}
        usage = None
        if usage_raw:
            usage = TokenUsage(
                prompt_tokens=usage_raw.get("prompt_tokens"),
                completion_tokens=usage_raw.get("completion_tokens"),
                total_tokens=usage_raw.get("total_tokens"),
            )
        return {
            "content": content,
            "model": body.get("model"),
            "model_version": body.get("system_fingerprint"),
            "token_usage": usage,
            "metadata": {"response_id": body.get("id")},
        }
