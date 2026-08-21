from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from eligibility.engine.comparison import compare_values, get_value_by_path
from eligibility.engine.temporal import period_key
from eligibility.schema.enums import PeriodUnit
from eligibility.schema.rule import ValuePredicate
from eligibility.schema.user_fact import ScheduledOccurrence, UserFact


def values_match_predicates(value: object, predicates: list[ValuePredicate]) -> bool:
    try:
        return all(
            compare_values(
                get_value_by_path(value, predicate.path),
                predicate.operator,
                predicate.expected,
            )
            for predicate in predicates
        )
    except (KeyError, TypeError, ValueError):
        return False


def facts_matching_predicates(
    facts: Iterable[UserFact],
    predicates: list[ValuePredicate],
) -> list[UserFact]:
    return [fact for fact in facts if values_match_predicates(fact.value, predicates)]


def records_matching_predicates(
    records: Iterable[Any],
    predicates: list[ValuePredicate],
) -> list[Any]:
    return [record for record in records if values_match_predicates(record, predicates)]


def fact_event_date(fact: UserFact, date_path: str | None) -> date | None:
    if date_path is None:
        return fact.valid_from
    try:
        value = get_value_by_path(fact.value, date_path)
    except KeyError:
        return None
    return _coerce_date(value)


def record_event_date(record: object, date_path: str | None, *, default_path: str) -> date | None:
    path = date_path or default_path
    try:
        value = get_value_by_path(record, path)
    except KeyError:
        return None
    return _coerce_date(value)


def _coerce_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None


def distinct_periods(
    facts: Iterable[UserFact],
    *,
    period: PeriodUnit,
    date_path: str | None,
    start: date,
    end: date,
    as_of: date,
) -> list[str]:
    periods: set[str] = set()
    current_cutoff = min(end, as_of)
    for fact in facts:
        occurred_at = fact_event_date(fact, date_path)
        if occurred_at is None:
            continue
        if start <= occurred_at <= current_cutoff:
            periods.add(period_key(occurred_at, period))
    return sorted(periods)


def distinct_record_periods(
    records: Iterable[object],
    *,
    period: PeriodUnit,
    date_path: str | None,
    default_path: str,
    start: date,
    end: date,
    as_of: date,
) -> list[str]:
    periods: set[str] = set()
    current_cutoff = min(end, as_of)
    for record in records:
        occurred_at = record_event_date(record, date_path, default_path=default_path)
        if occurred_at is None:
            continue
        if start <= occurred_at <= current_cutoff:
            periods.add(period_key(occurred_at, period))
    return sorted(periods)


def distinct_months(
    facts: Iterable[UserFact],
    *,
    date_path: str | None,
    start: date,
    end: date,
    as_of: date,
) -> list[str]:
    """v0.1 compatibility alias."""

    return distinct_periods(
        facts,
        period=PeriodUnit.MONTH,
        date_path=date_path,
        start=start,
        end=end,
        as_of=as_of,
    )


@dataclass(frozen=True)
class ConsecutiveCount:
    count: int
    matched_sequence_nos: tuple[int, ...]
    first_break_sequence: int | None
    break_reason: str
    break_occurrence_ids: tuple[str, ...] = ()
    start_sequence: int | None = None
    end_sequence: int | None = None
    trailing_count: int = 0
    latest_observed_sequence: int = 0


def count_consecutive_occurrences(
    occurrences: Iterable[ScheduledOccurrence],
    *,
    predicates: list[ValuePredicate],
    from_sequence: int | None,
) -> ConsecutiveCount:
    """Count a fixed-start streak or the longest streak anywhere.

    Multiple records may share a sequence number. A sequence qualifies if at
    least one record matches the predicate. Consequently, a manual success
    cannot repair a failed AUTO_TRANSFER sequence when the predicate requires
    method=AUTO_TRANSFER and status=SUCCESS.
    """

    by_sequence: dict[int, list[ScheduledOccurrence]] = {}
    for occurrence in occurrences:
        by_sequence.setdefault(occurrence.sequence_no, []).append(occurrence)
    latest = max(by_sequence, default=0)
    trailing = _trailing_qualifying_count(by_sequence, predicates, latest)

    if from_sequence is not None:
        matched: list[int] = []
        current = from_sequence
        while True:
            records = sorted(
                by_sequence.get(current, []), key=lambda item: item.occurrence_id
            )
            if not records:
                return ConsecutiveCount(
                    count=len(matched),
                    matched_sequence_nos=tuple(matched),
                    first_break_sequence=current,
                    break_reason="SEQUENCE_NOT_OBSERVED",
                    start_sequence=matched[0] if matched else from_sequence,
                    end_sequence=matched[-1] if matched else None,
                    trailing_count=trailing,
                    latest_observed_sequence=latest,
                )
            if any(values_match_predicates(record, predicates) for record in records):
                matched.append(current)
                current += 1
                continue
            return ConsecutiveCount(
                count=len(matched),
                matched_sequence_nos=tuple(matched),
                first_break_sequence=current,
                break_reason="SEQUENCE_PREDICATE_FAILED",
                break_occurrence_ids=tuple(record.occurrence_id for record in records),
                start_sequence=matched[0] if matched else from_sequence,
                end_sequence=matched[-1] if matched else None,
                trailing_count=trailing,
                latest_observed_sequence=latest,
            )

    if not by_sequence:
        return ConsecutiveCount(
            count=0,
            matched_sequence_nos=(),
            first_break_sequence=None,
            break_reason="NO_SEQUENCE_OBSERVED",
            trailing_count=0,
            latest_observed_sequence=0,
        )

    best: list[int] = []
    current_run: list[int] = []
    best_break: tuple[int | None, tuple[str, ...]] = (None, ())
    for sequence in range(min(by_sequence), latest + 1):
        records = sorted(by_sequence.get(sequence, []), key=lambda item: item.occurrence_id)
        qualifies = bool(records) and any(
            values_match_predicates(record, predicates) for record in records
        )
        if qualifies:
            current_run.append(sequence)
            if len(current_run) > len(best):
                best = list(current_run)
        else:
            if len(current_run) == len(best) and current_run:
                best_break = (
                    sequence,
                    tuple(record.occurrence_id for record in records),
                )
            current_run = []

    first_break = best[-1] + 1 if best and best[-1] < latest else None
    break_ids: tuple[str, ...] = ()
    if first_break is not None:
        break_ids = tuple(
            item.occurrence_id for item in sorted(
                by_sequence.get(first_break, []), key=lambda item: item.occurrence_id
            )
        )
    elif best_break[0] is not None:
        first_break, break_ids = best_break

    return ConsecutiveCount(
        count=len(best),
        matched_sequence_nos=tuple(best),
        first_break_sequence=first_break,
        break_reason=(
            "LONGEST_RUN_IDENTIFIED" if best else "NO_QUALIFYING_SEQUENCE"
        ),
        break_occurrence_ids=break_ids,
        start_sequence=best[0] if best else None,
        end_sequence=best[-1] if best else None,
        trailing_count=trailing,
        latest_observed_sequence=latest,
    )


def _trailing_qualifying_count(
    by_sequence: dict[int, list[ScheduledOccurrence]],
    predicates: list[ValuePredicate],
    latest: int,
) -> int:
    count = 0
    sequence = latest
    while sequence >= 1:
        records = by_sequence.get(sequence, [])
        if not records or not any(
            values_match_predicates(record, predicates) for record in records
        ):
            break
        count += 1
        sequence -= 1
    return count
