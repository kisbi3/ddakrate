import pytest

from eligibility.conversation_ledger import (
    Changeset, Decision, DecisionLedger, LedgerError, RevertDecisions, RecordStatus,
)


def d(i, before=None, after=None, target="policy"):
    return Decision(i, "c", 1, "SET", target, before, after)


def test_revert_is_append_only_and_restores_before():
    ledger = DecisionLedger({"policy": None}, allowed_targets={"policy"})
    ledger.commit(Changeset("c", 1, (d("DEC-1", None, "EXCLUDE"),)))
    result = ledger.commit(Changeset("c2", 2, reverts=(RevertDecisions(("DEC-1",)),)))
    assert ledger.state["policy"] is None
    assert ledger.entries[0].record_status is RecordStatus.REVERTED
    assert result[0].operation == "REVERT_DECISIONS"


def test_invalid_or_repeated_revert_is_rejected_without_mutation():
    ledger = DecisionLedger({"policy": None})
    with pytest.raises(LedgerError):
        ledger.commit(Changeset("c", 1, reverts=(RevertDecisions(("missing",)),)))
    assert ledger.state == {"policy": None} and not ledger.entries


def test_changeset_is_atomic_when_validator_fails():
    ledger = DecisionLedger({"a": 0, "b": 0})
    change = Changeset("c", 1, (d("1", 0, 1, "a"), d("2", 0, 2, "b")))
    with pytest.raises(RuntimeError):
        ledger.commit(change, validator=lambda _: (_ for _ in ()).throw(RuntimeError("bad")))
    assert ledger.state == {"a": 0, "b": 0} and not ledger.entries


def test_snapshot_is_current_authority_and_compaction_keeps_references():
    ledger = DecisionLedger({"policy": "ALLOW"})
    ledger.commit(Changeset("c", 1, (d("DEC-1", "ALLOW", "EXCLUDE"),)))
    assert ledger.snapshot()["policy"] == "EXCLUDE"
    assert ledger.compact() == ledger.entries


def test_superseded_decision_cannot_be_reverted_over_current_snapshot():
    ledger = DecisionLedger({"policy": None})
    ledger.commit(Changeset("c", 1, (d("DEC-1", None, "EXCLUDE"),)))
    ledger.commit(
        Changeset(
            "c2",
            2,
            (
                Decision(
                    "DEC-2",
                    "c2",
                    2,
                    "SET",
                    "policy",
                    "EXCLUDE",
                    "ALLOW",
                ),
            ),
        )
    )

    assert ledger.entries[0].record_status is RecordStatus.SUPERSEDED
    with pytest.raises(LedgerError):
        ledger.commit(
            Changeset("c3", 3, reverts=(RevertDecisions(("DEC-1",)),))
        )
    assert ledger.snapshot()["policy"] == "ALLOW"
