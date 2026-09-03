from __future__ import annotations

import json
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from eligibility.llm.client import (
    LLMConfigurationError,
    LLMProviderError,
    LLMStructuredOutputError,
    LLMTimeoutError,
)
from eligibility.llm.json_schema import openai_strict_json_schema
from eligibility.llm.model_registry import (
    OpenAIResponsesModelProfile,
    resolve_openai_responses_model_profile,
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


class OpenAIResponsesAdapter:
    """Native OpenAI Responses API adapter.

    ``OPENAI`` intentionally uses the Responses API rather than pretending that
    the provider is just another Chat Completions-compatible server.  Local and
    third-party OpenAI-compatible endpoints continue to use the separate
    ``OpenAICompatibleAdapter``.
    """

    provider = "OPENAI"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        reasoning_effort: str | None = None,
        model_profile: OpenAIResponsesModelProfile | None = None,
        transport: HTTPTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMConfigurationError(
                "OPENAI provider requires LLM_API_KEY or OPENAI_API_KEY"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.model_profile = model_profile or resolve_openai_responses_model_profile(
            model
        )
        try:
            self.reasoning_effort = self.model_profile.resolve_reasoning_effort(
                reasoning_effort
            )
        except ValueError as exc:
            raise LLMConfigurationError(str(exc)) from exc
        self.transport = transport or UrllibHTTPTransport()

    @property
    def responses_url(self) -> str:
        return f"{self.base_url}/responses"

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/models"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

    def generate_text(self, request: TextGenerationRequest) -> TextGenerationResponse:
        payload = self._base_payload(request)
        started = time.perf_counter()
        response, retry_count = self._request_with_retry(
            "POST",
            self.responses_url,
            payload,
            timeout_seconds=request.timeout_seconds,
        )
        parsed = self._parse_response(response)
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
        payload = self.debug_payload(request)
        started = time.perf_counter()
        response, retry_count = self._request_with_retry(
            "POST",
            self.responses_url,
            payload,
            timeout_seconds=request.timeout_seconds,
        )
        parsed = self._parse_response(response)
        raw_text = parsed["content"]
        fence_match = _JSON_FENCE_RE.match(raw_text.strip())
        if fence_match:
            raw_text = fence_match.group(1)
        try:
            decoded = json.loads(raw_text)
            data = response_model.model_validate(decoded)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise LLMStructuredOutputError(
                f"OpenAI structured response failed validation: {exc}"
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

    def debug_payload(
        self,
        request: TextGenerationRequest | StructuredGenerationRequest,
    ) -> dict[str, Any]:
        """Build the credential-free payload exactly as sent to Responses API."""

        payload = self._base_payload(request)
        if isinstance(request, StructuredGenerationRequest):
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.response_schema_name,
                    "schema": openai_strict_json_schema(
                        request.response_json_schema
                    ),
                    "strict": True,
                }
            }
        return payload

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
        except Exception as exc:  # Health checks report state instead of raising.
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
        payload: dict[str, Any] = {
            "model": self.model,
            "input": [message.model_dump(mode="json") for message in request.messages],
            # Financial-search prompts can contain user-derived data. Do not retain
            # API responses for later retrieval by default.
            "store": False,
        }
        payload.update(
            self.model_profile.generation_controls(
                temperature=request.temperature,
                reasoning_effort=self.reasoning_effort,
            )
        )
        return payload

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
            retryable = response.status_code in {408, 429} or response.status_code >= 500
            if retryable and retry_count < self.max_retries:
                retry_count += 1
                continue
            detail = response.body.decode("utf-8", errors="replace")[:800]
            raise LLMProviderError(
                f"OpenAI returned HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
            )

    @staticmethod
    def _parse_response(response: HTTPResponse) -> dict[str, Any]:
        body = response.json()
        if body.get("error"):
            raise LLMProviderError(f"OpenAI response error: {body['error']}")
        status = body.get("status")
        if status not in {None, "completed"}:
            raise LLMProviderError(
                f"OpenAI response did not complete (status={status!r})"
            )

        content = body.get("output_text")
        refusal_texts: list[str] = []
        if not isinstance(content, str) or not content:
            text_parts: list[str] = []
            for item in body.get("output") or []:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for part in item.get("content") or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
                    elif part.get("type") == "refusal":
                        refusal = part.get("refusal") or part.get("text")
                        if refusal:
                            refusal_texts.append(str(refusal))
            content = "".join(text_parts)

        if not content:
            if refusal_texts:
                raise LLMProviderError(
                    "OpenAI refused the requested generation: " + " ".join(refusal_texts)[:500]
                )
            raise LLMProviderError("OpenAI response has no output_text content")

        usage_raw = body.get("usage") or {}
        usage = None
        if usage_raw:
            usage = TokenUsage(
                prompt_tokens=usage_raw.get("input_tokens"),
                completion_tokens=usage_raw.get("output_tokens"),
                total_tokens=usage_raw.get("total_tokens"),
            )
        return {
            "content": content,
            "model": body.get("model"),
            "model_version": None,
            "token_usage": usage,
            "metadata": {
                "response_id": body.get("id"),
                "status": body.get("status"),
            },
        }
