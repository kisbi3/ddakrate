from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.schema.enums import (
    ComparisonOperator,
    ContextDateField,
    EntityType,
    EvaluationPhase,
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    PeriodUnit,
    RecurringPaymentCategory,
    RecurringPaymentMethod,
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
    ValuePredicate,
)
from eligibility.schema.user_fact import (
    DataCoverage,
    RecurringPaymentEvent,
    UserFact,
    UserFactStore,
)


USER_ID = "RECUR-U001"
DOMAIN = "QUALIFIED_RECURRING_PAYMENT"


def _event(
    event_id: str,
    occurred_at: datetime,
    *,
    category: RecurringPaymentCategory = RecurringPaymentCategory.UTILITY,
    method: RecurringPaymentMethod = RecurringPaymentMethod.AUTO_DEBIT,
    settlement_bank: str = "TARGET_BANK",
) -> RecurringPaymentEvent:
    return RecurringPaymentEvent(
        event_id=event_id,
        user_id=USER_ID,
        occurred_at=occurred_at,
        amount=Decimal("55000"),
        category=category,
        payment_method=method,
        source_account="ACCOUNT-001",
        institution="UTILITY_PROVIDER",
        settlement_bank=settlement_bank,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_EVENT,
    )


def test_utility_auto_debit_event_schema_round_trip():
    event = _event(
        "RECUR-E-1", datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)
    )

    payload = event.model_dump(mode="json")
    restored = RecurringPaymentEvent.model_validate(payload)

    assert payload["category"] == "UTILITY"
    assert payload["payment_method"] == "AUTO_DEBIT"
    assert restored.settlement_bank == "TARGET_BANK"
    assert restored.semantic_type == FactSemanticType.OBSERVED_EVENT


def test_recurring_payment_events_work_with_distinct_month_aggregation():
    eligibility = FactComparisonRule(
        rule_id="ELIG-RECUR",
        name="가입 가능",
        purpose=RulePurpose.ELIGIBILITY,
        fact_type="ELIGIBLE",
        operator=ComparisonOperator.EQ,
        expected=True,
    )
    recurring_rule = CountDistinctPeriodsRule(
        rule_id="RATE-UTILITY-AUTO-DEBIT-2M",
        name="공과금 자동납부 2개월",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        evaluation_phase=EvaluationPhase.POST_SUBSCRIPTION,
        period=PeriodUnit.MONTH,
        fact_type=DOMAIN,
        event_entity=EntityType.RECURRING_PAYMENT_EVENT,
        filters=[
            ValuePredicate(
                path="category",
                operator=ComparisonOperator.EQ,
                expected="UTILITY",
            ),
            ValuePredicate(
                path="payment_method",
                operator=ComparisonOperator.EQ,
                expected="AUTO_DEBIT",
            ),
            ValuePredicate(
                path="settlement_bank",
                operator=ComparisonOperator.EQ,
                expected="TARGET_BANK",
            ),
        ],
        window=TimeWindow(
            start=DateExpression.context(ContextDateField.SUBSCRIPTION_DATE),
            end=DateExpression.context(ContextDateField.MATURITY_DATE),
        ),
        operator=ComparisonOperator.GTE,
        expected=2,
        fact_acceptance_policy=FactAcceptancePolicy(
            strict_semantic_types=[FactSemanticType.OBSERVED_EVENT],
            strict_source_types=[FactSourceType.INSTITUTION_VERIFIED],
        ),
        coverage=CoverageRequirement(
            fact_domain=DOMAIN,
            institution="TARGET_BANK",
        ),
    )
    product = ProductDefinition(
        product_id="RECUR-PRODUCT",
        institution_id="TARGET_BANK",
        name="Recurring Payment Product",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=3,
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("3.0"),
        preferential_rate_cap=Decimal("1.0"),
        eligibility_rule=eligibility,
        preferential_rules=[
            PreferentialRateRule(
                rule=recurring_rule, reward=Reward(value=Decimal("1.0"))
            )
        ],
    )
    store = UserFactStore(
        user_id=USER_ID,
        facts=[
            UserFact(
                fact_id="RECUR-ELIG",
                user_id=USER_ID,
                fact_type="ELIGIBLE",
                value=True,
                source_type=FactSourceType.INSTITUTION_VERIFIED,
                semantic_type=FactSemanticType.OBSERVED_FACT,
            )
        ],
        recurring_payment_events=[
            _event("RECUR-E-JAN", datetime(2026, 1, 10, tzinfo=timezone.utc)),
            _event("RECUR-E-FEB", datetime(2026, 2, 10, tzinfo=timezone.utc)),
            _event(
                "RECUR-E-TELECOM",
                datetime(2026, 3, 10, tzinfo=timezone.utc),
                category=RecurringPaymentCategory.TELECOM,
            ),
        ],
        data_coverages=[
            DataCoverage(
                coverage_id="RECUR-COV",
                user_id=USER_ID,
                fact_domain=DOMAIN,
                institution="TARGET_BANK",
                covered_from=date(2026, 1, 1),
                covered_to=date(2026, 3, 31),
                source_type=FactSourceType.INSTITUTION_VERIFIED,
            )
        ],
    )
    context = EvaluationContext(
        as_of=date(2026, 3, 31),
        subscription_date=date(2026, 1, 1),
        maturity_date=date(2026, 3, 31),
    )

    result = FinancialEligibilityEngine().evaluate_product(product, store, context)
    rule = result.preferential_rule_results[0]

    assert rule.status == EvaluationStatus.SATISFIED
    assert rule.progress is not None and rule.progress.current == 2
    assert rule.evidence["qualifying_periods"] == ["2026-01", "2026-02"]
    assert rule.evidence["event_entity"] == "RECURRING_PAYMENT_EVENT"
