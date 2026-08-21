from __future__ import annotations

from datetime import date

from eligibility.engine.evaluator import RuleEvaluator
from eligibility.schema.enums import ComparisonOperator, EvaluationStatus, PeriodUnit
from eligibility.schema.rule import CountDistinctPeriodsRule, DateExpression, TimeWindow
from eligibility.schema.user_fact import UserFactStore


def test_count_distinct_periods_day_deduplicates_same_day(
    basic_context,
    fact_factory,
):
    context = basic_context.model_copy(update={"as_of": date(2026, 1, 10)}, deep=True)
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[
            fact_factory("D1-A", "SAVE", 1, valid_from=date(2026, 1, 1)),
            fact_factory("D1-B", "SAVE", 1, valid_from=date(2026, 1, 1)),
            fact_factory("D2", "SAVE", 1, valid_from=date(2026, 1, 2)),
        ],
    )
    rule = CountDistinctPeriodsRule(
        rule_id="DISTINCT-DAYS",
        name="서로 다른 저금일",
        period=PeriodUnit.DAY,
        fact_type="SAVE",
        window=TimeWindow(
            start=DateExpression.literal(date(2026, 1, 1)),
            end=DateExpression.literal(date(2026, 1, 31)),
        ),
        operator=ComparisonOperator.GTE,
        expected=2,
    )

    result = RuleEvaluator(store, context).evaluate(rule)

    assert result.status == EvaluationStatus.SATISFIED
    assert result.progress is not None
    assert result.progress.unit == "DAY"
    assert result.evidence["qualifying_periods"] == ["2026-01-01", "2026-01-02"]


def test_count_distinct_periods_month_matches_legacy_semantics(
    basic_context,
    fact_factory,
):
    context = basic_context.model_copy(update={"as_of": date(2026, 3, 1)}, deep=True)
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[
            fact_factory("JAN", "EVENT", 1, valid_from=date(2026, 1, 5)),
            fact_factory("FEB", "EVENT", 1, valid_from=date(2026, 2, 5)),
        ],
    )
    rule = CountDistinctPeriodsRule(
        rule_id="DISTINCT-MONTHS-V02",
        name="서로 다른 월",
        period=PeriodUnit.MONTH,
        fact_type="EVENT",
        window=TimeWindow(
            start=DateExpression.literal(date(2026, 1, 1)),
            end=DateExpression.literal(date(2026, 2, 28)),
        ),
        operator=ComparisonOperator.GTE,
        expected=2,
    )

    result = RuleEvaluator(store, context).evaluate(rule)

    assert result.status == EvaluationStatus.SATISFIED
    assert result.evidence["qualifying_periods"] == ["2026-01", "2026-02"]
