from __future__ import annotations

from datetime import date

from eligibility.engine.evaluator import RuleEvaluator
from eligibility.engine.temporal import intervals_overlap
from eligibility.fixtures.shinhan_youth_first import (
    golden_context,
    shinhan_youth_first_product,
)
from eligibility.fixtures.user_001 import user_001
from eligibility.schema.enums import EvaluationStatus
from eligibility.schema.rule import NotExistsRule, OrRule
from eligibility.schema.user_fact import UserFactStore


def test_interval_overlap_is_inclusive_at_boundaries():
    assert intervals_overlap(
        date(2026, 1, 1),
        date(2026, 1, 31),
        date(2026, 1, 31),
        date(2026, 2, 10),
    )
    assert not intervals_overlap(
        date(2026, 1, 1),
        date(2026, 1, 30),
        date(2026, 1, 31),
        date(2026, 2, 10),
    )


def test_not_exists_fails_when_holding_overlaps_lookback():
    product = shinhan_youth_first_product()
    composite = product.preferential_rules[3].rule
    assert isinstance(composite, OrRule)
    first_transaction = composite.children[0]
    assert isinstance(first_transaction, NotExistsRule)

    result = RuleEvaluator(user_001(), golden_context()).evaluate(first_transaction)

    assert result.status == EvaluationStatus.UNSATISFIABLE
    assert result.reason_code == "PRIOR_HOLDING_OVERLAPS_LOOKBACK"
    assert result.evidence["lookback_from"] == "2025-08-20"
    assert result.evidence["lookback_to"] == "2026-08-19"
    assert result.evidence["holding_from"] == "2025-11-15"
    assert result.evidence["holding_to"] == "2026-04-12"
    assert result.evidence["overlap"] is True


def test_not_exists_requires_authoritative_coverage_when_no_match_exists():
    product = shinhan_youth_first_product()
    composite = product.preferential_rules[3].rule
    assert isinstance(composite, OrRule)
    first_transaction = composite.children[0]
    assert isinstance(first_transaction, NotExistsRule)
    empty = UserFactStore(user_id="TEST_USER")

    result = RuleEvaluator(empty, golden_context()).evaluate(first_transaction)

    assert result.status == EvaluationStatus.UNKNOWN
    assert result.reason_code == "ABSENCE_QUERY_COVERAGE_INCOMPLETE"
