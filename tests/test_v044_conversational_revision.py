from __future__ import annotations

from copy import deepcopy
from datetime import date
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
from eligibility.schema.application_input import Capability, HardConstraint, Preference
from eligibility.schema.enums import (
    CapabilityState,
    ContributionFrequency,
    FactSemanticType,
    FactSourceType,
    HardConstraintValue,
    PreferenceValue,
    RankingObjective,
    TermUnit,
)
from eligibility.schema.search import (
    ContributionPlan,
    ContributionPlanPatch,
    IntentPatch,
    ProductSearchIntent,
)
from eligibility.schema.user_fact import UserFactStore

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_intent, make_product, verified_fact


def _orchestrator(*responses):
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: list(responses)})
    return ConversationOrchestrator(LLMGateway(adapter)), adapter


def _kakao_intent(maximum: str = "300000", *, capability: CapabilityState | None = None):
    capabilities = []
    if capability is not None:
        capabilities.append(Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=capability))
    return ProductSearchIntent(
        search_intent_id=f"INTENT-V044-{maximum}",
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
        requested_top_k=2,
        source_utterances=[f"월 최대 {maximum}원"],
    )


def _kakao_service(*, maximum="300000", orchestrator=None):
    product = kakao_26_week_product()
    service = ApplicationService(
        [product],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
        conversation_orchestrator=orchestrator,
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(maximum),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    return service, session, product


def _answer_kakao_start(service: ApplicationService, session_id: str, value: str):
    for _ in range(8):
        question = service.get_next_question(session_id)
        assert question is not None
        if (
            question.question_kind == "RANKING_INPUT"
            and question.ranking_input is not None
            and question.ranking_input.required_field == "preferred_start_amount"
        ):
            service.submit_user_answer(session_id, question_id=question.question_id, answer=value)
            return question
        # Kakao future-intent questions are valid user-declared booleans.
        if question.question_kind == "FINANCIAL_FACT":
            service.submit_user_answer(session_id, question_id=question.question_id, answer=True)
            continue
        raise AssertionError(f"Unexpected question before Kakao start amount: {question}")
    raise AssertionError("Kakao preferred_start_amount question not reached")


def test_product_contribution_choice_can_be_revised():
    service, session, product = _kakao_service(maximum="300000")
    _answer_kakao_start(service, session.search_session_id, "3000")
    service.revise_product_contribution_choice(
        session.search_session_id,
        product_id=product.product_id,
        field="preferred_start_amount",
        new_value="2000",
    )
    active = service.get_product_contribution_choices(session.search_session_id)
    assert len(active) == 1
    assert active[0].value == Decimal("2000")
    assert active[0].version == 2


def test_revised_choice_supersedes_old_choice():
    service, session, product = _kakao_service(maximum="300000")
    _answer_kakao_start(service, session.search_session_id, "3000")
    old = service.get_product_contribution_choices(session.search_session_id)[0]
    service.revise_product_contribution_choice(
        session.search_session_id,
        product_id=product.product_id,
        field="preferred_start_amount",
        new_value="1000",
    )
    history = service.get_product_contribution_choice_history(session.search_session_id)
    assert [(item.value, item.record_status, item.version) for item in history] == [
        (Decimal("3000"), "SUPERSEDED", 1),
        (Decimal("1000"), "ACTIVE", 2),
    ]
    assert history[1].supersedes_choice_id == old.choice_id
    events = service.get_evaluation_trace(session.search_session_id)
    assert any(e.event_type == AuditEventType.PRODUCT_CONTRIBUTION_CHOICE_SUPERSEDED for e in events)


def test_revised_choice_recalculates_interest():
    service, session, product = _kakao_service(maximum="300000")
    _answer_kakao_start(service, session.search_session_id, "3000")
    before = service.evaluate_candidates(session.search_session_id)[product.product_id]
    service.revise_product_contribution_choice(
        session.search_session_id,
        product_id=product.product_id,
        field="preferred_start_amount",
        new_value="1000",
    )
    after = service.evaluate_candidates(session.search_session_id)[product.product_id]
    assert after.estimated_total_principal < before.estimated_total_principal
    assert after.realizable_after_tax_interest < before.realizable_after_tax_interest


def test_revised_choice_reranks_product():
    product = kakao_26_week_product()
    competitor = make_product("REVISION-COMPETITOR", base_rate="0.4")
    store = kakao_pre_subscription_user(intent=True).with_fact(
        verified_fact("REVISION-ELIGIBLE", "ELIGIBLE", True, user_id=KAKAO_USER_ID)
    )
    service = ApplicationService(
        [product, competitor],
        user_fact_stores={KAKAO_USER_ID: store},
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent("300000").model_copy(update={"requested_top_k": 2}, deep=True),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    _answer_kakao_start(service, session.search_session_id, "3000")
    before_run = service.get_search_status(session.search_session_id).ranking_run_id
    before = service.get_top_recommendations(session.search_session_id)
    assert before.top_products[0].product_id == product.product_id

    service.revise_product_contribution_choice(
        session.search_session_id,
        product_id=product.product_id,
        field="preferred_start_amount",
        new_value="2000",
    )
    after_run = service.get_search_status(session.search_session_id).ranking_run_id
    after = service.get_top_recommendations(session.search_session_id)
    assert before_run != after_run
    assert after.top_products[0].product_id == competitor.product_id


def test_invalid_revision_is_atomic():
    service, session, product = _kakao_service(maximum="300000")
    _answer_kakao_start(service, session.search_session_id, "3000")
    runtime = service._runtime(session.search_session_id)
    before = deepcopy(
        (
            runtime.session,
            runtime.intent,
            runtime.product_contribution_choices,
            runtime.product_contribution_choice_history,
            runtime.active_question,
            runtime.evaluations,
            runtime.ranking,
        )
    )
    with pytest.raises(ValueError):
        service.revise_product_contribution_choice(
            session.search_session_id,
            product_id=product.product_id,
            field="preferred_start_amount",
            new_value="7000",
        )
    after = (
        runtime.session,
        runtime.intent,
        runtime.product_contribution_choices,
        runtime.product_contribution_choice_history,
        runtime.active_question,
        runtime.evaluations,
        runtime.ranking,
    )
    assert before == after


def test_global_affordability_change_revalidates_product_choices():
    service, session, product = _kakao_service(maximum="1000000")
    _answer_kakao_start(service, session.search_session_id, "10000")
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                maximum_affordable_periodic_amount=Decimal("300000")
            )
        ),
    )
    candidate = service.evaluate_candidates(session.search_session_id)[product.product_id]
    clarification = candidate.contribution_feasibility_clarification
    assert clarification is not None
    assert clarification.reason_code == "STALE_PRODUCT_CONTRIBUTION_CHOICE"
    assert clarification.current_choice_value == Decimal("10000")


