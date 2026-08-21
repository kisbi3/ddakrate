from __future__ import annotations

import json
from collections import deque
from typing import Any

import pytest
from pydantic import BaseModel

from eligibility.audit import AuditSession, InMemoryAuditSink
from eligibility.llm import (
    LLMGateway,
    LLMPurpose,
    LLMSettings,
    LLMStructuredOutputError,
    LLMTimeoutError,
    MockLLMAdapter,
    OpenAICompatibleAdapter,
)
from eligibility.llm.models import (
    LLMMessage,
    LLMRole,
    StructuredGenerationRequest,
)
from eligibility.llm.transport import HTTPResponse


class Answer(BaseModel):
    answer: int


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        purpose=LLMPurpose.RULE_EXTRACTION,
        messages=[LLMMessage(role=LLMRole.USER, content="extract")],
        prompt_template_id="test",
        prompt_template_version="1",
        response_schema_version="1",
        response_schema_name="Answer",
        response_json_schema=Answer.model_json_schema(),
        temperature=0,
        timeout_seconds=2,
    )


class FakeTransport:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        timeout_seconds: float,
    ) -> HTTPResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "timeout_seconds": timeout_seconds,
            }
        )
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _success_response(content: str = '{"answer": 7}') -> HTTPResponse:
    body = {
        "id": "resp-1",
        "model": "local-model",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }
    return HTTPResponse(200, json.dumps(body).encode(), {})


def test_mock_structured_generation():
    gateway = LLMGateway(
        MockLLMAdapter({LLMPurpose.RULE_EXTRACTION: {"answer": 42}})
    )

    response = gateway.generate_structured(
        LLMPurpose.RULE_EXTRACTION, "extract", Answer
    )

    assert response.data.answer == 42
    assert response.provider == "MOCK"


def test_openai_compatible_configuration():
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "OPENAI_COMPATIBLE",
            "LLM_BASE_URL": "http://localhost:11434/v1/",
            "LLM_MODEL": "qwen-local",
            "LLM_API_KEY": "local-key",
            "LLM_TIMEOUT_SECONDS": "5",
            "LLM_MAX_RETRIES": "0",
            "LLM_TEMPERATURE": "0",
            "LLM_LOG_PAYLOAD_MODE": "HASHED",
        }
    )
    transport = FakeTransport([_success_response()])
    adapter = OpenAICompatibleAdapter(
        base_url=settings.base_url,
        model=settings.model,
        api_key=settings.api_key,
        timeout_seconds=settings.timeout_seconds,
        max_retries=settings.max_retries,
        transport=transport,
    )

    response = adapter.generate_structured(_request(), Answer)

    assert response.data.answer == 7
    assert transport.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer local-key"
    assert transport.calls[0]["json_body"]["model"] == "qwen-local"
    assert transport.calls[0]["json_body"]["response_format"]["type"] == "json_schema"


def test_timeout_normalization():
    transport = FakeTransport(
        [LLMTimeoutError("timeout-1"), LLMTimeoutError("timeout-2")]
    )
    adapter = OpenAICompatibleAdapter(
        base_url="http://local/v1",
        model="model",
        max_retries=1,
        transport=transport,
    )

    with pytest.raises(LLMTimeoutError):
        adapter.generate_structured(_request(), Answer)

    assert len(transport.calls) == 2


def test_retry_behavior():
    transport = FakeTransport(
        [HTTPResponse(500, b'{"error":"temporary"}', {}), _success_response()]
    )
    adapter = OpenAICompatibleAdapter(
        base_url="https://external.example/v1",
        model="model",
        max_retries=2,
        transport=transport,
    )

    response = adapter.generate_structured(_request(), Answer)

    assert response.data.answer == 7
    assert response.retry_count == 1
    assert len(transport.calls) == 2


def test_invalid_structured_output():
    gateway = LLMGateway(
        MockLLMAdapter({LLMPurpose.RULE_EXTRACTION: '{"answer":"not-int"}'})
    )

    with pytest.raises(LLMStructuredOutputError):
        gateway.generate_structured(LLMPurpose.RULE_EXTRACTION, "extract", Answer)


