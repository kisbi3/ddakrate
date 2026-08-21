from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from eligibility.application import (
    GeneratedQuestion,
    QuestionGenerator,
    UserAnswerMapper,
    UserAnswerSubmission,
    revise_user_answer,
    submit_user_answer,
)
from eligibility.audit import AuditEventType, AuditSession, InMemoryAuditSink
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.fixtures.shinhan_youth_first import golden_context, shinhan_youth_first_product
from eligibility.fixtures.user_001 import user_001
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.llm.grounding import build_question_payload
from eligibility.schema.enums import (
    EvaluationStatus,
    FactRecordStatus,
    FactSemanticType,
    FactSourceType,
    ResolutionStrategy,
)
from eligibility.schema.evaluation import MissingFactRequest, MissingFactImpact
from eligibility.schema.user_fact import UserFact

from tests.v04_helpers import base_store, make_product


def _salary_request() -> MissingFactRequest:
    result = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
    )
    return result.missing_facts[0]


def _gateway_for(request: MissingFactRequest, question: str) -> LLMGateway:
    return LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.QUESTION_GENERATION: GeneratedQuestion(
                    question=question,
                    fact_type=request.fact_type,
                    rule_id=request.requested_by_rule_id,
                    action_id=request.action_id,
                    reward_id=request.reward_id,
                )
            }
        )
    )


def test_question_generator_rejects_rate_unit_rebinding():
    request = _salary_request()
    hallucination = "월급봉투 인정조건을 6개월 달성하면 +6.0%p 우대를 목표로 할까요?"

    question = QuestionGenerator(_gateway_for(request, hallucination)).generate(request)

    assert question == QuestionGenerator.deterministic_fallback(request)
    assert "+6.0%p" not in question
    assert "%p" not in question
    payload = build_question_payload(request, request.question or "")
    assert any(claim.unit == "MONTH" and claim.value == "6" for claim in payload.claims)
    assert any(
        claim.unit == "PERCENTAGE_POINT" and claim.value == "1"
        for claim in payload.claims
    )


def test_question_generator_rejects_new_financial_condition():
    request = _salary_request()
    hallucination = "월급봉투와 신한카드를 6개월 유지해 +1.0%p 우대를 받을까요?"

    question = QuestionGenerator(_gateway_for(request, hallucination)).generate(request)

    assert question == QuestionGenerator.deterministic_fallback(request)
    assert "신한카드" not in question


def test_question_generator_uses_deterministic_fallback():
    request = _salary_request()
    gateway = LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.QUESTION_GENERATION: {
                    "question": "source에 없는 휴대폰 요금 조건을 추가할까요?",
                    "fact_type": request.fact_type,
                    "rule_id": request.requested_by_rule_id,
                    "action_id": request.action_id,
                    "reward_id": request.reward_id,
                }
            }
        )
    )

    fallback = QuestionGenerator.deterministic_fallback(request)
    assert QuestionGenerator(gateway).generate(request) == fallback
    assert QuestionGenerator().generate(request) == fallback
    assert "%p" not in fallback


def _revision_request() -> MissingFactRequest:
    return MissingFactRequest(
        missing_fact_id="MFR-REVISION-001",
        fact_type="REVISION_CAPABILITY",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        impact=MissingFactImpact(rate_pp=Decimal("1.0")),
        question="이 조건을 수행할 수 있나요? +1.0%p",
        requested_by_rule_id="RATE-REVISION",
        expected_semantic_type=FactSemanticType.FUTURE_INTENT,
        action_id="ACTION-REVISION",
        reward_id="REWARD-REVISION",
        grounding_terms=["조건"],
    )


