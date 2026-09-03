from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from eligibility.application_service import ApplicationService
from eligibility.fixtures.kakao_26_week import kakao_26_week_product
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.enums import (
    ContributionFrequency,
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    RankingComparability,
    RankingObjective,
    VerificationLevel,
)
from eligibility.schema.product import PreferentialRateRule, Reward
from eligibility.schema.search import ContributionPlan
from eligibility.schema.user_fact import UserFact
from eligibility.schema.user_fact import UserFactStore
from eligibility.search.contribution import ContributionPlanner
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.ranking import RankingService
from eligibility.search.recommendation import (
    GroundedResultExplainer,
    RecommendationService,
)

from tests.v04_helpers import (
    AS_OF,
    SUBSCRIPTION_DATE,
    USER_ID,
    base_store,
    make_intent,
    make_product,
    verified_fact,
)


def _evaluations(products, *, store=None, intent=None):
    intent = intent or make_intent()
    return MultiProductEvaluator().evaluate(
        products,
        store or base_store(),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )


def _rank(products, evaluations, objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST, top_k=5):
    return RankingService().rank(
        evaluations,
        {product.product_id: product for product in products},
        objective=objective,
        top_k=top_k,
    )


def _candidate_with(candidate, **updates):
    return candidate.model_copy(update=updates, deep=True)


def test_missing_amount_uses_published_possible_max_rate_for_candidate_order():
    # Deliberately make the lower-rate product alphabetically first so this
    # test proves the published upper bound, rather than a name tie-break,
    # determines provisional order.
    lower = make_product("AAA-POSSIBLE-LOW", base_rate="1.0")
    higher = make_product("ZZZ-POSSIBLE-HIGH", base_rate="1.0")
    higher = higher.model_copy(
        update={
            "advertised_max_rate": Decimal("7.7"),
            "metadata": higher.metadata.model_copy(
                update={"advertised_max_rate": Decimal("7.7")}, deep=True
            ),
        },
        deep=True,
    )
    evaluations = _evaluations(
        [lower, higher],
        intent=make_intent(
            desired_amount=None,
            maximum_amount=None,
            selected_term_value=None,
            selected_term_unit=None,
            frequency=None,
            objective=RankingObjective.MAX_REALIZABLE_RATE,
        ),
    )
    provisional = {
        product_id: candidate.model_copy(
            update={
                "ranking_comparability": RankingComparability.MISSING_CONTRIBUTION_INPUT,
                "user_specific_conditional_upper_rate": None,
                "rate_evaluation": None,
            },
            deep=True,
        )
        for product_id, candidate in evaluations.items()
    }

    ranking, _ = _rank(
        [lower, higher], provisional, objective=RankingObjective.MAX_REALIZABLE_RATE
    )

    assert ranking.ordered_product_ids[:2] == [
        "ZZZ-POSSIBLE-HIGH",
        "AAA-POSSIBLE-LOW",
    ]


def test_rank_intervals_match_pairwise_definition_with_linear_metric_reads(monkeypatch):
    products = [make_product(f"INTERVAL-{index}") for index in range(8)]
    base = _evaluations(
        products,
        intent=make_intent(
            objective=RankingObjective.MAX_REALIZABLE_RATE,
            top_k=len(products),
        ),
    )
    realizable_values = ["5.0", "4.0", "4.0", "3.0", "2.0", "1.0", "0.5", "0.1"]
    upper_values = ["5.0", "6.0", "4.5", "8.0", "2.0", "7.0", "1.0", "0.05"]
    ordered = [
        _candidate_with(
            base[product.product_id],
            realizable_rate=Decimal(realizable),
            user_specific_conditional_upper_rate=Decimal(upper),
        )
        for product, realizable, upper in zip(
            products, realizable_values, upper_values, strict=True
        )
    ]
    expected = {}
    for candidate in ordered:
        own_realizable = RankingService.realizable_metric(
            candidate, RankingObjective.MAX_REALIZABLE_RATE
        )
        own_upper = RankingService.optimistic_metric(
            candidate, RankingObjective.MAX_REALIZABLE_RATE
        )
        expected[candidate.product_id] = (
            1
            + sum(
                RankingService.realizable_metric(
                    other, RankingObjective.MAX_REALIZABLE_RATE
                )
                > own_upper
                for other in ordered
                if other.product_id != candidate.product_id
            ),
            1
            + sum(
                RankingService.optimistic_metric(
                    other, RankingObjective.MAX_REALIZABLE_RATE
                )
                >= own_realizable
                for other in ordered
                if other.product_id != candidate.product_id
            ),
        )

    service = RankingService()
    metric_reads = {"realizable": 0, "optimistic": 0}
    original_realizable = service.realizable_metric
    original_optimistic = service.optimistic_metric

    def tracked_realizable(candidate, objective):
        metric_reads["realizable"] += 1
        return original_realizable(candidate, objective)

    def tracked_optimistic(candidate, objective):
        metric_reads["optimistic"] += 1
        return original_optimistic(candidate, objective)

    monkeypatch.setattr(service, "realizable_metric", tracked_realizable)
    monkeypatch.setattr(service, "optimistic_metric", tracked_optimistic)
    ranked = service._with_rank_intervals(
        ordered, RankingObjective.MAX_REALIZABLE_RATE
    )

    assert metric_reads == {
        "realizable": len(ordered),
        "optimistic": len(ordered),
    }
    for product_id, (best, worst) in expected.items():
        interval = ranked[product_id].rank_interval
        assert interval is not None
        assert interval.best_possible_rank == best
        assert interval.worst_possible_rank == max(best, worst)


