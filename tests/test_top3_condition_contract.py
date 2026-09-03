from __future__ import annotations

from decimal import Decimal

from eligibility.application_service import ApplicationService
from eligibility.schema.condition_requirement import (
    ConditionRequirement,
    UserConditionState,
)
from eligibility.schema.enums import RankingObjective
from eligibility.schema.enums import FactSemanticType, PreferenceValue, ResolutionStrategy
from eligibility.schema.application_input import Preference
from eligibility.schema.evaluation import MissingFactRequest
from eligibility.audit import AuditEventType
from eligibility.search.questions import RankingAwareQuestionPlanner
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.ranking import RankingService

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_intent, make_product


def _evaluate(products, intent):
    return MultiProductEvaluator().evaluate(
        products,
        base_store(),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )


def test_display_ranking_uses_possible_max_and_keeps_realizable_rate_separate():
    confirmed = make_product("CONFIRMED", base_rate="5")
    unresolved = make_product(
        "UNRESOLVED", base_rate="2", reward_pp="10", bonus_fact_type="BIG_BONUS"
    )
    intent = make_intent(
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=2,
    )
    evaluations = _evaluate([confirmed, unresolved], intent)

    # The unresolved product remains first through its still-possible upper
    # bound, while its currently expected rate remains a separate lower value.
    ranking, _ = RankingService().rank(
        evaluations,
        {item.product_id: item for item in [confirmed, unresolved]},
        objective=intent.ranking_objective,
        top_k=2,
    )
    assert ranking.ordered_product_ids[:2] == ["UNRESOLVED", "CONFIRMED"]
    assert ranking.items[0].user_specific_conditional_upper_rate == Decimal("12")
    assert ranking.items[0].realizable_rate == Decimal("2")
    assert RankingAwareQuestionPlanner()._frontier(evaluations, intent) == {
        "CONFIRMED", "UNRESOLVED"
    }


def test_legacy_fact_type_does_not_merge_different_grounded_questions():
    common = {
        "fact_type": "CUSTOMER_SALARY_TRANSFER_MONTH_COUNT",
        "resolution_strategy": ResolutionStrategy.ASK_USER,
        "expected_semantic_type": FactSemanticType.SELF_REPORTED_FACT,
    }
    military = MissingFactRequest(
        **common,
        question="KB국민은행으로 국군재정관리단 급여를 6개월 이상 받았나요?",
        requested_by_rule_id="KB-MILITARY-SALARY",
    )
    ordinary = MissingFactRequest(
        **common,
        question="BNK경남은행으로 급여를 3개월 이상 받았나요?",
        requested_by_rule_id="BNK-SALARY",
    )
    same_as_military = military.model_copy(
        update={"requested_by_rule_id": "ANOTHER-KB-MILITARY-SALARY"},
        deep=True,
    )

    assert RankingAwareQuestionPlanner.question_family_id(military) != (
        RankingAwareQuestionPlanner.question_family_id(ordinary)
    )
    assert RankingAwareQuestionPlanner.question_family_id(military) == (
        RankingAwareQuestionPlanner.question_family_id(same_as_military)
    )


def test_frontier_includes_challengers_beyond_legacy_ten_product_cap():
    products = [make_product(f"FIXED-{n}", base_rate=str(5 - n / 10)) for n in range(3)]
    products.extend(
        make_product(
            f"CHALLENGER-{n}",
            base_rate="1",
            reward_pp="5",
            bonus_fact_type=f"BONUS-{n}",
        )
        for n in range(20)
    )
    intent = make_intent(
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=20,
    )
    evaluations = _evaluate(products, intent)
    frontier = RankingAwareQuestionPlanner().frontier_product_ids(evaluations, intent)

    assert len(frontier) == len(products)
    assert "CHALLENGER-19" in frontier


def test_willing_without_amount_is_saved_and_not_reasked():
    product = make_product(
        "CARD-THRESHOLD",
        base_rate="2",
        reward_pp="1",
        bonus_fact_type="CARD_MONTHLY_SPEND_LIMIT",
    )
    intent = make_intent(
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=1,
    )
    service = ApplicationService([product], user_fact_stores={intent.user_id: base_store()})
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None

    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer="카드는 가능해요",
    )
    runtime = service._runtime(session.search_session_id)
    state = runtime.condition_states["ACTION-CARD_MONTHLY_SPEND_LIMIT"]
    assert state.status == "WILLING_UNSPECIFIED"
    assert runtime.active_question is None
    assert runtime.session.condition_states[0].status == "WILLING_UNSPECIFIED"
    detail = service.get_product_recommendation_detail(
        session.search_session_id,
        product.product_id,
        include_explanation=False,
    )
    assert detail.realizable_rate == Decimal("2")
    assert detail.additional_possible_rate_pp == Decimal("1")
    assert detail.rate_breakdown[0].display_status == "확인 전"
    assert detail.rate_breakdown[0].user_condition_status == "WILLING_UNSPECIFIED"
    listed = service.get_top_recommendations(
        session.search_session_id
    ).top_products[0]
    assert listed.realizable_rate == detail.realizable_rate
    assert listed.estimated_pre_tax_interest == detail.estimated_pre_tax_interest


