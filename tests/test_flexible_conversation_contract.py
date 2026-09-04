"""Contract tests for flexible conversation turns (design v2026-08-28).

These tests deliberately exercise the ApplicationService/orchestrator boundary.
They are expected to fail against the pre-flexible implementation until the
new turn-plan schema and ledger workflow are integrated.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from eligibility.application_service import ApplicationService
from eligibility.catalog import load_default_product_catalog
from eligibility.conversation import ConversationOrchestrator
from eligibility.llm import LLMGateway, MockLLMAdapter, LLMPurpose
from eligibility.schema.enums import ContributionFrequency, PreSearchAnswerStatus
from eligibility.schema.search import ContributionPlanPatch, IntentPatch

from tests.v04_helpers import intent_from_patch


def _service(*plans: dict[str, Any]):
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: list(plans)})
    service = ApplicationService(
        load_default_product_catalog(),
        conversation_orchestrator=ConversationOrchestrator(LLMGateway(adapter)),
        pre_search_enabled=True,
    )
    # "월 30만원씩 적금 찾아줘" (monthly 300,000 KRW installment savings) expressed
    # as a structured intent -- no LLM gateway is configured for intent parsing
    # in this test, only for conversation orchestration.
    intent = intent_from_patch(
        IntentPatch(
            upsert_product_types=["INSTALLMENT_SAVINGS"],
            contribution_plan_patch=ContributionPlanPatch(
                desired_periodic_amount=Decimal("300000"),
                frequency=ContributionFrequency.MONTHLY,
            ),
        ),
        user_id="FLEXIBLE-USER",
    )
    session = service.create_search_session(user_id="FLEXIBLE-USER", intent=intent)
    return service, session, adapter


def _pending(service: ApplicationService, session_id: str):
    question = service.get_next_question(session_id)
    assert question is not None
    return question


@pytest.mark.xfail(reason="turn-plan schema and ledger workflow not yet integrated", strict=True)
def test_unrelated_action_does_not_consume_active_pre_search_question():
    service, session, adapter = _service(
        {
            "actions": [{"operation": "SHOW_CURRENT_RESULTS"}],
            "assistant_message": "현재 후보를 먼저 보여드릴게요.",
        }
    )
    pending = _pending(service, session.search_session_id)

    turn = service.handle_user_message(
        session.search_session_id,
        message="일단 지금 결과 보여줘",
    )

    assert turn.current_results_requested is True
    assert service.get_next_question(session.search_session_id).question_id == pending.question_id
    assert len(adapter.call_history) == 1


@pytest.mark.xfail(reason="turn-plan schema and ledger workflow not yet integrated", strict=True)
def test_answer_and_action_are_applied_in_one_turn():
    service, session, _adapter = _service(
        {
            "profile_updates": [
                {
                    "question_key": "YOUTH_POLICY_ACCOUNT_HOLDING",
                    "resolution": "ANSWER",
                    "youth_policy_account_held": False,
                }
            ],
            "actions": [{"operation": "SHOW_CURRENT_RESULTS"}],
            "assistant_message": "조건을 반영해 결과를 갱신했어요.",
        }
    )
    service.handle_user_message(
        session.search_session_id,
        message="청년정책계좌는 없고 결과도 보여줘",
    )

    # The answer is verified through the public question lifecycle: the
    # unrelated action must not prevent the next unresolved question from
    # being selected.
    assert service.get_next_question(session.search_session_id) is not None


@pytest.mark.xfail(reason="turn-plan schema and ledger workflow not yet integrated", strict=True)
def test_duplicate_active_pre_search_answer_is_applied_once():
    service, session, _adapter = _service(
        {
            "profile_updates": [
                {
                    "question_key": "APPLICATION_CAPACITY",
                    "resolution": "ANSWER",
                    "application_capacity": "INDIVIDUAL",
                }
            ],
            "actions": [
                {
                    "operation": "SUBMIT_ACTIVE_QUESTION_ANSWER",
                    "field": "application_capacity",
                    "new_value": "INDIVIDUAL",
                    "answer": "INDIVIDUAL",
                }
            ],
            "assistant_message": "개인 명의로 반영했어요.",
        }
    )
    active = _pending(service, session.search_session_id)
    assert active.pre_search_key == "APPLICATION_CAPACITY"

    turn = service.handle_user_message(
        session.search_session_id,
        message="개인 명의로 가입할 상품을 찾고 있어요.",
    )

    runtime = service._runtime(session.search_session_id)
    assert runtime.intent.application_capacity == "INDIVIDUAL"
    assert (
        runtime.pre_search_profile["APPLICATION_CAPACITY"].answer_status
        == PreSearchAnswerStatus.ANSWERED
    )
    assert turn.operations_executed.count("SUBMIT_PRE_SEARCH_ANSWER") == 1
    assert "SUBMIT_ACTIVE_QUESTION_ANSWER" not in turn.operations_executed
    assert turn.next_question is not None
    assert turn.next_question.pre_search_key == "BIRTH_DATE"


@pytest.mark.xfail(reason="turn-plan schema and ledger workflow not yet integrated", strict=True)
def test_global_profile_answer_is_order_independent_of_displayed_question():
    service, session, _adapter = _service(
        {
            "profile_updates": [
                {
                    "question_key": "SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY",
                    "resolution": "ANSWER",
                    "soldier_tomorrow_savings_eligible": True,
                }
            ],
            "actions": [{"operation": "NO_OP"}],
        }
    )
    displayed = _pending(service, session.search_session_id)
    service.handle_user_message(
        session.search_session_id,
        message="장병내일준비적금 가입 자격은 있어요",
    )

    runtime = service._runtime(session.search_session_id)
    assert (
        runtime.pre_search_profile["SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY"].answer_status
        == PreSearchAnswerStatus.ANSWERED
    )
    assert service.get_next_question(session.search_session_id).question_id == displayed.question_id


@pytest.mark.xfail(reason="turn-plan schema and ledger workflow not yet integrated", strict=True)
def test_backend_automatically_presents_the_deterministic_active_question():
    service, session, _adapter = _service(
        {
            "actions": [{"operation": "NO_OP"}],
            "assistant_message": "알겠습니다.",
        }
    )
    question = _pending(service, session.search_session_id)
    turn = service.handle_user_message(
        session.search_session_id,
        message="알겠습니다",
    )

    assert turn.next_question is not None
    assert turn.next_question.question_id == question.question_id
    assert service.get_next_question(session.search_session_id).question_id == question.question_id


@pytest.mark.xfail(reason="turn-plan schema and ledger workflow not yet integrated", strict=True)
def test_llm_cannot_reorder_or_suppress_deterministic_questions():
    service, session, _adapter = _service(
        {
            "actions": [{"operation": "NO_OP"}],
        },
    )
    first = _pending(service, session.search_session_id)
    turn = service.handle_user_message(
        session.search_session_id,
        message="다른 질문부터 할게요",
    )
    assert turn.next_question is not None
    assert turn.next_question.question_id == first.question_id
    assert service.get_next_question(session.search_session_id).question_id == first.question_id
    assert (
        service._runtime(session.search_session_id)
        .pre_search_profile[first.pre_search_key]
        .answer_status
        == PreSearchAnswerStatus.NOT_ASKED
    )
