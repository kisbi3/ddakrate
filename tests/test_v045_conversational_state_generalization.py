from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from eligibility.adapters import MCPToolAdapter, RestApplicationAdapter
from eligibility.application_service import ApplicationService
from eligibility.audit import AuditEventType
from eligibility.conversation import ConversationOrchestrator
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_pre_subscription_user,
)
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.application_input import Capability
from eligibility.schema.enums import (
    CapabilityState,
    ContributionFrequency,
    FactSemanticType,
    FactSourceType,
    RankingObjective,
    TermUnit,
)
from eligibility.schema.search import (
    ContributionPlan,
    ContributionPlanPatch,
    IntentPatch,
    ProductSearchIntent,
)

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_intent, make_product, verified_fact


def _orchestrator(*responses):
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: list(responses)})
    return ConversationOrchestrator(LLMGateway(adapter)), adapter


def _kakao_intent(maximum: str = "300000", *, top_k: int = 2):
    return ProductSearchIntent(
        search_intent_id=f"INTENT-V045-{maximum}",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            maximum_affordable_periodic_amount=Decimal(maximum),
            frequency=ContributionFrequency.MONTHLY,
            selected_term_value=26,
            selected_term_unit=TermUnit.WEEK,
        ),
        requested_top_k=top_k,
        source_utterances=[f"월 최대 {maximum}원"],
    )


def _kakao_service(*, maximum="300000", orchestrator=None, extra_products=()):
    product = kakao_26_week_product()
    service = ApplicationService(
        [product, *extra_products],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
        conversation_orchestrator=orchestrator,
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(maximum, top_k=max(2, len(extra_products) + 1)),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    return service, session, product


def _reach_kakao_start(service: ApplicationService, session_id: str):
    for _ in range(8):
        question = service.get_next_question(session_id)
        assert question is not None
        if question.question_kind == "RANKING_INPUT":
            assert question.ranking_input is not None
            if question.ranking_input.required_field == "preferred_start_amount":
                return question
        if question.question_kind == "FINANCIAL_FACT":
            service.submit_user_answer(session_id, question_id=question.question_id, answer=True)
            continue
        raise AssertionError(f"unexpected question: {question}")
    raise AssertionError("preferred_start_amount question not reached")


def _answer_kakao_start(service: ApplicationService, session_id: str, value: str):
    question = _reach_kakao_start(service, session_id)
    service.submit_user_answer(session_id, question_id=question.question_id, answer=value)
    return question


def test_natural_language_restores_excluded_product():
    kakao = kakao_26_week_product()
    responses = (
        {"actions": [{"operation": "SET_PRODUCT_EXCLUSION", "product_id": kakao.product_id, "excluded": True}]},
        {"actions": [{"operation": "SET_PRODUCT_EXCLUSION", "product_id": kakao.product_id, "excluded": False}]},
    )
    orch, _ = _orchestrator(*responses)
    service, session, _ = _kakao_service(orchestrator=orch)

    service.handle_user_message(session.search_session_id, message="카카오는 빼줘.")
    assert kakao.product_id in service.get_search_status(session.search_session_id).excluded_product_ids
    assert kakao.product_id not in service.get_search_status(session.search_session_id).candidate_product_ids

    service.handle_user_message(session.search_session_id, message="아까 카카오 뺀 건 취소하고 다시 같이 보여줘.")
    assert kakao.product_id not in service.get_search_status(session.search_session_id).excluded_product_ids
    assert kakao.product_id in service.get_search_status(session.search_session_id).candidate_product_ids


def test_previous_decline_does_not_require_reopen_operation():
    kakao = kakao_26_week_product()
    response = {
        "actions": [{
            "operation": "SET_PRODUCT_CONTRIBUTION_CHOICE",
            "product_id": kakao.product_id,
            "field": "preferred_start_amount",
            "new_value": "2000",
        }]
    }
    orch, _ = _orchestrator(response)
    service, session, _ = _kakao_service(orchestrator=orch)
    question = _reach_kakao_start(service, session.search_session_id)
    service.submit_user_answer(session.search_session_id, question_id=question.question_id, answer="답변 안함")
    assert service._runtime(session.search_session_id).declined_ranking_input_ids

    service.handle_user_message(session.search_session_id, message="아까는 답 안 했는데 카카오는 2천원으로 시작할게.")
    choices = service.get_product_contribution_choices(session.search_session_id)
    assert len(choices) == 1 and choices[0].value == Decimal("2000")
    assert not service._runtime(session.search_session_id).declined_ranking_input_ids


def test_product_choice_revision_uses_same_conversational_loop():
    kakao = kakao_26_week_product()
    responses = (
        {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "3000"}]},
        {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "2000"}]},
    )
    orch, _ = _orchestrator(*responses)
    service, session, _ = _kakao_service(maximum="300000", orchestrator=orch)
    _answer_kakao_start(service, session.search_session_id, "1000")

    service.handle_user_message(session.search_session_id, message="카카오는 3천원으로 바꿔줘.")
    service.handle_user_message(session.search_session_id, message="아니, 2천원으로 할게.")
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("2000")
    history = service.get_product_contribution_choice_history(session.search_session_id)
    assert [item.value for item in history] == [Decimal("1000"), Decimal("3000"), Decimal("2000")]
    assert [item.record_status for item in history] == ["SUPERSEDED", "SUPERSEDED", "ACTIVE"]


