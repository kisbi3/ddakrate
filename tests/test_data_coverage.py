from __future__ import annotations

from datetime import date

from eligibility.engine.evaluator import RuleEvaluator
from eligibility.schema.enums import EntityType, EvaluationStatus, FactSourceType
from eligibility.schema.rule import (
    CoverageRequirement,
    DateExpression,
    NotExistsRule,
    TimeWindow,
)
from eligibility.schema.user_fact import DataCoverage, UserFactStore


def _rule() -> NotExistsRule:
    return NotExistsRule(
        rule_id="NO_PRIOR_HOLDING",
        name="직전 1년 미보유",
        entity=EntityType.ACCOUNT_HOLDING_INTERVAL,
        filters={"institution": "SHINHAN_BANK"},
        overlaps=TimeWindow(
            start=DateExpression.literal(date(2025, 8, 20)),
            end=DateExpression.literal(date(2026, 8, 19)),
        ),
        coverage=CoverageRequirement(
            fact_domain="ACCOUNT_HOLDING_HISTORY",
            institution="SHINHAN_BANK",
        ),
    )


def _coverage(coverage_id: str, start: date, end: date) -> DataCoverage:
    return DataCoverage(
        coverage_id=coverage_id,
        user_id="TEST_USER",
        fact_domain="ACCOUNT_HOLDING_HISTORY",
        institution="SHINHAN_BANK",
        covered_from=start,
        covered_to=end,
        source_type=FactSourceType.MYDATA_VERIFIED,
    )


def test_not_exists_with_full_coverage(basic_context):
    store = UserFactStore(
        user_id="TEST_USER",
        data_coverages=[
            _coverage("C-1", date(2024, 1, 1), date(2026, 8, 19))
        ],
    )

    result = RuleEvaluator(store, basic_context).evaluate(_rule())

    assert result.status == EvaluationStatus.SATISFIED
    assert result.evidence["coverage_complete"] is True
    assert result.evidence["required_coverage_from"] == "2025-08-20"
    assert result.evidence["required_coverage_to"] == "2026-08-19"


def test_not_exists_with_incomplete_coverage_returns_unknown(basic_context):
    store = UserFactStore(
        user_id="TEST_USER",
        data_coverages=[
            _coverage("C-RECENT-6M", date(2026, 2, 20), date(2026, 8, 19))
        ],
    )

    result = RuleEvaluator(store, basic_context).evaluate(_rule())

    assert result.status == EvaluationStatus.UNKNOWN
    assert result.reason_code == "ABSENCE_QUERY_COVERAGE_INCOMPLETE"
    assert result.evidence["coverage_complete"] is False


def test_adjacent_coverage_intervals_can_jointly_cover_window(basic_context):
    store = UserFactStore(
        user_id="TEST_USER",
        data_coverages=[
            _coverage("C-A", date(2025, 8, 20), date(2026, 2, 19)),
            _coverage("C-B", date(2026, 2, 20), date(2026, 8, 19)),
        ],
    )

    result = RuleEvaluator(store, basic_context).evaluate(_rule())

    assert result.status == EvaluationStatus.SATISFIED
    assert result.evidence["coverage_complete"] is True
