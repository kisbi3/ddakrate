from __future__ import annotations

from decimal import Decimal

from eligibility.adapters import RestApplicationAdapter
from eligibility.application import GeneratedQuestion, QuestionGenerator
from eligibility.application_service import ApplicationService
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_pre_subscription_user,
)
from eligibility.fixtures.shinhan_youth_first import golden_context, shinhan_youth_first_product
from eligibility.fixtures.user_001 import USER_ID as SHINHAN_USER_ID, user_001
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.application_input import Preference
from eligibility.schema.enums import PreferenceValue, RankingObjective
from eligibility.schema.search import ContributionPlan, ProductSearchIntent
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.ranking import RankingService
from eligibility.search.recommendation import GroundedResultExplainer, RecommendationService

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_product


def _salary_request():
    return FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
    ).missing_facts[0]


def _question_gateway(request, wording):
    return LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.QUESTION_GENERATION: GeneratedQuestion(
                    question=wording,
                    fact_type=request.fact_type,
                    rule_id=request.requested_by_rule_id,
                    action_id=request.action_id,
                    reward_id=request.reward_id,
                )
            }
        )
    )


def test_question_rejects_new_non_numeric_action():
    request = _salary_request()
    hallucination = (
        "신규 입출금통장을 개설하고 월급봉투 인정조건을 6개월 달성해 "
        "+1.0%p 우대를 목표로 관리할까요?"
    )

    result = QuestionGenerator(_question_gateway(request, hallucination)).generate(request)

    assert result == request.question
    assert "입출금통장" not in result


def test_allowed_action_paraphrase_remains_possible():
    request = _salary_request()
    wording = "가입 후 월급봉투 실적을 6개월 이상 채워 +1.0%p 우대를 목표로 관리할까요?"

    result = QuestionGenerator(_question_gateway(request, wording)).generate(request)

    assert result == wording


def _single_detail():
    product = make_product(
        "GROUND-DETAIL",
        base_rate="3.0",
        reward_pp="0.5",
        rule_name="자동이체 우대",
    )
    intent = ProductSearchIntent(
        search_intent_id="INTENT-GROUND-DETAIL",
        user_id="V04-USER",
        ranking_objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            maximum_affordable_periodic_amount=Decimal("300000"),
            selected_term_value=12,
            selected_term_unit="MONTH",
            frequency="MONTHLY",
        ),
        requested_top_k=1,
    )
    candidate = MultiProductEvaluator().evaluate(
        [product],
        base_store(),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )[product.product_id]
    ranking, ranked = RankingService().rank(
        {product.product_id: candidate},
        {product.product_id: product},
        objective=intent.ranking_objective,
        top_k=1,
    )
    recommendation = RecommendationService()
    result = recommendation.build_result("SEARCH-GROUND", ranking)
    detail = recommendation.build_detail(
        search_session_id="SEARCH-GROUND",
        recommendation_id=result.recommendation_id,
        product=product,
        candidate=ranked[product.product_id],
        rank=1,
        intent=intent,
        ranking=ranking,
        include_explanation=False,
    )
    return detail


def test_explainer_rejects_new_non_numeric_condition():
    detail = _single_detail()
    hallucination = (
        f"{detail.product_name}은 현재 추천 1위입니다. 신규 입출금통장을 개설하고 "
        f"자동이체를 관리하면 예상금리 {detail.realizable_rate}%입니다."
    )
    # Bind every *allowed* claim correctly so rejection is specifically caused
    # by the newly invented account-opening action, not by missing bindings.
    fallback = GroundedResultExplainer.deterministic_fallback(detail)
    payload = GroundedResultExplainer._payload(detail, fallback)
    bindings = [
        {
            "claim_id": claim.claim_id,
            "claim_type": claim.claim_type.value,
            "value": claim.value,
            "unit": claim.unit,
        }
        for claim in payload.claims
    ]
    gateway = LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.RESULT_EXPLANATION: {
                    "explanation": hallucination,
                    "claim_bindings": bindings,
                }
            }
        )
    )
    explainer = GroundedResultExplainer(gateway)

    result = explainer.explain(detail)

    assert result == fallback
    assert "입출금통장" not in result


def test_invalid_claim_uses_deterministic_fallback():
    detail = _single_detail()
    gateway = LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.RESULT_EXPLANATION: {
                    "explanation": "가입하면 무조건 99.0%를 보장합니다.",
                    "claim_bindings": [],
                }
            }
        )
    )
    explainer = GroundedResultExplainer(gateway)

    assert explainer.explain(detail) == explainer.deterministic_fallback(detail)


def test_web_search_session_accepts_quick_input():
    product = make_product("WEB-QUICK")
    service = ApplicationService([product], user_fact_stores={"WEB-U": base_store(user_id="WEB-U")})
    rest = RestApplicationAdapter(service)

    response = rest.handle(
        "POST",
        "/search-sessions",
        {
            "user_id": "WEB-U",
            "natural_language_query": "적금 찾아줘",
            "quick_input": {
                "capabilities": [
                    {"capability_id": "NEW_CARD_ISSUANCE", "state": "CANNOT"}
                ]
            },
            "as_of": "2026-08-19",
            "subscription_date": "2026-08-20",
        },
    )

    assert response.status_code == 201
    intent = service.get_search_intent(response.body["search_session_id"])
    assert any(
        item.capability_id == "NEW_CARD_ISSUANCE" and item.state.value == "CANNOT"
        for item in intent.capabilities
    )


