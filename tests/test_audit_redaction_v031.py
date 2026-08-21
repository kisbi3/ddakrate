from __future__ import annotations

import json

from eligibility.audit import (
    AuditEventType,
    AuditSession,
    InMemoryAuditSink,
    LLMPayloadMode,
)


def test_all_sensitive_key_families_are_redacted_recursively():
    secrets = {
        "api_key": "SECRET-api_key",
        "apikey": "SECRET-apikey",
        "access_token": "SECRET-access_token",
        "refresh_token": "SECRET-refresh_token",
        "id_token": "SECRET-id_token",
        "token": "SECRET-token",
        "api_token": "SECRET-api_token",
        "authorization": "Bearer SECRET-authorization",
        "password": "SECRET-password",
        "secret": "SECRET-secret",
        "client_secret": "SECRET-client_secret",
    }
    payload = {
        "top": secrets,
        "nested": [
            {"LLM_API_KEY": "SECRET-llm-api-key"},
            {"credentials": [{"api_token": "SECRET-deep-api-token"}]},
        ],
        "safe": "visible",
    }
    sink = InMemoryAuditSink()
    audit = AuditSession(
        sink,
        request_id="REQ-REDACT-V031",
        trace_id="TRACE-REDACT-V031",
        llm_payload_mode=LLMPayloadMode.FULL_DEBUG,
    )

    event = audit.emit(
        "TEST",
        AuditEventType.LLM_CALL_STARTED,
        payload=payload,
        payload_mode=LLMPayloadMode.FULL_DEBUG,
    )
    serialized = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)

    for secret in [*secrets.values(), "SECRET-llm-api-key", "SECRET-deep-api-token"]:
        assert secret not in serialized
    assert event.payload["top"]["token"] == "[REDACTED]"
    assert event.payload["nested"][0]["LLM_API_KEY"] == "[REDACTED]"
    assert event.payload["safe"] == "visible"
