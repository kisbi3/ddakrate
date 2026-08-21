from __future__ import annotations

import json
import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(SRC), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

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
from eligibility.schema.enums import CapabilityState, ContributionFrequency, FactSemanticType, FactSourceType, RankingObjective, TermUnit
from eligibility.schema.search import ContributionPlan, ProductSearchIntent
from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, make_product, verified_fact

OUT = ROOT / "examples" / "v0.4.5" / "adversarial-results.json"


def orchestrator(*responses):
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: list(responses)})
    return ConversationOrchestrator(LLMGateway(adapter)), adapter


def intent(maximum="300000", *, capability=None, top_k=2):
    caps = [] if capability is None else [Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=capability)]
    return ProductSearchIntent(
        search_intent_id=f"V045-ADV-{maximum}-{capability}",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        capabilities=caps,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            maximum_affordable_periodic_amount=Decimal(maximum),
            frequency=ContributionFrequency.MONTHLY,
            selected_term_value=26,
            selected_term_unit=TermUnit.WEEK,
        ),
        requested_top_k=top_k,
        source_utterances=["v0.4.5 adversarial"],
    )


def service(*responses, maximum="300000", products=None, capability=None):
    orch, adapter = orchestrator(*responses)
    products = products or [kakao_26_week_product()]
    store = kakao_pre_subscription_user(intent=True).with_fact(
        verified_fact("V045-ELIG", "ELIGIBLE", True, user_id=KAKAO_USER_ID)
    )
    svc = ApplicationService(products, user_fact_stores={KAKAO_USER_ID: store}, conversation_orchestrator=orch)
    session = svc.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=intent(maximum, capability=capability, top_k=max(1, len(products))),
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
    raise AssertionError("Kakao start question not reached")


def answer_start(svc, sid, value):
    q = reach_start(svc, sid)
    svc.submit_user_answer(sid, question_id=q.question_id, answer=value)


def snapshot(svc, sid):
    r = svc._runtime(sid)
    return deepcopy((r.session, r.intent, r.fact_store, r.product_contribution_choices, r.product_contribution_choice_history, r.active_question, r.evaluations, r.ranking))


