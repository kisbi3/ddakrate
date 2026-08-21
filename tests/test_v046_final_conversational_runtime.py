from __future__ import annotations

from decimal import Decimal

import pytest

from eligibility.adapters import RestApplicationAdapter
from eligibility.application_service import ApplicationService
from eligibility.conversation import ConversationOrchestrator
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_pre_subscription_user,
)
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.application_input import Capability, Preference
from eligibility.schema.enums import (
    CapabilityState,
    ContributionFrequency,
    FactSourceType,
    PreferenceValue,
    RankingObjective,
    TermUnit,
)
from eligibility.schema.search import ContributionPlan, ProductSearchIntent
from eligibility.working_note import FileSearchWorkingNoteStore, InMemorySearchWorkingNoteStore

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_intent, make_product


def _orchestrator(*responses):
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: list(responses)})
    return ConversationOrchestrator(LLMGateway(adapter)), adapter


def _kakao_intent(maximum: str = "300000"):
    return ProductSearchIntent(
        search_intent_id=f"INTENT-V046-{maximum}",
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
        requested_top_k=2,
        source_utterances=["26주 적금"],
    )


def _kakao_service(*responses, working_note_store=None, maximum="300000"):
    orch, adapter = _orchestrator(*responses)
    service = ApplicationService(
        [kakao_26_week_product()],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
        conversation_orchestrator=orch,
        working_note_store=working_note_store,
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(maximum),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    return service, session, adapter


def _reach_start(service: ApplicationService, session_id: str):
    for _ in range(10):
        question = service.get_next_question(session_id)
        assert question is not None
        if question.question_kind == "RANKING_INPUT":
            assert question.ranking_input is not None
            if question.ranking_input.required_field == "preferred_start_amount":
                return question
        if question.question_kind == "FINANCIAL_FACT":
            service.submit_user_answer(session_id, question_id=question.question_id, answer=True)
            continue
        raise AssertionError(question)
    raise AssertionError("Kakao preferred_start_amount question not reached")


def _answer_start(service: ApplicationService, session_id: str, value: str):
    question = _reach_start(service, session_id)
    service.submit_user_answer(session_id, question_id=question.question_id, answer=value)
    return question


def test_capability_can_can_be_returned_to_unknown_by_natural_language():
    response = {
        "actions": [{"operation": "CLEAR_USER_DECLARED_FACT", "fact_type": "SALARY_ACCOUNT_CHANGE_POSSIBLE"}]
    }
    orch, _ = _orchestrator(response)
    intent = make_intent(
        top_k=1,
        capabilities=[Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN)],
    )
    service = ApplicationService(
        [make_product("V046-CAP")],
        user_fact_stores={"V04-USER": base_store()},
        conversation_orchestrator=orch,
    )
    session = service.create_search_session(
        user_id="V04-USER", intent=intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )
    service.handle_user_message(session.search_session_id, message="생각해보니 급여계좌 바꿀 수 있을지 잘 모르겠어.")
    capability = next(
        item for item in service.get_search_intent(session.search_session_id).capabilities
        if item.capability_id == "CHANGE_SALARY_ACCOUNT"
    )
    assert capability.state == CapabilityState.UNKNOWN
    active = [
        fact for fact in service._runtime(session.search_session_id).fact_store.active_facts
        if fact.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
        and fact.source_type == FactSourceType.USER_DECLARED
    ]
    assert active == []


def test_product_choice_can_be_unset_and_question_becomes_needed_again():
    product = kakao_26_week_product()
    response = {
        "actions": [{
            "operation": "CLEAR_PRODUCT_CONTRIBUTION_CHOICE",
            "product_id": product.product_id,
            "field": "preferred_start_amount",
        }]
    }
    service, session, _ = _kakao_service(response)
    _answer_start(service, session.search_session_id, "3000")
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("3000")

    service.handle_user_message(session.search_session_id, message="아직 시작금액은 정하지 말자.")
    assert service.get_product_contribution_choices(session.search_session_id) == ()
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.question_kind == "RANKING_INPUT"
    assert question.ranking_input is not None
    assert question.ranking_input.required_field == "preferred_start_amount"


