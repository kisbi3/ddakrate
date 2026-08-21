from __future__ import annotations

from pathlib import Path

from eligibility.audit.models import AuditEvent
from eligibility.audit.sink import JsonLinesAuditSink


def format_event_sequence(events: tuple[AuditEvent, ...] | list[AuditEvent]) -> str:
    lines: list[str] = []
    for event in events:
        correlation = [event.request_id, event.trace_id]
        if event.evaluation_id:
            correlation.append(event.evaluation_id)
        suffix = " / ".join(correlation)
        entity = ""
        if event.entity_refs:
            entity = " " + ", ".join(
                f"{key}={value}" for key, value in sorted(event.entity_refs.items())
            )
        lines.append(f"{event.event_type.value}{entity}  [{suffix}]")
    return "\n".join(lines)


def replay_file(path: str | Path, *, trace_id: str) -> str:
    events = JsonLinesAuditSink(path).read(trace_id=trace_id)
    return format_event_sequence(events)