def test_llm_audit_does_not_capture_api_key():
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-LLM", trace_id="TRACE-LLM")
    gateway = LLMGateway(
        MockLLMAdapter({LLMPurpose.RULE_EXTRACTION: {"answer": 1}}),
        audit=audit,
    )

    gateway.generate_structured(LLMPurpose.RULE_EXTRACTION, "extract", Answer)

    serialized = json.dumps(
        [event.model_dump(mode="json") for event in sink.events]
    )
    assert "Authorization" not in serialized
    assert "LLM_API_KEY" not in serialized
    assert "LLM_CALL_STARTED" in serialized
    assert "LLM_CALL_COMPLETED" in serialized


def test_gateway_from_settings_applies_global_timeout_temperature_and_payload_mode():
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "MOCK",
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "mock-configured",
            "LLM_TIMEOUT_SECONDS": "9",
            "LLM_MAX_RETRIES": "1",
            "LLM_TEMPERATURE": "0.25",
            "LLM_LOG_PAYLOAD_MODE": "HASHED",
        }
    )
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-SETTINGS", trace_id="TRACE-SETTINGS")
    gateway = LLMGateway.from_settings(
        settings,
        mock_responses={LLMPurpose.RULE_EXTRACTION: {"answer": 9}},
        audit=audit,
    )

    response = gateway.generate_structured(
        LLMPurpose.RULE_EXTRACTION, "extract", Answer
    )

    assert response.data.answer == 9
    request = gateway.client.call_history[0]  # type: ignore[attr-defined]
    assert request.temperature == 0.25
    assert request.timeout_seconds == 9
    assert audit.llm_payload_mode.value == "HASHED"
    assert all(event.payload["provider"] == "MOCK" for event in sink.events)
    assert all(event.payload["model"] == "mock-configured" for event in sink.events)
    assert all(event.payload["llm_log_payload_mode"] == "HASHED" for event in sink.events)
    assert all("request_payload_hash" in event.payload for event in sink.events)
    assert all("request_payload" not in event.payload for event in sink.events)


def test_openai_compatible_adapter_allows_local_server_without_api_key():
    transport = FakeTransport([_success_response()])
    adapter = OpenAICompatibleAdapter(
        base_url="http://localhost:1234/v1",
        model="local-model",
        api_key=None,
        max_retries=0,
        transport=transport,
    )

    adapter.generate_structured(_request(), Answer)

    assert "Authorization" not in transport.calls[0]["headers"]


def test_llm_payload_mode_none_keeps_safe_metadata_but_no_raw_payload():
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "MOCK",
            "LLM_MODEL": "mock-none",
            "LLM_LOG_PAYLOAD_MODE": "NONE",
        }
    )
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-NONE", trace_id="TRACE-NONE")
    gateway = LLMGateway.from_settings(
        settings,
        mock_responses={LLMPurpose.RULE_EXTRACTION: {"answer": 1}},
        audit=audit,
    )

    gateway.generate_structured(LLMPurpose.RULE_EXTRACTION, "private prompt", Answer)

    assert all(event.payload["provider"] == "MOCK" for event in sink.events)
    assert all(event.payload["request_payload_stored"] is False for event in sink.events)
    serialized = json.dumps([event.payload for event in sink.events])
    assert "private prompt" not in serialized


def test_llm_payload_mode_full_debug_still_redacts_credentials():
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "MOCK",
            "LLM_MODEL": "mock-debug",
            "LLM_LOG_PAYLOAD_MODE": "FULL_DEBUG",
        }
    )
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-DEBUG", trace_id="TRACE-DEBUG")
    gateway = LLMGateway.from_settings(
        settings,
        mock_responses={LLMPurpose.RULE_EXTRACTION: {"answer": 2}},
        audit=audit,
    )

    gateway.generate_structured(
        LLMPurpose.RULE_EXTRACTION,
        "synthetic prompt",
        Answer,
        metadata={"Authorization": "Bearer secret-token", "password": "secret-password"},
    )

    serialized = json.dumps(
        [event.model_dump(mode="json") for event in sink.events]
    )
    assert "synthetic prompt" in serialized
    assert "secret-token" not in serialized
    assert "secret-password" not in serialized
    assert "[REDACTED]" in serialized
