from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
from threading import Condition, RLock, Thread
import time
from typing import Any, Callable, Iterator
import uuid

from pydantic import BaseModel

from eligibility.audit import redact_sensitive
from eligibility.audit import canonical_hash


_current_request_id: ContextVar[str | None] = ContextVar(
    "eligibility_debug_request_id", default=None
)


_SEARCH_SESSION_ID = re.compile(r"^SEARCH-[0-9a-f]{16}$")


_ACCOUNT_NUMBER_WITH_SEPARATORS = re.compile(
    r"(?<!\d)(?:\d{2,6}[- ]+){1,3}\d{1,6}(?!\d)"
)
_LONG_FINANCIAL_IDENTIFIER = re.compile(r"(?<!\d)\d{10,16}(?!\d)")
_INTERNAL_IDENTIFIER_FIELDS = {
    "search_session_id",
    "request_id",
    "request_ids",
    "supporting_request_ids",
    "search_intent_id",
    "question_id",
    "active_question_id",
    "answered_question_ids",
    "ranking_run_id",
    "recommendation_id",
    "product_id",
    "candidate_product_ids",
    "excluded_product_ids",
    "affected_product_ids",
    "visible_top_product_ids",
    "institution_id",
    "rule_id",
    "reward_id",
    "action_id",
    "missing_fact_id",
    "claim_id",
    "request_reference",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _redact_account_text(value: Any) -> Any:
    """Mask account-like free text after the shared redactor ran once."""

    if isinstance(value, dict):
        return {
            str(key): (
                item
                if str(key) in _INTERNAL_IDENTIFIER_FIELDS
                else _redact_account_text(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_account_text(item) for item in value]
    if isinstance(value, str):
        value = _ACCOUNT_NUMBER_WITH_SEPARATORS.sub(
            lambda match: (
                "[REDACTED_ACCOUNT]"
                if 10 <= sum(character.isdigit() for character in match.group(0)) <= 16
                else match.group(0)
            ),
            value,
        )
        return _LONG_FINANCIAL_IDENTIFIER.sub("[REDACTED_ACCOUNT]", value)
    return value


def _redact_persisted_text(value: Any) -> Any:
    """Apply credential and account-number redaction in two linear passes.

    ``redact_sensitive`` already walks the complete value recursively. Calling
    it again at every child made a multi-megabyte debug snapshot effectively
    quadratic and kept the user-facing HTTP response waiting for several
    seconds. Run the shared redactor exactly once, then perform the additional
    free-text account masking in one second traversal. Product IDs such as
    ``00101363-1-0001`` remain deliberately exempt.
    """

    return _redact_account_text(redact_sensitive(_jsonable(value)))


def assemble_conversation_turns(
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group transport requests around user-authored state-changing requests.

    The browser performs several GETs after one message.  Those calls are useful
    transport evidence, but counting each one as a conversation turn makes the
    inspector misleading.  A new turn starts on POST/PATCH; following GET/DELETE
    requests are retained as supporting HTTP calls until the next write.
    """

    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for request in requests:
        method = str(request.get("method") or "").upper()
        path = str(request.get("path") or "")
        starts_turn = method in {"POST", "PATCH"}
        if starts_turn:
            payload = request.get("request_payload") or {}
            user_input = next(
                (
                    payload.get(field)
                    for field in (
                        "natural_language_query",
                        "utterance",
                        "message",
                        "answer",
                    )
                    if payload.get(field) not in {None, ""}
                ),
                None,
            )
            if path == "/api/search-sessions":
                kind = "SESSION_START"
            elif path.endswith("/messages"):
                kind = "NATURAL_LANGUAGE_MESSAGE"
            elif "/answers" in path:
                kind = "STRUCTURED_ANSWER"
            else:
                kind = "STATE_CHANGE"
            current = {
                "turn_id": f"TURN-{len(turns) + 1:03d}",
                "sequence": len(turns) + 1,
                "kind": kind,
                "started_at": request.get("started_at"),
                "primary_request_id": request.get("request_id"),
                "request_ids": [request.get("request_id")],
                "supporting_request_ids": [],
                "user_input": user_input,
                "llm_call_count": len(request.get("llm_calls") or []),
                "application_event_count": len(
                    request.get("application_events") or []
                ),
            }
            turns.append(current)
            continue
        if current is not None:
            current["request_ids"].append(request.get("request_id"))
            current["supporting_request_ids"].append(request.get("request_id"))
            current["llm_call_count"] += len(request.get("llm_calls") or [])
            current["application_event_count"] += len(
                request.get("application_events") or []
            )
    return turns


class DebugTraceStore:
    """Process-local, credential-redacted trace for the private Web inspector.

    This intentionally keeps user messages and model payloads because the purpose
    of the inspector is to show the exact orchestration input. Credentials remain
    recursively redacted by the shared audit redactor.
    """

    def __init__(
        self,
        *,
        max_requests: int = 300,
        history_dir: str | Path | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.max_requests = max_requests
        self._requests: list[dict[str, Any]] = []
        self._schemas: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        self.history_dir = (
            Path(history_dir).expanduser().resolve()
            if history_dir is not None
            else None
        )
        self.runtime_metadata = _redact_persisted_text(runtime_metadata or {})
        self._persisted_request_ids: set[str] = set()
        self._first_request_at_by_session: dict[str, str] = {}
        self._persistence_errors: list[str] = []
        self._dropped_write_count = 0
        self._last_dropped_at: str | None = None
        self._dropped_write_count_by_kind: dict[str, int] = {}
        self._archive_summary_cache: dict[
            str, tuple[tuple[int, int], dict[str, Any]]
        ] = {}
        self._write_queue: Queue[tuple[str, Any]] = Queue(maxsize=256)
        self._write_condition = Condition(RLock())
        self._pending_write_count = 0
        self._writer_thread: Thread | None = None

    @contextmanager
    def request(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        search_session_id: str | None,
    ) -> Iterator[str]:
        request_id = f"WEB-{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(timezone.utc)
        row = {
            "request_id": request_id,
            "method": method,
            "path": path,
            "request_payload": redact_sensitive(_jsonable(payload)),
            "search_session_id": search_session_id,
            "started_at": started_at.isoformat(),
            "completed_at": None,
            "duration_ms": None,
            "status_code": None,
            "response_payload": None,
            "error": None,
            "llm_calls": [],
            # These are request-scoped snapshots, deliberately separate from the
            # live session snapshot returned by the debug endpoint.  Without
            # them, selecting an old Web request can misleadingly show the
            # Engine state produced by a later turn.
            "state_before": None,
            "state_after": None,
            "application_events": [],
        }
        with self._lock:
            self._requests.append(row)
            if len(self._requests) > self.max_requests:
                del self._requests[: len(self._requests) - self.max_requests]
        token = _current_request_id.set(request_id)
        try:
            yield request_id
        except Exception as exc:
            self.finish_request(request_id, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            _current_request_id.reset(token)

    def finish_request(
        self,
        request_id: str,
        *,
        status_code: int | None = None,
        response_payload: Any = None,
        search_session_id: str | None = None,
        error: str | None = None,
    ) -> None:
        completed_at = datetime.now(timezone.utc)
        completed_row: dict[str, Any] | None = None
        with self._lock:
            row = self._find(request_id)
            if row is None:
                return
            started_at = datetime.fromisoformat(row["started_at"])
            row.update(
                {
                    "completed_at": completed_at.isoformat(),
                    "duration_ms": max(
                        0, int((completed_at - started_at).total_seconds() * 1000)
                    ),
                    "status_code": status_code,
                    "response_payload": redact_sensitive(_jsonable(response_payload)),
                    "search_session_id": search_session_id or row["search_session_id"],
                    "error": error,
                }
            )
            session_id = row.get("search_session_id")
            if (
                session_id
                and _SEARCH_SESSION_ID.fullmatch(str(session_id))
                and (
                    row.get("state_before") is not None
                    or row.get("state_after") is not None
                )
                and request_id not in self._persisted_request_ids
                and row.get("completed_at") is not None
            ):
                self._first_request_at_by_session.setdefault(
                    str(session_id), str(row["started_at"])
                )
                self._persisted_request_ids.add(request_id)
                completed_row = _jsonable(row)
        if completed_row is not None:
            self._enqueue_persistence("REQUEST", completed_row)

    def session_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = list(self._requests)
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            session_id = row.get("search_session_id")
            if not session_id:
                continue
            stats = result.setdefault(
                session_id,
                {"request_count": 0, "llm_call_count": 0, "last_request_at": None},
            )
            stats["request_count"] += 1
            stats["llm_call_count"] += len(row.get("llm_calls") or [])
            stats["last_request_at"] = row.get("started_at")
        return result

    def observe_llm(self, phase: str, data: dict[str, Any]) -> None:
        request_id = _current_request_id.get()
        if request_id is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        safe_data = redact_sensitive(_jsonable(data))
        with self._lock:
            row = self._find(request_id)
            if row is None:
                return
            if phase == "started":
                request_payload = safe_data.get("request")
                if isinstance(request_payload, dict):
                    for field, ref_field in (
                        ("domain_response_json_schema", "domain_schema_ref"),
                        ("transport_response_json_schema", "transport_schema_ref"),
                        ("response_json_schema", "response_schema_ref"),
                    ):
                        schema = request_payload.pop(field, None)
                        if isinstance(schema, dict):
                            schema_ref = f"SCHEMA-{canonical_hash(schema)[:16]}"
                            self._schemas[schema_ref] = schema
                            request_payload[ref_field] = schema_ref
                            if field == "response_json_schema":
                                request_payload["response_json_schema"] = {
                                    "$ref": schema_ref
                                }
                    transport_payload = request_payload.get("transport_payload")
                    if isinstance(transport_payload, dict):
                        if "input" in transport_payload:
                            transport_payload.pop("input", None)
                            transport_payload["input_ref"] = "request.messages"
                        text_format = (
                            transport_payload.get("text", {}).get("format", {})
                            if isinstance(transport_payload.get("text"), dict)
                            else {}
                        )
                        schema = text_format.pop("schema", None)
                        if isinstance(schema, dict):
                            schema_ref = f"SCHEMA-{canonical_hash(schema)[:16]}"
                            self._schemas[schema_ref] = schema
                            text_format["schema_ref"] = schema_ref
                row["llm_calls"].append(
                    {
                        "call_id": f"LLM-{uuid.uuid4().hex[:12]}",
                        "started_at": now,
                        "completed_at": None,
                        "status": "RUNNING",
                        **safe_data,
                    }
                )
                return
            if not row["llm_calls"]:
                return
            call = row["llm_calls"][-1]
            call.update(safe_data)
            call["completed_at"] = now
            call["status"] = "COMPLETED" if phase == "completed" else "FAILED"

    def record_execution(
        self,
        request_id: str,
        *,
        state_before: Any = None,
        state_after: Any = None,
        application_events: list[dict[str, Any]] | None = None,
    ) -> None:
        """Attach the state transition and audit slice for one Web request."""

        with self._lock:
            row = self._find(request_id)
            if row is None:
                return
            row["state_before"] = redact_sensitive(_jsonable(state_before))
            row["state_after"] = redact_sensitive(_jsonable(state_after))
            row["application_events"] = redact_sensitive(
                _jsonable(application_events or [])
            )

    def requests_for_session(self, search_session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                _jsonable(row)
                for row in self._requests
                if row["search_session_id"] == search_session_id
            ]

    def all_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [_jsonable(row) for row in self._requests]

    def schema_registry(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return _jsonable(self._schemas)

    def observer(self) -> Callable[[str, dict[str, Any]], None]:
        return self.observe_llm

    def persist_session_snapshot(
        self,
        search_session_id: str,
        *,
        snapshot: dict[str, Any],
        audit_events: list[dict[str, Any]],
    ) -> None:
        """Persist the latest complete domain snapshot for later LLM review.

        Request history remains append-only in ``requests.jsonl``.  The snapshot
        is intentionally replaced because it is a point-in-time materialized
        view, not an event log or a resumable SearchSession.
        """

        if self.history_dir is None or not _SEARCH_SESSION_ID.fullmatch(
            search_session_id
        ):
            return
        payload = {
            "history_format_version": 1,
            "search_session_id": search_session_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": snapshot,
            "audit_events": audit_events,
        }
        self._enqueue_persistence("SNAPSHOT", payload)

    def _persist_session_snapshot_now(self, payload: dict[str, Any]) -> None:
        search_session_id = str(payload["search_session_id"])
        try:
            session_dir = self._session_dir(search_session_id)
            self._atomic_write_json(
                session_dir / "latest-session-snapshot.json",
                _redact_persisted_text(payload),
            )
        except Exception as exc:  # History capture must never break the Web request.
            self._record_persistence_error(
                f"snapshot {search_session_id}: {type(exc).__name__}: {exc}"
            )

    def _enqueue_persistence(self, kind: str, payload: Any) -> bool:
        """Queue one ordered disk write without delaying the HTTP response."""

        if self.history_dir is None:
            return False
        with self._write_condition:
            try:
                self._write_queue.put_nowait((kind, payload))
            except Full:
                dropped_at = datetime.now(timezone.utc).isoformat()
                with self._lock:
                    self._dropped_write_count += 1
                    self._last_dropped_at = dropped_at
                    self._dropped_write_count_by_kind[kind] = (
                        self._dropped_write_count_by_kind.get(kind, 0) + 1
                    )
                self._record_persistence_error(
                    f"queue full: dropped {kind.lower()} persistence job"
                )
                return False
            self._pending_write_count += 1
            if self._writer_thread is None or not self._writer_thread.is_alive():
                self._writer_thread = Thread(
                    target=self._writer_loop,
                    name="ddakrate-debug-history-writer",
                    daemon=True,
                )
                self._writer_thread.start()
            return True

    def _writer_loop(self) -> None:
        """Write queued jobs sequentially so request and snapshot order is stable."""

        while True:
            try:
                kind, payload = self._write_queue.get(timeout=0.5)
            except Empty:
                with self._write_condition:
                    if self._write_queue.empty():
                        self._writer_thread = None
                        return
                continue
            try:
                if kind == "REQUEST":
                    self._persist_completed_request(payload)
                elif kind == "SNAPSHOT":
                    self._persist_session_snapshot_now(payload)
                else:
                    self._record_persistence_error(
                        f"unknown persistence job kind: {kind}"
                    )
            except Exception as exc:  # The worker must survive one bad record.
                self._record_persistence_error(
                    f"worker {kind.lower()}: {type(exc).__name__}: {exc}"
                )
            finally:
                self._write_queue.task_done()
                with self._write_condition:
                    self._pending_write_count -= 1
                    self._write_condition.notify_all()

    def flush(self, *, timeout_seconds: float | None = 5.0) -> bool:
        """Wait for queued writes, primarily during shutdown and explicit tests."""

        deadline = (
            None
            if timeout_seconds is None
            else time.monotonic() + max(0.0, timeout_seconds)
        )
        with self._write_condition:
            while self._pending_write_count:
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    return False
                self._write_condition.wait(timeout=remaining)
        return True

    def persistence_status(self) -> dict[str, Any]:
        with self._lock:
            errors = list(self._persistence_errors)
            dropped_write_count = self._dropped_write_count
            last_dropped_at = self._last_dropped_at
            dropped_write_count_by_kind = dict(
                self._dropped_write_count_by_kind
            )
        with self._write_condition:
            pending_write_count = self._pending_write_count
            writer_active = bool(
                self._writer_thread is not None
                and self._writer_thread.is_alive()
            )
        return {
            "enabled": self.history_dir is not None,
            "history_dir": str(self.history_dir) if self.history_dir is not None else None,
            "mode": "BACKGROUND_QUEUE" if self.history_dir is not None else "DISABLED",
            "pending_write_count": pending_write_count,
            "writer_active": writer_active,
            "dropped_write_count": dropped_write_count,
            "last_dropped_at": last_dropped_at,
            "dropped_write_count_by_kind": dropped_write_count_by_kind,
            "error_count": len(errors),
            "last_error": errors[-1] if errors else None,
        }

    def archived_session_summaries(self) -> list[dict[str, Any]]:
        """List durable, read-only histories without loading large snapshots."""

        if self.history_dir is None or not self.history_dir.exists():
            return []
        summaries: list[dict[str, Any]] = []
        for session_dir in self.history_dir.iterdir():
            if not session_dir.is_dir():
                continue
            if not _SEARCH_SESSION_ID.fullmatch(session_dir.name):
                continue
            request_path = session_dir / "requests.jsonl"
            if not request_path.is_file():
                continue
            try:
                stat = request_path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                cached = self._archive_summary_cache.get(session_dir.name)
                if cached is not None and cached[0] == signature:
                    summaries.append(_jsonable(cached[1]))
                    continue
                rows = self._read_jsonl(request_path)
                summary = self._archived_summary(session_dir.name, rows)
                self._archive_summary_cache[session_dir.name] = (
                    signature,
                    summary,
                )
                summaries.append(_jsonable(summary))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self._record_persistence_error(
                    f"archive list {session_dir.name}: {type(exc).__name__}: {exc}"
                )
        return sorted(
            summaries,
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )

    def archived_session_bundle(
        self, search_session_id: str
    ) -> dict[str, Any] | None:
        """Load one persisted history for read-only inspection."""

        session_dir = self._existing_session_dir(search_session_id)
        if session_dir is None:
            return None
        try:
            rows = self._read_jsonl(session_dir / "requests.jsonl")
            schemas = self._read_json(session_dir / "schemas.json", default={})
            materialized = self._read_json(
                session_dir / "latest-session-snapshot.json", default={}
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._record_persistence_error(
                f"archive load {search_session_id}: {type(exc).__name__}: {exc}"
            )
            return None
        snapshot = materialized.get("snapshot")
        if not isinstance(snapshot, dict):
            return None
        return {
            "search_session_id": search_session_id,
            "source": "ARCHIVED",
            "read_only": True,
            "resumable": False,
            "requests": rows,
            "turns": assemble_conversation_turns(rows),
            "schemas": schemas if isinstance(schemas, dict) else {},
            "snapshot": snapshot,
            "audit_events": materialized.get("audit_events") or [],
        }

    def archived_audit_events(
        self, search_session_id: str
    ) -> list[dict[str, Any]] | None:
        bundle = self.archived_session_bundle(search_session_id)
        if bundle is None:
            return None
        return bundle["audit_events"]

    def _archived_summary(
        self,
        search_session_id: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        last_state: dict[str, Any] = {}
        source_utterances: list[str] = []
        llm_call_count = 0
        for row in rows:
            llm_call_count += len(row.get("llm_calls") or [])
            payload = row.get("request_payload") or {}
            utterance = next(
                (
                    payload.get(field)
                    for field in ("natural_language_query", "utterance", "message")
                    if isinstance(payload.get(field), str) and payload.get(field)
                ),
                None,
            )
            if utterance is not None:
                source_utterances.append(utterance)
            candidate = row.get("state_after") or row.get("state_before")
            if isinstance(candidate, dict) and candidate:
                last_state = candidate
        session = last_state.get("session") or {}
        active_question = last_state.get("active_question") or {}
        first_at = rows[0].get("started_at") if rows else None
        last_at = (
            rows[-1].get("completed_at") or rows[-1].get("started_at")
            if rows
            else None
        )
        return {
            "search_session_id": search_session_id,
            "source": "ARCHIVED",
            "read_only": True,
            "resumable": False,
            "status": "ARCHIVED",
            "created_at": first_at,
            "updated_at": last_at,
            "intent_version": session.get("intent_version"),
            "candidate_count": len(last_state.get("candidate_product_ids") or []),
            "answered_question_count": len(
                session.get("answered_question_ids") or []
            ),
            "active_question": active_question.get("question"),
            "source_utterances": source_utterances,
            "request_count": len(rows),
            "llm_call_count": llm_call_count,
            "last_request_at": last_at,
            "turn_count": len(assemble_conversation_turns(rows)),
        }

    def _existing_session_dir(self, search_session_id: str) -> Path | None:
        if self.history_dir is None:
            return None
        if not _SEARCH_SESSION_ID.fullmatch(search_session_id):
            return None
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", search_session_id)
        if safe_id != search_session_id:
            return None
        session_dir = self.history_dir / safe_id
        return session_dir if session_dir.is_dir() else None

    @staticmethod
    def _read_json(path: Path, *, default: Any) -> Any:
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not path.is_file():
            return rows
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A process may stop mid-append.  One corrupt line must not hide
                # the other complete turns in this or any other session.
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _persist_completed_request(self, row: dict[str, Any]) -> None:
        if self.history_dir is None:
            return
        search_session_id = str(row["search_session_id"])
        if not _SEARCH_SESSION_ID.fullmatch(search_session_id):
            return
        safe_row = _redact_persisted_text(row)
        try:
            session_dir = self._session_dir(search_session_id)
            request_path = session_dir / "requests.jsonl"
            serialized = json.dumps(
                safe_row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            with self._lock, request_path.open("a", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.write("\n")
            self._atomic_write_json(
                session_dir / "schemas.json",
                _redact_persisted_text(self.schema_registry()),
            )
            self._atomic_write_json(
                session_dir / "metadata.json",
                {
                    "history_format_version": 1,
                    "search_session_id": search_session_id,
                    "first_request_at": self._first_request_at(search_session_id),
                    "last_persisted_at": datetime.now(timezone.utc).isoformat(),
                    "runtime": self.runtime_metadata,
                    "files": {
                        "requests": "requests.jsonl",
                        "schemas": "schemas.json",
                        "latest_session_snapshot": "latest-session-snapshot.json",
                    },
                    "purpose": (
                        "Raw history for analysis in a separate session; "
                        "this is not a resumable SearchSession."
                    ),
                },
            )
        except Exception as exc:  # History capture must never break the Web request.
            self._record_persistence_error(
                f"request {row.get('request_id')}: {type(exc).__name__}: {exc}"
            )

    def _session_dir(self, search_session_id: str) -> Path:
        assert self.history_dir is not None
        if not _SEARCH_SESSION_ID.fullmatch(search_session_id):
            raise ValueError(f"invalid SearchSession identifier: {search_session_id}")
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", search_session_id)
        session_dir = self.history_dir / safe_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def _first_request_at(self, search_session_id: str) -> str | None:
        with self._lock:
            return self._first_request_at_by_session.get(search_session_id)

    def _atomic_write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    _redact_persisted_text(payload),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _record_persistence_error(self, message: str) -> None:
        with self._lock:
            self._persistence_errors.append(message)
            if len(self._persistence_errors) > 20:
                del self._persistence_errors[:-20]

    def _find(self, request_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in reversed(self._requests) if row["request_id"] == request_id),
            None,
        )
