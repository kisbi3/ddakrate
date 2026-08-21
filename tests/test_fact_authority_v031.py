from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.schema.enums import (
    ComparisonOperator,
    ContextDateField,
    EvaluationPhase,
    EvaluationStatus,
    EvaluationTrustMode,
    FactSemanticType,
    FactSourceType,
    PeriodUnit,
    RulePurpose,
    VerificationLevel,
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


USER_ID = "AUTH-U001"
EVENT_TYPE = "SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED"
COVERAGE_DOMAIN = "SALARY_ENVELOPE_EVENT"


def _product() -> ProductDefinition:
    rule = CountDistinctPeriodsRule(
        rule_id="RATE-AUTHORITY-6M",
        name="기관 관측 월급봉투 6개월",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        evaluation_phase=EvaluationPhase.POST_SUBSCRIPTION,
        period=PeriodUnit.MONTH,
        fact_type=EVENT_TYPE,
        window=TimeWindow(
            start=DateExpression.context(ContextDateField.SUBSCRIPTION_DATE),
            end=DateExpression.context(ContextDateField.MATURITY_DATE),
        ),
        operator=ComparisonOperator.GTE,
        expected=6,
        fact_acceptance_policy=FactAcceptancePolicy(
            strict_semantic_types=[FactSemanticType.OBSERVED_EVENT],
            strict_source_types=[
                FactSourceType.INSTITUTION_VERIFIED,
                FactSourceType.MYDATA_VERIFIED,
            ],
            provisional_semantic_types=[FactSemanticType.SELF_REPORTED_FACT],
            provisional_source_types=[FactSourceType.USER_DECLARED],
        ),
        coverage=CoverageRequirement(
            fact_domain=COVERAGE_DOMAIN,
            institution="SHINHAN_BANK",
        ),
    )
    return ProductDefinition(
        product_id="AUTHORITY-PRODUCT",
        institution_id="SHINHAN_BANK",
        name="Authority Test Product",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=6,
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("3.0"),
        preferential_rate_cap=Decimal("1.0"),
        eligibility_rule=FactComparisonRule(
            rule_id="ELIG-AUTH",
            name="가입 가능",
            purpose=RulePurpose.ELIGIBILITY,
            fact_type="ELIGIBLE",
            operator=ComparisonOperator.EQ,
            expected=True,
        ),
        preferential_rules=[
            PreferentialRateRule(rule=rule, reward=Reward(value=Decimal("1.0")))
        ],
    )


def _context() -> EvaluationContext:
    return EvaluationContext(
        as_of=date(2026, 6, 30),
        subscription_date=date(2026, 1, 1),
        maturity_date=date(2026, 6, 30),
    )


def _coverage() -> DataCoverage:
    return DataCoverage(
        coverage_id="AUTH-COVERAGE",
        user_id=USER_ID,
        fact_domain=COVERAGE_DOMAIN,
        institution="SHINHAN_BANK",
        covered_from=date(2026, 1, 1),
        covered_to=date(2026, 6, 30),
        source_type=FactSourceType.INSTITUTION_VERIFIED,
    )


def _fact(
    index: int,
    *,
    semantic_type: FactSemanticType,
    source_type: FactSourceType,
) -> UserFact:
    return UserFact(
        fact_id=f"AUTH-E-{index}-{semantic_type.value}",
        user_id=USER_ID,
        fact_type=EVENT_TYPE,
        value=True,
        valid_from=date(2026, index, 15),
        source_type=source_type,
        semantic_type=semantic_type,
        collected_at=datetime(2026, index, 16, tzinfo=timezone.utc),
    )


def _store(events: list[UserFact]) -> UserFactStore:
    eligibility = UserFact(
        fact_id="AUTH-ELIG",
        user_id=USER_ID,
        fact_type="ELIGIBLE",
        value=True,
        valid_from=date(2026, 1, 1),
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_FACT,
    )
    return UserFactStore(
        user_id=USER_ID,
        facts=[eligibility, *events],
        data_coverages=[_coverage()],
    )


def _evaluate(store: UserFactStore, mode: EvaluationTrustMode = EvaluationTrustMode.STRICT):
    return FinancialEligibilityEngine().evaluate_product(
        _product(), _store([]) if store is None else store, _context(), trust_mode=mode
    )


def test_user_declared_observed_like_fact_cannot_verify_strict_satisfaction():
    reports = [
        _fact(
            month,
            semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            source_type=FactSourceType.USER_DECLARED,
        )
        for month in range(1, 7)
    ]

    result = _evaluate(_store(reports))
    rule = result.preferential_rule_results[0]

    assert rule.status == EvaluationStatus.UNKNOWN
    assert rule.reason_code == "FACT_AUTHORITY_INSUFFICIENT"
    assert rule.progress is not None and rule.progress.current == 0
    assert result.rates.confirmed_rate == Decimal("2.0")
    assert result.is_provisional is False


def test_future_intent_never_counts_as_observed_event():
    intents = [
        _fact(
            month,
            semantic_type=FactSemanticType.FUTURE_INTENT,
            source_type=FactSourceType.USER_DECLARED,
        )
        for month in range(1, 7)
    ]

    result = _evaluate(_store(intents))
    rule = result.preferential_rule_results[0]

    assert rule.status == EvaluationStatus.UNKNOWN
    assert rule.progress is not None and rule.progress.current == 0
    assert rule.reason_code == "FACT_AUTHORITY_INSUFFICIENT"


def test_authoritative_observed_events_are_used_for_actual_progress():
    events = [
        _fact(
            month,
            semantic_type=FactSemanticType.OBSERVED_EVENT,
            source_type=FactSourceType.INSTITUTION_VERIFIED,
        )
        for month in range(1, 7)
    ]

    result = _evaluate(_store(events))
    rule = result.preferential_rule_results[0]

    assert rule.status == EvaluationStatus.SATISFIED
    assert rule.progress is not None and rule.progress.current == 6
    assert rule.verification_level == VerificationLevel.INSTITUTION_VERIFIED
    assert result.is_provisional is False
    assert result.rates.confirmed_rate == Decimal("3.0")


def test_mvp_provisional_uses_self_report_but_marks_result():
    reports = [
        _fact(
            month,
            semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            source_type=FactSourceType.USER_DECLARED,
        )
        for month in range(1, 7)
    ]

    result = _evaluate(_store(reports), EvaluationTrustMode.MVP_PROVISIONAL)
    rule = result.preferential_rule_results[0]

    # Global trust mode is a deprecated compatibility argument. Event-count
    # policies remain authoritative-only, so self-reports cannot become
    # observed performance under either mode.
    assert rule.status == EvaluationStatus.UNKNOWN
    assert rule.reason_code == "FACT_AUTHORITY_INSUFFICIENT"
    assert rule.verification_level == VerificationLevel.UNKNOWN
    assert result.is_provisional is False
    assert not hasattr(result, "trust_mode")


def test_historical_or_current_fact_comparison_can_be_provisional_without_becoming_verified():
    condition = FactComparisonRule(
        rule_id="RATE-SELF-REPORTED-CURRENT-CARD",
        name="현재 카드 보유",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        evaluation_phase=EvaluationPhase.PRE_SUBSCRIPTION,
        fact_type="CURRENT_TEST_BANK_CARD_HELD",
        operator=ComparisonOperator.EQ,
        expected=True,
        fact_acceptance_policy=FactAcceptancePolicy(
            strict_semantic_types=[FactSemanticType.OBSERVED_FACT],
            strict_source_types=[FactSourceType.INSTITUTION_VERIFIED],
            provisional_semantic_types=[FactSemanticType.SELF_REPORTED_FACT],
            provisional_source_types=[FactSourceType.USER_DECLARED],
            allow_provisional=True,
        ),
    )
    product = ProductDefinition(
        product_id="AUTHORITY-FACT-COMPARE-PRODUCT",
        institution_id="TEST_BANK",
        name="Fact comparison authority test",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=6,
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("3.0"),
        preferential_rate_cap=Decimal("1.0"),
        eligibility_rule=FactComparisonRule(
            rule_id="ELIG-AUTH-FACT-COMPARE",
            name="가입 가능",
            purpose=RulePurpose.ELIGIBILITY,
            fact_type="ELIGIBLE",
            operator=ComparisonOperator.EQ,
            expected=True,
        ),
        preferential_rules=[
            PreferentialRateRule(rule=condition, reward=Reward(value=Decimal("1.0")))
        ],
    )
    store = UserFactStore(
        user_id=USER_ID,
        facts=[
            UserFact(
                fact_id="AUTH-FACT-COMPARE-ELIGIBLE",
                user_id=USER_ID,
                fact_type="ELIGIBLE",
                value=True,
                source_type=FactSourceType.INSTITUTION_VERIFIED,
                semantic_type=FactSemanticType.OBSERVED_FACT,
            ),
            UserFact(
                fact_id="AUTH-FACT-COMPARE-SELF-REPORT",
                user_id=USER_ID,
                fact_type="CURRENT_TEST_BANK_CARD_HELD",
                value=True,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            ),
        ],
    )

    strict = FinancialEligibilityEngine().evaluate_product(
        product, store, _context(), trust_mode=EvaluationTrustMode.STRICT
    )
    provisional = FinancialEligibilityEngine().evaluate_product(
        product, store, _context(), trust_mode=EvaluationTrustMode.MVP_PROVISIONAL
    )

    strict_rule = strict.preferential_rule_results[0]
    provisional_rule = provisional.preferential_rule_results[0]
    # v0.3.2: per-Fact policy, not global mode, controls acceptance.
    for evaluated in (strict_rule, provisional_rule):
        assert evaluated.status == EvaluationStatus.SATISFIED
        assert evaluated.is_provisional is True
        assert evaluated.verification_level == VerificationLevel.SELF_REPORTED
    assert strict.is_provisional is True
    assert provisional.is_provisional is True
    assert strict.rates.confirmed_rate == Decimal("2.0")
    assert strict.rates.realizable_rate == Decimal("3.0")
