from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterator

from pydantic import BaseModel

from eligibility.audit.models import AuditEvent, AuditEventType, LLMPayloadMode
from eligibility.audit.sink import AuditSink


_SENSITIVE_KEY_COMPACT = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "token",
    "apitoken",
    "authorization",
    "password",
    "passwd",
    "secret",
    "clientsecret",
    "credential",
}


def _is_sensitive_key(key: str) -> bool:
    """Return True for credential-bearing key families, independent of style.

    Keys are compared after removing separators so ``api_key``, ``api-key``,
    ``apiKey`` and ``apikey`` receive the same treatment.  The comparison is
    intentionally exact after normalization: observability metadata such as
    ``token_usage`` is not itself an authentication token.
    """

    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(compact == name or compact.endswith(name) for name in _SENSITIVE_KEY_COMPACT)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def redact_sensitive(value: Any) -> Any:
    value = _jsonable(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_sensitive(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("bearer "):
        return "[REDACTED]"
    return value


class AuditSession:
    """Correlation and span manager shared across LLM, engine, goal, and alert.

    A session deliberately permits more than one deterministic evaluation ID in
    a request chain. Calling :meth:`bind_evaluation_id` changes the ID attached
    to subsequent events while preserving request/trace correlation.
    """

    def __init__(
        self,
        sink: AuditSink,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        evaluation_id: str | None = None,
        llm_payload_mode: LLMPayloadMode = LLMPayloadMode.REDACTED,
        captured_event_types: frozenset[AuditEventType] | None = None,
    ) -> None:
        self.sink = sink
        self.request_id = request_id or f"REQ-{uuid.uuid4().hex[:16]}"
        self.trace_id = trace_id or f"TRACE-{uuid.uuid4().hex[:16]}"
        self.evaluation_id = evaluation_id
        self.search_session_id: str | None = None
        self.intent_version: int | None = None
        self.ranking_run_id: str | None = None
        self.question_id: str | None = None
        self.recommendation_id: str | None = None
        self.llm_payload_mode = llm_payload_mode
        self.captured_event_types = captured_event_types
        self._span_stack: list[str] = []
        self._span_counter = 0

    def bind_evaluation_id(self, evaluation_id: str | None) -> None:
        self.evaluation_id = evaluation_id

    def bind_search_context(
        self,
        *,
        search_session_id: str | None = None,
        intent_version: int | None = None,
        ranking_run_id: str | None = None,
        question_id: str | None = None,
        recommendation_id: str | None = None,
    ) -> None:
        if search_session_id is not None:
            self.search_session_id = search_session_id
        if intent_version is not None:
            self.intent_version = intent_version
        if ranking_run_id is not None:
            self.ranking_run_id = ranking_run_id
        if question_id is not None:
            self.question_id = question_id
        if recommendation_id is not None:
            self.recommendation_id = recommendation_id

    @property
    def current_span_id(self) -> str | None:
        return self._span_stack[-1] if self._span_stack else None

    @property
    def parent_span_id(self) -> str | None:
        return self._span_stack[-2] if len(self._span_stack) >= 2 else None

    @contextmanager
    def span(self, component: str, *, span_id: str | None = None) -> Iterator[str]:
        self._span_counter += 1
        generated = span_id or f"SPAN-{component}-{self._span_counter:05d}"
        self._span_stack.append(generated)
        try:
            yield generated
        finally:
            popped = self._span_stack.pop()
            if popped != generated:  # pragma: no cover - impossible without misuse.
                raise RuntimeError("Audit span stack corruption")

    def emit(
        self,
        component: str,
        event_type: AuditEventType,
        *,
        entity_refs: dict[str, str] | None = None,
        input_data: Any | None = None,
        output_data: Any | None = None,
        payload: dict[str, Any] | None = None,
        provenance: list[dict[str, Any]] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        payload_mode: LLMPayloadMode | None = None,
    ) -> AuditEvent | None:
        # Large recommendation sessions can emit several rule-level records per
        # product. Summary-mode callers skip those events before model creation,
        # redaction, and hashing while preserving orchestration history.
        if (
            self.captured_event_types is not None
            and event_type not in self.captured_event_types
        ):
            return None
        chosen_mode = payload_mode
        prepared_payload = self._prepare_payload(payload or {}, chosen_mode)
        event = AuditEvent(
            event_id=f"AUD-{uuid.uuid4().hex}",
            request_id=self.request_id,
            evaluation_id=self.evaluation_id,
            trace_id=self.trace_id,
            search_session_id=self.search_session_id,
            intent_version=self.intent_version,
            ranking_run_id=self.ranking_run_id,
            question_id=self.question_id,
            recommendation_id=self.recommendation_id,
            span_id=span_id if span_id is not None else self.current_span_id,
            parent_span_id=(
                parent_span_id if parent_span_id is not None else self.parent_span_id
            ),
            component=component,
            event_type=event_type,
            entity_refs=entity_refs or {},
            input_hash=canonical_hash(input_data) if input_data is not None else None,
            output_hash=canonical_hash(output_data) if output_data is not None else None,
            payload=prepared_payload,
            provenance=redact_sensitive(provenance or []),
        )
        self.sink.append(event)
        return event

    def _prepare_payload(
        self,
        payload: dict[str, Any],
        mode: LLMPayloadMode | None,
    ) -> dict[str, Any]:
        if mode is None:
            return redact_sensitive(payload)
        if mode == LLMPayloadMode.NONE:
            return {}
        if mode == LLMPayloadMode.HASHED:
            return {"payload_hash": canonical_hash(payload)}
        # Even FULL_DEBUG always redacts secrets and credentials.
        return redact_sensitive(payload)