def test_display_ranking_uses_possible_max_while_expected_rate_remains_separate():
    confirmed = make_product("REALIZABLE-WINS", base_rate="5.0")
    optimistic_only = make_product(
        "UNKNOWN-UPPER-LOSER",
        base_rate="2.0",
        reward_pp="10.0",
        bonus_fact_type="UNKNOWN_BIG_REWARD",
    )
    products = [confirmed, optimistic_only]
    evaluations = _evaluations(products, intent=make_intent(top_k=2))

    ranking, _ = _rank(
        products,
        evaluations,
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=2,
    )

    assert evaluations["UNKNOWN-UPPER-LOSER"].user_specific_conditional_upper_rate == Decimal("12.0")
    assert ranking.ordered_product_ids[0] == "UNKNOWN-UPPER-LOSER"
    assert ranking.items[0].realizable_rate == Decimal("2.0")
    challenger_item = next(
        item for item in ranking.items if item.product_id == "UNKNOWN-UPPER-LOSER"
    )
    assert challenger_item.realizable_rate == Decimal("2.0")
    assert challenger_item.user_specific_conditional_upper_rate == Decimal("12.0")


def test_possible_max_display_order_is_product_family_neutral():
    for family in (
        "TIME_DEPOSIT",
        "INSTALLMENT_SAVINGS",
        "PARKING_ACCOUNT",
        "CMA",
    ):
        confirmed = make_product(
            f"{family}-CONFIRMED",
            product_type=family,
            base_rate="5.0",
        )
        challenger = make_product(
            f"{family}-CHALLENGER",
            product_type=family,
            base_rate="2.0",
            reward_pp="4.0",
            bonus_fact_type=f"{family}_UNKNOWN_REWARD",
        )
        products = [confirmed, challenger]
        intent = make_intent(
            objective=RankingObjective.MAX_REALIZABLE_RATE,
            top_k=2,
        )
        evaluations = _evaluations(products, intent=intent)

        ranking, _ = _rank(
            products,
            evaluations,
            objective=RankingObjective.MAX_REALIZABLE_RATE,
            top_k=2,
        )

        assert ranking.ordered_product_ids[0] == challenger.product_id


def test_equal_possible_max_rates_share_rank_even_when_expected_rates_differ():
    first = make_product(
        "POSSIBLE-TIE-LOWER-EXPECTED",
        base_rate="2.0",
        reward_pp="5.0",
        bonus_fact_type="POSSIBLE_TIE_BONUS_A",
    )
    second = make_product(
        "POSSIBLE-TIE-HIGHER-EXPECTED",
        base_rate="5.0",
        reward_pp="2.0",
        bonus_fact_type="POSSIBLE_TIE_BONUS_B",
    )
    products = [first, second]
    evaluations = _evaluations(
        products,
        intent=make_intent(
            objective=RankingObjective.MAX_REALIZABLE_RATE,
            top_k=2,
        ),
    )

    ranking, _ = _rank(
        products,
        evaluations,
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=2,
    )

    assert [item.rank for item in ranking.items] == [1, 1]
    assert {
        item.realizable_rate for item in ranking.items
    } == {Decimal("2.0"), Decimal("5.0")}
    assert {
        item.user_specific_conditional_upper_rate for item in ranking.items
    } == {Decimal("7.0")}


