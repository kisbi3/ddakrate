from __future__ import annotations

import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(SRC), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from eligibility.adapters import RestApplicationAdapter
from eligibility.application_service import ApplicationService
from eligibility.conversation import ConversationOrchestrator
from eligibility.fixtures.kakao_26_week import USER_ID as KAKAO_USER_ID, kakao_26_week_product, kakao_pre_subscription_user
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.application_input import Capability
from eligibility.schema.enums import CapabilityState, ContributionFrequency, FactSourceType, RankingObjective, TermUnit
from eligibility.schema.search import ContributionPlan, ProductSearchIntent
from eligibility.working_note import FileSearchWorkingNoteStore
from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_intent, make_product, verified_fact

OUT = ROOT / "examples" / "v0.4.6" / "adversarial-results.json"


def orchestrator(*responses):
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: list(responses)})
    return ConversationOrchestrator(LLMGateway(adapter)), adapter


def kakao_intent(maximum="300000"):
    return ProductSearchIntent(
        search_intent_id=f"V046-ADV-{maximum}",
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
        source_utterances=["v0.4.6 adversarial"],
    )


def service(*responses, store=None, maximum="300000"):
    orch, adapter = orchestrator(*responses)
    svc = ApplicationService(
        [kakao_26_week_product()],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
        conversation_orchestrator=orch,
        working_note_store=store,
    )
    session = svc.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=kakao_intent(maximum),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    return svc, session, adapter


def reach_start(svc, sid):
    for _ in range(10):
        q = svc.get_next_question(sid)
        assert q is not None
        if q.question_kind == "RANKING_INPUT" and q.ranking_input and q.ranking_input.required_field == "preferred_start_amount":
            return q
        if q.question_kind == "FINANCIAL_FACT":
            svc.submit_user_answer(sid, question_id=q.question_id, answer=True)
            continue
        raise AssertionError(q)
    raise AssertionError("start amount question not reached")


def main():
    results = {}
    kakao = kakao_26_week_product()

    # CAN -> UNKNOWN
    response = {"actions": [{"operation": "CLEAR_USER_DECLARED_FACT", "fact_type": "SALARY_ACCOUNT_CHANGE_POSSIBLE"}]}
    orch, _ = orchestrator(response)
    intent = make_intent(top_k=1, capabilities=[Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN)])
    svc = ApplicationService([make_product("V046-CAP")], user_fact_stores={"V04-USER": base_store()}, conversation_orchestrator=orch)
    sess = svc.create_search_session(user_id="V04-USER", intent=intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    svc.handle_user_message(sess.search_session_id, message="생각해보니 급여계좌는 잘 모르겠어.")
    cap = next(x for x in svc.get_search_intent(sess.search_session_id).capabilities if x.capability_id == "CHANGE_SALARY_ACCOUNT")
    assert cap.state == CapabilityState.UNKNOWN
    results["capability_can_to_unknown"] = {"state": cap.state.value}

    # product choice set -> unset -> question again
    response = {"actions": [{"operation": "CLEAR_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount"}]}
    svc, sess, _ = service(response)
    q = reach_start(svc, sess.search_session_id)
    svc.submit_user_answer(sess.search_session_id, question_id=q.question_id, answer="3000")
    svc.handle_user_message(sess.search_session_id, message="아직 시작금액은 정하지 말자.")
    reopened = svc.get_next_question(sess.search_session_id)
    assert reopened and reopened.ranking_input and reopened.ranking_input.required_field == "preferred_start_amount"
    results["product_choice_unset"] = {"active_choice_count": len(svc.get_product_contribution_choices(sess.search_session_id)), "question_reopened": True}

    # current result request with material question remaining; ranking state does not change.
    response = {"actions": [{"operation": "SHOW_CURRENT_RESULTS"}]}
    svc, sess, _ = service(response)
    before_run = svc.get_search_status(sess.search_session_id).ranking_run_id
    turn = svc.handle_user_message(sess.search_session_id, message="질문은 그만하고 지금 결과 보여줘.")
    after_run = svc.get_search_status(sess.search_session_id).ranking_run_id
    assert turn.recommendations is not None and turn.next_question is None
    assert turn.unresolved_warning is not None and before_run == after_run
    assert svc.get_next_question(sess.search_session_id) is not None
    results["current_results_now"] = {"warning": turn.unresolved_warning, "ranking_run_unchanged": True, "question_retained": True}

    # Working note + compaction recovery.
    with tempfile.TemporaryDirectory() as tmp:
        store = FileSearchWorkingNoteStore(tmp)
        responses = (
            {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "3000"}]},
            {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "2000"}]},
        )
        svc, sess, adapter = service(*responses, store=store)
        reach_start(svc, sess.search_session_id)
        svc.handle_user_message(sess.search_session_id, message="카카오는 3천원으로 할게.")
        note_before = svc.get_search_working_note(sess.search_session_id)
        assert note_before and "카카오는 3천원으로 할게." in note_before
        svc.handle_user_message(sess.search_session_id, message="그거 2천원으로 바꿔줘.")
        prompt = adapter.call_history[-1].messages[-1].content
        assert "SEARCH_WORKING_NOTE" in prompt and '"value": "3000"' in prompt
        assert svc.get_product_contribution_choices(sess.search_session_id)[0].value == Decimal("2000")
        path = Path(tmp) / f"{sess.search_session_id}.md"
        assert path.exists()
        rest = RestApplicationAdapter(svc)
        closed = rest.handle("DELETE", f"/search-sessions/{sess.search_session_id}")
        assert closed.status_code == 204 and not path.exists()
        results["working_note_compaction"] = {"note_used": True, "choice_after_compaction": "2000", "deleted_on_close": True}

    # authoritative clear attempt is rejected.
    response = {"actions": [{"operation": "CLEAR_USER_DECLARED_FACT", "fact_type": "AUTHORITATIVE_STATUS"}]}
    orch, _ = orchestrator(response)
    auth_svc = ApplicationService(
        [make_product("V046-AUTH")],
        user_fact_stores={"V04-USER": base_store(verified_fact("V046-AUTH-F", "AUTHORITATIVE_STATUS", True))},
        conversation_orchestrator=orch,
    )
    auth_sess = auth_svc.create_search_session(user_id="V04-USER", intent=make_intent(top_k=1), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    try:
        auth_svc.handle_user_message(auth_sess.search_session_id, message="그 계좌 이력은 모르는 걸로 해줘.")
    except ValueError as exc:
        assert "AUTHORITATIVE_FACT_IMMUTABLE_BY_USER" in str(exc)
    else:
        raise AssertionError("authoritative fact was cleared")
    active = [f for f in auth_svc._runtime(auth_sess.search_session_id).fact_store.active_facts if f.fact_type == "AUTHORITATIVE_STATUS"]
    assert active and active[0].source_type == FactSourceType.INSTITUTION_VERIFIED
    results["authoritative_protection"] = {"protected": True}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"[PASS] v0.4.6 final conversational runtime adversarial -> {OUT}")


if __name__ == "__main__":
    main()
