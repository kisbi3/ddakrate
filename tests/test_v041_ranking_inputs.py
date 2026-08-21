from __future__ import annotations

from decimal import Decimal

from eligibility.application_service import ApplicationService
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_pre_subscription_user,
)
from eligibility.schema.enums import (
    ContributionFrequency,
    RankingComparability,
    RankingObjective,
    TermUnit,
)
from eligibility.schema.search import ContributionPlan, MissingRankingInput, ProductSearchIntent
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.ranking import RankingService

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_intent, make_product


def test_interest_ranking_never_substitutes_percentage_for_missing_krw_interest():
    products = [make_product(f"KRW-{index}", base_rate="0.01") for index in range(1, 7)]
    intent = make_intent(top_k=6)
    base = MultiProductEvaluator().evaluate(
        products,
        base_store(),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    evaluations = {}
    for product in products[:5]:
        evaluations[product.product_id] = base[product.product_id].model_copy(
            update={
                "realizable_rate": Decimal("0.01"),
                "realizable_after_tax_interest": Decimal("165"),
                "conditional_upper_after_tax_interest": Decimal("165"),
                "ranking_comparability": RankingComparability.COMPARABLE,
            },
            deep=True,
        )
    missing = MissingRankingInput(
        input_id="MRI-KRW-6",
        product_id="KRW-6",
        required_field="preferred_start_amount",
        allowed_options=["1000", "2000"],
        reason="cashflow unresolved",
        question="시작금액을 선택해 주세요.",
    )
    evaluations["KRW-6"] = base["KRW-6"].model_copy(
        update={
            "realizable_rate": Decimal("5.0"),
            "realizable_after_tax_interest": None,
            "conditional_upper_after_tax_interest": None,
            "ranking_comparability": RankingComparability.MISSING_CONTRIBUTION_INPUT,
            "missing_ranking_inputs": [missing],
        },
        deep=True,
    )

    ranking, ranked = RankingService().rank(
        evaluations,
        {item.product_id: item for item in products},
        objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        top_k=6,
    )

    assert RankingService.realizable_metric(
        ranked["KRW-6"], RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST
    ) == Decimal("0")
    assert RankingService.optimistic_metric(
        ranked["KRW-6"], RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST
    ) == Decimal("0")
    assert ranking.items[-1].product_id == "KRW-6"
    assert ranking.items[-1].ranking_comparability == RankingComparability.MISSING_CONTRIBUTION_INPUT
    assert ranking.items[-1].estimated_after_tax_interest is None
    assert ranking.stability.stable is False


def _kakao_intent() -> ProductSearchIntent:
    return ProductSearchIntent(
        search_intent_id="INTENT-KAKAO-V041",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            maximum_affordable_periodic_amount=Decimal("1000000"),
            frequency=ContributionFrequency.MONTHLY,
            selected_term_value=26,
            selected_term_unit=TermUnit.WEEK,
        ),
        requested_top_k=1,
        source_utterances=["월 30만원 정도 생각하고 있어"],
    )


def test_kakao_missing_start_amount_becomes_ranking_input_question():
    service = ApplicationService(
        [kakao_26_week_product()],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )

    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.question_kind == "RANKING_INPUT"
    assert question.ranking_input is not None
    assert question.ranking_input.required_field == "preferred_start_amount"
    assert question.ranking_input.allowed_options == ["1000", "2000", "3000", "5000", "10000"]
    assert "시작금액" in question.question


def test_kakao_start_amount_answer_builds_26_week_cashflow_interest_and_ranking():
    service = ApplicationService(
        [kakao_26_week_product()],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.question_kind == "RANKING_INPUT"

    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer="10000",
    )
    candidate = service.evaluate_candidates(session.search_session_id)[
        kakao_26_week_product().product_id
    ]
    recommendation = service.get_top_recommendations(session.search_session_id)

    assert candidate.ranking_comparability == RankingComparability.COMPARABLE
    assert candidate.missing_ranking_inputs == []
    assert candidate.contribution_projection.contribution_count == 26
    assert candidate.contribution_projection.initial_amount == Decimal("10000")
    assert candidate.contribution_projection.increment_amount == Decimal("10000")
    assert candidate.contribution_projection.estimated_total_principal == Decimal("3510000")
    assert candidate.realizable_after_tax_interest is not None
    assert candidate.realizable_after_tax_interest > 0
    assert recommendation.top_products[0].ranking_comparability == RankingComparability.COMPARABLE
    assert recommendation.top_products[0].estimated_after_tax_interest == candidate.realizable_after_tax_interest


def test_declined_contribution_question_remains_explicitly_not_comparable():
    service = ApplicationService(
        [kakao_26_week_product()],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer="거절",
    )

    item = service.get_top_recommendations(session.search_session_id).top_products[0]
    detail = service.get_product_recommendation_detail(
        session.search_session_id, item.product_id, include_explanation=False
    )
    assert item.ranking_comparability == RankingComparability.MISSING_CONTRIBUTION_INPUT
    assert item.estimated_after_tax_interest is None
    assert item.realizable_rate == Decimal("5.0")
    assert detail.ranking_comparability == RankingComparability.MISSING_CONTRIBUTION_INPUT
    assert detail.estimated_after_tax_interest is None
    assert detail.missing_ranking_inputs[0].required_field == "preferred_start_amount"
