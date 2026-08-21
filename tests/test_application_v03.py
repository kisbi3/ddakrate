from __future__ import annotations

from eligibility.application import GeneratedQuestion, QuestionGenerator, submit_user_fact
from eligibility.audit import AuditEventType, AuditSession, InMemoryAuditSink
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.enums import FactSemanticType, ResolutionStrategy
from eligibility.schema.evaluation import MissingFactRequest


def _missing_request():
    evaluation = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
    )
    return evaluation.missing_facts[0]


def test_question_generator_deterministic_fallback_preserves_supplied_question():
    request = _missing_request()

    question = QuestionGenerator().generate(request)

    assert question == request.question
    assert "6개월" in question
    assert "+1.0%p" in question


def test_question_generator_uses_grounded_llm_wording():
    request = _missing_request()
    wording = "가입 후 월급봉투 인정조건을 6개월 이상 달성해 +1.0%p 우대를 목표로 관리할까요?"
    gateway = LLMGateway(
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

    question = QuestionGenerator(gateway).generate(request)

    assert question == wording


def test_question_generator_rejects_hallucinated_number_and_falls_back():
    request = _missing_request()
    gateway = LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.QUESTION_GENERATION: {
                    "question": "12개월 동안 +9.0%p를 받을 목표로 관리할까요?",
                    "fact_type": request.fact_type,
                    "rule_id": request.requested_by_rule_id,
                    "action_id": request.action_id,
                    "reward_id": request.reward_id,
                }
            }
        )
    )

    question = QuestionGenerator(gateway).generate(request)

    assert question == request.question
    assert "+9.0%p" not in question


def test_submit_user_fact_is_immutable_and_audited():
    original = salary_envelope_user()
    fact = future_intent_fact(SALARY_INTENT_FACT_TYPE, True)
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-ANSWER", trace_id="TRACE-ANSWER")

    updated = submit_user_fact(original, fact, audit=audit)

    assert original.facts != updated.facts
    assert fact not in original.facts
    assert fact in updated.facts
    event = next(
        item
        for item in sink.events
        if item.event_type == AuditEventType.USER_FACT_RECEIVED
    )
    assert event.payload["semantic_type"] == "FUTURE_INTENT"
    assert event.payload["source_type"] == "USER_DECLARED"


def test_question_generator_rejects_action_hallucination_with_same_numbers():
    request = _missing_request()
    wrong_action = "카드를 6개월 사용해 +1.0%p 우대를 목표로 관리할까요?"
    gateway = LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.QUESTION_GENERATION: GeneratedQuestion(
                    question=wrong_action,
                    fact_type=request.fact_type,
                    rule_id=request.requested_by_rule_id,
                    action_id=request.action_id,
                    reward_id=request.reward_id,
                )
            }
        )
    )

    question = QuestionGenerator(gateway).generate(request)

    assert question == request.question
    assert "월급봉투" in question
    assert "카드" not in question


def test_question_generator_historical_self_report_fallback_discloses_provisional_use():
    request = MissingFactRequest(
        fact_type="HISTORICAL_SHINHAN_SAVINGS_HELD",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        requested_by_rule_id="RULE-HISTORICAL-HOLDING",
        expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
    )

    question = QuestionGenerator().generate(request)

    assert "개인화 판정에 사용" in question
    assert "금융데이터 확인값과 구분" in question
    assert "기관 검증 정보가 아닙니다" in question