def test_after_tax_interest_primary_ranking():
    products = [make_product("INTEREST-A"), make_product("INTEREST-B")]
    base = _evaluations(products)
    evaluations = {
        "INTEREST-A": _candidate_with(
            base["INTEREST-A"],
            realizable_rate=Decimal("5.0"),
            realizable_after_tax_interest=Decimal("100000"),
            conditional_upper_after_tax_interest=Decimal("100000"),
        ),
        "INTEREST-B": _candidate_with(
            base["INTEREST-B"],
            realizable_rate=Decimal("4.0"),
            realizable_after_tax_interest=Decimal("120000"),
            conditional_upper_after_tax_interest=Decimal("120000"),
        ),
    }

    ranking, _ = _rank(products, evaluations, top_k=2)

    assert ranking.ordered_product_ids[:2] == ["INTEREST-B", "INTEREST-A"]


def test_realizable_rate_tiebreaker():
    products = [make_product("RATE-A"), make_product("RATE-B")]
    base = _evaluations(products)
    evaluations = {
        "RATE-A": _candidate_with(
            base["RATE-A"],
            realizable_rate=Decimal("4.0"),
            realizable_after_tax_interest=Decimal("100000"),
            conditional_upper_after_tax_interest=Decimal("100000"),
        ),
        "RATE-B": _candidate_with(
            base["RATE-B"],
            realizable_rate=Decimal("4.5"),
            realizable_after_tax_interest=Decimal("100000"),
            conditional_upper_after_tax_interest=Decimal("100000"),
        ),
    }

    ranking, _ = _rank(products, evaluations, top_k=2)

    assert ranking.ordered_product_ids[0] == "RATE-B"


def test_equal_pre_tax_rate_and_interest_share_rank_and_sort_by_korean_names():
    def named_product(product_id: str, institution_name: str, product_name: str):
        product = make_product(
            product_id,
            institution_id=product_id,
            name=product_name,
            base_rate="4.0",
        )
        return product.model_copy(
            update={
                "metadata": product.metadata.model_copy(
                    update={"institution_name": institution_name},
                    deep=True,
                )
            },
            deep=True,
        )

    products = [
        named_product("TIE-Z", "나은행", "가상품"),
        named_product("TIE-B", "가은행", "나상품"),
        named_product("TIE-A", "(주)가은행", "가상품"),
        make_product("LOWER", institution_id="다은행", name="낮은 상품", base_rate="3.9"),
    ]
    evaluations = _evaluations(
        products,
        intent=make_intent(
            objective=RankingObjective.MAX_REALIZABLE_RATE,
            top_k=4,
        ),
    )

    ranking, _ = _rank(
        products,
        evaluations,
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=4,
    )

    assert [item.product_id for item in ranking.items] == [
        "TIE-A",
        "TIE-B",
        "TIE-Z",
        "LOWER",
    ]
    assert [item.rank for item in ranking.items] == [1, 1, 1, 4]


def test_ranked_display_list_is_capped_at_100_and_excludes_unsatisfiable():
    products = [
        make_product(f"DISPLAY-{index:03d}", base_rate=str(2 + index / 1000))
        for index in range(106)
    ]
    evaluations = _evaluations(
        products,
        intent=make_intent(
            objective=RankingObjective.MAX_REALIZABLE_RATE,
            top_k=5,
        ),
    )
    evaluations["DISPLAY-105"] = _candidate_with(
        evaluations["DISPLAY-105"],
        product_evaluation=evaluations["DISPLAY-105"].product_evaluation.model_copy(
            update={"eligibility_status": EvaluationStatus.UNSATISFIABLE},
            deep=True,
        ),
    )

    ranking, _ = _rank(
        products,
        evaluations,
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=5,
    )
    result = RecommendationService().build_result("SEARCH-DISPLAY", ranking)

    assert len(result.top_products) == 5
    assert len(result.ranked_products) == 100
    assert "DISPLAY-105" not in {item.product_id for item in result.ranked_products}

def test_preference_tiebreaker():
    products = [
        make_product("PREF-A", name="동일 상품"),
        make_product("PREF-B", name="동일 상품"),
    ]
    base = _evaluations(products)
    common = {
        "realizable_rate": Decimal("4.0"),
        "realizable_after_tax_interest": Decimal("100000"),
        "conditional_upper_after_tax_interest": Decimal("100000"),
    }
    evaluations = {
        "PREF-A": _candidate_with(base["PREF-A"], preference_score=0, **common),
        "PREF-B": _candidate_with(base["PREF-B"], preference_score=3, **common),
    }

    ranking, _ = _rank(products, evaluations, top_k=2)

    assert ranking.ordered_product_ids[0] == "PREF-B"