def test_stale_choice_generates_new_valid_options():
    service, session, _product = _kakao_service(maximum="1000000")
    original = _answer_kakao_start(service, session.search_session_id, "10000")
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                maximum_affordable_periodic_amount=Decimal("300000")
            )
        ),
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.question_kind == "CONTRIBUTION_FEASIBILITY"
    clarification = question.feasibility_clarification
    assert clarification is not None
    assert clarification.feasible_options == ["1000", "2000", "3000"]
    assert "CHANGE_PRODUCT_CONTRIBUTION_CHOICE" in clarification.allowed_resolutions
    assert original.question_id in service.get_search_status(session.search_session_id).answered_question_ids


def test_stale_choice_can_be_revised_instead_of_excluding_product():
    service, session, product = _kakao_service(maximum="1000000")
    _answer_kakao_start(service, session.search_session_id, "10000")
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                maximum_affordable_periodic_amount=Decimal("300000")
            )
        ),
    )
    question = service.get_next_question(session.search_session_id)
    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer={"resolution": "CHANGE_PRODUCT_CONTRIBUTION_CHOICE", "new_value": "3000"},
    )
    assert product.product_id in service.get_search_status(session.search_session_id).candidate_product_ids
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("3000")


def test_valid_existing_choice_is_not_reasked():
    service, session, _product = _kakao_service(maximum="300000")
    original = _answer_kakao_start(service, session.search_session_id, "3000")
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            upsert_preferences=[
                Preference(field="INCREMENTAL_CONTRIBUTION", preference=PreferenceValue.PREFER_PRESENT)
            ]
        ),
    )
    question = service.get_next_question(session.search_session_id)
    if question is not None:
        assert not (
            question.question_kind in {"RANKING_INPUT", "CONTRIBUTION_FEASIBILITY"}
            and (
                (question.ranking_input is not None and question.ranking_input.required_field == "preferred_start_amount")
                or (
                    question.feasibility_clarification is not None
                    and question.feasibility_clarification.field == "preferred_start_amount"
                )
            )
        )
    assert original.question_id in service.get_search_status(session.search_session_id).answered_question_ids


