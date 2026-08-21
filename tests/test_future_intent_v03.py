from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from eligibility.application import submit_user_fact
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    STEP_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
    step_30000_300d_product,
    step_30000_context,
    step_30000_user,
)
from eligibility.schema.enums import EvaluationStatus, FactSemanticType, FactSourceType
from eligibility.schema.user_fact import UserFact


def _rate_result(result):
    return result.preferential_rule_results[0]


def test_future_intent_is_distinct_from_observed_fact():
    observed = UserFact(
        fact_id="F-OBSERVED-NOT-INTENT",
        user_id="U-FUTURE-001",
        fact_type=SALARY_INTENT_FACT_TYPE,
        value=True,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_FACT,
        valid_from=date(2026, 8, 19),
        collected_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    store = salary_envelope_user().with_fact(observed)

    result = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(), store, salary_envelope_context()
    )

    assert observed.semantic_type == FactSemanticType.OBSERVED_FACT
    assert _rate_result(result).status == EvaluationStatus.UNKNOWN
    assert _rate_result(result).reason_code == "FUTURE_INTENT_REQUIRED"


def test_future_intent_requires_user_declared_source():
    with pytest.raises(ValidationError):
        UserFact(
            fact_id="BAD-FUTURE-INTENT",
            user_id="U-FUTURE-001",
            fact_type=SALARY_INTENT_FACT_TYPE,
            value=True,
            source_type=FactSourceType.INSTITUTION_VERIFIED,
            semantic_type=FactSemanticType.FUTURE_INTENT,
        )


def test_post_subscription_missing_intent_returns_unknown():
    result = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
    )

    rate = _rate_result(result)
    assert rate.status == EvaluationStatus.UNKNOWN
    assert rate.progress is not None
    assert (rate.progress.current, rate.progress.required, rate.progress.unit) == (
        0,
        6,
        "MONTH",
    )
    assert result.missing_facts[0].fact_type == SALARY_INTENT_FACT_TYPE
    assert result.missing_facts[0].resolution_strategy.value == "ASK_USER"


def test_user_yes_and_time_feasible_returns_achievable():
    store = submit_user_fact(
        salary_envelope_user(),
        future_intent_fact(SALARY_INTENT_FACT_TYPE, True),
    )

    result = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(), store, salary_envelope_context()
    )

    rate = _rate_result(result)
    assert rate.status == EvaluationStatus.ACHIEVABLE
    assert rate.reason_code == "FUTURE_INTENT_CONFIRMED_AND_TIME_FEASIBLE"
    assert rate.evidence["intent_semantic_type"] == "FUTURE_INTENT"
    assert rate.evidence["time_feasible"] is True
    assert result.rates.realizable_rate == Decimal("4.05")


def test_user_no_excludes_reward():
    store = submit_user_fact(
        salary_envelope_user(),
        future_intent_fact(SALARY_INTENT_FACT_TYPE, False),
    )

    result = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(), store, salary_envelope_context()
    )

    rate = _rate_result(result)
    assert rate.status == EvaluationStatus.UNSATISFIABLE
    assert rate.reason_code == "USER_DECLINED"
    assert rate.evidence["ui_disposition"] == "NOT_SELECTED"
    assert result.rates.realizable_rate == Decimal("3.05")
    assert result.rates.user_specific_conditional_upper_rate == Decimal("3.05")


def test_insufficient_future_opportunities_unsatisfiable():
    context = step_30000_context(available_days=299)

    result = FinancialEligibilityEngine().evaluate_product(
        step_30000_300d_product(), step_30000_user(), context
    )

    rate = _rate_result(result)
    assert rate.status == EvaluationStatus.UNSATISFIABLE
    assert rate.reason_code == "INSUFFICIENT_FUTURE_OPPORTUNITIES"
    assert rate.evidence["remaining_opportunities"] == 299
    assert rate.evidence["available_capacity"] == 299
    assert rate.missing_facts == []


def test_step_30000_300d_time_feasible():
    result = FinancialEligibilityEngine().evaluate_product(
        step_30000_300d_product(),
        step_30000_user(),
        step_30000_context(available_days=365),
    )

    rate = _rate_result(result)
    assert rate.status == EvaluationStatus.UNKNOWN
    assert rate.evidence["remaining_opportunities"] == 365
    assert rate.evidence["time_feasible"] is True


def test_step_30000_300d_time_infeasible():
    result = FinancialEligibilityEngine().evaluate_product(
        step_30000_300d_product(),
        step_30000_user(),
        step_30000_context(available_days=250),
    )

    rate = _rate_result(result)
    assert rate.status == EvaluationStatus.UNSATISFIABLE
    assert rate.evidence["buffer"] == -50


def test_step_30000_300d_user_intent():
    store = submit_user_fact(
        step_30000_user(),
        future_intent_fact(STEP_INTENT_FACT_TYPE, True),
    )

    result = FinancialEligibilityEngine().evaluate_product(
        step_30000_300d_product(), store, step_30000_context()
    )

    rate = _rate_result(result)
    assert rate.status == EvaluationStatus.ACHIEVABLE
    assert rate.progress is not None
    assert rate.progress.current == 0
    assert rate.progress.required == 300
    assert rate.progress.unit == "DAY"
