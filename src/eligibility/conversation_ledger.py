"""Append-only decision ledger and atomic turn changesets."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

class RecordStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REVERTED = "REVERTED"

class LedgerError(ValueError):
    pass

@dataclass(frozen=True)
class Decision:
    decision_id: str
    changeset_id: str
    turn_id: int
    operation: str
    target: str
    before: Any = None
    after: Any = None
    reversible: bool = True
    record_status: RecordStatus = RecordStatus.ACTIVE
    supersedes: str | None = None
    source_text: str | None = None

@dataclass(frozen=True)
class RevertDecisions:
    decision_ids: tuple[str, ...]

@dataclass(frozen=True)
class Changeset:
    changeset_id: str
    turn_id: int
    decisions: tuple[Decision, ...] = ()
    reverts: tuple[RevertDecisions, ...] = ()

class DecisionLedger:
    def __init__(
        self,
        state: Mapping[str, Any] | None = None,
        *,
        allowed_targets: set[str] | None = None,
    ) -> None:
        self._state = deepcopy(dict(state or {}))
        self._entries: list[Decision] = []
        self.allowed_targets = set(allowed_targets or ())

    @property
    def state(self) -> dict[str, Any]:
        return deepcopy(self._state)

    @property
    def entries(self) -> tuple[Decision, ...]:
        return tuple(self._entries)

    @property
    def active_entries(self) -> tuple[Decision, ...]:
        return tuple(
            entry
            for entry in self._entries
            if entry.record_status is RecordStatus.ACTIVE
        )

    def commit(
        self,
        changeset: Changeset,
        *,
        validator: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> tuple[Decision, ...]:
        staged = deepcopy(self._state)
        staged_entries = list(self._entries)
        existing = {entry.decision_id: entry for entry in self._entries}
        additions: list[Decision] = []
        ids = [decision.decision_id for decision in changeset.decisions]
        if len(ids) != len(set(ids)) or any(ident in existing for ident in ids):
            raise LedgerError("decision IDs must be unique and append-only")
        for d in changeset.decisions:
            if d.changeset_id != changeset.changeset_id:
                raise LedgerError("decision changeset_id mismatch")
            if self.allowed_targets and d.target not in self.allowed_targets:
                raise LedgerError(f"unknown target: {d.target}")
            prior = next(
                (
                    entry
                    for entry in reversed(staged_entries)
                    if entry.target == d.target
                    and entry.record_status is RecordStatus.ACTIVE
                ),
                None,
            )
            if prior is not None:
                staged_entries = [
                    Decision(
                        **{
                            **entry.__dict__,
                            "record_status": RecordStatus.SUPERSEDED,
                        }
                    )
                    if entry.decision_id == prior.decision_id
                    else entry
                    for entry in staged_entries
                ]
                d = Decision(**{**d.__dict__, "supersedes": prior.decision_id})
            staged[d.target] = deepcopy(d.after)
            additions.append(d)
        for req in changeset.reverts:
            if len(req.decision_ids) != len(set(req.decision_ids)):
                raise LedgerError("revert decision IDs must be unique")
            for ident in req.decision_ids:
                target = existing.get(ident) or next(
                    (d for d in additions if d.decision_id == ident), None
                )
                current = next(
                    (
                        entry
                        for entry in staged_entries
                        if entry.decision_id == ident
                    ),
                    target,
                )
                if (
                    target is None
                    or current is None
                    or not current.reversible
                    or current.record_status is not RecordStatus.ACTIVE
                ):
                    raise LedgerError(f"decision is not reversible: {ident}")
                staged[target.target] = deepcopy(target.before)
                staged_entries = [
                    Decision(
                        **{
                            **entry.__dict__,
                            "record_status": RecordStatus.REVERTED,
                        }
                    )
                    if entry.decision_id == ident
                    else entry
                    for entry in staged_entries
                ]
                additions.append(
                    Decision(
                        decision_id=f"{changeset.changeset_id}-REV-{len(additions) + 1:03d}",
                        changeset_id=changeset.changeset_id,
                        turn_id=changeset.turn_id,
                        operation="REVERT_DECISIONS",
                        target=target.target,
                        before=target.after,
                        after=target.before,
                        reversible=False,
                        supersedes=ident,
                    )
                )
        if validator:
            validator(staged)
        self._state = staged
        self._entries = staged_entries + additions
        return tuple(additions)

    def snapshot(self) -> dict[str, Any]:
        return self.state

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self._state = deepcopy(dict(snapshot))

    def compact(self) -> tuple[Decision, ...]:
        return tuple(
            entry
            for entry in self._entries
            if entry.record_status is not RecordStatus.SUPERSEDED
        )