def test_answered_question_can_reopen_after_dependency_change():
    service, session, _product = _kakao_service(maximum="1000000")
    original = _answer_kakao_start(service, session.search_session_id, "10000")
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                maximum_affordable_periodic_amount=Decimal("300000")
            )
        ),
    )
    reopened = service.get_next_question(session.search_session_id)
    assert reopened is not None and reopened.question_kind == "CONTRIBUTION_FEASIBILITY"
    assert reopened.question_id != original.question_id
    events = service.get_evaluation_trace(session.search_session_id)
    assert any(e.event_type == AuditEventType.QUESTION_REOPENED for e in events)


def test_irrelevant_old_question_does_not_reopen():
    service, session, _product = _kakao_service(maximum="1000000")
    _answer_kakao_start(service, session.search_session_id, "3000")
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                maximum_affordable_periodic_amount=Decimal("500000")
            )
        ),
    )
    question = service.get_next_question(session.search_session_id)
    assert question is None or question.question_kind != "CONTRIBUTION_FEASIBILITY"


def test_user_cannot_supersede_observed_fact():
    product = make_product("AUTH-OBS")
    store = base_store(
        verified_fact(
            "OBS-1",
            "PAST_SHINHAN_HOLDING",
            True,
            semantic_type=FactSemanticType.OBSERVED_FACT,
            source_type=FactSourceType.MYDATA_VERIFIED,
        )
    )
    service = ApplicationService([product], user_fact_stores={"V04-USER": store})
    session = service.create_search_session(
        user_id="V04-USER", intent=make_intent(top_k=1), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )
    with pytest.raises(ValueError, match="AUTHORITATIVE_FACT_IMMUTABLE_BY_USER"):
        service.revise_user_declared_fact(
            session.search_session_id,
            fact_type="PAST_SHINHAN_HOLDING",
            new_value=False,
        )
    assert any(
        e.event_type == AuditEventType.AUTHORITATIVE_REVISION_REJECTED
        for e in service.get_evaluation_trace(session.search_session_id)
    )


def test_user_cannot_supersede_institution_verified_fact():
    product = make_product("AUTH-INST")
    store = base_store(
        verified_fact(
            "INST-1",
            "INSTITUTION_COUPON_STATUS",
            True,
            source_type=FactSourceType.INSTITUTION_VERIFIED,
        )
    )
    service = ApplicationService([product], user_fact_stores={"V04-USER": store})
    session = service.create_search_session(
        user_id="V04-USER", intent=make_intent(top_k=1), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )
    with pytest.raises(ValueError, match="AUTHORITATIVE_FACT_IMMUTABLE_BY_USER"):
        service.revise_user_declared_fact(
            session.search_session_id,
            fact_type="INSTITUTION_COUPON_STATUS",
            new_value=False,
        )


