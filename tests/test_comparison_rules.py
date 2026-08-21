from __future__ import annotations

import pytest

from eligibility.engine.evaluator import RuleEvaluator
from eligibility.schema.enums import ComparisonOperator, EvaluationStatus
from eligibility.schema.rule import FactComparisonRule
from eligibility.schema.user_fact import UserFactStore


@pytest.mark.parametrize(
    ("actual", "operator", "expected"),
    [
        ("SHINHAN_BANK", ComparisonOperator.EQ, "SHINHAN_BANK"),
        (6, ComparisonOperator.GTE, 6),
        ("CHECK", ComparisonOperator.IN, ["CREDIT", "CHECK"]),
    ],
)
def test_eq_gte_and_in_are_deterministic(
    actual,
    operator,
    expected,
    basic_context,
    fact_factory,
):
    store = UserFactStore(
        user_id="TEST_USER", facts=[fact_factory("F", "FACT", actual)]
    )
    rule = FactComparisonRule(
        rule_id="COMPARE",
        name="compare",
        fact_type="FACT",
        operator=operator,
        expected=expected,
    )

    result = RuleEvaluator(store, basic_context).evaluate(rule)

    assert result.status == EvaluationStatus.SATISFIED


def test_missing_fact_returns_unknown(basic_context, empty_store):
    rule = FactComparisonRule(
        rule_id="MISSING",
        name="missing",
        fact_type="NOT_THERE",
        operator=ComparisonOperator.EQ,
        expected=True,
    )

    result = RuleEvaluator(empty_store, basic_context).evaluate(rule)

    assert result.status == EvaluationStatus.UNKNOWN
    assert result.reason_code == "REQUIRED_FACT_MISSING"