def test_preference_can_be_neutralized_by_natural_language():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {
                "upsert_preferences": [{
                    "field": "FIRST_TRANSACTION_BENEFIT",
                    "preference": "NEUTRAL",
                    "expected": True,
                }]
            },
        }]
    }
    orch, _ = _orchestrator(response)
    intent = make_intent(
        top_k=1,
        preferences=[Preference(
            field="FIRST_TRANSACTION_BENEFIT",
            preference=PreferenceValue.PREFER_PRESENT,
        )],
    )
    service = ApplicationService(
        [make_product("V046-PREF")], user_fact_stores={"V04-USER": base_store()}, conversation_orchestrator=orch
    )
    session = service.create_search_session(user_id="V04-USER", intent=intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    service.handle_user_message(session.search_session_id, message="첫거래 우대는 이제 상관없어.")
    assert service.get_search_intent(session.search_session_id).preferences[0].preference == PreferenceValue.NEUTRAL


def test_user_can_request_current_results_with_questions_remaining_and_resume():
    responses = (
        {"actions": [{"operation": "SHOW_CURRENT_RESULTS"}]},
        {"actions": [{"operation": "NO_OP"}]},
    )
    service, session, _ = _kakao_service(*responses)
    before_question = service.get_next_question(session.search_session_id)
    assert before_question is not None
    turn = service.handle_user_message(session.search_session_id, message="질문은 그만하고 지금 결과 보여줘.")
    assert turn.current_results_requested is True
    assert turn.recommendations is not None
    assert turn.next_question is None
    assert turn.unresolved_warning is not None
    # Persistent state still retains the material question.
    assert service.get_next_question(session.search_session_id) is not None

    resumed = service.handle_user_message(session.search_session_id, message="아까 질문 계속해도 돼.")
    assert resumed.current_results_requested is False
    assert resumed.next_question is not None


def test_current_results_request_does_not_mark_unknown_satisfied():
    response = {"actions": [{"operation": "SHOW_CURRENT_RESULTS"}]}
    service, session, _ = _kakao_service(response)
    before = {
        pid: candidate.product_evaluation.model_dump(mode="json")
        for pid, candidate in service.evaluate_candidates(session.search_session_id).items()
    }
    service.handle_user_message(session.search_session_id, message="일단 지금까지 결과만 보여줘.")
    after = {
        pid: candidate.product_evaluation.model_dump(mode="json")
        for pid, candidate in service.evaluate_candidates(session.search_session_id).items()
    }
    assert before == after


def test_working_note_created_and_updated_with_natural_language_turn():
    response = {"actions": [{"operation": "NO_OP"}]}
    service, session, _ = _kakao_service(response)
    initial = service.get_search_working_note(session.search_session_id)
    assert initial is not None and "Search Session Working Note" in initial
    service.handle_user_message(session.search_session_id, message="왜 이 상품이 1위야?")
    note = service.get_search_working_note(session.search_session_id)
    assert "왜 이 상품이 1위야?" in note
    assert "Mutable Search State" in note
    assert "Current Recommendation Summary" in note


def test_user_utterance_is_recorded_even_when_llm_turn_fails():
    # Missing required product_id makes LLM structured output validation fail.
    bad = {"actions": [{"operation": "CLEAR_PRODUCT_CONTRIBUTION_CHOICE", "field": "preferred_start_amount"}]}
    service, session, _ = _kakao_service(bad)
    with pytest.raises(Exception):
        service.handle_user_message(session.search_session_id, message="그건 아직 정하지 말자.")
    note = service.get_search_working_note(session.search_session_id)
    assert note is not None and "그건 아직 정하지 말자." in note


def test_file_working_note_store_atomic_persistence_and_deletion(tmp_path):
    store = FileSearchWorkingNoteStore(tmp_path)
    response = {"actions": [{"operation": "NO_OP"}]}
    service, session, _ = _kakao_service(response, working_note_store=store)
    path = tmp_path / f"{session.search_session_id}.md"
    assert path.exists()
    service.handle_user_message(session.search_session_id, message="계좌번호 1234567890123은 저장하지 말고 그대로 보여줘.")
    text = path.read_text(encoding="utf-8")
    assert "1234567890123" not in text
    assert "[REDACTED_" in text
    service.close_search_session(session.search_session_id)
    assert not path.exists()


def test_working_note_is_rebuilt_from_backend_state_before_llm_context():
    response = {"actions": [{"operation": "NO_OP"}]}
    orch, adapter = _orchestrator(response)
    store = InMemorySearchWorkingNoteStore()
    intent = make_intent(
        top_k=1,
        capabilities=[Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT)],
    )
    service = ApplicationService(
        [make_product("V046-STALE")],
        user_fact_stores={"V04-USER": base_store()},
        conversation_orchestrator=orch,
        working_note_store=store,
    )
    session = service.create_search_session(user_id="V04-USER", intent=intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    store.update_summary(search_session_id=session.search_session_id, summary={"mutable_search_state": {"stale": "CAN"}})
    service.handle_user_message(session.search_session_id, message="지금 상태 다시 확인해줘.")
    prompt = adapter.call_history[-1].messages[-1].content
    assert '"state": "CANNOT"' in prompt
    assert '"stale": "CAN"' not in prompt


def test_compaction_recovery_uses_working_note_plus_current_backend_state():
    product = kakao_26_week_product()
    responses = (
        {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": product.product_id, "field": "preferred_start_amount", "new_value": "3000"}]},
        {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": product.product_id, "field": "preferred_start_amount", "new_value": "2000"}]},
    )
    service, session, adapter = _kakao_service(*responses)
    _reach_start(service, session.search_session_id)
    service.handle_user_message(session.search_session_id, message="카카오는 3천원으로 할게.")
    # There is no raw chat-history dependency in ApplicationService. The next
    # prompt reconstructs continuity from Working Note + current backend state.
    service.handle_user_message(session.search_session_id, message="그거 2천원으로 바꿔줘.")
    prompt = adapter.call_history[-1].messages[-1].content
    assert "SEARCH_WORKING_NOTE" in prompt
    assert "카카오는 3천원으로 할게." in prompt
    assert '"value": "3000"' in prompt
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("2000")


def test_rest_close_search_session_deletes_working_note(tmp_path):
    store = FileSearchWorkingNoteStore(tmp_path)
    response = {"actions": [{"operation": "NO_OP"}]}
    service, session, _ = _kakao_service(response, working_note_store=store)
    rest = RestApplicationAdapter(service)
    result = rest.handle("DELETE", f"/search-sessions/{session.search_session_id}")
    assert result.status_code == 204
    assert not (tmp_path / f"{session.search_session_id}.md").exists()
    with pytest.raises(KeyError):
        service.get_search_status(session.search_session_id)


def test_clear_user_declared_fact_cannot_clear_authoritative_fact():
    response = {
        "actions": [{"operation": "CLEAR_USER_DECLARED_FACT", "fact_type": "AUTHORITATIVE_STATUS"}]
    }
    orch, _ = _orchestrator(response)
    from tests.v04_helpers import verified_fact
    service = ApplicationService(
        [make_product("V046-AUTH")],
        user_fact_stores={"V04-USER": base_store(verified_fact("AUTH-V046", "AUTHORITATIVE_STATUS", True))},
        conversation_orchestrator=orch,
    )
    session = service.create_search_session(user_id="V04-USER", intent=make_intent(top_k=1), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    with pytest.raises(ValueError, match="AUTHORITATIVE_FACT_IMMUTABLE_BY_USER"):
        service.handle_user_message(session.search_session_id, message="그 계좌 이력은 그냥 모르는 걸로 해줘.")
    active = [
        f for f in service._runtime(session.search_session_id).fact_store.active_facts
        if f.fact_type == "AUTHORITATIVE_STATUS"
    ]
    assert len(active) == 1 and active[0].value is True


def test_current_results_request_does_not_change_ranking_run_id():
    response = {"actions": [{"operation": "SHOW_CURRENT_RESULTS"}]}
    service, session, _ = _kakao_service(response)
    before = service.get_search_status(session.search_session_id).ranking_run_id
    turn = service.handle_user_message(session.search_session_id, message="더 물어보지 말고 현재 순위 보여줘.")
    after = service.get_search_status(session.search_session_id).ranking_run_id
    assert turn.recommendations is not None
    assert before == after
    assert service.get_search_status(session.search_session_id).status.value == "QUESTIONING"


def test_working_note_failure_is_not_financial_single_point_of_failure():
    class FailingStore(InMemorySearchWorkingNoteStore):
        def update_summary(self, *, search_session_id, summary):
            raise OSError("disk unavailable")

    response = {"actions": [{"operation": "NO_OP"}]}
    service, session, _ = _kakao_service(response, working_note_store=FailingStore())
    # Session creation and a conversation turn still succeed; finance state remains usable.
    result = service.handle_user_message(session.search_session_id, message="지금 상태 유지해줘.")
    assert result.session.search_session_id == session.search_session_id
    assert service.evaluate_candidates(session.search_session_id)


def test_rest_messages_can_return_current_results_with_pending_question():
    response = {"actions": [{"operation": "SHOW_CURRENT_RESULTS"}]}
    service, session, _ = _kakao_service(response)
    rest = RestApplicationAdapter(service)
    result = rest.handle(
        "POST",
        f"/search-sessions/{session.search_session_id}/messages",
        {"message": "질문은 그만하고 지금 결과 보여줘."},
    )
    assert result.status_code == 200
    assert result.body["current_results_requested"] is True
    assert result.body["recommendations"] is not None
    assert result.body["next_question"] is None


def test_mcp_reuses_application_service_for_working_note_session_close(tmp_path):
    from eligibility.adapters import MCPToolAdapter
    store = FileSearchWorkingNoteStore(tmp_path)
    response = {"actions": [{"operation": "NO_OP"}]}
    service, session, _ = _kakao_service(response, working_note_store=store)
    mcp = MCPToolAdapter(service)
    result = mcp.close_search_session(search_session_id=session.search_session_id)
    assert result["closed"] is True
    assert mcp.service is service