def test_action_burden_tiebreaker():
    products = [
        make_product("BURDEN-A", name="동일 상품"),
        make_product("BURDEN-B", name="동일 상품"),
    ]
    base = _evaluations(products)
    common = {
        "realizable_rate": Decimal("4.0"),
        "realizable_after_tax_interest": Decimal("100000"),
        "conditional_upper_after_tax_interest": Decimal("100000"),
        "preference_score": 0,
    }
    evaluations = {
        "BURDEN-A": _candidate_with(base["BURDEN-A"], action_burden_score=4, **common),
        "BURDEN-B": _candidate_with(base["BURDEN-B"], action_burden_score=1, **common),
    }

    ranking, _ = _rank(products, evaluations, top_k=2)

    assert ranking.ordered_product_ids[0] == "BURDEN-B"


def test_explicit_max_rate_objective_override():
    products = [make_product("MAX-RATE-A"), make_product("MAX-RATE-B")]
    base = _evaluations(products)
    evaluations = {
        "MAX-RATE-A": _candidate_with(
            base["MAX-RATE-A"],
            realizable_rate=Decimal("5.0"),
            realizable_after_tax_interest=Decimal("80000"),
            conditional_upper_after_tax_interest=Decimal("80000"),
        ),
        "MAX-RATE-B": _candidate_with(
            base["MAX-RATE-B"],
            realizable_rate=Decimal("4.0"),
            realizable_after_tax_interest=Decimal("120000"),
            conditional_upper_after_tax_interest=Decimal("120000"),
        ),
    }

    ranking, _ = _rank(
        products,
        evaluations,
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=2,
    )

    assert ranking.ordered_product_ids[0] == "MAX-RATE-A"


def test_top5_stability_against_remaining_upper_bounds():
    products = [make_product(f"STABLE-{index}") for index in range(1, 7)]
    base = _evaluations(products, intent=make_intent(top_k=5))
    rates = [Decimal("10"), Decimal("9"), Decimal("8"), Decimal("7"), Decimal("6"), Decimal("4")]
    evaluations = {}
    for product, rate in zip(products, rates, strict=True):
        upper = rate if product.product_id != "STABLE-6" else Decimal("5")
        evaluations[product.product_id] = _candidate_with(
            base[product.product_id],
            realizable_rate=rate,
            user_specific_conditional_upper_rate=upper,
            realizable_after_tax_interest=rate * Decimal("1000"),
            conditional_upper_after_tax_interest=upper * Decimal("1000"),
        )

    ranking, _ = _rank(products, evaluations, top_k=5)
    assert ranking.stability.stable_membership is True
    assert ranking.stability.current_kth_realizable_score == Decimal("6000")
    assert ranking.stability.maximum_nonselected_optimistic_score == Decimal("5000")

    unstable = dict(evaluations)
    unstable["STABLE-6"] = _candidate_with(
        unstable["STABLE-6"],
        conditional_upper_after_tax_interest=Decimal("7000"),
        user_specific_conditional_upper_rate=Decimal("7"),
    )
    unstable_ranking, _ = _rank(products, unstable, top_k=5)
    assert unstable_ranking.stability.stable_membership is False


def test_top5_response_contains_five_or_available_candidates():
    products = [
        make_product(f"TOP-{index}", base_rate=str(2 + index / 10))
        for index in range(1, 7)
    ]
    intent = make_intent(top_k=5)
    service = ApplicationService(products, user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )

    result = service.get_top_recommendations(session.search_session_id)

    assert len(result.top_products) == 5
    assert len(result.ranked_products) == 6
    assert [item.rank for item in result.top_products] == [1, 2, 3, 4, 5]
    sixth = result.ranked_products[5]
    assert service.get_product_recommendation_detail(
        session.search_session_id,
        sixth.product_id,
        include_explanation=False,
    ).product_id == sixth.product_id

    small_service = ApplicationService(products[:3], user_fact_stores={USER_ID: base_store()})
    small_session = small_service.create_search_session(
        user_id=USER_ID,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    assert len(small_service.get_top_recommendations(small_session.search_session_id).top_products) == 3


def test_list_contains_rate_term_contribution_and_interest():
    product = make_product(
        "LIST-CONTRACT",
        name="목록 검증 적금",
        institution_id="SHINHAN_BANK",
        base_rate="4.1",
        periodic_max="500000",
    )
    intent = make_intent(desired_amount="300000", maximum_amount="500000", top_k=1)
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )

    item = service.get_top_recommendations(session.search_session_id).top_products[0]

    assert item.realizable_rate == Decimal("4.1")
    assert item.advertised_max_rate == Decimal("4.1")
    assert item.base_rate == Decimal("4.1")
    assert item.term_summary == "12개월"
    assert "월" in item.contribution_summary
    assert item.maximum_deposit_summary == "월 최대 500,000원"
    assert item.planned_contribution_summary == "월 300,000원 납입 계획"
    assert item.estimated_total_principal == Decimal("3600000")
    assert item.estimated_after_tax_interest is not None


