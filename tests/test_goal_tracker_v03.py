from __future__ import annotations

from eligibility.audit import AuditEventType, AuditSession, InMemoryAuditSink
from eligibility.application import submit_user_fact
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    STEP_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
    step_30000_300d_product,
    step_30000_context,
    step_30000_user,
)
from eligibility.goal import (
    AlertSeverity,
    AlertType,
    GoalFactory,
    GoalStatus,
    GoalTracker,
)


def _step_goal(*, audit=None):
    product = step_30000_300d_product()
    context = step_30000_context()
    store = submit_user_fact(
        step_30000_user(),
        future_intent_fact(STEP_INTENT_FACT_TYPE, True),
        audit=audit,
    )
    evaluation = FinancialEligibilityEngine().evaluate_product(
        product, store, context, audit=audit
    )
    goal = GoalFactory(audit).create_from_evaluation(
        product=product,
        evaluation=evaluation,
        context=context,
        subscription_id="SUB-STEP-001",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]
    return goal, context


def _salary_goal(*, audit=None):
    product = salary_envelope_6m_product()
    context = salary_envelope_context()
    store = submit_user_fact(
        salary_envelope_user(),
        future_intent_fact(SALARY_INTENT_FACT_TYPE, True),
        audit=audit,
    )
    evaluation = FinancialEligibilityEngine().evaluate_product(
        product, store, context, audit=audit
    )
    goal = GoalFactory(audit).create_from_evaluation(
        product=product,
        evaluation=evaluation,
        context=context,
        subscription_id="SUB-SALARY-001",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]
    return goal, context


def test_accumulative_goal_progress():
    goal, context = _step_goal()

    updated = GoalTracker().update_accumulative(
        goal,
        current_progress=170,
        as_of=context.subscription_date,
        remaining_opportunities=150,
    )

    assert updated.goal.current_progress == 170
    assert updated.goal.remaining_required == 130


def test_goal_remaining_opportunities():
    goal, context = _step_goal()

    updated = GoalTracker().update_accumulative(
        goal,
        current_progress=170,
        as_of=context.subscription_date,
        remaining_opportunities=150,
    )

    assert updated.goal.remaining_opportunities == 150


def test_goal_buffer():
    goal, context = _step_goal()

    updated = GoalTracker().update_accumulative(
        goal,
        current_progress=170,
        as_of=context.subscription_date,
        remaining_opportunities=150,
    )

    assert updated.goal.buffer == 20
    assert updated.goal.status == GoalStatus.ACTIVE
    assert updated.alert is None


def test_goal_at_risk():
    goal, context = _salary_goal()

    updated = GoalTracker().update_accumulative(
        goal,
        current_progress=4,
        as_of=context.subscription_date,
        remaining_opportunities=2,
    )

    assert updated.goal.remaining_required == 2
    assert updated.goal.buffer == 0
    assert updated.goal.status == GoalStatus.AT_RISK
    assert updated.alert is not None
    assert updated.alert.alert_type == AlertType.CRITICAL
    assert updated.alert.severity == AlertSeverity.CRITICAL


def test_goal_failed_when_opportunities_insufficient():
    goal, context = _step_goal()

    updated = GoalTracker().update_accumulative(
        goal,
        current_progress=249,
        as_of=context.subscription_date,
        remaining_opportunities=50,
    )

    assert updated.goal.remaining_required == 51
    assert updated.goal.buffer == -1
    assert updated.goal.status == GoalStatus.FAILED
    assert updated.alert is not None
    assert updated.alert.alert_type == AlertType.FAILED


def test_goal_completed():
    goal, context = _step_goal()

    updated = GoalTracker().update_accumulative(
        goal,
        current_progress=300,
        as_of=context.subscription_date,
        remaining_opportunities=40,
    )

    assert updated.goal.status == GoalStatus.COMPLETED
    assert updated.goal.remaining_required == 0
    assert updated.alert is not None
    assert updated.alert.alert_type == AlertType.COMPLETED


def test_goal_creation_requires_subscription_confirmation_and_tracking_intent():
    product = step_30000_300d_product()
    context = step_30000_context()
    store = submit_user_fact(
        step_30000_user(), future_intent_fact(STEP_INTENT_FACT_TYPE, True)
    )
    evaluation = FinancialEligibilityEngine().evaluate_product(
        product, store, context
    )
    factory = GoalFactory()

    assert factory.create_from_evaluation(
        product=product,
        evaluation=evaluation,
        context=context,
        subscription_id="SUB-NO",
        subscription_confirmed=False,
        user_tracking_intent=True,
    ) == []
    assert factory.create_from_evaluation(
        product=product,
        evaluation=evaluation,
        context=context,
        subscription_id="SUB-NO",
        subscription_confirmed=True,
        user_tracking_intent=False,
    ) == []


def test_goal_and_alert_audit_events():
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-GOAL", trace_id="TRACE-GOAL")
    goal, context = _salary_goal(audit=audit)

    GoalTracker(audit).update_accumulative(
        goal,
        current_progress=4,
        as_of=context.subscription_date,
        remaining_opportunities=2,
    )

    event_types = [event.event_type for event in sink.events]
    assert AuditEventType.GOAL_CREATED in event_types
    assert AuditEventType.GOAL_PROGRESS_UPDATED in event_types
    assert AuditEventType.GOAL_FEASIBILITY_RECALCULATED in event_types
    assert AuditEventType.ALERT_TRIGGERED in event_types
    assert {event.trace_id for event in sink.events} == {"TRACE-GOAL"}
