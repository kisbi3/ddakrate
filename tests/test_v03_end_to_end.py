from __future__ import annotations

from decimal import Decimal

from eligibility.application import QuestionGenerator, submit_user_fact
from eligibility.audit import AuditEventType, AuditSession, InMemoryAuditSink
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.extraction import (
    salary_envelope_document_sections_payload,
    salary_envelope_extraction_draft,
)
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.goal import AlertType, GoalFactory, GoalStatus, GoalTracker
from eligibility.ingestion import (
    DocumentSection,
    DraftReviewState,
    RuleExtractor,
    activate_approved_draft,
    approve_rule_draft,
    product_definition_from_draft,
)
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.enums import EvaluationStatus


def _extracted_product(audit: AuditSession):
    draft = salary_envelope_extraction_draft()
    gateway = LLMGateway(
        MockLLMAdapter(
            {LLMPurpose.RULE_EXTRACTION: draft.model_dump(mode="json")}
        ),
        audit=audit,
    )
    extractor = RuleExtractor(gateway, audit=audit)
    sections = [
        DocumentSection.model_validate(item)
        for item in salary_envelope_document_sections_payload()
    ]
    record = extractor.extract(sections)
    approved = approve_rule_draft(
        record,
        reviewer_id="HUMAN-REVIEWER-001",
        note="Golden AST, required facts, service reference and provenance checked",
        audit=audit,
    )
    active, _ = activate_approved_draft(approved)
    assert active.state == DraftReviewState.ACTIVE
    assert active.draft is not None
    return product_definition_from_draft(active.draft)


def test_salary_envelope_6m_unknown_to_achievable():
    sink = InMemoryAuditSink()
    audit = AuditSession(
        sink,
        request_id="REQ-SALARY-E2E-001",
        trace_id="TRACE-SALARY-E2E-001",
    )
    product = _extracted_product(audit)
    context = salary_envelope_context()
    store = salary_envelope_user()
    engine = FinancialEligibilityEngine()

    before = engine.evaluate_product(product, store, context, audit=audit)
    before_rule = before.preferential_rule_results[0]
    question = QuestionGenerator().generate(before.missing_facts[0])

    assert before_rule.status == EvaluationStatus.UNKNOWN
    assert before_rule.progress is not None
    assert (before_rule.progress.current, before_rule.progress.required) == (0, 6)
    assert before.rates.realizable_rate == Decimal("3.05")
    assert "6개월" in question
    assert "+1.0%p" in question

    store = submit_user_fact(
        store,
        future_intent_fact(SALARY_INTENT_FACT_TYPE, True),
        audit=audit,
    )
    after = engine.evaluate_product(product, store, context, audit=audit)
    after_rule = after.preferential_rule_results[0]

    assert after_rule.status == EvaluationStatus.ACHIEVABLE
    assert after_rule.progress is not None
    assert (after_rule.progress.current, after_rule.progress.required) == (0, 6)
    assert after.rates.realizable_rate == Decimal("4.05")

    goal = GoalFactory(audit).create_from_evaluation(
        product=product,
        evaluation=after,
        context=context,
        subscription_id="SUB-SALARY-E2E-001",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]
    update = GoalTracker(audit).update_accumulative(
        goal,
        current_progress=4,
        as_of=context.subscription_date,
        remaining_opportunities=2,
    )

    assert update.goal.current_progress == 4
    assert update.goal.remaining_required == 2
    assert update.goal.remaining_opportunities == 2
    assert update.goal.buffer == 0
    assert update.goal.status == GoalStatus.AT_RISK
    assert update.alert is not None
    assert update.alert.alert_type == AlertType.CRITICAL


def test_end_to_end_trace_correlates_document_fact_rule_rate_goal_alert():
    sink = InMemoryAuditSink()
    audit = AuditSession(
        sink,
        request_id="REQ-CORRELATION-001",
        trace_id="TRACE-CORRELATION-001",
    )
    product = _extracted_product(audit)
    context = salary_envelope_context()
    engine = FinancialEligibilityEngine()

    initial = engine.evaluate_product(
        product, salary_envelope_user(), context, audit=audit
    )
    answered_store = submit_user_fact(
        salary_envelope_user(),
        future_intent_fact(SALARY_INTENT_FACT_TYPE, True),
        audit=audit,
    )
    reevaluated = engine.evaluate_product(
        product, answered_store, context, audit=audit
    )
    goal = GoalFactory(audit).create_from_evaluation(
        product=product,
        evaluation=reevaluated,
        context=context,
        subscription_id="SUB-CORRELATION-001",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]
    GoalTracker(audit).update_accumulative(
        goal,
        current_progress=4,
        as_of=context.subscription_date,
        remaining_opportunities=2,
    )

    assert initial.evaluation_id != reevaluated.evaluation_id
    assert {event.request_id for event in sink.events} == {"REQ-CORRELATION-001"}
    assert {event.trace_id for event in sink.events} == {"TRACE-CORRELATION-001"}
    assert {event.evaluation_id for event in sink.events if event.evaluation_id} == {
        initial.evaluation_id,
        reevaluated.evaluation_id,
    }

    event_types = [event.event_type for event in sink.events]
    required = {
        AuditEventType.DOCUMENT_PARSED,
        AuditEventType.LLM_CALL_STARTED,
        AuditEventType.LLM_CALL_COMPLETED,
        AuditEventType.SCHEMA_VALIDATION_COMPLETED,
        AuditEventType.SEMANTIC_VALIDATION_COMPLETED,
        AuditEventType.HUMAN_REVIEW_RECORDED,
        AuditEventType.REQUEST_RECEIVED,
        AuditEventType.RULE_LOADED,
        AuditEventType.FACT_REQUESTED,
        AuditEventType.FUTURE_CAPACITY_CHECKED,
        AuditEventType.MISSING_FACT_CREATED,
        AuditEventType.USER_FACT_RECEIVED,
        AuditEventType.STATUS_DERIVED,
        AuditEventType.RATE_SUMMARY_CREATED,
        AuditEventType.GOAL_CREATED,
        AuditEventType.GOAL_PROGRESS_UPDATED,
        AuditEventType.GOAL_FEASIBILITY_RECALCULATED,
        AuditEventType.ALERT_TRIGGERED,
    }
    assert required.issubset(set(event_types))

    # High-level stage ordering is append-only and identifies the failing layer.
    def first(event_type: AuditEventType) -> int:
        return event_types.index(event_type)

    assert first(AuditEventType.DOCUMENT_PARSED) < first(
        AuditEventType.LLM_CALL_STARTED
    ) < first(AuditEventType.LLM_CALL_COMPLETED)
    assert first(AuditEventType.LLM_CALL_COMPLETED) < first(
        AuditEventType.SCHEMA_VALIDATION_COMPLETED
    ) < first(AuditEventType.SEMANTIC_VALIDATION_COMPLETED)
    assert first(AuditEventType.MISSING_FACT_CREATED) < first(
        AuditEventType.USER_FACT_RECEIVED
    ) < first(AuditEventType.GOAL_CREATED)
    assert first(AuditEventType.GOAL_CREATED) < first(
        AuditEventType.ALERT_TRIGGERED
    )