def _simple_conversation_service(response, *, products=None, intent=None):
    orch, adapter = _orchestrator(response)
    products = products or [make_product("CHAT-P")]
    service = ApplicationService(
        products,
        user_fact_stores={"V04-USER": base_store()},
        conversation_orchestrator=orch,
    )
    session = service.create_search_session(
        user_id="V04-USER",
        intent=intent or make_intent(top_k=len(products)),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    return service, session, adapter


def test_natural_language_changes_global_contribution():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {"contribution_plan_patch": {"maximum_affordable_periodic_amount": "300000"}},
        }]
    }
    service, session, _ = _simple_conversation_service(response)
    service.handle_user_message(session.search_session_id, message="생각해보니 월 50만원은 부담돼. 30만원까지만.")
    assert service.get_search_intent(session.search_session_id).contribution_plan.maximum_affordable_periodic_amount == Decimal("300000")


def test_natural_language_changes_desired_contribution_from_context():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {"contribution_plan_patch": {"desired_periodic_amount": "200000"}},
        }]
    }
    service, session, _ = _simple_conversation_service(response)
    service.handle_user_message(session.search_session_id, message="30만원 말고 20만원.")
    assert service.get_search_intent(
        session.search_session_id
    ).contribution_plan.desired_periodic_amount == Decimal("200000")


def test_natural_language_changes_product_specific_choice():
    response = {
        "actions": [{
            "operation": "REVISE_PRODUCT_CONTRIBUTION_CHOICE",
            "product_id": kakao_26_week_product().product_id,
            "field": "preferred_start_amount",
            "new_value": "2000",
        }]
    }
    orch, _ = _orchestrator(response)
    service, session, product = _kakao_service(maximum="300000", orchestrator=orch)
    _answer_kakao_start(service, session.search_session_id, "3000")
    service.handle_user_message(session.search_session_id, message="카카오는 아까 3천원이라고 했는데 2천원으로 바꿔줘.")
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("2000")


def test_natural_language_changes_capability():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {"upsert_capabilities": [{"capability_id": "CHANGE_SALARY_ACCOUNT", "state": "CANNOT"}]},
        }]
    }
    intent = make_intent(
        top_k=1,
        capabilities=[Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN)],
    )
    service, session, _ = _simple_conversation_service(response, intent=intent)
    service.handle_user_message(session.search_session_id, message="급여계좌는 역시 못 바꿀 것 같아.")
    assert service.get_search_intent(session.search_session_id).capabilities[0].state == CapabilityState.CANNOT
    runtime = service._runtime(session.search_session_id)
    active = [
        f for f in runtime.fact_store.active_facts
        if f.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE" and f.source_type == FactSourceType.USER_DECLARED
    ]
    assert [f.value for f in active] == [False]


def test_natural_language_changes_supersol_capability():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {
                "upsert_capabilities": [
                    {"capability_id": "JOIN_SUPERSOL", "state": "CAN"}
                ]
            },
        }]
    }
    service, session, _ = _simple_conversation_service(response)
    service.handle_user_message(session.search_session_id, message="SuperSOL 가입은 할 수 있을 것 같아.")
    capability = next(
        item
        for item in service.get_search_intent(session.search_session_id).capabilities
        if item.capability_id == "JOIN_SUPERSOL"
    )
    assert capability.state == CapabilityState.CAN


def test_natural_language_changes_new_card_capability():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {
                "upsert_capabilities": [
                    {"capability_id": "NEW_CARD_ISSUANCE", "state": "CANNOT"}
                ]
            },
        }]
    }
    intent = make_intent(
        top_k=1,
        capabilities=[
            Capability(capability_id="NEW_CARD_ISSUANCE", state=CapabilityState.CAN)
        ],
    )
    service, session, _ = _simple_conversation_service(response, intent=intent)
    service.handle_user_message(session.search_session_id, message="새 카드는 이제 만들기 싫어.")
    capability = next(
        item
        for item in service.get_search_intent(session.search_session_id).capabilities
        if item.capability_id == "NEW_CARD_ISSUANCE"
    )
    assert capability.state == CapabilityState.CANNOT


