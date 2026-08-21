from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from eligibility.audit import (
    AuditEventType,
    AuditSession,
    InMemoryAuditSink,
    LLMPayloadMode,
    format_event_sequence,
)
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.schema.enums import FactSemanticType, FactSourceType
from eligibility.schema.user_fact import UserFact


def test_audit_events_are_append_only():
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-A", trace_id="TRACE-A")
    first = audit.emit("TEST", AuditEventType.REQUEST_RECEIVED)
    snapshot = sink.events
    audit.emit("TEST", AuditEventType.STATUS_DERIVED)

    assert isinstance(snapshot, tuple)
    assert snapshot == (first,)
    assert len(sink.events) == 2
    with pytest.raises(ValidationError):
        first.event_id = "MUTATED"  # type: ignore[misc]


def test_trace_ids_propagate():
    sink = InMemoryAuditSink()
    audit = AuditSession(
        sink,
        request_id="REQ-TRACE-001",
        trace_id="TRACE-TRACE-001",
    )
    result = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
        audit=audit,
    )

    assert sink.events
    assert {event.request_id for event in sink.events} == {"REQ-TRACE-001"}
    assert {event.trace_id for event in sink.events} == {"TRACE-TRACE-001"}
    assert {event.evaluation_id for event in sink.events} == {result.evaluation_id}
    rule_events = [
        event for event in sink.events if event.component == "RULE_EVALUATOR"
    ]
    assert any(event.parent_span_id is not None for event in rule_events)


def test_fact_source_selection_logged():
    store = salary_envelope_user()
    store = store.with_fact(
        UserFact(
            fact_id="F-LOW-PRIORITY-ELIGIBLE",
            user_id=store.user_id,
            fact_type="SHINHAN_GOLDEN_ELIGIBLE",
            value=True,
            source_type=FactSourceType.USER_DECLARED,
            semantic_type=FactSemanticType.OBSERVED_FACT,
            valid_from=date(2026, 8, 19),
            collected_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
    )
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-SOURCE", trace_id="TRACE-SOURCE")

    FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(), store, salary_envelope_context(), audit=audit
    )

    selected = [
        event
        for event in sink.events
        if event.event_type == AuditEventType.FACT_SOURCE_SELECTED
        and event.entity_refs.get("fact_type") == "SHINHAN_GOLDEN_ELIGIBLE"
    ]
    assert len(selected) == 1
    assert selected[0].payload["source_type"] == "INSTITUTION_VERIFIED"
    assert selected[0].payload["selected_fact_id"] == "F-SHINHAN-GOLDEN-ELIGIBLE"


def test_aggregation_execution_logged():
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-AGG", trace_id="TRACE-AGG")
    FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
        audit=audit,
    )

    aggregation = next(
        event
        for event in sink.events
        if event.event_type == AuditEventType.AGGREGATION_EXECUTED
    )
    assert aggregation.entity_refs["rule_id"] == "RATE_SALARY_ENVELOPE_6M"
    assert aggregation.payload["aggregation"] == "COUNT_DISTINCT_PERIODS"
    assert aggregation.payload["period"] == "MONTH"


def test_sensitive_credentials_not_logged():
    sink = InMemoryAuditSink()
    audit = AuditSession(
        sink,
        request_id="REQ-SECRET",
        trace_id="TRACE-SECRET",
        llm_payload_mode=LLMPayloadMode.FULL_DEBUG,
    )
    audit.emit(
        "LLM_GATEWAY",
        AuditEventType.LLM_CALL_STARTED,
        payload={
            "LLM_API_KEY": "super-secret-key",
            "Authorization": "Bearer actual-token",
            "password": "actual-password",
            "safe": "visible",
        },
    )

    serialized = json.dumps(
        [event.model_dump(mode="json") for event in sink.events],
        ensure_ascii=False,
    )
    assert "super-secret-key" not in serialized
    assert "actual-token" not in serialized
    assert "actual-password" not in serialized
    assert "[REDACTED]" in serialized
    assert "visible" in serialized


def test_audit_replay_sequence_is_human_readable():
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-R", trace_id="TRACE-R")
    audit.emit("APP", AuditEventType.REQUEST_RECEIVED)
    audit.emit("ENGINE", AuditEventType.STATUS_DERIVED)

    rendered = format_event_sequence(sink.events)

    assert "REQUEST_RECEIVED" in rendered
    assert "STATUS_DERIVED" in rendered
    assert "REQ-R / TRACE-R" in rendered
