from __future__ import annotations

from decimal import Decimal

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.kakao_26_week import (
    kakao_26_week_context,
    kakao_26_week_pre_subscription_context,
    kakao_26_week_product,
    kakao_pre_subscription_user,
    kakao_user_all_success,
    kakao_user_from_statuses,
    kakao_user_with_failure,
)
from eligibility.schema.enums import (
    EvaluationStatus,
    ScheduledOccurrenceStatus,
    TermUnit,
)


def _evaluate(store, *, context=None):
    return FinancialEligibilityEngine().evaluate_product(
        kakao_26_week_product(),
        store,
        context or kakao_26_week_context(),
    )


def test_kakao_26_week_all_success_reaches_advertised_rate():
    product = kakao_26_week_product()
    result = _evaluate(kakao_user_all_success())

    assert product.contract_term is not None
    assert product.contract_term.value == 26
    assert product.contract_term.unit == TermUnit.WEEK
    assert [item.status for item in result.preferential_rule_results] == [
        EvaluationStatus.SATISFIED,
        EvaluationStatus.SATISFIED,
    ]
    assert result.rates.confirmed_rate == Decimal("5.0")
    assert result.rates.realizable_rate == Decimal("5.0")
    assert result.rates.user_specific_conditional_upper_rate == Decimal("5.0")


def test_kakao_failure_then_later_long_run_keeps_7_week_reward_only():
    result = _evaluate(kakao_user_with_failure())
    seven_week, twenty_six_week = result.preferential_rule_results

    assert seven_week.status == EvaluationStatus.SATISFIED
    assert seven_week.progress is not None
    assert seven_week.progress.current == 22
    assert seven_week.evidence["streak_start_sequence"] == 5
    assert seven_week.evidence["streak_end_sequence"] == 26
    assert twenty_six_week.status == EvaluationStatus.UNSATISFIABLE
    assert twenty_six_week.progress is not None
    assert twenty_six_week.progress.current == 22
    assert result.rates.confirmed_rate == Decimal("3.0")
    assert result.rates.user_specific_conditional_upper_rate == Decimal("3.0")


def test_kakao_manual_fill_does_not_restore_auto_26_week_streak():
    result = _evaluate(kakao_user_with_failure(manual_recovery=True))
    seven_week, twenty_six_week = result.preferential_rule_results

    assert seven_week.status == EvaluationStatus.SATISFIED
    assert twenty_six_week.status == EvaluationStatus.UNSATISFIABLE
    assert twenty_six_week.progress is not None
    assert twenty_six_week.progress.current == 22
    assert "K26-O-04-MANUAL-FILL" not in twenty_six_week.evidence[
        "matched_sequence_nos"
    ]
    assert result.rates.realizable_rate == Decimal("3.0")


def test_kakao_sequences_3_to_9_satisfy_7_week_reward():
    statuses = [
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.FAILED,
        *([ScheduledOccurrenceStatus.SUCCESS] * 7),
        *([ScheduledOccurrenceStatus.FAILED] * 17),
    ]

    result = _evaluate(kakao_user_from_statuses(statuses))
    seven_week, twenty_six_week = result.preferential_rule_results

    assert seven_week.status == EvaluationStatus.SATISFIED
    assert seven_week.evidence["matched_sequence_nos"] == list(range(3, 10))
    assert twenty_six_week.status == EvaluationStatus.UNSATISFIABLE


def test_kakao_first_6_success_then_failure_does_not_satisfy_7_week_reward():
    statuses = [
        *([ScheduledOccurrenceStatus.SUCCESS] * 6),
        ScheduledOccurrenceStatus.FAILED,
        *([ScheduledOccurrenceStatus.FAILED] * 19),
    ]

    result = _evaluate(kakao_user_from_statuses(statuses))
    seven_week = result.preferential_rule_results[0]

    assert seven_week.status == EvaluationStatus.UNSATISFIABLE
    assert seven_week.progress is not None
    assert seven_week.progress.current == 6


def test_kakao_pre_subscription_asks_future_intent_instead_of_using_zero_progress():
    result = _evaluate(
        kakao_pre_subscription_user(),
        context=kakao_26_week_pre_subscription_context(),
    )

    assert [item.status for item in result.preferential_rule_results] == [
        EvaluationStatus.UNKNOWN,
        EvaluationStatus.UNKNOWN,
    ]
    assert all(
        item.reason_code == "FUTURE_INTENT_REQUIRED"
        for item in result.preferential_rule_results
    )
    assert len(result.missing_facts) == 2


def test_kakao_pre_subscription_user_yes_returns_achievable():
    result = _evaluate(
        kakao_pre_subscription_user(intent=True),
        context=kakao_26_week_pre_subscription_context(),
    )

    assert [item.status for item in result.preferential_rule_results] == [
        EvaluationStatus.ACHIEVABLE,
        EvaluationStatus.ACHIEVABLE,
    ]
    assert result.rates.realizable_rate == Decimal("5.0")