def test_natural_language_changes_preference():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {"remove_preference_keys": ["FIRST_TRANSACTION_BENEFIT"]},
        }]
    }
    intent = make_intent(
        top_k=1,
        preferences=[Preference(field="FIRST_TRANSACTION_BENEFIT", preference=PreferenceValue.PREFER_PRESENT)],
    )
    service, session, _ = _simple_conversation_service(response, intent=intent)
    service.handle_user_message(session.search_session_id, message="첫거래 우대는 이제 상관없어.")
    assert service.get_search_intent(session.search_session_id).preferences == []


def test_natural_language_changes_term_constraint():
    p12 = make_product("TERM-12", term_value=12)
    p6 = make_product("TERM-6", term_value=6)
    initial = make_intent(
        top_k=2,
        hard_constraints=[
            HardConstraint(field="TERM_MONTHS", constraint=HardConstraintValue.REQUIRE, expected=12)
        ],
    )
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {
                "remove_hard_constraint_keys": ["TERM_MONTHS"],
                "upsert_hard_constraints": [
                    {"field": "MAX_TERM_MONTHS", "constraint": "REQUIRE", "expected": 12}
                ],
            },
        }]
    }
    service, session, _ = _simple_conversation_service(response, products=[p12, p6], intent=initial)
    assert "TERM-6" not in service.get_search_status(session.search_session_id).candidate_product_ids
    service.handle_user_message(session.search_session_id, message="12개월만 보려고 했는데 6개월 상품도 괜찮아.")
    assert {"TERM-12", "TERM-6"} <= set(service.get_search_status(session.search_session_id).candidate_product_ids)


def test_natural_language_changes_ranking_objective():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {"ranking_objective_patch": "MAX_REALIZABLE_RATE"},
        }]
    }
    service, session, _ = _simple_conversation_service(response)
    service.handle_user_message(session.search_session_id, message="이제 금리 높은 순으로 보여줘.")
    assert service.get_search_intent(session.search_session_id).ranking_objective == RankingObjective.MAX_REALIZABLE_RATE


def test_conversational_combined_revision_revalidates_and_reranks():
    kakao = kakao_26_week_product()
    salary = make_product(
        "SALARY-BONUS",
        reward_pp="1.0",
        bonus_fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
        bonus_semantic_type=FactSemanticType.FUTURE_INTENT,
    )
    store = kakao_pre_subscription_user(intent=True)
    store = store.with_fact(
        verified_fact(
            "ELIGIBLE-CHAT",
            "ELIGIBLE",
            True,
            user_id=KAKAO_USER_ID,
        )
    )
    response = {
        "actions": [
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
            {
                "operation": "REVISE_PRODUCT_CONTRIBUTION_CHOICE",
                "product_id": kakao.product_id,
                "field": "preferred_start_amount",
                "new_value": "3000",
            },
        ]
    }
    orch, _adapter = _orchestrator(response)
    service = ApplicationService(
        [kakao, salary],
        user_fact_stores={KAKAO_USER_ID: store},
        conversation_orchestrator=orch,
    )
    intent = _kakao_intent("1000000", capability=CapabilityState.CAN)
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    _answer_kakao_start(service, session.search_session_id, "10000")
    before_run = service.get_search_status(session.search_session_id).ranking_run_id

    turn = service.handle_user_message(
        session.search_session_id,
        message="생각해보니 월 30만원까지만 가능하고 급여계좌도 못 바꿀 것 같아. 카카오는 3천원으로 바꿔줘.",
    )
    updated = service.get_search_intent(session.search_session_id)
    assert updated.contribution_plan.maximum_affordable_periodic_amount == Decimal("300000")
    assert next(c for c in updated.capabilities if c.capability_id == "CHANGE_SALARY_ACCOUNT").state == CapabilityState.CANNOT
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("3000")
    active_salary = [
        f for f in service._runtime(session.search_session_id).fact_store.active_facts
        if f.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE" and f.source_type == FactSourceType.USER_DECLARED
    ]
    assert [f.value for f in active_salary] == [False]
    assert service.get_search_status(session.search_session_id).ranking_run_id != before_run
    assert turn.recommendations is not None
    assert turn.recommendations.top_products