def test_multiple_changes_in_one_message_latest_state_is_effective():
    kakao = kakao_26_week_product()
    salary = make_product(
        "V045-SALARY",
        reward_pp="1.0",
        bonus_fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
        bonus_semantic_type=FactSemanticType.FUTURE_INTENT,
    )
    response = {
        "actions": [
            {
                "operation": "UPDATE_SEARCH_INTENT",
                "intent_patch": {
                    "upsert_capabilities": [
                        {"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CANNOT"},
                        {"capability_id": "NEW_CARD_ISSUANCE", "state": "CANNOT"},
                    ],
                    "contribution_plan_patch": {
                        "desired_periodic_amount": "300000",
                        "maximum_affordable_periodic_amount": "300000",
                    },
                },
            },
            {
                "operation": "SET_PRODUCT_CONTRIBUTION_CHOICE",
                "product_id": kakao.product_id,
                "field": "preferred_start_amount",
                "new_value": "3000",
            },
        ]
    }
    orch, _ = _orchestrator(response)
    service = ApplicationService(
        [kakao, salary],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
        conversation_orchestrator=orch,
    )
    initial = _kakao_intent("1000000").model_copy(
        update={
            "capabilities": [
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN),
                Capability(capability_id="NEW_CARD_ISSUANCE", state=CapabilityState.CAN),
            ]
        },
        deep=True,
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=initial,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    _answer_kakao_start(service, session.search_session_id, "10000")
    turn = service.handle_user_message(
        session.search_session_id,
        message="생각해보니 월 30만원까지만 하고, 급여계좌는 못 바꾸겠고, 카카오는 3천원으로 해. 카드도 새로 만들기 싫어.",
    )
    intent = service.get_search_intent(session.search_session_id)
    assert intent.contribution_plan.desired_periodic_amount == Decimal("300000")
    assert intent.contribution_plan.maximum_affordable_periodic_amount == Decimal("300000")
    states = {item.capability_id: item.state for item in intent.capabilities}
    assert states["CHANGE_SALARY_ACCOUNT"] == CapabilityState.CANNOT
    assert states["NEW_CARD_ISSUANCE"] == CapabilityState.CANNOT
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("3000")
    assert turn.session.ranking_run_id is not None


