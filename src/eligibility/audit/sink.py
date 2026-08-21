from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Iterable, Protocol

from eligibility.audit.models import AuditEvent


class AuditSink(Protocol):
    """Minimal append-only sink contract."""

    def append(self, event: AuditEvent) -> None:
        ...

    def read(
        self,
        *,
        trace_id: str | None = None,
        request_id: str | None = None,
        evaluation_id: str | None = None,
    ) -> tuple[AuditEvent, ...]:
        ...


class InMemoryAuditSink:
    """Thread-safe test/demo sink exposing immutable snapshots only."""

    def __init__(self, initial_events: Iterable[AuditEvent] = ()) -> None:
        self._events: list[AuditEvent] = list(initial_events)
        self._lock = RLock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def read(
        self,
        *,
        trace_id: str | None = None,
        request_id: str | None = None,
        evaluation_id: str | None = None,
    ) -> tuple[AuditEvent, ...]:
        with self._lock:
            snapshot = tuple(self._events)
        return tuple(
            event
            for event in snapshot
            if (trace_id is None or event.trace_id == trace_id)
            and (request_id is None or event.request_id == request_id)
            and (evaluation_id is None or event.evaluation_id == evaluation_id)
        )

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return self.read()


class JsonLinesAuditSink:
    """Append-only JSON Lines sink suitable for the replay CLI."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def append(self, event: AuditEvent) -> None:
        serialized = event.model_dump_json(exclude_none=True)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.write("\n")

    def read(
        self,
        *,
        trace_id: str | None = None,
        request_id: str | None = None,
        evaluation_id: str | None = None,
    ) -> tuple[AuditEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[AuditEvent] = []
        with self._lock, self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = AuditEvent.model_validate(json.loads(line))
                except Exception as exc:  # pragma: no cover - defensive CLI path.
                    raise ValueError(
                        f"Invalid audit event at {self.path}:{line_number}"
                    ) from exc
                if trace_id is not None and event.trace_id != trace_id:
                    continue
                if request_id is not None and event.request_id != request_id:
                    continue
                if evaluation_id is not None and event.evaluation_id != evaluation_id:
                    continue
                events.append(event)
        return tuple(events)