def test_declined_condition_keeps_product_and_detail_marks_not_applied():
    product = make_product(
        "DECLINED-CARD",
        base_rate="2",
        reward_pp="1",
        bonus_fact_type="CARD_MONTHLY_SPEND_LIMIT",
    )
    intent = make_intent(objective=RankingObjective.MAX_REALIZABLE_RATE, top_k=1)
    service = ApplicationService([product], user_fact_stores={intent.user_id: base_store()})
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer=False,
    )

    detail = service.get_product_recommendation_detail(
        session.search_session_id,
        product.product_id,
        include_explanation=False,
    )
    assert product.product_id in service.get_search_status(
        session.search_session_id
    ).candidate_product_ids
    assert detail.realizable_rate == Decimal("2")
    assert detail.rate_breakdown[0].display_status == "적용 안 함"
    assert detail.rate_breakdown[0].user_condition_status == "DECLINED"

    request_reference = service.get_answer_history(
        session.search_session_id
    )[0].request_reference
    service.revise_user_answer(
        session.search_session_id,
        request_reference=request_reference,
        new_value=True,
    )
    runtime = service._runtime(session.search_session_id)
    assert runtime.condition_states[
        "ACTION-CARD_MONTHLY_SPEND_LIMIT"
    ].status == "DECLARED_FEASIBLE"

    service.clear_user_declared_fact(
        session.search_session_id,
        fact_type="CARD_MONTHLY_SPEND_LIMIT",
    )
    runtime = service._runtime(session.search_session_id)
    assert "ACTION-CARD_MONTHLY_SPEND_LIMIT" not in runtime.condition_states
    assert service.get_next_question(session.search_session_id) is not None


def test_condition_requirement_defaults_to_review_required():
    requirement = ConditionRequirement(
        requirement_id="req:p:1",
        product_id="p",
        scope="RATE_BENEFIT",
        variable_id="CARD_MONTHLY_SPEND_LIMIT",
        operator="GTE",
        threshold={"amount": 300000, "currency": "KRW", "period": "MONTH"},
        rate_effect={"percentage_point": Decimal("1.0")},
    )
    assert requirement.review_status == "REVIEW_REQUIRED"
    assert UserConditionState(variable_id="x").remains_optimistic


def test_requirement_coverage_and_frontier_are_emitted_as_shadow_trace():
    product = make_product(
        "TRACE",
        reward_pp="1",
        bonus_fact_type="AUTO_TRANSFER",
    )
    intent = make_intent(top_k=1)
    service = ApplicationService([product], user_fact_stores={intent.user_id: base_store()})
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )

    events = service.get_evaluation_trace(session.search_session_id)
    coverage = [
        item for item in events if item.event_type == AuditEventType.DATA_COVERAGE_CHECKED
    ]
    frontier = [
        item for item in events if item.event_type == AuditEventType.TOP_K_STABILITY_CHECKED
    ]
    assert coverage[-1].payload["activation_mode"] == "SHADOW_REVIEWED_ONLY"
    assert frontier[-1].payload["visible_top3_product_ids"] == ["TRACE"]
    assert frontier[-1].payload["legacy_frontier_product_ids"] == ["TRACE"]


def test_question_candidate_preserves_fact_type_and_exposes_family_id():
    product = make_product(
        "CARD-FAMILY",
        reward_pp="1",
        bonus_fact_type="CARD_MONTHLY_SPEND_LIMIT",
    )
    intent = make_intent(top_k=1)

    candidate = RankingAwareQuestionPlanner().score_candidates(
        _evaluate([product], intent), intent
    )[0]

    assert candidate.fact_type == "CARD_MONTHLY_SPEND_LIMIT"
    assert candidate.family_id == "ACTION-CARD_MONTHLY_SPEND_LIMIT"

    question = RankingAwareQuestionPlanner().select_next(
        _evaluate([product], intent),
        intent,
    )
    assert question is not None and question.question_spec is not None
    assert question.question_spec.variable_id == "ACTION-CARD_MONTHLY_SPEND_LIMIT"
    assert question.question_spec.scope["condition_scope"] == "RATE_BENEFIT"
    assert question.answer_mode == "OPTIONS"
    assert question.question_spec.value_schema["type"] == "money"
    assert question.question_spec.options == [0, 100000, 300000, 500000, 1000000]


def test_declined_card_benefits_are_not_asked_again() -> None:
    product = make_product(
        "CARD-DECLINED-GLOBALLY",
        reward_pp="1",
        bonus_fact_type="CARD_MONTHLY_SPEND_LIMIT",
    )
    intent = make_intent(top_k=1).model_copy(
        update={
            "preferences": [
                Preference(
                    field="CARD_BENEFIT",
                    preference=PreferenceValue.PREFER_ABSENT,
                )
            ]
        },
        deep=True,
    )

    assert RankingAwareQuestionPlanner().select_next(
        _evaluate([product], intent), intent
    ) is None


def test_product_specific_card_spend_action_ids_share_one_question_family() -> None:
    planner = RankingAwareQuestionPlanner()
    requests = [
        MissingFactRequest(
            fact_type=f"CARD_MONTHLY_SPEND_AMOUNT_{suffix}",
            action_id=f"ACTION-{suffix}-CARD-SPEND",
            resolution_strategy=ResolutionStrategy.ASK_USER,
            requested_by_rule_id=f"RULE-{suffix}",
            question="카드 이용실적은 한 달에 최대 얼마까지 가능하신가요?",
        )
        for suffix in ("A", "B")
    ]

    assert {planner.question_family_id(request) for request in requests} == {
        "ACTION-CARD_MONTHLY_SPEND_LIMIT"
    }
