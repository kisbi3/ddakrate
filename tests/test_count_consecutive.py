from __future__ import annotations

from datetime import datetime, timedelta, timezone

from eligibility.engine.evaluator import RuleEvaluator
from eligibility.schema.enums import (
    ComparisonOperator,
    EntityType,
    EvaluationStatus,
    FactSourceType,
    ScheduledOccurrenceMethod,
    ScheduledOccurrenceStatus,
)
from eligibility.schema.rule import CountConsecutiveRule, ValuePredicate
from eligibility.schema.user_fact import ScheduledOccurrence, UserFactStore


_BASE = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _occurrence(
    sequence: int,
    status: ScheduledOccurrenceStatus,
    *,
    method: ScheduledOccurrenceMethod = ScheduledOccurrenceMethod.AUTO_TRANSFER,
    suffix: str = "AUTO",
) -> ScheduledOccurrence:
    return ScheduledOccurrence(
        occurrence_id=f"O-{sequence:02d}-{suffix}",
        user_id="TEST_USER",
        schedule_id="SCHEDULE-001",
        sequence_no=sequence,
        scheduled_at=_BASE + timedelta(weeks=sequence - 1),
        method=method,
        status=status,
        observed_event_id=f"E-{sequence:02d}-{suffix}",
        source_type=FactSourceType.INSTITUTION_VERIFIED,
    )


def _rule(expected: int = 7) -> CountConsecutiveRule:
    return CountConsecutiveRule(
        rule_id="CONSECUTIVE",
        name="자동이체 연속 성공",
        entity=EntityType.SCHEDULED_OCCURRENCE,
        schedule_id="SCHEDULE-001",
        filters=[
            ValuePredicate(
                path="method",
                operator=ComparisonOperator.EQ,
                expected="AUTO_TRANSFER",
            ),
            ValuePredicate(
                path="status",
                operator=ComparisonOperator.EQ,
                expected="SUCCESS",
            ),
        ],
        from_sequence=1,
        operator=ComparisonOperator.GTE,
        expected=expected,
    )


def _context_after_week_7(basic_context):
    return basic_context.model_copy(
        update={"as_of": (_BASE + timedelta(weeks=7)).date()}, deep=True
    )


def test_count_consecutive_all_success(basic_context):
    store = UserFactStore(
        user_id="TEST_USER",
        scheduled_occurrences=[
            _occurrence(sequence, ScheduledOccurrenceStatus.SUCCESS)
            for sequence in range(1, 8)
        ],
    )

    result = RuleEvaluator(store, _context_after_week_7(basic_context)).evaluate(
        _rule()
    )

    assert result.status == EvaluationStatus.SATISFIED
    assert result.progress is not None
    assert result.progress.current == 7
    assert result.evidence["qualifying_occurrence_count"] == 7
    assert result.evidence["matched_sequence_nos"] == list(range(1, 8))


def test_count_consecutive_stops_at_failure(basic_context):
    statuses = [
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.FAILED,
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.SUCCESS,
    ]
    store = UserFactStore(
        user_id="TEST_USER",
        scheduled_occurrences=[
            _occurrence(sequence, status)
            for sequence, status in enumerate(statuses, start=1)
        ],
    )

    result = RuleEvaluator(store, _context_after_week_7(basic_context)).evaluate(
        _rule()
    )

    assert result.status == EvaluationStatus.UNSATISFIABLE
    assert result.progress is not None
    assert result.progress.current == 3
    assert result.evidence["qualifying_occurrence_count"] == 6
    assert result.evidence["first_break_sequence"] == 4
    assert result.evidence["break_reason"] == "SEQUENCE_PREDICATE_FAILED"


def test_count_consecutive_manual_recovery_does_not_restore(basic_context):
    statuses = [
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.FAILED,
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.SUCCESS,
    ]
    occurrences = [
        _occurrence(sequence, status)
        for sequence, status in enumerate(statuses, start=1)
    ]
    occurrences.append(
        _occurrence(
            4,
            ScheduledOccurrenceStatus.SUCCESS,
            method=ScheduledOccurrenceMethod.MANUAL_TRANSFER,
            suffix="MANUAL-FILL",
        )
    )
    store = UserFactStore(
        user_id="TEST_USER",
        scheduled_occurrences=occurrences,
    )

    result = RuleEvaluator(store, _context_after_week_7(basic_context)).evaluate(
        _rule()
    )

    assert result.status == EvaluationStatus.UNSATISFIABLE
    assert result.progress is not None
    assert result.progress.current == 3
    assert result.evidence["qualifying_occurrence_count"] == 6
    assert result.evidence["first_break_sequence"] == 4
    assert set(result.evidence["break_occurrence_ids"]) == {
        "O-04-AUTO",
        "O-04-MANUAL-FILL",
    }
