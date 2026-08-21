from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Iterator

from eligibility.schema.enums import PeriodUnit, TimeExpressionType
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.rule import DateExpression, TimeWindow


class TemporalEvaluationError(ValueError):
    """Raised when a temporal expression is malformed or unsupported."""


def add_months(value: date, amount: int) -> date:
    month_index = value.month - 1 + amount
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_years(value: date, amount: int) -> date:
    target_year = value.year + amount
    try:
        return value.replace(year=target_year)
    except ValueError:
        # 29 February maps to 28 February in a non-leap year.
        return value.replace(year=target_year, month=2, day=28)


def start_of_month(value: date) -> date:
    return value.replace(day=1)


def end_of_month(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def business_day_offset(
    value: date,
    amount: int,
    holidays: set[date] | None = None,
) -> date:
    holidays = holidays or set()
    if amount == 0:
        return value

    direction = 1 if amount > 0 else -1
    remaining = abs(amount)
    current = value
    while remaining:
        current += timedelta(days=direction)
        if current.weekday() < 5 and current not in holidays:
            remaining -= 1
    return current


def resolve_date_expression(
    expression: DateExpression,
    context: EvaluationContext,
) -> date:
    if expression.type == TimeExpressionType.LITERAL:
        assert expression.value is not None
        return expression.value
    if expression.type == TimeExpressionType.CONTEXT:
        assert expression.context_field is not None
        return getattr(context, expression.context_field.value)

    if expression.expression is None:
        raise TemporalEvaluationError(f"Missing nested expression for {expression.type}")
    nested = resolve_date_expression(expression.expression, context)

    if expression.type == TimeExpressionType.START_OF_MONTH:
        return start_of_month(nested)
    if expression.type == TimeExpressionType.END_OF_MONTH:
        return end_of_month(nested)
    if expression.type == TimeExpressionType.ADD_DAYS:
        assert expression.amount is not None
        return nested + timedelta(days=expression.amount)
    if expression.type == TimeExpressionType.ADD_MONTHS:
        assert expression.amount is not None
        return add_months(nested, expression.amount)
    if expression.type == TimeExpressionType.ADD_YEARS:
        assert expression.amount is not None
        return add_years(nested, expression.amount)
    if expression.type == TimeExpressionType.BUSINESS_DAY_OFFSET:
        assert expression.amount is not None
        return business_day_offset(nested, expression.amount, context.business_holidays)

    raise TemporalEvaluationError(f"Unsupported time expression: {expression.type}")


def resolve_window(window: TimeWindow, context: EvaluationContext) -> tuple[date, date]:
    start = resolve_date_expression(window.start, context)
    end = resolve_date_expression(window.end, context)
    if start > end:
        raise TemporalEvaluationError(f"Invalid window: {start} > {end}")
    return start, end


def intervals_overlap(
    left_start: date,
    left_end: date | None,
    right_start: date,
    right_end: date | None,
    *,
    inclusive: bool = True,
) -> bool:
    left_end_value = left_end or date.max
    right_end_value = right_end or date.max
    if inclusive:
        return left_start <= right_end_value and right_start <= left_end_value
    return left_start < right_end_value and right_start < left_end_value


def interval_contains(
    start: date,
    end: date | None,
    point: date,
    *,
    inclusive: bool = True,
) -> bool:
    end_value = end or date.max
    if inclusive:
        return start <= point <= end_value
    return start < point < end_value


def coverage_intervals_fully_cover(
    intervals: Iterable[tuple[date, date]],
    required_start: date,
    required_end: date,
) -> bool:
    """Return whether the inclusive union of intervals covers the full window."""

    relevant = sorted(
        (
            (max(start, required_start), min(end, required_end))
            for start, end in intervals
            if intervals_overlap(start, end, required_start, required_end)
        ),
        key=lambda item: (item[0], item[1]),
    )
    if not relevant or relevant[0][0] > required_start:
        return False

    covered_to = relevant[0][1]
    if covered_to >= required_end:
        return True

    for start, end in relevant[1:]:
        # Inclusive date intervals are continuous when the next interval starts
        # no later than the day after the currently covered endpoint.
        if start > covered_to + timedelta(days=1):
            return False
        covered_to = max(covered_to, end)
        if covered_to >= required_end:
            return True
    return covered_to >= required_end


def period_key(value: date, period: PeriodUnit) -> str:
    if period == PeriodUnit.DAY:
        return value.isoformat()
    if period == PeriodUnit.WEEK:
        iso_year, iso_week, _ = value.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    if period == PeriodUnit.MONTH:
        return f"{value.year:04d}-{value.month:02d}"
    raise TemporalEvaluationError(f"Unsupported period: {period}")


def month_key(value: date) -> str:
    """v0.1 compatibility helper."""

    return period_key(value, PeriodUnit.MONTH)


def iter_month_starts(start: date, end: date) -> Iterator[date]:
    current = start_of_month(start)
    final = start_of_month(end)
    while current <= final:
        yield current
        current = add_months(current, 1)


def available_period_keys(
    start: date,
    end: date,
    *,
    period: PeriodUnit,
    excluded: Iterable[str] = (),
) -> list[str]:
    if start > end:
        return []

    keys: set[str] = set()
    if period == PeriodUnit.MONTH:
        keys = {period_key(value, period) for value in iter_month_starts(start, end)}
    else:
        current = start
        while current <= end:
            keys.add(period_key(current, period))
            current += timedelta(days=1)

    return sorted(keys - set(excluded))


def available_month_keys(
    start: date,
    end: date,
    *,
    excluded: Iterable[str] = (),
) -> list[str]:
    """v0.1 compatibility helper."""

    return available_period_keys(
        start,
        end,
        period=PeriodUnit.MONTH,
        excluded=excluded,
    )


@dataclass(frozen=True)
class OpportunityProjection:
    period: PeriodUnit
    opportunity_start: date
    deadline: date
    qualifying_periods: tuple[str, ...]
    future_periods: tuple[str, ...]

    @property
    def remaining_opportunities(self) -> int:
        return len(self.future_periods)


def project_remaining_opportunities(
    *,
    window_start: date,
    deadline: date,
    as_of: date,
    subscription_date: date,
    period: PeriodUnit,
    qualifying_periods: Iterable[str] = (),
) -> OpportunityProjection:
    """Project future period buckets without re-counting achieved buckets.

    The current DAY/WEEK/MONTH remains an opportunity when it has not yet
    qualified. Once it is present in ``qualifying_periods`` it is excluded, so
    RuleEvaluator and GoalFactory share the same bucket semantics.
    """

    achieved = tuple(sorted(set(qualifying_periods)))
    opportunity_start = max(window_start, as_of, subscription_date)
    future = (
        available_period_keys(
            opportunity_start,
            deadline,
            period=period,
            excluded=achieved,
        )
        if opportunity_start <= deadline
        else []
    )
    return OpportunityProjection(
        period=period,
        opportunity_start=opportunity_start,
        deadline=deadline,
        qualifying_periods=achieved,
        future_periods=tuple(future),
    )


def calculate_age(birth_date: date, reference_date: date) -> int:
    years = reference_date.year - birth_date.year
    before_birthday = (reference_date.month, reference_date.day) < (
        birth_date.month,
        birth_date.day,
    )
    return years - int(before_birthday)