def test_incremental_product_has_readable_contribution_summary():
    product = kakao_26_week_product()
    plan = ContributionPlan(
        desired_periodic_amount=Decimal("10000"),
        maximum_affordable_periodic_amount=Decimal("3510000"),
        frequency=ContributionFrequency.WEEKLY,
        selected_term_value=26,
        selected_term_unit=product.contract_term.unit,
        preferred_start_amount=Decimal("10000"),
    )

    planned = ContributionPlanner().build(product, plan).projection

    assert planned.term_summary == "26주"
    assert planned.contribution_summary == "주간 점증 납입"
    assert planned.planned_contribution_summary == "주 10,000원 시작 · 매주 10,000원 증액"
    assert planned.estimated_total_principal == Decimal("3510000")


def test_detail_contains_rule_reward_status_and_evidence():
    product = make_product(
        "DETAIL-RULE",
        institution_id="SHINHAN_BANK",
        base_rate="3.0",
        reward_pp="0.5",
        bonus_fact_type="DETAIL_RULE_FACT",
        bonus_semantic_type=FactSemanticType.OBSERVED_FACT,
        on_true_status=EvaluationStatus.SATISFIED,
        rule_name="카드 우대",
    )
    store = base_store(verified_fact("DETAIL-TRUE", "DETAIL_RULE_FACT", True))
    intent = make_intent(top_k=1)
    service = ApplicationService([product], user_fact_stores={USER_ID: store})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    result = service.get_top_recommendations(session.search_session_id)

    detail = service.get_product_recommendation_detail(
        session.search_session_id,
        result.top_products[0].product_id,
        include_explanation=False,
    )

    assert detail.confirmed_rate == Decimal("3.5")
    assert detail.realizable_rate == Decimal("3.5")
    assert len(detail.rate_breakdown) == 1
    rule = detail.rate_breakdown[0]
    assert rule.rule_label == "카드 우대"
    assert rule.nominal_reward_pp == Decimal("0.5")
    assert rule.status == EvaluationStatus.SATISFIED
    assert rule.verification_level == VerificationLevel.INSTITUTION_VERIFIED
    assert rule.evidence_basis == "금융데이터로 확인"
    assert rule.source_reference is not None


