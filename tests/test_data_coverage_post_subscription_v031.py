from __future__ import annotations

from datetime import date
from decimal import Decimal

from eligibility.application import submit_user_fact
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.goal import AlertType, GoalFactory, GoalStatus, GoalTracker
from eligibility.schema.enums import (
    ComparisonOperator,
    ContextDateField,
    EntityType,
    EvaluationPhase,
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    PeriodUnit,
    RulePurpose,
)
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.product import PreferentialRateRule, ProductDefinition, Reward
from eligibility.schema.rule import (
    CountDistinctPeriodsRule,
    CoverageRequirement,
    DateExpression,
    FactAcceptancePolicy,
    FactComparisonRule,
    TimeWindow,
)
from eligibility.schema.user_fact import DataCoverage, UserFact, UserFactStore


USER_ID = "COVERAGE-U001"
DOMAIN = "POST_SUBSCRIPTION_EVENT_DOMAIN"


def _product() -> ProductDefinition:
    return ProductDefinition(
        product_id="COVERAGE-PRODUCT",
        institution_id="TEST_BANK",
        name="Coverage Test Product",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=1,
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("3.0"),
        preferential_rate_cap=Decimal("1.0"),
        eligibility_rule=FactComparisonRule(
            rule_id="ELIG-COVERAGE",
            name="가입 가능",
            purpose=RulePurpose.ELIGIBILITY,
            fact_type="ELIGIBLE",
            operator=ComparisonOperator.EQ,
            expected=True,
        ),
        preferential_rules=[
            PreferentialRateRule(
                rule=CountDistinctPeriodsRule(
                    rule_id="RATE-COVERAGE",
                    name="관측 이벤트 1일",
                    purpose=RulePurpose.PREFERENTIAL_RATE,
                    evaluation_phase=EvaluationPhase.POST_SUBSCRIPTION,
                    period=PeriodUnit.DAY,
                    fact_type="QUALIFIED_EVENT",
                    window=TimeWindow(
                        start=DateExpression.context(
                            ContextDateField.SUBSCRIPTION_DATE
                        ),
                        end=DateExpression.context(ContextDateField.MATURITY_DATE),
                    ),
                    operator=ComparisonOperator.GTE,
                    expected=1,
                    fact_acceptance_policy=FactAcceptancePolicy(
                        strict_semantic_types=[FactSemanticType.OBSERVED_EVENT],
                        strict_source_types=[FactSourceType.INSTITUTION_VERIFIED],
                    ),
                    coverage=CoverageRequirement(
                        fact_domain=DOMAIN,
                        institution="TEST_BANK",
                    ),
                ),
                reward=Reward(value=Decimal("1.0")),
            )
        ],
    )


def _context() -> EvaluationContext:
    return EvaluationContext(
        as_of=date(2026, 1, 31),
        subscription_date=date(2026, 1, 1),
        maturity_date=date(2026, 1, 31),
    )


def _base_store(*, covered: bool) -> UserFactStore:
    coverage = []
    if covered:
        coverage = [
            DataCoverage(
                coverage_id="COV-FULL",
                user_id=USER_ID,
                fact_domain=DOMAIN,
                institution="TEST_BANK",
                covered_from=date(2026, 1, 1),
                covered_to=date(2026, 1, 31),
                source_type=FactSourceType.INSTITUTION_VERIFIED,
            )
        ]
    return UserFactStore(
        user_id=USER_ID,
        facts=[
            UserFact(
                fact_id="ELIG-COVERAGE-F",
                user_id=USER_ID,
                fact_type="ELIGIBLE",
                value=True,
                source_type=FactSourceType.INSTITUTION_VERIFIED,
                semantic_type=FactSemanticType.OBSERVED_FACT,
            )
        ],
        data_coverages=coverage,
    )


def test_no_event_in_covered_interval_means_observed_zero_not_sync_failure():
    result = FinancialEligibilityEngine().evaluate_product(
        _product(), _base_store(covered=True), _context()
    )
    rule = result.preferential_rule_results[0]

    assert rule.status == EvaluationStatus.UNSATISFIABLE
    assert rule.reason_code == "DISTINCT_PERIOD_COUNT_NOT_REACHED"
    assert rule.progress is not None and rule.progress.current == 0
    assert rule.evidence["coverage_complete"] is True
    assert rule.evidence["observation_state"] == "OBSERVED"


def test_no_event_in_uncovered_interval_requires_data_sync():
    result = FinancialEligibilityEngine().evaluate_product(
        _product(), _base_store(covered=False), _context()
    )
    rule = result.preferential_rule_results[0]

    assert rule.status == EvaluationStatus.UNKNOWN
    assert rule.reason_code == "DATA_SYNC_REQUIRED"
    assert rule.evidence["coverage_complete"] is False
    assert result.missing_facts[0].fact_type == f"DATA_COVERAGE:{DOMAIN}"


def test_goal_tracker_uses_data_sync_required_alert_and_preserves_progress():
    product = salary_envelope_6m_product()
    context = salary_envelope_context()
    store = submit_user_fact(
        salary_envelope_user(), future_intent_fact(SALARY_INTENT_FACT_TYPE, True)
    )
    evaluation = FinancialEligibilityEngine().evaluate_product(product, store, context)
    goal = GoalFactory().create_from_evaluation(
        product=product,
        evaluation=evaluation,
        context=context,
        subscription_id="SUB-COVERAGE-GOAL",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]

    update = GoalTracker().update_accumulative(
        goal,
        current_progress=3,
        as_of=context.subscription_date,
        coverage_complete=False,
    )

    assert update.goal.current_progress == goal.current_progress
    assert update.goal.qualified_opportunity_keys == goal.qualified_opportunity_keys
    assert update.goal.status == GoalStatus.PAUSED
    assert update.goal.data_sync_required is True
    assert update.alert is not None
    assert update.alert.alert_type == AlertType.DATA_SYNC_REQUIRED
    assert update.alert.trigger_reason == "OBSERVATION_DATA_SYNC_UNAVAILABLE"
