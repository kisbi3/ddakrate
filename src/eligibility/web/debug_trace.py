from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Iterator
import uuid

from pydantic import BaseModel

from eligibility.audit import redact_sensitive


_current_request_id: ContextVar[str | None] = ContextVar(
    "eligibility_debug_request_id", default=None
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class DebugTraceStore:
    """Process-local, credential-redacted trace for the private Web inspector.

    This intentionally keeps user messages and model payloads because the purpose
    of the inspector is to show the exact orchestration input. Credentials remain
    recursively redacted by the shared audit redactor.
    """

    def __init__(self, *, max_requests: int = 300) -> None:
        self.max_requests = max_requests
        self._requests: list[dict[str, Any]] = []
        self._lock = RLock()

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

    def observer(self) -> Callable[[str, dict[str, Any]], None]:
        return self.observe_llm

    def _find(self, request_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in reversed(self._requests) if row["request_id"] == request_id),
            None,
        )
