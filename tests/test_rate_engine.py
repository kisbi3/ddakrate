from __future__ import annotations

from decimal import Decimal

from eligibility.engine.rate_engine import RateEngine
from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
from eligibility.schema.enums import EvaluationStatus, VerificationLevel
from eligibility.schema.evaluation import RuleEvaluation


def _result(rule_id: str, name: str, status: EvaluationStatus) -> RuleEvaluation:
    if status == EvaluationStatus.SATISFIED:
        verification = VerificationLevel.INSTITUTION_VERIFIED
        levels = [verification]
    elif status == EvaluationStatus.ACHIEVABLE:
        verification = VerificationLevel.USER_INTENT
        levels = [verification]
    else:
        verification = VerificationLevel.UNKNOWN
        levels = []
    return RuleEvaluation(
        rule_id=rule_id,
        rule_name=name,
        status=status,
        reason_code="TEST",
        verification_level=verification,
        evidence_levels=levels,
    )


def _results(statuses: list[EvaluationStatus]):
    product = shinhan_youth_first_product()
    return product, [
        _result(pref.rule.rule_id, pref.rule.name, status)
        for pref, status in zip(product.preferential_rules, statuses, strict=True)
    ]


def test_reward_sum_separates_confirmed_realizable_and_upper():
    product, results = _results(
        [
            EvaluationStatus.SATISFIED,
            EvaluationStatus.ACHIEVABLE,
            EvaluationStatus.UNKNOWN,
            EvaluationStatus.UNSATISFIABLE,
        ]
    )
    guards = [_result("G", "guard", EvaluationStatus.ACHIEVABLE)]

    summary = RateEngine.calculate(product, results, guards)

    assert summary.confirmed_rate == Decimal("4.05")
    assert summary.realizable_rate == Decimal("4.55")
    assert summary.conditional_upper_rate == Decimal("5.05")


def test_preferential_reward_sum_is_capped():
    product, results = _results([EvaluationStatus.SATISFIED] * 4)
    capped_product = product.model_copy(
        update={
            "preferential_rate_cap": Decimal("1.0"),
            "advertised_max_rate": Decimal("4.05"),
        },
        deep=True,
    )

    summary = RateEngine.calculate(capped_product, results, [])

    assert summary.confirmed_rate == Decimal("4.05")
    assert summary.realizable_rate == Decimal("4.05")
    assert summary.conditional_upper_rate == Decimal("4.05")


def test_unsatisfiable_global_guard_blocks_all_preferential_rewards():
    product, results = _results([EvaluationStatus.SATISFIED] * 4)
    guards = [_result("G", "guard", EvaluationStatus.UNSATISFIABLE)]

    summary = RateEngine.calculate(product, results, guards)

    assert summary.confirmed_rate == product.base_rate
    assert summary.realizable_rate == product.base_rate
    assert summary.conditional_upper_rate == product.base_rate
