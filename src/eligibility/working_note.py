from __future__ import annotations

import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Protocol


_SENSITIVE_PATTERNS = (
    (re.compile(r"(?<!\d)\d{6}-?\d{7}(?!\d)"), "[REDACTED_ID]"),
    (re.compile(r"(?<!\d)\d{10,16}(?!\d)"), "[REDACTED_LONG_NUMBER]"),
)
_SENSITIVE_KEYS = {
    "resident_registration_number",
    "rrn",
    "account_number",
    "api_key",
    "credential",
    "password",
    "raw_transactions",
    "transaction_raw_payload",
}


def _sanitize_text(value: str) -> str:
    text = value
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _sanitize(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    return value


class SearchWorkingNoteStore(Protocol):
    """Ephemeral continuity store. Never a financial source of truth."""

    def initialize(self, *, search_session_id: str, user_id: str) -> None: ...
    def record_user_turn(self, *, search_session_id: str, sequence: int, utterance: str) -> None: ...
    def update_summary(self, *, search_session_id: str, summary: dict[str, Any]) -> None: ...
    def load(self, search_session_id: str) -> str | None: ...
    def delete(self, search_session_id: str) -> None: ...


class InMemorySearchWorkingNoteStore:
    def __init__(self, *, recent_turn_limit: int = 8) -> None:
        self.recent_turn_limit = recent_turn_limit
        self._notes: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def initialize(self, *, search_session_id: str, user_id: str) -> None:
        with self._lock:
            self._notes[search_session_id] = {
                "user_id": user_id,
                "recent_turns": [],
                "summary": {},
            }

    def record_user_turn(self, *, search_session_id: str, sequence: int, utterance: str) -> None:
        with self._lock:
            note = self._notes.setdefault(
                search_session_id, {"user_id": "UNKNOWN", "recent_turns": [], "summary": {}}
            )
            note["recent_turns"].append({"sequence": sequence, "utterance": _sanitize_text(utterance)})
            note["recent_turns"] = note["recent_turns"][-self.recent_turn_limit :]

    def update_summary(self, *, search_session_id: str, summary: dict[str, Any]) -> None:
        with self._lock:
            note = self._notes.setdefault(
                search_session_id, {"user_id": "UNKNOWN", "recent_turns": [], "summary": {}}
            )
            note["summary"] = _sanitize(deepcopy(summary))

    def load(self, search_session_id: str) -> str | None:
        with self._lock:
            note = deepcopy(self._notes.get(search_session_id))
        return _render_markdown(search_session_id, note) if note is not None else None

    def delete(self, search_session_id: str) -> None:
        with self._lock:
            self._notes.pop(search_session_id, None)


class FileSearchWorkingNoteStore(InMemorySearchWorkingNoteStore):
    """Human-readable Markdown store with atomic file replacement."""

    def __init__(self, root: str | Path, *, recent_turn_limit: int = 8) -> None:
        super().__init__(recent_turn_limit=recent_turn_limit)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, search_session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", search_session_id)
        return self.root / f"{safe}.md"

    def _persist(self, search_session_id: str) -> None:
        text = super().load(search_session_id)
        if text is None:
            return
        destination = self._path(search_session_id)
        fd, tmp_name = tempfile.mkstemp(prefix=destination.name + ".", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, destination)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def initialize(self, *, search_session_id: str, user_id: str) -> None:
        super().initialize(search_session_id=search_session_id, user_id=user_id)
        self._persist(search_session_id)

    def record_user_turn(self, *, search_session_id: str, sequence: int, utterance: str) -> None:
        super().record_user_turn(search_session_id=search_session_id, sequence=sequence, utterance=utterance)
        self._persist(search_session_id)

    def update_summary(self, *, search_session_id: str, summary: dict[str, Any]) -> None:
        super().update_summary(search_session_id=search_session_id, summary=summary)
        self._persist(search_session_id)

    def load(self, search_session_id: str) -> str | None:
        text = super().load(search_session_id)
        if text is not None:
            return text
        path = self._path(search_session_id)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def delete(self, search_session_id: str) -> None:
        super().delete(search_session_id)
        self._path(search_session_id).unlink(missing_ok=True)


def _render_markdown(search_session_id: str, note: dict[str, Any]) -> str:
    summary = note.get("summary", {})
    lines = ["# Search Session Working Note", "", f"Session: {search_session_id}"]
    user_id = note.get("user_id")
    if user_id:
        lines.append(f"User: {user_id}")
    lines.extend(["", "## Current State Summary"])
    for key in (
        "current_user_goal",
        "mutable_search_state",
        "product_specific_choices",
        "product_selection",
        "unresolved_material_information",
        "current_recommendation_summary",
        "active_question",
    ):
        if key not in summary:
            continue
        lines.append(f"### {key.replace('_', ' ').title()}")
        lines.append("```json")
        import json
        lines.append(json.dumps(summary[key], ensure_ascii=False, indent=2, default=str))
        lines.append("```")
    lines.extend(["", "## Recent Important User Turns"])
    turns = note.get("recent_turns", [])
    if not turns:
        lines.append("- (none)")
    else:
        for turn in turns:
            lines.append(f"{turn['sequence']}. {turn['utterance']!r}")
    lines.extend([
        "",
        "> This note is ephemeral LLM-continuity context. Structured backend state and authoritative facts are the source of truth.",
        "",
    ])
    return "\n".join(lines)
