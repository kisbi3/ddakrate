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


def _same_source_tier_product():
    """KB아이사랑적금-shaped fixture: one sentence, rewards 1/2/3, empty relations."""

    shared = (
        "아이사랑 우대이율 (최고 연 4.0%p) 아래 가와 나 조건 충족 여부에 따라 "
        "최고 연 4.0%p적용 [1명] 연 1.0%p, [2명] 연 2.0%p, [3명] 연 3.0%p"
    )
    preferential_rules = [
        SimpleNamespace(
            rule=SimpleNamespace(rule_id=f"R{index}"),
            canonical_rule_id=f"R{index}",
            reward_kind="ADD_RATE",
            application={},
        )
        for index in (1, 2, 3)
    ]
    rules = [
        {
            "rule_id": f"R{index}",
            "source_clause_text": shared,
            "reward": {
                "kind": "ADD_RATE",
                "value": str(index),
                "unit": "PERCENTAGE_POINT",
            },
        }
        for index in (1, 2, 3)
    ]
    return SimpleNamespace(
        preferential_rules=preferential_rules,
        base_rate=None,
        normalized=SimpleNamespace(
            return_policy={"preferential_policy": {"rules": rules, "relations": []}}
        ),
    )


def test_same_source_child_count_tiers_take_max_not_sum():
    product = _same_source_tier_product()

    total = RateEngine._aggregate_canonical_rewards(
        product,
        {"R1", "R2", "R3"},
        {"R1": Decimal("1"), "R2": Decimal("2"), "R3": Decimal("3")},
        None,
    )

    assert total == Decimal("3")


def test_distinct_source_texts_remain_additive():
    product = _same_source_tier_product()
    rules = product.normalized.return_policy["preferential_policy"]["rules"]
    rules[1]["source_clause_text"] = "급여이체 우대 연 2.0%p"
    rules[2]["source_clause_text"] = "마케팅 동의 우대 연 3.0%p"

    total = RateEngine._aggregate_canonical_rewards(
        product,
        {"R1", "R2", "R3"},
        {"R1": Decimal("1"), "R2": Decimal("2"), "R3": Decimal("3")},
        None,
    )

    assert total == Decimal("6")


