from __future__ import annotations

from decimal import Decimal

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
from eligibility.schema.evaluation import MissingFactImpact, MissingFactRequest


def _missing_request():
    evaluation = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
    )
    return evaluation.missing_facts[0]


def test_question_generator_deterministic_fallback_preserves_condition_not_rate_copy():
    request = _missing_request()

    question = QuestionGenerator().generate(request)

    assert "6개월" in question
    assert "월급봉투" in question
    assert "%p" not in question
    assert "목표로 관리" not in question


def test_question_generator_uses_grounded_llm_wording():
    request = _missing_request()
    wording = "가입 후 월급봉투 실적을 6개월 이상 꾸준히 채울 수 있으세요?"
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

    assert question == QuestionGenerator.deterministic_fallback(request)
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

    assert question == QuestionGenerator.deterministic_fallback(request)
    assert "월급봉투" in question
    assert "카드" not in question


def test_fractional_payment_question_is_rewritten_for_conversation():
    request = MissingFactRequest(
        missing_fact_id="MFR-PAYMENT-FRACTION",
        fact_type="WILL_ACHIEVE_PAYMENT_MONTHS",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        impact=MissingFactImpact(rate_pp=Decimal("4.0")),
        question="계약월수 2/3 이상 납입월 달성을 목표로 관리할까요?",
        requested_by_rule_id="RATE-PAYMENT-FRACTION",
        expected_semantic_type=FactSemanticType.FUTURE_INTENT,
        grounding_terms=["계약월수 2/3 이상 납입월 달성"],
    )

    question = QuestionGenerator().generate(request)

    assert question == "가입 기간 동안 3개월 중 2개월 이상 꾸준히 납입할 수 있으세요?"
    assert "%p" not in question


def test_repeated_daily_deposit_question_uses_one_high_water_mark():
    request = MissingFactRequest(
        missing_fact_id="MFR-DAILY-DEPOSIT-31",
        fact_type="WILL_DAILY_DEPOSIT",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        impact=MissingFactImpact(rate_pp=Decimal("0.15")),
        question="직접 입금 31일차 우대을 목표로 관리할까요?",
        requested_by_rule_id="RATE-DAILY-DEPOSIT-31",
        expected_semantic_type=FactSemanticType.FUTURE_INTENT,
        grounding_terms=["직접 입금 31일차 우대"],
    )

    question = QuestionGenerator().generate(request)

    assert question == "가입 기간 동안 매일 직접 입금해 31일 이상 채울 수 있으세요?"
    assert "우대" not in question


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