def test_detail_re_evaluates_from_current_session_facts():
    known = make_product("KNOWN-LOWER", base_rate="2.7")
    unknown = make_product("UNKNOWN-HIGHER", base_rate="3.0")
    intent = make_intent(
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=1,
    )
    service = ApplicationService(
        [known, unknown],
        user_fact_stores={USER_ID: base_store()},
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    runtime = service._runtime(session.search_session_id)
    runtime.evaluations[unknown.product_id] = MultiProductEvaluator().evaluate(
        [unknown],
        UserFactStore(user_id=USER_ID, facts=[]),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )[unknown.product_id]
    runtime.ranking, runtime.evaluations = RankingService().rank(
        runtime.evaluations,
        runtime.candidate_products,
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=1,
    )
    runtime.recommendation = RecommendationService().build_result(
        session.search_session_id,
        runtime.ranking,
    )

    detail = service.get_product_recommendation_detail(
        session.search_session_id,
        unknown.product_id,
        include_explanation=False,
    )

    assert detail.rank == 1
    assert detail.realizable_rate == Decimal("3.0")
    # Detail rendering must not trust a stale/injected list evaluation. It
    # rebuilds the product state from the same current session facts used by
    # the normal recommendation pipeline.
    assert detail.eligibility_status == EvaluationStatus.SATISFIED
    assert detail.confirmed_or_achievable_products_ahead == 0
    assert detail.recommendation_reason.unknowns == []


def _capped_product_and_store():
    product = make_product(
        "CAP-DETAIL",
        base_rate="2.0",
        reward_pp="0.75",
        preferential_cap="1.0",
        bonus_fact_type="CAP_A",
        rule_name="우대 A",
    )
    first = product.preferential_rules[0]
    second_rule = first.rule.model_copy(
        update={
            "rule_id": "RATE-CAP-DETAIL-B",
            "name": "우대 B",
            "fact_type": "CAP_B",
            "source": first.rule.source.model_copy(update={"section": "우대 B"}),
        },
        deep=True,
    )
    product = product.model_copy(
        update={
            "preferential_rules": [
                first,
                PreferentialRateRule(rule=second_rule, reward=Reward(value=Decimal("0.75"))),
            ],
            "advertised_max_rate": Decimal("3.0"),
            "preferential_rate_cap": Decimal("1.0"),
            "metadata": product.metadata.model_copy(
                update={
                    "advertised_max_rate": Decimal("3.0"),
                    "preferential_rate_cap": Decimal("1.0"),
                },
                deep=True,
            ),
        },
        deep=True,
    )
    facts = []
    for suffix in ("A", "B"):
        facts.append(
            UserFact(
                fact_id=f"CAP-{suffix}-TRUE",
                user_id=USER_ID,
                fact_type=f"CAP_{suffix}",
                value=True,
                valid_from=AS_OF,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.FUTURE_INTENT,
                collected_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
            )
        )
    return product, base_store(*facts)


def test_rate_cap_presented_as_aggregate_adjustment():
    product, store = _capped_product_and_store()
    service = ApplicationService([product], user_fact_stores={USER_ID: store})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    detail = service.get_product_recommendation_detail(
        session.search_session_id,
        product.product_id,
        include_explanation=False,
    )

    assert detail.realizable_rate == Decimal("3.0")
    assert detail.rate_cap_adjustment.pre_cap_total_pp == Decimal("1.50")
    assert detail.rate_cap_adjustment.cap_pp == Decimal("1.0")
    assert detail.rate_cap_adjustment.cap_reduction_pp == Decimal("0.50")
    assert detail.rate_cap_adjustment.post_cap_total_pp == Decimal("1.0")
    assert all(not hasattr(item, "credited_reward_pp") for item in detail.rate_contributions)


def test_llm_explainer_cannot_change_numeric_claims():
    product, store = _capped_product_and_store()
    evaluations = _evaluations([product], store=store, intent=make_intent(top_k=1))
    ranking, _ = _rank([product], evaluations, top_k=1)
    service = RecommendationService()
    result = service.build_result("SEARCH-EXPLAIN", ranking)
    detail = service.build_detail(
        search_session_id="SEARCH-EXPLAIN",
        recommendation_id=result.recommendation_id,
        product=product,
        candidate=evaluations[product.product_id],
        rank=1,
        include_explanation=False,
    )
    gateway = LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.RESULT_EXPLANATION: {
                    "explanation": "이 상품은 6.0%를 확정적으로 보장하며 세후 999,999원을 받습니다.",
                    "claim_bindings": [],
                }
            }
        )
    )
    explainer = GroundedResultExplainer(gateway)

    explanation = explainer.explain(detail)

    assert explanation == explainer.deterministic_fallback(detail)
    assert "6.0%" not in explanation
    assert "999,999원" not in explanation


def test_llm_explainer_accepts_exact_bound_claims():
    from eligibility.llm.grounding import ClaimBinding

    product, store = _capped_product_and_store()
    evaluations = _evaluations([product], store=store, intent=make_intent(top_k=1))
    ranking, _ = _rank([product], evaluations, top_k=1)
    recommendation = RecommendationService()
    result = recommendation.build_result("SEARCH-BOUND-EXPLAIN", ranking)
    detail = recommendation.build_detail(
        search_session_id="SEARCH-BOUND-EXPLAIN",
        recommendation_id=result.recommendation_id,
        product=product,
        candidate=evaluations[product.product_id],
        rank=1,
        include_explanation=False,
    )
    fallback = GroundedResultExplainer.deterministic_fallback(detail)
    payload = GroundedResultExplainer._payload(detail, fallback)
    bindings = [
        ClaimBinding(
            claim_id=claim.claim_id,
            claim_type=claim.claim_type,
            value=claim.value,
            unit=claim.unit,
        )
        for claim in payload.claims
    ]
    gateway = LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.RESULT_EXPLANATION: {
                    "explanation": fallback,
                    "claim_bindings": [item.model_dump(mode="json") for item in bindings],
                }
            }
        )
    )

    assert GroundedResultExplainer(gateway).explain(detail) == fallback