def test_same_capability_can_cannot_can_leaves_one_effective_declaration():
    responses = (
        {"actions": [{"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"upsert_capabilities": [{"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CAN"}]}}]},
        {"actions": [{"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"upsert_capabilities": [{"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CANNOT"}]}}]},
        {"actions": [{"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"upsert_capabilities": [{"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CAN"}]}}]},
    )
    orch, _ = _orchestrator(*responses)
    product = make_product("V045-CAP")
    service = ApplicationService([product], user_fact_stores={"V04-USER": base_store()}, conversation_orchestrator=orch)
    session = service.create_search_session(user_id="V04-USER", intent=make_intent(top_k=1), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    for message in ("급여계좌 변경 가능해.", "아니 생각해보니 안 될 것 같아.", "다시 확인했는데 가능해."):
        service.handle_user_message(session.search_session_id, message=message)
    runtime = service._runtime(session.search_session_id)
    active = [
        fact for fact in runtime.fact_store.active_facts
        if fact.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE" and fact.source_type == FactSourceType.USER_DECLARED
    ]
    assert [fact.value for fact in active] == [True]


def test_multi_change_invalid_turn_rolls_back_all_mutable_changes():
    kakao = kakao_26_week_product()
    response = {
        "actions": [
            {
                "operation": "UPDATE_SEARCH_INTENT",
                "intent_patch": {"contribution_plan_patch": {"desired_periodic_amount": "200000", "maximum_affordable_periodic_amount": "200000"}},
            },
            {
                "operation": "SET_PRODUCT_CONTRIBUTION_CHOICE",
                "product_id": kakao.product_id,
                "field": "preferred_start_amount",
                "new_value": "7000",
            },
        ]
    }
    orch, _ = _orchestrator(response)
    service, session, _ = _kakao_service(maximum="300000", orchestrator=orch)
    _answer_kakao_start(service, session.search_session_id, "3000")
    runtime = service._runtime(session.search_session_id)
    before = deepcopy((runtime.session, runtime.intent, runtime.product_contribution_choices, runtime.evaluations, runtime.ranking))
    with pytest.raises(ValueError):
        service.handle_user_message(session.search_session_id, message="월 최대는 20만원으로 줄이고, 카카오는 7천원으로 바꿔줘.")
    after = (runtime.session, runtime.intent, runtime.product_contribution_choices, runtime.evaluations, runtime.ranking)
    assert after == before
    assert any(event.event_type == AuditEventType.CONVERSATION_TURN_ROLLED_BACK for event in service.get_evaluation_trace(session.search_session_id))


def test_question_need_derived_from_current_state_not_answer_history():
    service, session, product = _kakao_service(maximum="1000000")
    original = _answer_kakao_start(service, session.search_session_id, "10000")
    assert original.question_id in service.get_search_status(session.search_session_id).answered_question_ids
    service.update_search_intent(
        session.search_session_id,
        utterance=None,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                maximum_affordable_periodic_amount=Decimal("300000"),
                desired_periodic_amount=Decimal("300000"),
            )
        ),
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.question_kind == "CONTRIBUTION_FEASIBILITY"
    assert question.feasibility_clarification.product_id == product.product_id


def test_valid_existing_choice_is_not_reasked_after_full_research():
    service, session, _ = _kakao_service(maximum="300000")
    _answer_kakao_start(service, session.search_session_id, "3000")
    service._run_pipeline(service._runtime(session.search_session_id))
    question = service.get_next_question(session.search_session_id)
    if question is not None:
        assert not (
            question.question_kind == "RANKING_INPUT"
            and question.ranking_input is not None
            and question.ranking_input.required_field == "preferred_start_amount"
        )


def test_user_message_cannot_overwrite_institution_verified_fact():
    product = make_product("V045-AUTH")
    store = base_store(
        verified_fact("AUTH-1", "AUTHORITATIVE_STATUS", True)
    )
    response = {"actions": [{"operation": "REVISE_USER_DECLARED_FACT", "fact_type": "AUTHORITATIVE_STATUS", "new_value": False}]}
    orch, _ = _orchestrator(response)
    service = ApplicationService([product], user_fact_stores={"V04-USER": store}, conversation_orchestrator=orch)
    session = service.create_search_session(user_id="V04-USER", intent=make_intent(top_k=1), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    with pytest.raises(ValueError, match="AUTHORITATIVE_FACT_IMMUTABLE_BY_USER"):
        service.handle_user_message(session.search_session_id, message="그 금융이력은 없었던 걸로 해줘.")
    active = [f for f in service._runtime(session.search_session_id).fact_store.active_facts if f.fact_type == "AUTHORITATIVE_STATUS"]
    assert len(active) == 1 and active[0].value is True and active[0].source_type == FactSourceType.INSTITUTION_VERIFIED


def test_conversation_context_separates_mutable_and_authoritative_state():
    product = make_product("V045-CONTEXT")
    store = base_store(
        verified_fact("AUTH-CTX", "AUTH_FACT", True)
    )
    response = {"actions": [{"operation": "NO_OP"}]}
    orch, adapter = _orchestrator(response)
    service = ApplicationService([product], user_fact_stores={"V04-USER": store}, conversation_orchestrator=orch)
    session = service.create_search_session(user_id="V04-USER", intent=make_intent(top_k=1), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    service.handle_user_message(session.search_session_id, message="그대로 보여줘.")
    prompt = adapter.call_history[-1].messages[-1].content
    assert "MUTABLE_SEARCH_STATE" in prompt
    assert "AUTHORITATIVE_FACT_SUMMARY" in prompt
    assert "AUTH_FACT" in prompt


def test_state_update_triggers_full_candidate_retrieval_and_rerank():
    p12 = make_product("V045-TERM12", term_value=12)
    p6 = make_product("V045-TERM6", term_value=6)
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {
                "upsert_hard_constraints": [{"field": "MAX_TERM_MONTHS", "constraint": "REQUIRE", "expected": 6}]
            },
        }]
    }
    orch, _ = _orchestrator(response)
    service = ApplicationService(
        [p12, p6],
        user_fact_stores={"V04-USER": base_store()},
        conversation_orchestrator=orch,
    )
    session = service.create_search_session(user_id="V04-USER", intent=make_intent(top_k=2), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    before_run = service.get_search_status(session.search_session_id).ranking_run_id
    service.handle_user_message(session.search_session_id, message="6개월 상품만 다시 봐줘.")
    status = service.get_search_status(session.search_session_id)
    assert status.ranking_run_id != before_run
    assert "V045-TERM6" in status.candidate_product_ids
    events = service.get_evaluation_trace(session.search_session_id)
    assert any(event.event_type == AuditEventType.SEARCH_RECALCULATED for event in events)


def test_rest_messages_is_canonical_conversational_entrypoint():
    response = {"actions": [{"operation": "SET_PRODUCT_EXCLUSION", "product_id": kakao_26_week_product().product_id, "excluded": True}]}
    orch, _ = _orchestrator(response)
    service, session, product = _kakao_service(orchestrator=orch)
    rest = RestApplicationAdapter(service)
    result = rest.handle("POST", f"/search-sessions/{session.search_session_id}/messages", {"message": "카카오는 빼줘."})
    assert result.status_code == 200
    assert result.body["operations_executed"] == ["SET_PRODUCT_EXCLUSION"]
    assert product.product_id in result.body["session"]["excluded_product_ids"]


def test_mcp_reuses_same_generalized_application_service():
    response = {"actions": [{"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"ranking_objective_patch": "MAX_REALIZABLE_RATE"}}]}
    orch, _ = _orchestrator(response)
    service, session, _ = _kakao_service(orchestrator=orch)
    mcp = MCPToolAdapter(service)
    result = mcp.handle_user_message(search_session_id=session.search_session_id, message="세후이자 말고 금리 높은 순으로 보여줘.")
    assert result["operations_executed"] == ["UPDATE_SEARCH_INTENT"]
    assert service.get_search_intent(session.search_session_id).ranking_objective == RankingObjective.MAX_REALIZABLE_RATE
    assert mcp.service is service


def test_user_message_cannot_overwrite_observed_fact():
    product = make_product("V045-OBS")
    observed = verified_fact(
        "OBS-1",
        "OBSERVED_STATUS",
        True,
        semantic_type=FactSemanticType.OBSERVED_FACT,
        source_type=FactSourceType.MYDATA_VERIFIED,
    )
    response = {
        "actions": [{
            "operation": "REVISE_USER_DECLARED_FACT",
            "fact_type": "OBSERVED_STATUS",
            "new_value": False,
        }]
    }
    orch, _ = _orchestrator(response)
    service = ApplicationService(
        [product],
        user_fact_stores={"V04-USER": base_store(observed)},
        conversation_orchestrator=orch,
    )
    session = service.create_search_session(
        user_id="V04-USER",
        intent=make_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    with pytest.raises(ValueError, match="AUTHORITATIVE_FACT_IMMUTABLE_BY_USER"):
        service.handle_user_message(session.search_session_id, message="그 관측 기록은 없었던 걸로 해줘.")
    active = [
        f for f in service._runtime(session.search_session_id).fact_store.active_facts
        if f.fact_type == "OBSERVED_STATUS"
    ]
    assert len(active) == 1 and active[0].value is True


def test_include_and_set_product_choice_can_happen_in_one_natural_language_turn():
    kakao = kakao_26_week_product()
    response = {
        "actions": [
            {"operation": "SET_PRODUCT_EXCLUSION", "product_id": kakao.product_id, "excluded": False},
            {
                "operation": "SET_PRODUCT_CONTRIBUTION_CHOICE",
                "product_id": kakao.product_id,
                "field": "preferred_start_amount",
                "new_value": "3000",
            },
        ]
    }
    orch, _ = _orchestrator(response)
    service, session, _ = _kakao_service(orchestrator=orch)
    service.set_product_exclusion(session.search_session_id, product_id=kakao.product_id, excluded=True)
    assert kakao.product_id not in service.get_search_status(session.search_session_id).candidate_product_ids

    service.handle_user_message(
        session.search_session_id,
        message="카카오도 다시 보여주고, 시작은 3천원으로 할게.",
    )
    assert kakao.product_id in service.get_search_status(session.search_session_id).candidate_product_ids
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("3000")


def test_followup_pronoun_context_exposes_active_product_focus():
    kakao = kakao_26_week_product()
    response = {
        "actions": [{
            "operation": "SET_PRODUCT_CONTRIBUTION_CHOICE",
            "product_id": kakao.product_id,
            "field": "preferred_start_amount",
            "new_value": "3000",
        }]
    }
    orch, adapter = _orchestrator(response)
    service, session, _ = _kakao_service(orchestrator=orch)
    question = _reach_kakao_start(service, session.search_session_id)
    assert kakao.product_id in question.affected_product_ids
    service.handle_user_message(session.search_session_id, message="그거 3천원으로 할게.")
    prompt = adapter.call_history[-1].messages[-1].content
    assert "RECENT_PRODUCT_FOCUS" in prompt
    assert kakao.product_id in prompt
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("3000")


def test_state_update_recalculates_interest_and_top5():
    kakao = kakao_26_week_product()
    competitor = make_product("V045-INTEREST-COMP", base_rate="0.45")
    store = kakao_pre_subscription_user(intent=True).with_fact(
        verified_fact("V045-ELIG-COMP", "ELIGIBLE", True, user_id=KAKAO_USER_ID)
    )
    responses = (
        {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "3000"}]},
        {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "1000"}]},
    )
    orch, _ = _orchestrator(*responses)
    service = ApplicationService(
        [kakao, competitor],
        user_fact_stores={KAKAO_USER_ID: store},
        conversation_orchestrator=orch,
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent("300000"),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    _answer_kakao_start(service, session.search_session_id, "2000")
    service.handle_user_message(session.search_session_id, message="카카오는 3천원으로 바꿔줘.")
    before_eval = service.evaluate_candidates(session.search_session_id)[kakao.product_id]
    before_top = service.get_top_recommendations(session.search_session_id).top_products[0].product_id
    service.handle_user_message(session.search_session_id, message="아니 카카오는 1천원으로 할게.")
    after_eval = service.evaluate_candidates(session.search_session_id)[kakao.product_id]
    after_top = service.get_top_recommendations(session.search_session_id).top_products[0].product_id
    assert after_eval.realizable_after_tax_interest < before_eval.realizable_after_tax_interest
    assert before_top != after_top