def main():
    results = {}
    kakao = kakao_26_week_product()

    # 1 capability CAN -> CANNOT -> CAN through one conversational entrypoint.
    responses = [
        {"actions": [{"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"upsert_capabilities": [{"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CAN"}]}}]},
        {"actions": [{"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"upsert_capabilities": [{"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CANNOT"}]}}]},
        {"actions": [{"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"upsert_capabilities": [{"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CAN"}]}}]},
    ]
    svc, sess, _ = service(*responses)
    for msg in ("급여계좌 변경 가능해.", "아니 생각해보니 안 될 것 같아.", "다시 확인했는데 가능해."):
        svc.handle_user_message(sess.search_session_id, message=msg)
    active = [f.value for f in svc._runtime(sess.search_session_id).fact_store.active_facts if f.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE" and f.source_type == FactSourceType.USER_DECLARED]
    assert active == [True]
    results["capability_can_cannot_can"] = {"active": active}

    # 2 exclude -> include.
    responses = [
        {"actions": [{"operation": "SET_PRODUCT_EXCLUSION", "product_id": kakao.product_id, "excluded": True}]},
        {"actions": [{"operation": "SET_PRODUCT_EXCLUSION", "product_id": kakao.product_id, "excluded": False}]},
    ]
    svc, sess, _ = service(*responses)
    svc.handle_user_message(sess.search_session_id, message="카카오는 빼줘.")
    assert kakao.product_id not in svc.get_search_status(sess.search_session_id).candidate_product_ids
    svc.handle_user_message(sess.search_session_id, message="아까 카카오 뺀 건 취소. 다시 같이 보여줘.")
    assert kakao.product_id in svc.get_search_status(sess.search_session_id).candidate_product_ids
    results["exclude_include"] = {"restored": True}

    # 3 decline -> explicit value without a reopen operation.
    set_after_decline = {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "2000"}]}
    svc, sess, _ = service(set_after_decline)
    q = reach_start(svc, sess.search_session_id)
    svc.submit_user_answer(sess.search_session_id, question_id=q.question_id, answer="답변 안함")
    svc.handle_user_message(sess.search_session_id, message="아까는 답 안 했는데 카카오는 2천원으로 할게.")
    assert svc.get_product_contribution_choices(sess.search_session_id)[0].value == Decimal("2000")
    results["decline_then_value"] = {"active_choice": "2000"}

    # 4 repeated product choice mutation.
    responses = [
        {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "3000"}]},
        {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "2000"}]},
    ]
    svc, sess, _ = service(*responses, maximum="1000000")
    answer_start(svc, sess.search_session_id, "10000")
    svc.handle_user_message(sess.search_session_id, message="카카오는 3천원으로 바꿔줘.")
    svc.handle_user_message(sess.search_session_id, message="아니 2천원으로 할게.")
    hist = svc.get_product_contribution_choice_history(sess.search_session_id)
    assert [str(x.value) for x in hist] == ["10000", "3000", "2000"]
    results["choice_10000_3000_2000"] = {"history": [str(x.value) for x in hist], "active": "2000"}

    # 5 one utterance changes four mutable state entries and triggers full search.
    salary = make_product("V045-SALARY", reward_pp="1.0", bonus_fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE", bonus_semantic_type=FactSemanticType.FUTURE_INTENT)
    combined = {"actions": [
        {"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"upsert_capabilities": [{"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CANNOT"}, {"capability_id": "NEW_CARD_ISSUANCE", "state": "CANNOT"}], "contribution_plan_patch": {"desired_periodic_amount": "300000", "maximum_affordable_periodic_amount": "300000"}}},
        {"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "3000"},
    ]}
    svc, sess, _ = service(combined, maximum="1000000", products=[kakao, salary], capability=CapabilityState.CAN)
    answer_start(svc, sess.search_session_id, "10000")
    old_run = svc.get_search_status(sess.search_session_id).ranking_run_id
    svc.handle_user_message(sess.search_session_id, message="생각해보니 월 30만원까지만 하고, 급여계좌는 못 바꾸겠고, 카카오는 3천원으로 해. 카드도 새로 만들기 싫어.")
    new_run = svc.get_search_status(sess.search_session_id).ranking_run_id
    assert old_run != new_run
    results["four_changes_one_turn"] = {"ranking_recalculated": True, "choice": "3000"}

    # 6 invalid one of multiple changes => entire business state rolls back.
    invalid = {"actions": [
        {"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"contribution_plan_patch": {"desired_periodic_amount": "200000", "maximum_affordable_periodic_amount": "200000"}}},
        {"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "7000"},
    ]}
    svc, sess, _ = service(invalid)
    answer_start(svc, sess.search_session_id, "3000")
    before = snapshot(svc, sess.search_session_id)
    try:
        svc.handle_user_message(sess.search_session_id, message="월 최대 20만원으로 줄이고 카카오는 7천원으로 해.")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid combined turn committed")
    assert snapshot(svc, sess.search_session_id) == before
    results["invalid_multi_change_rollback"] = {"rolled_back": True}

    # 7 authoritative fact protection.
    auth_product = make_product("V045-AUTH")
    auth_store = kakao_pre_subscription_user(intent=True).with_fact(verified_fact("AUTH", "AUTHORITATIVE_HISTORY", True, user_id=KAKAO_USER_ID, source_type=FactSourceType.INSTITUTION_VERIFIED, semantic_type=FactSemanticType.OBSERVED_FACT))
    auth_plan = {"actions": [{"operation": "REVISE_USER_DECLARED_FACT", "fact_type": "AUTHORITATIVE_HISTORY", "new_value": False}]}
    orch, _ = orchestrator(auth_plan)
    auth_svc = ApplicationService([auth_product], user_fact_stores={KAKAO_USER_ID: auth_store}, conversation_orchestrator=orch)
    auth_sess = auth_svc.create_search_session(user_id=KAKAO_USER_ID, intent=intent("300000", top_k=1), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    try:
        auth_svc.handle_user_message(auth_sess.search_session_id, message="그 과거 금융이력은 없었던 걸로 해줘.")
    except ValueError as exc:
        assert "AUTHORITATIVE_FACT_IMMUTABLE_BY_USER" in str(exc)
    else:
        raise AssertionError("authoritative state overwritten")
    results["authoritative_protection"] = {"protected": True}

    # 8 context-dependent pronoun and 9 option-relative expression: backend supplies context/options, LLM routes only.
    pronoun = {"actions": [{"operation": "SET_PRODUCT_CONTRIBUTION_CHOICE", "product_id": kakao.product_id, "field": "preferred_start_amount", "new_value": "3000"}]}
    svc, sess, adapter = service(pronoun)
    q = reach_start(svc, sess.search_session_id)
    assert q.ranking_input.allowed_options == ["1000", "2000", "3000"]
    svc.handle_user_message(sess.search_session_id, message="그거 제일 큰 걸로.")
    prompt = adapter.call_history[-1].messages[-1].content
    assert "RECENT_PRODUCT_FOCUS" in prompt and all(v in prompt for v in ("1000", "2000", "3000"))
    assert svc.get_product_contribution_choices(sess.search_session_id)[0].value == Decimal("3000")
    results["context_relative_expression"] = {"message": "그거 제일 큰 걸로.", "resolved": "3000"}

    # 10 audit shows current-state update -> search recalculation.
    events = svc.get_evaluation_trace(sess.search_session_id)
    assert any(e.event_type == AuditEventType.MUTABLE_SEARCH_STATE_UPDATED for e in events)
    assert any(e.event_type == AuditEventType.SEARCH_RECALCULATED for e in events)
    results["audit_chain"] = {"mutable_update": True, "search_recalculated": True}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"[PASS] v0.4.5 conversational adversarial scenarios -> {OUT}")


if __name__ == "__main__":
    main()
