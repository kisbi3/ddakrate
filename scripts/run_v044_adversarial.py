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

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, make_product, verified_fact

OUT = ROOT / "examples" / "v0.4.4"


def _orchestrator(*responses):
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: list(responses)})
    return ConversationOrchestrator(LLMGateway(adapter)), adapter


def _intent(maximum: str, *, capability: CapabilityState | None = None, top_k: int = 2):
    capabilities = []
    if capability is not None:
        capabilities.append(
            Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=capability)
        )
    return ProductSearchIntent(
        search_intent_id=f"INTENT-V044-ADV-{maximum}",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        capabilities=capabilities,
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


def _service(*products, maximum="300000", orchestrator=None, capability=None):
    products = list(products) or [kakao_26_week_product()]
    store = kakao_pre_subscription_user(intent=True)
    # Synthetic application products in combined scenarios use the standard ELIGIBLE fact.
    store = store.with_fact(
        verified_fact(
            "V044-ELIGIBLE",
            "ELIGIBLE",
            True,
            user_id=KAKAO_USER_ID,
        )
    )
    service = ApplicationService(
        products,
        user_fact_stores={KAKAO_USER_ID: store},
        conversation_orchestrator=orchestrator,
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_intent(maximum, capability=capability, top_k=len(products)),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    return service, session


def _answer_kakao_start(service: ApplicationService, session_id: str, value: str):
    for _ in range(10):
        question = service.get_next_question(session_id)
        assert question is not None
        if (
            question.question_kind == "RANKING_INPUT"
            and question.ranking_input is not None
            and question.ranking_input.required_field == "preferred_start_amount"
        ):
            service.submit_user_answer(session_id, question_id=question.question_id, answer=value)
            return question
        if question.question_kind == "FINANCIAL_FACT":
            service.submit_user_answer(session_id, question_id=question.question_id, answer=True)
            continue
        raise AssertionError(f"unexpected question before Kakao start amount: {question}")
    raise AssertionError("Kakao start amount question not reached")


def _snapshot(service: ApplicationService, session_id: str):
    runtime = service._runtime(session_id)
    return deepcopy(
        (
            runtime.session,
            runtime.intent,
            runtime.fact_store,
            runtime.product_contribution_choices,
            runtime.product_contribution_choice_history,
            runtime.active_question,
            runtime.evaluations,
            runtime.ranking,
        )
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {}
    kakao = kakao_26_week_product()

    # A. A previously committed product-specific contribution choice is revisionable,
    # but historical state remains append-only/auditable.
    service, session = _service(kakao, maximum="300000")
    _answer_kakao_start(service, session.search_session_id, "3000")
    before = service.evaluate_candidates(session.search_session_id)[kakao.product_id]
    old = service.get_product_contribution_choices(session.search_session_id)[0]
    service.revise_product_contribution_choice(
        session.search_session_id,
        product_id=kakao.product_id,
        field="preferred_start_amount",
        new_value="2000",
    )
    after = service.evaluate_candidates(session.search_session_id)[kakao.product_id]
    history = service.get_product_contribution_choice_history(session.search_session_id)
    assert history[0].record_status == "SUPERSEDED"
    assert history[-1].record_status == "ACTIVE" and history[-1].value == Decimal("2000")
    assert history[-1].supersedes_choice_id == old.choice_id
    assert after.estimated_total_principal < before.estimated_total_principal
    results["A_product_choice_revision"] = {
        "old": str(history[0].value),
        "old_status": history[0].record_status,
        "new": str(history[-1].value),
        "new_status": history[-1].record_status,
        "principal_before": str(before.estimated_total_principal),
        "principal_after": str(after.estimated_total_principal),
    }

    # B. A global affordability revision can stale the old choice. The backend
    # deterministically reopens a choice question with currently feasible options.
    largest_plan = {
        "actions": [
            {
                "operation": "REVISE_PRODUCT_CONTRIBUTION_CHOICE",
                "product_id": kakao.product_id,
                "field": "preferred_start_amount",
                "new_value": "3000",
            }
        ]
    }
    orch, llm_adapter = _orchestrator(largest_plan)
    stale_service, stale_session = _service(kakao, maximum="1000000", orchestrator=orch)
    original_question = _answer_kakao_start(stale_service, stale_session.search_session_id, "10000")
    stale_service.update_search_intent(
        stale_session.search_session_id,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                maximum_affordable_periodic_amount=Decimal("300000")
            )
        ),
    )
    reopened = stale_service.get_next_question(stale_session.search_session_id)
    assert reopened is not None and reopened.question_kind == "CONTRIBUTION_FEASIBILITY"
    clarification = reopened.feasibility_clarification
    assert clarification is not None
    assert clarification.feasible_options == ["1000", "2000", "3000"]
    assert original_question.question_id in stale_service.get_search_status(
        stale_session.search_session_id
    ).answered_question_ids
    stale_service.handle_user_message(stale_session.search_session_id, message="제일 큰 걸로.")
    active_choice = stale_service.get_product_contribution_choices(
        stale_session.search_session_id
    )[0]
    assert active_choice.value == Decimal("3000")
    assert any(
        e.event_type == AuditEventType.QUESTION_REOPENED
        for e in stale_service.get_evaluation_trace(stale_session.search_session_id)
    )
    prompt = llm_adapter.call_history[-1].messages[-1].content
    assert all(token in prompt for token in ("1000", "2000", "3000"))
    results["B_stale_choice_reopened"] = {
        "old_choice": "10000",
        "new_global_monthly_limit": "300000",
        "feasible_options": clarification.feasible_options,
        "natural_reply": "제일 큰 걸로.",
        "resolved_choice": str(active_choice.value),
        "question_reopened": True,
    }

    # C. One natural-language turn can change global affordability, a user-declared
    # capability, and a product choice. The LLM only routes operations; backend
    # validation/ranking remains deterministic. Intent/global changes are applied
    # before the dependent product choice revision in the transaction.
    salary = make_product(
        "V044-SALARY-BONUS",
        reward_pp="1.0",
        bonus_fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
        bonus_semantic_type=FactSemanticType.FUTURE_INTENT,
    )
    combined_plan = {
        "actions": [
            {
                "operation": "REVISE_PRODUCT_CONTRIBUTION_CHOICE",
                "product_id": kakao.product_id,
                "field": "preferred_start_amount",
                "new_value": "3000",
            },
            {
                "operation": "UPDATE_SEARCH_INTENT",
                "intent_patch": {
                    "upsert_capabilities": [
                        {"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CANNOT"}
                    ],
                    "contribution_plan_patch": {
                        "maximum_affordable_periodic_amount": "300000"
                    },
                },
            },
        ]
    }
    combined_orch, _ = _orchestrator(combined_plan)
    combined_service, combined_session = _service(
        kakao,
        salary,
        maximum="1000000",
        orchestrator=combined_orch,
        capability=CapabilityState.CAN,
    )
    _answer_kakao_start(combined_service, combined_session.search_session_id, "10000")
    old_run = combined_service.get_search_status(combined_session.search_session_id).ranking_run_id
    combined_service.handle_user_message(
        combined_session.search_session_id,
        message="생각해보니 월 30만원까지만 가능하고 급여계좌도 못 바꿀 것 같아. 카카오는 3천원으로 바꿔줘.",
    )
    updated_intent = combined_service.get_search_intent(combined_session.search_session_id)
    assert updated_intent.contribution_plan.maximum_affordable_periodic_amount == Decimal("300000")
    assert next(
        item for item in updated_intent.capabilities if item.capability_id == "CHANGE_SALARY_ACCOUNT"
    ).state == CapabilityState.CANNOT
    assert combined_service.get_product_contribution_choices(
        combined_session.search_session_id
    )[0].value == Decimal("3000")
    active_salary = [
        f
        for f in combined_service._runtime(combined_session.search_session_id).fact_store.active_facts
        if f.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
        and f.source_type == FactSourceType.USER_DECLARED
    ]
    assert [f.value for f in active_salary] == [False]
    new_run = combined_service.get_search_status(combined_session.search_session_id).ranking_run_id
    assert new_run != old_run
    results["C_combined_natural_revision"] = {
        "message": "생각해보니 월 30만원까지만 가능하고 급여계좌도 못 바꿀 것 같아. 카카오는 3천원으로 바꿔줘.",
        "monthly_limit": str(updated_intent.contribution_plan.maximum_affordable_periodic_amount),
        "salary_capability": "CANNOT",
        "kakao_choice": "3000",
        "single_active_salary_declaration": [f.value for f in active_salary],
        "ranking_recalculated": True,
    }

    # D. Invalid natural-language revision: LLM may misunderstand or choose an
    # invalid amount, but deterministic validation rejects it atomically.
    invalid_plan = {
        "actions": [
            {
                "operation": "REVISE_PRODUCT_CONTRIBUTION_CHOICE",
                "product_id": kakao.product_id,
                "field": "preferred_start_amount",
                "new_value": "7000",
            }
        ]
    }
    invalid_orch, _ = _orchestrator(invalid_plan)
    invalid_service, invalid_session = _service(
        kakao, maximum="300000", orchestrator=invalid_orch
    )
    _answer_kakao_start(invalid_service, invalid_session.search_session_id, "3000")
    state_before = _snapshot(invalid_service, invalid_session.search_session_id)
    try:
        invalid_service.handle_user_message(
            invalid_session.search_session_id, message="카카오는 7천원으로 바꿔줘."
        )
    except ValueError:
        pass
    else:
        raise AssertionError("invalid conversational revision unexpectedly committed")
    state_after = _snapshot(invalid_service, invalid_session.search_session_id)
    assert state_before == state_after
    assert invalid_service.get_product_contribution_choices(
        invalid_session.search_session_id
    )[0].value == Decimal("3000")
    assert any(
        e.event_type == AuditEventType.CONVERSATION_TURN_ROLLED_BACK
        for e in invalid_service.get_evaluation_trace(invalid_session.search_session_id)
    )
    results["D_invalid_revision_atomicity"] = {
        "requested": "7000",
        "active_choice_after_rejection": "3000",
        "business_state_unchanged": True,
        "rollback_audited": True,
    }

    # E. Authoritative observed/institution facts cannot be overwritten by a
    # conversational user revision operation.
    auth_product = make_product("V044-AUTH")
    auth_store = kakao_pre_subscription_user(intent=True).with_fact(
        verified_fact(
            "V044-AUTH-FACT",
            "AUTHORITATIVE_ACCOUNT_HISTORY",
            True,
            user_id=KAKAO_USER_ID,
            source_type=FactSourceType.INSTITUTION_VERIFIED,
            semantic_type=FactSemanticType.OBSERVED_FACT,
        )
    )
    auth_service = ApplicationService([auth_product], user_fact_stores={KAKAO_USER_ID: auth_store})
    auth_session = auth_service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_intent("300000", top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    try:
        auth_service.revise_user_declared_fact(
            auth_session.search_session_id,
            fact_type="AUTHORITATIVE_ACCOUNT_HISTORY",
            new_value=False,
        )
    except ValueError as exc:
        assert "AUTHORITATIVE_FACT_IMMUTABLE_BY_USER" in str(exc)
    else:
        raise AssertionError("authoritative fact unexpectedly superseded")
    assert any(
        e.event_type == AuditEventType.AUTHORITATIVE_REVISION_REJECTED
        for e in auth_service.get_evaluation_trace(auth_session.search_session_id)
    )
    results["E_authoritative_protection"] = {
        "authoritative_value": True,
        "user_overwrite_rejected": True,
    }

    # F. REST and MCP remain thin adapters over the same ApplicationService
    # conversational operation.
    rest_plan = {
        "actions": [
            {
                "operation": "UPDATE_SEARCH_INTENT",
                "intent_patch": {"ranking_objective_patch": "MAX_REALIZABLE_RATE"},
            }
        ]
    }
    rest_orch, _ = _orchestrator(rest_plan)
    rest_service, rest_session = _service(
        make_product("V044-REST"), maximum="300000", orchestrator=rest_orch
    )
    rest = RestApplicationAdapter(rest_service)
    rest_result = rest.handle(
        "POST",
        f"/search-sessions/{rest_session.search_session_id}/messages",
        {"message": "이제 금리 높은 순으로 다시 보여줘."},
    )
    assert rest_result.status_code == 200
    assert rest_service.get_search_intent(
        rest_session.search_session_id
    ).ranking_objective == RankingObjective.MAX_REALIZABLE_RATE

    mcp_plan = {
        "actions": [
            {
                "operation": "UPDATE_SEARCH_INTENT",
                "intent_patch": {"ranking_objective_patch": "MAX_REALIZABLE_RATE"},
            }
        ]
    }
    mcp_orch, _ = _orchestrator(mcp_plan)
    mcp_service, mcp_session = _service(
        make_product("V044-MCP"), maximum="300000", orchestrator=mcp_orch
    )
    mcp = MCPToolAdapter(mcp_service)
    mcp_body = mcp.handle_user_message(
        search_session_id=mcp_session.search_session_id,
        message="금리 기준으로 다시 찾아줘.",
    )
    assert mcp_body["operations_executed"] == ["UPDATE_SEARCH_INTENT"]
    assert mcp.service is mcp_service
    results["F_rest_mcp_thin_adapters"] = {
        "rest_status": rest_result.status_code,
        "rest_objective": "MAX_REALIZABLE_RATE",
        "mcp_operation": mcp_body["operations_executed"],
        "same_application_service": True,
    }

    output = OUT / "adversarial-results.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"v0.4.4 adversarial scenarios passed -> {output}")


if __name__ == "__main__":
    main()