def test_same_source_independent_bonuses_remain_additive():
    shared = (
        "급여이체 시 연 0.3%p 및 당행 카드 이용실적 시 연 0.2%p를 제공한다"
    )
    product = _same_source_tier_product()
    product.normalized.return_policy["preferential_policy"]["rules"] = [
        {
            "rule_id": "R1",
            "title": "급여이체 우대",
            "source_clause_text": shared,
            "condition": {"predicate": {"fact_key": "SALARY_TRANSFER"}},
            "reward": {"kind": "ADD_RATE", "value": "0.3", "unit": "PERCENTAGE_POINT"},
        },
        {
            "rule_id": "R2",
            "title": "카드실적 우대",
            "source_clause_text": shared,
            "condition": {"predicate": {"fact_key": "CARD_PERFORMANCE"}},
            "reward": {"kind": "ADD_RATE", "value": "0.2", "unit": "PERCENTAGE_POINT"},
        },
    ]
    product.preferential_rules = [
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

    total = RateEngine._aggregate_canonical_rewards(
        product,
        {"R1", "R2"},
        {"R1": Decimal("0.3"), "R2": Decimal("0.2")},
        None,
    )

    assert total == Decimal("0.5")


def test_mixed_child_ladder_and_independent_salary_stay_additive():
    """Same source with 1명/2명 plus an extra salary bonus is 2.5, not max 2."""

    shared = "자녀 1명 연 1%p, 자녀 2명 연 2%p, 급여이체 연 0.5%p 추가 제공"
    product = _same_source_tier_product()
    product.normalized.return_policy["preferential_policy"]["rules"] = [
        {
            "rule_id": "R1",
            "title": "자녀 1명",
            "source_clause_text": shared,
            "reward": {"kind": "ADD_RATE", "value": "1", "unit": "PERCENTAGE_POINT"},
        },
        {
            "rule_id": "R2",
            "title": "자녀 2명",
            "source_clause_text": shared,
            "reward": {"kind": "ADD_RATE", "value": "2", "unit": "PERCENTAGE_POINT"},
        },
        {
            "rule_id": "R3",
            "title": "급여이체",
            "source_clause_text": shared,
            "reward": {"kind": "ADD_RATE", "value": "0.5", "unit": "PERCENTAGE_POINT"},
        },
    ]
    product.preferential_rules = [
        SimpleNamespace(
            rule=SimpleNamespace(rule_id=rule_id),
            canonical_rule_id=rule_id,
            reward_kind="ADD_RATE",
            application={},
        )
        for rule_id in ("R1", "R2", "R3")
    ]
    values = {"R1": Decimal("1"), "R2": Decimal("2"), "R3": Decimal("0.5")}

    total = RateEngine._aggregate_canonical_rewards(product, set(values), values, None)

    assert total == Decimal("2.5")
    groups = RateEngine._same_source_tier_groups(
        product.normalized.return_policy["preferential_policy"],
        values,
    )
    assert groups == [["R1", "R2"]]


def test_mixed_ladder_with_fact_keys_also_keeps_independent_bonus():
    shared = "자녀 1명 연 1%p, 자녀 2명 연 2%p, 급여이체 연 0.5%p 추가 제공"
    product = _same_source_tier_product()
    product.normalized.return_policy["preferential_policy"]["rules"] = [
        {
            "rule_id": "R1",
            "source_clause_text": shared,
            "condition": {"predicate": {"fact_key": "CHILD_COUNT", "expected": 1}},
            "reward": {"kind": "ADD_RATE", "value": "1", "unit": "PERCENTAGE_POINT"},
        },
        {
            "rule_id": "R2",
            "source_clause_text": shared,
            "condition": {"predicate": {"fact_key": "CHILD_COUNT", "expected": 2}},
            "reward": {"kind": "ADD_RATE", "value": "2", "unit": "PERCENTAGE_POINT"},
        },
        {
            "rule_id": "R3",
            "source_clause_text": shared,
            "condition": {"predicate": {"fact_key": "SALARY_TRANSFER"}},
            "reward": {"kind": "ADD_RATE", "value": "0.5", "unit": "PERCENTAGE_POINT"},
        },
    ]
    product.preferential_rules = [
        SimpleNamespace(
            rule=SimpleNamespace(rule_id=rule_id),
            canonical_rule_id=rule_id,
            reward_kind="ADD_RATE",
            application={},
        )
        for rule_id in ("R1", "R2", "R3")
    ]
    values = {"R1": Decimal("1"), "R2": Decimal("2"), "R3": Decimal("0.5")}

    total = RateEngine._aggregate_canonical_rewards(product, set(values), values, None)

    assert total == Decimal("2.5")


def test_unclear_same_source_copy_is_not_forced_to_max_or_new_exclusive():
    """Matching source text without ladder evidence must not invent a max group."""

    shared = "아래 조건을 충족하는 경우 우대금리를 제공한다"
    product = _same_source_tier_product()
    product.normalized.return_policy["preferential_policy"]["rules"] = [
        {
            "rule_id": f"R{index}",
            "title": f"우대 {index}",
            "source_clause_text": shared,
            "reward": {
                "kind": "ADD_RATE",
                "value": str(index),
                "unit": "PERCENTAGE_POINT",
            },
        }
        for index in (1, 2)
    ]
    product.preferential_rules = [
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

    total = RateEngine._aggregate_canonical_rewards(
        product,
        {"R1", "R2"},
        {"R1": Decimal("1"), "R2": Decimal("2")},
        None,
    )

    assert total == Decimal("3")
    assert RateEngine._same_source_tier_groups(
        product.normalized.return_policy["preferential_policy"],
        {"R1": Decimal("1"), "R2": Decimal("2")},
    ) == []
