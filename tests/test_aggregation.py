from __future__ import annotations

from datetime import date

from eligibility.engine.evaluator import RuleEvaluator
from eligibility.schema.enums import ComparisonOperator, ContextDateField, EvaluationStatus
from eligibility.schema.rule import (
    CountDistinctMonthsRule,
    DateExpression,
    FactComparisonRule,
    FutureAchievementSpec,
    TimeWindow,
)
from eligibility.schema.user_fact import UserFactStore


def _window(start: date, end: date) -> TimeWindow:
    return TimeWindow(start=DateExpression.literal(start), end=DateExpression.literal(end))


def test_count_distinct_months_deduplicates_multiple_events_in_same_month(
    basic_context,
    fact_factory,
):
    context = basic_context.model_copy(
        update={"as_of": date(2026, 3, 1)}, deep=True
    )
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[
            fact_factory("JAN-1", "EVENT", 1, valid_from=date(2026, 1, 3)),
            fact_factory("JAN-2", "EVENT", 1, valid_from=date(2026, 1, 20)),
            fact_factory("FEB", "EVENT", 1, valid_from=date(2026, 2, 5)),
            fact_factory("OUTSIDE", "EVENT", 1, valid_from=date(2026, 3, 1)),
        ],
    )
    rule = CountDistinctMonthsRule(
        rule_id="MONTHS",
        name="two months",
        fact_type="EVENT",
        window=_window(date(2026, 1, 1), date(2026, 2, 28)),
        operator=ComparisonOperator.GTE,
        expected=2,
    )

    result = RuleEvaluator(store, context).evaluate(rule)

    assert result.status == EvaluationStatus.SATISFIED
    assert result.progress.current == 2
    assert result.evidence["qualifying_months"] == ["2026-01", "2026-02"]


def test_incomplete_month_count_is_achievable_when_capability_and_time_remain(
    basic_context,
    fact_factory,
):
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[fact_factory("CAP", "CAN_DO", True)],
    )
    capability = FactComparisonRule(
        rule_id="CAP",
        name="capability",
        fact_type="CAN_DO",
        operator=ComparisonOperator.EQ,
        expected=True,
    )
    rule = CountDistinctMonthsRule(
        rule_id="MONTHS",
        name="six months",
        fact_type="EVENT",
        window=_window(date(2026, 1, 1), date(2026, 6, 30)),
        operator=ComparisonOperator.GTE,
        expected=6,
        future_achievement=FutureAchievementSpec(capability_rule=capability),
    )

    result = RuleEvaluator(store, basic_context).evaluate(rule)

    assert result.status == EvaluationStatus.ACHIEVABLE
    assert result.progress.current == 0
    assert result.progress.required == 6


def test_incomplete_month_count_is_unsatisfiable_when_not_enough_months_remain(
    basic_context,
    fact_factory,
):
    context = basic_context.model_copy(update={"as_of": date(2026, 5, 15)}, deep=True)
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[fact_factory("CAP", "CAN_DO", True)],
    )
    capability = FactComparisonRule(
        rule_id="CAP",
        name="capability",
        fact_type="CAN_DO",
        operator=ComparisonOperator.EQ,
        expected=True,
    )
    rule = CountDistinctMonthsRule(
        rule_id="MONTHS",
        name="six months",
        fact_type="EVENT",
        window=_window(date(2026, 1, 1), date(2026, 6, 30)),
        operator=ComparisonOperator.GTE,
        expected=6,
        future_achievement=FutureAchievementSpec(capability_rule=capability),
    )

    result = RuleEvaluator(store, context).evaluate(rule)

    assert result.status == EvaluationStatus.UNSATISFIABLE
    assert result.reason_code == "INSUFFICIENT_REMAINING_MONTHS"
