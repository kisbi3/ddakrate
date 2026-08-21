from __future__ import annotations

from datetime import date, datetime, timezone

from eligibility.application import submit_user_fact
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.engine.temporal import project_remaining_opportunities
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
from eligibility.goal import GoalFactory
from eligibility.schema.enums import FactSemanticType, FactSourceType, PeriodUnit
from eligibility.schema.user_fact import DataCoverage, UserFact


def test_current_qualified_month_is_not_counted_as_future_opportunity():
    context = salary_envelope_context().model_copy(
        update={"as_of": salary_envelope_context().subscription_date}
    )
    event = UserFact(
        fact_id="SALARY-MONTH-1",
        user_id=salary_envelope_user().user_id,
        fact_type="SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED",
        value=True,
        valid_from=context.subscription_date,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_EVENT,
        collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    coverage = DataCoverage(
        coverage_id="SALARY-COV-CURRENT",
        user_id=salary_envelope_user().user_id,
        fact_domain="SHINHAN_SALARY_CLUB_ENVELOPE_EVENT",
        institution="SHINHAN_BANK",
        covered_from=context.subscription_date,
        covered_to=context.subscription_date,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
    )
    store = salary_envelope_user().model_copy(
        update={
            "facts": [
                *salary_envelope_user().facts,
                future_intent_fact(SALARY_INTENT_FACT_TYPE, True),
                event,
            ],
            "data_coverages": [coverage],
        },
        deep=True,
    )
    product = salary_envelope_6m_product()
    evaluation = FinancialEligibilityEngine().evaluate_product(product, store, context)
    rule = evaluation.preferential_rule_results[0]

    assert rule.progress is not None and rule.progress.current == 1
    assert rule.evidence["remaining_opportunities"] == 11
    goal = GoalFactory().create_from_evaluation(
        product=product,
        evaluation=evaluation,
        context=context,
        subscription_id="SUB-SALARY-CURRENT-MONTH",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]
    assert goal.current_progress == 1
    assert goal.remaining_opportunities == 11
    assert goal.qualified_opportunity_keys == ["2026-08"]


def test_current_qualified_day_is_not_counted_as_future_opportunity():
    base_context = step_30000_context()
    context = base_context.model_copy(update={"as_of": base_context.subscription_date})
    event = UserFact(
        fact_id="STEP-DAY-1",
        user_id=step_30000_user().user_id,
        fact_type="QUALIFIED_DAILY_STEP_METRIC",
        value={"steps": 30000},
        valid_from=context.subscription_date,
        source_type=FactSourceType.DERIVED,
        semantic_type=FactSemanticType.OBSERVED_EVENT,
        collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    coverage = DataCoverage(
        coverage_id="STEP-COV-CURRENT",
        user_id=step_30000_user().user_id,
        fact_domain="QUALIFIED_DAILY_STEP_METRIC",
        institution="HEALTH_SERVICE",
        covered_from=context.subscription_date,
        covered_to=context.subscription_date,
        source_type=FactSourceType.DERIVED,
    )
    store = step_30000_user().model_copy(
        update={
            "facts": [
                *step_30000_user().facts,
                future_intent_fact(STEP_INTENT_FACT_TYPE, True),
                event,
            ],
            "data_coverages": [coverage],
        },
        deep=True,
    )
    product = step_30000_300d_product()
    evaluation = FinancialEligibilityEngine().evaluate_product(product, store, context)
    goal = GoalFactory().create_from_evaluation(
        product=product,
        evaluation=evaluation,
        context=context,
        subscription_id="SUB-STEP-CURRENT-DAY",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]

    assert evaluation.preferential_rule_results[0].evidence["remaining_opportunities"] == 364
    assert goal.current_progress == 1
    assert goal.remaining_opportunities == 364


def test_multiple_qualified_months_do_not_reappear_in_future_projection():
    projection = project_remaining_opportunities(
        window_start=date(2026, 8, 1),
        deadline=date(2027, 7, 31),
        as_of=date(2026, 11, 15),
        subscription_date=date(2026, 8, 1),
        period=PeriodUnit.MONTH,
        qualifying_periods=["2026-08", "2026-09", "2026-10", "2026-11"],
    )

    assert projection.remaining_opportunities == 8
    assert "2026-11" not in projection.future_periods
    assert projection.future_periods[0] == "2026-12"


def test_deadline_bucket_is_available_only_until_it_qualifies():
    unqualified_day = project_remaining_opportunities(
        window_start=date(2026, 8, 20),
        deadline=date(2026, 8, 20),
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
        period=PeriodUnit.DAY,
    )
    qualified_day = project_remaining_opportunities(
        window_start=date(2026, 8, 20),
        deadline=date(2026, 8, 20),
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
        period=PeriodUnit.DAY,
        qualifying_periods=["2026-08-20"],
    )
    qualified_month = project_remaining_opportunities(
        window_start=date(2026, 8, 1),
        deadline=date(2026, 8, 31),
        as_of=date(2026, 8, 31),
        subscription_date=date(2026, 8, 1),
        period=PeriodUnit.MONTH,
        qualifying_periods=["2026-08"],
    )

    assert unqualified_day.remaining_opportunities == 1
    assert qualified_day.remaining_opportunities == 0
    assert qualified_month.remaining_opportunities == 0
