from __future__ import annotations

from decimal import Decimal

from eligibility.application_service import ApplicationService
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.kakao_26_week import (
    kakao_26_week_pre_subscription_context,
    kakao_26_week_product,
    kakao_future_intent,
    kakao_pre_subscription_user,
)
from eligibility.schema.application_input import Capability, QuickInputProfile
from eligibility.schema.enums import CapabilityState, EvaluationStatus, RankingObjective
from eligibility.schema.search import IntentPatch
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.intent import IntentParser
from eligibility.search.questions import RankingAwareQuestionPlanner
from eligibility.search.ranking import RankingService
from eligibility.search.recommendation import RecommendationService

from tests.v04_helpers import (
    AS_OF,
    SUBSCRIPTION_DATE,
    USER_ID,
    ScriptedIntentPatchGateway,
    base_store,
    make_intent,
    make_product,
)


def test_kakao_26_week_decline_does_not_close_7_week_future_intent():
    store = kakao_pre_subscription_user()
    store = store.with_fact(kakao_future_intent(True, target=7))
    store = store.with_fact(kakao_future_intent(False, target=26))

    result = FinancialEligibilityEngine().evaluate_product(
        kakao_26_week_product(),
        store,
        kakao_26_week_pre_subscription_context(),
    )
    seven, twenty_six = result.preferential_rule_results

    assert seven.status == EvaluationStatus.ACHIEVABLE
    assert twenty_six.status == EvaluationStatus.UNSATISFIABLE
    assert result.rates.realizable_rate == Decimal("3.0")
    assert kakao_26_week_product().preferential_rules[0].rule.from_sequence is None


def test_topk_stability_uses_actual_question_planner_materiality():
    products = [
        make_product("STAB-1", base_rate="6.0", reward_pp="1.0", bonus_fact_type="MATERIAL_FACT"),
        make_product("STAB-2", base_rate="5.0"),
        make_product("STAB-3", base_rate="4.0"),
        make_product("STAB-4", base_rate="3.0"),
        make_product("STAB-5", base_rate="2.0"),
        make_product("STAB-6", base_rate="0.5"),
    ]
    intent = make_intent(top_k=5)
    evaluations = MultiProductEvaluator().evaluate(
        products,
        base_store(),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    ranking_service = RankingService()
    ranking, ranked = ranking_service.rank(
        evaluations,
        {item.product_id: item for item in products},
        objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        top_k=5,
    )
    planner = RankingAwareQuestionPlanner(ranking_service=ranking_service)
    question = planner.select_next(ranked, intent)
    ordered = [ranked[product_id] for product_id in ranking.ordered_product_ids]
    stability = ranking_service.check_stability(
        ordered,
        top_k=5,
        objective=intent.ranking_objective,
        material_internal_question_remaining=question is not None,
    )

    assert ranking.stability.stable_membership is True
    assert question is not None
    assert question.request is not None
    assert question.request.fact_type == "MATERIAL_FACT"
    assert stability.stable_membership is True
    assert stability.material_internal_question_remaining is True
    assert stability.stable is False
    assert stability.reason_code == "MATERIAL_QUESTION_REMAINS"


def test_recommendation_reason_does_not_claim_term_match_without_period_preference():
    product = make_product("REASON-NO-TERM", base_rate="4.0")
    intent = make_intent(selected_term_value=None, selected_term_unit=None, top_k=1)
    candidate = MultiProductEvaluator().evaluate(
        [product], base_store(), intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )[product.product_id]
    ranking, ranked = RankingService().rank(
        {product.product_id: candidate},
        {product.product_id: product},
        objective=intent.ranking_objective,
        top_k=1,
    )
    service = RecommendationService()
    result = service.build_result("SEARCH-REASON-NO-TERM", ranking)
    detail = service.build_detail(
        search_session_id="SEARCH-REASON-NO-TERM",
        recommendation_id=result.recommendation_id,
        product=product,
        candidate=ranked[product.product_id],
        rank=1,
        intent=intent,
        ranking=ranking,
        include_explanation=False,
    )

    codes = {item.code for item in detail.recommendation_reason.positive_entries}
    assert "TERM_MATCH" not in codes
    assert all("희망 기간" not in text for text in detail.recommendation_reason.positives)
    assert "HIGHEST_AFTER_TAX_INTEREST" in codes


def test_recommendation_reason_term_match_has_structured_evidence_when_true():
    product = make_product("REASON-TERM", base_rate="4.0")
    intent = make_intent(selected_term_value=12, top_k=1)
    candidate = MultiProductEvaluator().evaluate(
        [product], base_store(), intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )[product.product_id]
    ranking, ranked = RankingService().rank(
        {product.product_id: candidate},
        {product.product_id: product},
        objective=intent.ranking_objective,
        top_k=1,
    )
    service = RecommendationService()
    result = service.build_result("SEARCH-REASON-TERM", ranking)
    detail = service.build_detail(
        search_session_id="SEARCH-REASON-TERM",
        recommendation_id=result.recommendation_id,
        product=product,
        candidate=ranked[product.product_id],
        rank=1,
        intent=intent,
        ranking=ranking,
        include_explanation=False,
    )

    term_entry = next(
        item for item in detail.recommendation_reason.positive_entries if item.code == "TERM_MATCH"
    )
    assert term_entry.evidence["requested_value"] == 12
    assert term_entry.evidence["resolved_value"] == 12


def test_quick_input_overrides_same_key_llm_or_natural_inference():
    product = make_product("QUICK-PRIORITY")
    # "카드 새로 만드는 건 싫어. 적금 찾아줘." (don't want to open a new card, find me
    # savings) -- scripted as the deterministic patch an LLM would extract for
    # NEW_CARD_ISSUANCE=CANNOT, so the test can assert quick_input (CAN) wins
    # over this LLM-derived value for the same key.
    gateway = ScriptedIntentPatchGateway(
        [
            IntentPatch(
                upsert_product_types=["INSTALLMENT_SAVINGS"],
                upsert_capabilities=[
                    Capability(
                        capability_id="NEW_CARD_ISSUANCE",
                        state=CapabilityState.CANNOT,
                    )
                ],
            )
        ]
    )
    service = ApplicationService(
        [product],
        user_fact_stores={USER_ID: base_store()},
        intent_parser=IntentParser(gateway),
    )

    session = service.create_search_session(
        user_id=USER_ID,
        utterance="카드 새로 만드는 건 싫어. 적금 찾아줘.",
        quick_input=QuickInputProfile(
            capabilities=[
                Capability(
                    capability_id="NEW_CARD_ISSUANCE",
                    state=CapabilityState.CAN,
                )
            ]
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    intent = service.get_search_intent(session.search_session_id)

    card = next(item for item in intent.capabilities if item.capability_id == "NEW_CARD_ISSUANCE")
    assert card.state == CapabilityState.CAN