def test_answer_revision_supersedes_prior_fact():
    request = _revision_request()
    store = base_store()
    ref = UserAnswerMapper.request_reference(request)
    first_at = datetime(2026, 8, 19, 10, tzinfo=timezone.utc)
    second_at = datetime(2026, 8, 19, 11, tzinfo=timezone.utc)

    store, first = submit_user_answer(
        store,
        request,
        UserAnswerSubmission(request_reference=ref, answer=False, answered_at=first_at),
    )
    store, second, history = revise_user_answer(
        store,
        request,
        UserAnswerSubmission(request_reference=ref, answer=True, answered_at=second_at),
    )

    assert first.value is False
    assert second.value is True
    assert [item.status for item in history] == [
        FactRecordStatus.SUPERSEDED,
        FactRecordStatus.ACTIVE,
    ]
    assert second.version == 2
    assert second.supersedes_fact_id == first.fact_id
    assert [fact.value for fact in store.active_facts if fact.fact_type == request.fact_type] == [
        True
    ]


def test_answer_revision_re_evaluates_without_conflict():
    product = make_product(
        "REVISION-PRODUCT",
        reward_pp="1.0",
        bonus_fact_type="REVISION_CAPABILITY",
        on_true_status=EvaluationStatus.ACHIEVABLE,
    )
    request = _revision_request()
    ref = UserAnswerMapper.request_reference(request)
    store = base_store()
    store, _ = submit_user_answer(
        store,
        request,
        UserAnswerSubmission(
            request_reference=ref,
            answer=False,
            answered_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
        ),
    )
    store, _, _ = revise_user_answer(
        store,
        request,
        UserAnswerSubmission(
            request_reference=ref,
            answer=True,
            answered_at=datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
        ),
    )

    from eligibility.search.contribution import ContributionPlanner, build_evaluation_context
    from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, make_intent

    intent = make_intent()
    plan = ContributionPlanner().build(product, intent.contribution_plan)
    result = FinancialEligibilityEngine().evaluate_product(
        product,
        store,
        build_evaluation_context(
            product,
            intent.contribution_plan,
            as_of=AS_OF,
            subscription_date=SUBSCRIPTION_DATE,
        ),
        contribution_plan=plan.core_plan,
    )

    assert result.preferential_rule_results[0].status == EvaluationStatus.ACHIEVABLE
    assert result.rates.realizable_rate == Decimal("3.0")
    assert all(item.status.value != "CONFLICT" for item in result.missing_facts)


def test_revision_history_remains_auditable():
    request = _revision_request()
    ref = UserAnswerMapper.request_reference(request)
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-V04-REV", trace_id="TRACE-V04-REV")
    store = base_store()
    store, _ = submit_user_answer(
        store,
        request,
        UserAnswerSubmission(
            request_reference=ref,
            answer=False,
            answered_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
        ),
        audit=audit,
    )
    store, active, records = revise_user_answer(
        store,
        request,
        UserAnswerSubmission(
            request_reference=ref,
            answer=True,
            answered_at=datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
        ),
        audit=audit,
    )

    assert len(records) == 2
    assert len(store.answer_history(ref)) == 2
    supersession = [
        event for event in sink.events if event.event_type == AuditEventType.USER_ANSWER_SUPERSEDED
    ]
    assert len(supersession) == 1
    assert supersession[0].payload["new_fact_id"] == active.fact_id


def test_shinhan_single_path_failure_does_not_close_unmodeled_paths():
    store = user_001()
    facts = [
        fact
        for fact in store.facts
        if fact.fact_type != "SALARY_ACCOUNT_CHANGE_POSSIBLE"
    ]
    facts.append(
        UserFact(
            fact_id="USER-DECLINED-SALARY-CHANGE",
            user_id=store.user_id,
            fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
            value=False,
            valid_from=golden_context().as_of,
            source_type=FactSourceType.USER_DECLARED,
            semantic_type=FactSemanticType.FUTURE_INTENT,
            collected_at=datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
        )
    )
    store = store.model_copy(update={"facts": facts}, deep=True)

    result = FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(), store, golden_context()
    )
    salary = next(
        item for item in result.preferential_rule_results if item.rule_id == "RATE_SALARY"
    )

    assert salary.status == EvaluationStatus.UNKNOWN
    assert salary.reason_code == "ALTERNATIVE_ACTION_PATH_NOT_EVALUATED"
    assert result.rates.realizable_rate < result.rates.user_specific_conditional_upper_rate
