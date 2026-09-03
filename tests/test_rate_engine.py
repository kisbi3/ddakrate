from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

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


def test_conditional_upper_never_exceeds_published_maximum():
    product, results = _results([EvaluationStatus.UNKNOWN] * 4)
    published_ceiling_product = product.model_copy(
        update={"advertised_max_rate": Decimal("4.60")}, deep=True
    )

    summary = RateEngine.calculate(published_ceiling_product, results, [])

    assert summary.conditional_upper_rate == Decimal("4.60")


def test_unsatisfiable_global_guard_blocks_all_preferential_rewards():
    product, results = _results([EvaluationStatus.SATISFIED] * 4)
    guards = [_result("G", "guard", EvaluationStatus.UNSATISFIABLE)]

    summary = RateEngine.calculate(product, results, guards)

    assert summary.confirmed_rate == product.base_rate
    assert summary.realizable_rate == product.base_rate
    assert summary.conditional_upper_rate == product.base_rate


def test_unmodeled_random_reward_stays_in_possible_max_not_expected_rate():
    product = SimpleNamespace(
        preferential_rules=[],
        base_rate=Decimal("3"),
        preferential_rate_cap=Decimal("10"),
        advertised_max_rate=Decimal("13"),
        normalized=SimpleNamespace(
            return_policy={
                "return_kind": "POSTED_RATE",
                "preferential_policy": {
                    "rules": [
                        {
                            "rule_id": "RANDOM-DRAW-RATE",
                            "structuring_status": "NON_DETERMINISTIC",
                        }
                    ]
                },
            }
        ),
    )

    summary = RateEngine.calculate(product, [], [])

    assert summary.realizable_rate == Decimal("3")
    assert summary.user_specific_conditional_upper_rate == Decimal("13")
    assert summary.evidence_breakdown.unknown_conditional_reward_pp == Decimal("10")


def test_unsatisfiable_guard_also_blocks_unmodeled_published_reward():
    product = SimpleNamespace(
        preferential_rules=[],
        base_rate=Decimal("3"),
        preferential_rate_cap=Decimal("10"),
        advertised_max_rate=Decimal("13"),
        normalized=SimpleNamespace(
            return_policy={
                "return_kind": "POSTED_RATE",
                "preferential_policy": {"rules": [{"rule_id": "UNMODELED"}]},
            }
        ),
    )

    summary = RateEngine.calculate(
        product,
        [],
        [_result("G", "guard", EvaluationStatus.UNSATISFIABLE)],
    )

    assert summary.user_specific_conditional_upper_rate == Decimal("3")


def _relation_product(relation: dict):
    preferential_rules = [
        SimpleNamespace(
            rule=SimpleNamespace(rule_id="R1"),
            canonical_rule_id="R1",
            reward_kind="ADD_RATE",
            application={},
        ),
        SimpleNamespace(
            rule=SimpleNamespace(rule_id="R2"),
            canonical_rule_id="R2",
            reward_kind="ADD_RATE",
            application={},
        ),
    ]
    return SimpleNamespace(
        preferential_rules=preferential_rules,
        base_rate=None,
        normalized=SimpleNamespace(
            return_policy={"preferential_policy": {"relations": [relation]}}
        ),
    )


def test_source_first_operator_and_members_drive_exclusive_relation():
    product = _relation_product(
        {"operator": "EXCLUSIVE_ONE", "members": ["R1", "R2"]}
    )

    total = RateEngine._aggregate_canonical_rewards(
        product,
        {"R1", "R2"},
        {"R1": Decimal("0.25"), "R2": Decimal("0.35")},
        None,
    )

    assert total == Decimal("0.35")


def test_source_first_operator_and_member_rule_ids_drive_component_cap():
    product = _relation_product(
        {
            "operator": "SUM_WITH_CAP",
            "member_rule_ids": ["R1", "R2"],
            "cap": {"value": "0.35", "unit": "PERCENTAGE_POINT"},
        }
    )

    total = RateEngine._aggregate_canonical_rewards(
        product,
        {"R1", "R2"},
        {"R1": Decimal("0.25"), "R2": Decimal("0.35")},
        None,
    )

    assert total == Decimal("0.35")