def test_web_can_fetch_current_clarification():
    product = make_product("WEB-CLARIFY")
    service = ApplicationService([product], user_fact_stores={"WEB-C": base_store(user_id="WEB-C")})
    rest = RestApplicationAdapter(service)

    created = rest.handle(
        "POST",
        "/search-sessions",
        {
            "user_id": "WEB-C",
            "natural_language_query": "첫거래 우대가 있는 상품만 보여줘",
            "quick_input": {
                "preferences": [
                    {
                        "field": "FIRST_TRANSACTION_BENEFIT",
                        "preference": "PREFER_ABSENT",
                        "expected": True,
                    }
                ]
            },
            "as_of": "2026-08-19",
            "subscription_date": "2026-08-20",
        },
    )
    session_id = created.body["search_session_id"]

    current = rest.handle(
        "GET", f"/search-sessions/{session_id}/clarifications/current"
    )

    assert created.body["status"] == "CLARIFICATION_REQUIRED"
    assert current.status_code == 200
    assert current.body["status"] == "PENDING"
    assert current.body["question"]
    assert current.body["allowed_resolutions"]


def test_web_can_answer_ranking_input_question():
    service = ApplicationService(
        [kakao_26_week_product()],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
    )
    rest = RestApplicationAdapter(service)
    created = rest.handle(
        "POST",
        "/search-sessions",
        {
            "user_id": KAKAO_USER_ID,
            "natural_language_query": "26주 적금에 월 30만원 정도 생각하고 있어",
            "as_of": "2026-08-19",
            "subscription_date": "2026-08-20",
        },
    )
    session_id = created.body["search_session_id"]
    next_question = rest.handle(
        "GET", f"/search-sessions/{session_id}/questions/next"
    )

    assert next_question.body["question_kind"] == "RANKING_INPUT"
    answered = rest.handle(
        "POST",
        f"/search-sessions/{session_id}/answers",
        {"question_id": next_question.body["question_id"], "answer": "10000"},
    )
    recommendation = rest.handle(
        "GET", f"/search-sessions/{session_id}/recommendations"
    )

    assert answered.status_code == 200
    assert recommendation.status_code == 200
    assert recommendation.body["top_products"][0]["ranking_comparability"] == "COMPARABLE"
    assert recommendation.body["top_products"][0]["estimated_after_tax_interest"] is not None


def _shinhan_detail():
    product = shinhan_youth_first_product()
    intent = ProductSearchIntent(
        search_intent_id="INTENT-SHINHAN-DETAIL",
        user_id=SHINHAN_USER_ID,
        ranking_objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            maximum_affordable_periodic_amount=Decimal("300000"),
            selected_term_value=12,
            selected_term_unit="MONTH",
            frequency="MONTHLY",
        ),
        requested_top_k=1,
    )
    candidate = MultiProductEvaluator().evaluate(
        [product],
        user_001(),
        intent,
        as_of=golden_context().as_of,
        subscription_date=golden_context().subscription_date,
    )[product.product_id]
    ranking, ranked = RankingService().rank(
        {product.product_id: candidate},
        {product.product_id: product},
        objective=intent.ranking_objective,
        top_k=1,
    )
    service = RecommendationService()
    result = service.build_result("SEARCH-SHINHAN", ranking)
    return service.build_detail(
        search_session_id="SEARCH-SHINHAN",
        recommendation_id=result.recommendation_id,
        product=product,
        candidate=ranked[product.product_id],
        rank=1,
        intent=intent,
        ranking=ranking,
        include_explanation=False,
    )


def test_detail_breakdown_exposes_meaningful_or_branches():
    detail = _shinhan_detail()
    first_or_event = next(item for item in detail.rate_breakdown if item.rule_id == "RATE_FIRST_OR_EVENT")

    assert len(first_or_event.children) == 2
    assert {item.rule_id for item in first_or_event.children} == {
        "RATE_FIRST_TRANSACTION_BRANCH",
        "RATE_EVENT_BRANCH",
    }
    assert all(item.nominal_reward_pp is None for item in first_or_event.children)


def test_first_transaction_unsat_event_unknown_visible_separately():
    detail = _shinhan_detail()
    parent = next(item for item in detail.rate_breakdown if item.rule_id == "RATE_FIRST_OR_EVENT")
    by_id = {item.rule_id: item for item in parent.children}

    assert by_id["RATE_FIRST_TRANSACTION_BRANCH"].status.value == "UNSATISFIABLE"
    assert by_id["RATE_EVENT_BRANCH"].status.value == "UNKNOWN"
    assert by_id["RATE_FIRST_TRANSACTION_BRANCH"].reason_code == "PRIOR_HOLDING_OVERLAPS_LOOKBACK"
    assert by_id["RATE_EVENT_BRANCH"].reason_code == "EVENT_COUPON_STATUS_UNRESOLVED"