def test_largest_feasible_option_natural_language_uses_current_clarification_context():
    kakao = kakao_26_week_product()
    response = {
        "actions": [{
            "operation": "REVISE_PRODUCT_CONTRIBUTION_CHOICE",
            "product_id": kakao.product_id,
            "field": "preferred_start_amount",
            "new_value": "3000",
        }]
    }
    orch, adapter = _orchestrator(response)
    service, session, _ = _kakao_service(maximum="1000000", orchestrator=orch)
    _answer_kakao_start(service, session.search_session_id, "10000")
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(maximum_affordable_periodic_amount=Decimal("300000"))
        ),
    )
    active = service.get_next_question(session.search_session_id)
    assert active.feasibility_clarification.feasible_options == ["1000", "2000", "3000"]
    service.handle_user_message(session.search_session_id, message="제일 큰 걸로.")
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("3000")
    prompt = adapter.call_history[-1].messages[-1].content
    assert "1000" in prompt and "2000" in prompt and "3000" in prompt


def test_invalid_conversational_revision_rolls_back_business_state():
    kakao = kakao_26_week_product()
    response = {
        "actions": [{
            "operation": "REVISE_PRODUCT_CONTRIBUTION_CHOICE",
            "product_id": kakao.product_id,
            "field": "preferred_start_amount",
            "new_value": "7000",
        }]
    }
    orch, _ = _orchestrator(response)
    service, session, _ = _kakao_service(maximum="300000", orchestrator=orch)
    _answer_kakao_start(service, session.search_session_id, "3000")
    runtime = service._runtime(session.search_session_id)
    before = deepcopy((runtime.session, runtime.intent, runtime.product_contribution_choices, runtime.evaluations, runtime.ranking))
    with pytest.raises(ValueError):
        service.handle_user_message(session.search_session_id, message="카카오 7천원으로 바꿔줘.")
    after = (runtime.session, runtime.intent, runtime.product_contribution_choices, runtime.evaluations, runtime.ranking)
    assert before == after


def test_rest_conversational_follow_up_endpoint():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {"ranking_objective_patch": "MAX_REALIZABLE_RATE"},
        }]
    }
    service, session, _ = _simple_conversation_service(response)
    rest = RestApplicationAdapter(service)
    result = rest.handle(
        "POST",
        f"/search-sessions/{session.search_session_id}/messages",
        {"message": "금리 높은 순으로 다시 보여줘."},
    )
    assert result.status_code == 200
    assert result.body["operations_executed"] == ["UPDATE_SEARCH_INTENT"]
    assert service.get_search_intent(session.search_session_id).ranking_objective == RankingObjective.MAX_REALIZABLE_RATE


def test_mcp_reuses_same_conversational_application_service():
    response = {
        "actions": [{
            "operation": "UPDATE_SEARCH_INTENT",
            "intent_patch": {"ranking_objective_patch": "MAX_REALIZABLE_RATE"},
        }]
    }
    service, session, _ = _simple_conversation_service(response)
    mcp = MCPToolAdapter(service)
    body = mcp.handle_user_message(
        search_session_id=session.search_session_id,
        message="이제 금리 기준으로 다시 찾아줘.",
    )
    assert body["operations_executed"] == ["UPDATE_SEARCH_INTENT"]
    assert mcp.service is service
