from __future__ import annotations

from decimal import Decimal

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.shinhan_youth_first import (
    golden_context,
    golden_contribution_plan,
    shinhan_youth_first_product,
)
from eligibility.fixtures.user_001 import (
    user_001,
    with_event_coupon_answer,
    with_supersol_answer,
)
from eligibility.schema.enums import EvaluationStatus


def _by_id(result):
    return {rule.rule_id: rule for rule in result.preferential_rule_results}


def test_golden_user_condition_statuses_and_rates():
    result = FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(),
        user_001(),
        golden_context(),
        golden_contribution_plan(),
    )
    rules = _by_id(result)
    eligibility_children = {
        child.rule_id: child for child in result.eligibility.children
    }

    assert result.eligibility_status == EvaluationStatus.SATISFIED
    assert eligibility_children["ELIG_AGE"].status == EvaluationStatus.SATISFIED
    assert eligibility_children["ELIG_ONE_ACCOUNT"].status == EvaluationStatus.SATISFIED
    assert rules["RATE_SALARY"].status == EvaluationStatus.ACHIEVABLE
    assert rules["RATE_CARD"].status == EvaluationStatus.ACHIEVABLE
    assert rules["RATE_SUPERSOL"].status == EvaluationStatus.UNKNOWN
    assert rules["RATE_FIRST_OR_EVENT"].status == EvaluationStatus.UNKNOWN
    assert result.global_guard_results[0].status == EvaluationStatus.ACHIEVABLE

    first_branch, event_branch = rules["RATE_FIRST_OR_EVENT"].children
    assert first_branch.status == EvaluationStatus.UNSATISFIABLE
    assert event_branch.status == EvaluationStatus.UNKNOWN

    assert result.rates.advertised_max_rate == Decimal("6.05")
    assert result.rates.confirmed_rate == Decimal("3.05")
    assert result.rates.realizable_rate == Decimal("4.55")
    assert result.rates.conditional_upper_rate == Decimal("6.05")

    missing = {fact.fact_type for fact in result.missing_facts}
    assert missing == {
        "SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING",
        "SPECIAL_RATE_COUPON_VALID",
    }


def test_first_transaction_trace_contains_exact_overlap_evidence():
    result = FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(), user_001(), golden_context()
    )
    first_branch = _by_id(result)["RATE_FIRST_OR_EVENT"].children[0]

    assert first_branch.reason_code == "PRIOR_HOLDING_OVERLAPS_LOOKBACK"
    assert first_branch.evidence == {
        "entity": "ACCOUNT_HOLDING_INTERVAL",
        "evidence_origin": "AUTHORITATIVE",
        "filters": {
            "institution": "SHINHAN_BANK",
            "product_type": [
                "TIME_DEPOSIT",
                "INSTALLMENT_SAVINGS",
                "HOUSING_SUBSCRIPTION",
            ],
        },
        "matched_count": 1,
        "matched_account_ids": ["A-SHINHAN-SAVINGS-OLD"],
        "window": {
            "start": "2025-08-20",
            "end": "2026-08-19",
            "inclusive": True,
        },
        "holding_from": "2025-11-15",
        "holding_to": "2026-04-12",
        "lookback_from": "2025-08-20",
        "lookback_to": "2026-08-19",
        "overlap": True,
    }
    source = first_branch.source_provenance[0]
    assert source.details["page"] == 3
    assert source.details["section"] == "[4] 첫거래 또는 이벤트 우대"


def test_supersol_user_answer_changes_realizable_rate_without_treating_event_unknown_as_achievable():
    answered_user = with_supersol_answer(user_001(), True)

    result = FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(), answered_user, golden_context()
    )

    assert _by_id(result)["RATE_SUPERSOL"].status == EvaluationStatus.ACHIEVABLE
    assert result.rates.confirmed_rate == Decimal("3.05")
    assert result.rates.realizable_rate == Decimal("5.05")
    assert result.rates.conditional_upper_rate == Decimal("6.05")
    assert [fact.fact_type for fact in result.missing_facts] == [
        "SPECIAL_RATE_COUPON_VALID"
    ]


def test_resolved_negative_event_answer_reduces_evidence_aware_conditional_upper():
    answered_user = with_event_coupon_answer(
        with_supersol_answer(user_001(), True), False
    )

    result = FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(), answered_user, golden_context()
    )

    assert _by_id(result)["RATE_FIRST_OR_EVENT"].status == EvaluationStatus.UNSATISFIABLE
    assert result.rates.realizable_rate == Decimal("5.05")
    assert result.rates.conditional_upper_rate == Decimal("5.05")


def test_same_input_produces_same_evaluation_id_and_result():
    engine = FinancialEligibilityEngine()
    product = shinhan_youth_first_product()
    user = user_001()
    context = golden_context()

    first = engine.evaluate_product(product, user, context)
    second = engine.evaluate_product(product, user, context)

    assert first == second
    assert first.evaluation_id == second.evaluation_id


def test_supersol_missing_fact_request_has_strategy_and_rate_impact():
    result = FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(), user_001(), golden_context()
    )
    request = next(
        fact
        for fact in result.missing_facts
        if fact.fact_type == "SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING"
    )

    assert request.resolution_strategy.value == "ASK_USER"
    assert request.impact is not None
    assert request.impact.rate_pp == Decimal("0.5")
    assert request.question is not None


def test_early_termination_fact_makes_maturity_guard_unsatisfiable_and_blocks_rewards():
    from datetime import date, datetime, timezone

    from eligibility.schema.enums import FactSourceType
    from eligibility.schema.user_fact import UserFact

    store = user_001().with_fact(
        UserFact(
            fact_id="F-EARLY-TERMINATION",
            user_id="U001",
            fact_type="TERMINATION_TYPE",
            value="EARLY_TERMINATED",
            valid_from=date(2027, 8, 20),
            source_type=FactSourceType.INSTITUTION_VERIFIED,
            collected_at=datetime(2027, 8, 20, tzinfo=timezone.utc),
        )
    )

    result = FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(), store, golden_context()
    )

    assert result.global_guard_results[0].status == EvaluationStatus.UNSATISFIABLE
    assert result.rates.confirmed_rate == Decimal("3.05")
    assert result.rates.realizable_rate == Decimal("3.05")
    assert result.rates.conditional_upper_rate == Decimal("3.05")
