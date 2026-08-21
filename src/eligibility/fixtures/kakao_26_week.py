from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from eligibility.schema.enums import (
    AchievementMode,
    ComparisonOperator,
    ContributionFrequency,
    ContributionMode,
    ContextDateField,
    EntityType,
    EvaluationPhase,
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    PeriodUnit,
    ResolutionStrategy,
    RulePurpose,
    ScheduledOccurrenceMethod,
    ScheduledOccurrenceStatus,
    SaleStatus,
    TermUnit,
)
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.product import (
    ContractTerm,
    ContributionPolicy,
    PreferentialRateRule,
    ProductDefinition,
    ProductFeature,
    ProductMetadata,
    Reward,
)
from eligibility.schema.rule import (
    ActionDefinition,
    CountConsecutiveRule,
    CoverageRequirement,
    DateExpression,
    FactAcceptancePolicy,
    FactComparisonRule,
    FutureAchievementSpec,
    GoalTemplate,
    MissingFactSpec,
    RateImpact,
    SourceReference,
    TimeWindow,
    ValuePredicate,
)
from eligibility.schema.user_fact import (
    DataCoverage,
    FactProvenance,
    ScheduledOccurrence,
    UserFact,
    UserFactStore,
)


PRODUCT_ID = "KAKAOBANK_26_WEEK_SAVINGS_20260819"
SCHEDULE_ID = "KAKAO_26W_001"
USER_ID = "KAKAO_U001"
SEVEN_WEEK_INTENT_FACT_TYPE = "WILL_ATTEMPT_KAKAO_7_CONSECUTIVE"
TWENTY_SIX_WEEK_INTENT_FACT_TYPE = "WILL_ATTEMPT_KAKAO_26_CONSECUTIVE"
# Compatibility alias for external fixture consumers; the executable rules use
# target-specific intent facts below.
INTENT_FACT_TYPE = TWENTY_SIX_WEEK_INTENT_FACT_TYPE
COVERAGE_DOMAIN = "KAKAO_26W_AUTO_TRANSFER_OCCURRENCE"
DOCUMENT_URL = "https://www.kakaobank.com/products/26weeks"
_SUBSCRIPTION_DATE = date(2026, 8, 20)
_MATURITY_DATE = _SUBSCRIPTION_DATE + timedelta(weeks=26)
_COLLECTED_AT = datetime(2027, 2, 18, 9, 0, tzinfo=timezone.utc)


def _source(section: str) -> SourceReference:
    return SourceReference(
        document="카카오뱅크 26주적금 공식 상품페이지",
        version_date=date(2026, 8, 19),
        section=section,
        source_url=DOCUMENT_URL,
    )


def _auto_success_predicates() -> list[ValuePredicate]:
    return [
        ValuePredicate(
            path="method",
            operator=ComparisonOperator.EQ,
            expected="AUTO_TRANSFER",
        ),
        ValuePredicate(
            path="status",
            operator=ComparisonOperator.EQ,
            expected="SUCCESS",
        ),
    ]


def _future_spec(
    *,
    rule_id: str,
    threshold: int,
    reward: Decimal,
    intent_fact_type: str,
) -> FutureAchievementSpec:
    return FutureAchievementSpec(
        intent_fact_type=intent_fact_type,
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.ASK_USER,
            required_source="USER_DECLARED_FUTURE_INTENT",
            question=(
                f"가입 후 자동이체 성공을 {threshold}주 연속 유지해야 +{reward}%p "
                "우대를 받을 수 있습니다. 이 조건을 목표로 관리할까요?"
            ),
            impact=RateImpact(rate_pp=reward),
            expected_semantic_type=FactSemanticType.FUTURE_INTENT,
            action_id="MAINTAIN_KAKAO_26W_AUTO_TRANSFER",
            reward_id=f"REWARD_{rule_id}",
            grounding_terms=["자동이체", f"{threshold}주"],
        ),
        achievement_mode=AchievementMode.CONSECUTIVE,
        action=ActionDefinition(
            action_id="MAINTAIN_KAKAO_26W_AUTO_TRANSFER",
            description="매주 예정된 자동이체가 성공하도록 출금계좌 잔액을 관리한다.",
            burden_score=2,
        ),
        goal_template=GoalTemplate(
            metric="KAKAO_26W_AUTO_TRANSFER_SUCCESS_STREAK",
            unit="OCCURRENCE",
            tracking_mode=AchievementMode.CONSECUTIVE,
            safety_threshold=0,
            description=f"자동이체 {threshold}주 연속 성공",
            coverage_fact_domain=COVERAGE_DOMAIN,
            coverage_institution="KAKAOBANK",
        ),
        achievable_reason_code="FUTURE_INTENT_CONFIRMED_AND_TIME_FEASIBLE",
        insufficient_time_reason_code="INSUFFICIENT_FUTURE_OPPORTUNITIES",
    )


def kakao_26_week_product() -> ProductDefinition:
    subscription_date = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)
    maturity_date = DateExpression.context(ContextDateField.MATURITY_DATE)
    evaluation_window = TimeWindow(start=subscription_date, end=maturity_date)
    acceptance = FactAcceptancePolicy(
        strict_semantic_types=[FactSemanticType.OBSERVED_EVENT],
        strict_source_types=[
            FactSourceType.INSTITUTION_VERIFIED,
            FactSourceType.MYDATA_VERIFIED,
            FactSourceType.DERIVED,
        ],
        provisional_semantic_types=[FactSemanticType.SELF_REPORTED_FACT],
        provisional_source_types=[FactSourceType.USER_DECLARED],
    )
    coverage = CoverageRequirement(
        fact_domain=COVERAGE_DOMAIN,
        institution="KAKAOBANK",
    )
    eligibility = FactComparisonRule(
        rule_id="K26_ELIG_INITIAL_AMOUNT",
        name="허용된 최초 가입금액",
        purpose=RulePurpose.ELIGIBILITY,
        evaluation_phase=EvaluationPhase.PRE_SUBSCRIPTION,
        fact_type="KAKAO_26W_INITIAL_AMOUNT_KRW",
        operator=ComparisonOperator.IN,
        expected=[1000, 2000, 3000, 5000, 10000],
        true_reason_code="INITIAL_AMOUNT_ALLOWED",
        false_reason_code="INITIAL_AMOUNT_NOT_ALLOWED",
        source=_source("최초 가입금액 선택"),
    )
    seven_week = CountConsecutiveRule(
        rule_id="K26_RATE_7_CONSECUTIVE",
        name="가입기간 중 자동이체 7주 연속 성공",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        evaluation_phase=EvaluationPhase.POST_SUBSCRIPTION,
        entity=EntityType.SCHEDULED_OCCURRENCE,
        schedule_id=SCHEDULE_ID,
        filters=_auto_success_predicates(),
        # Corrected semantics: the qualifying run may begin at any week.
        from_sequence=None,
        window=evaluation_window,
        operator=ComparisonOperator.GTE,
        expected=7,
        expected_occurrence_count=26,
        opportunity_period=PeriodUnit.WEEK,
        fact_acceptance_policy=acceptance,
        coverage=coverage,
        future_achievement=_future_spec(
            rule_id="K26_RATE_7_CONSECUTIVE",
            threshold=7,
            reward=Decimal("1.0"),
            intent_fact_type=SEVEN_WEEK_INTENT_FACT_TYPE,
        ),
        true_reason_code="ANY_7_AUTO_TRANSFERS_CONSECUTIVE",
        false_reason_code="NO_7_WEEK_AUTO_TRANSFER_STREAK",
        source=_source("7주 연속 자동이체 우대"),
    )
    twenty_six_week = CountConsecutiveRule(
        rule_id="K26_RATE_26_CONSECUTIVE",
        name="자동이체 26주 연속 성공",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        evaluation_phase=EvaluationPhase.POST_SUBSCRIPTION,
        entity=EntityType.SCHEDULED_OCCURRENCE,
        schedule_id=SCHEDULE_ID,
        filters=_auto_success_predicates(),
        # In a 26-occurrence product, any run of 26 is necessarily all occurrences.
        from_sequence=None,
        window=evaluation_window,
        operator=ComparisonOperator.GTE,
        expected=26,
        expected_occurrence_count=26,
        opportunity_period=PeriodUnit.WEEK,
        fact_acceptance_policy=acceptance,
        coverage=coverage,
        future_achievement=_future_spec(
            rule_id="K26_RATE_26_CONSECUTIVE",
            threshold=26,
            reward=Decimal("2.0"),
            intent_fact_type=TWENTY_SIX_WEEK_INTENT_FACT_TYPE,
        ),
        true_reason_code="ALL_26_AUTO_TRANSFERS_CONSECUTIVE",
        false_reason_code="FULL_26_WEEK_AUTO_TRANSFER_STREAK_BROKEN",
        source=_source("26주 연속 자동이체 추가 우대"),
    )
    maturity_guard = FactComparisonRule(
        rule_id="K26_GUARD_MATURITY",
        name="만기해지",
        purpose=RulePurpose.GLOBAL_GUARD,
        evaluation_phase=EvaluationPhase.BOTH,
        fact_type="TERMINATION_TYPE",
        effective_at=maturity_date,
        operator=ComparisonOperator.EQ,
        expected="MATURED",
        on_missing_status=EvaluationStatus.ACHIEVABLE,
        true_reason_code="MATURED_NORMALLY",
        false_reason_code="EARLY_TERMINATION_BLOCKS_PREFERENTIAL_RATE",
        missing_reason_code="MATURITY_GUARD_PENDING",
        source=_source("만기해지 우대금리 적용"),
    )
    metadata = ProductMetadata(
        institution_id="KAKAOBANK",
        product_id=PRODUCT_ID,
        product_name="26주적금",
        product_type="INSTALLMENT_SAVINGS",
        sale_status=SaleStatus.ON_SALE,
        min_term=ContractTerm(value=26, unit=TermUnit.WEEK),
        max_term=ContractTerm(value=26, unit=TermUnit.WEEK),
        available_terms=[ContractTerm(value=26, unit=TermUnit.WEEK)],
        contribution_policy=ContributionPolicy(
            initial_amount_min=Decimal("1000"),
            initial_amount_max=Decimal("10000"),
            initial_amount_options=[
                Decimal("1000"), Decimal("2000"), Decimal("3000"),
                Decimal("5000"), Decimal("10000"),
            ],
            contribution_frequency=ContributionFrequency.WEEKLY,
            contribution_mode=ContributionMode.INCREMENTAL,
            # The weekly increment equals the selected initial amount, so the
            # exact source-backed alternatives are preserved instead of a
            # fabricated single increment.
            increment_amount_options=[
                Decimal("1000"), Decimal("2000"), Decimal("3000"),
                Decimal("5000"), Decimal("10000"),
            ],
        ),
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("5.0"),
        preferential_rate_cap=Decimal("3.0"),
        features=[
            ProductFeature(
                feature_id="INCREMENTAL_CONTRIBUTION",
                source_reference=_source("상품 기본조건 및 최초 가입금액"),
            ),
            ProductFeature(
                feature_id="CONSECUTIVE_AUTO_TRANSFER_BENEFIT",
                source_reference=_source("자동이체 연속 성공 우대"),
            ),
        ],
        source_reference=_source("상품 기본조건 및 최초 가입금액"),
    )

    return ProductDefinition(
        product_id=PRODUCT_ID,
        institution_id="KAKAOBANK",
        name="26주적금",
        product_type="INSTALLMENT_SAVINGS",
        contract_term=ContractTerm(value=26, unit=TermUnit.WEEK),
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("5.0"),
        preferential_rate_cap=Decimal("3.0"),
        eligibility_rule=eligibility,
        preferential_rules=[
            PreferentialRateRule(rule=seven_week, reward=Reward(value=Decimal("1.0"))),
            PreferentialRateRule(
                rule=twenty_six_week,
                reward=Reward(value=Decimal("2.0")),
            ),
        ],
        global_guards=[maturity_guard],
        metadata=metadata,
    )


def kakao_26_week_context() -> EvaluationContext:
    return EvaluationContext(
        as_of=_MATURITY_DATE,
        subscription_date=_SUBSCRIPTION_DATE,
        maturity_date=_MATURITY_DATE,
    )


def kakao_26_week_pre_subscription_context() -> EvaluationContext:
    return EvaluationContext(
        as_of=_SUBSCRIPTION_DATE - timedelta(days=1),
        subscription_date=_SUBSCRIPTION_DATE,
        maturity_date=_MATURITY_DATE,
    )


def _fact(fact_id: str, fact_type: str, value: object) -> UserFact:
    return UserFact(
        fact_id=fact_id,
        user_id=USER_ID,
        fact_type=fact_type,
        value=value,
        valid_from=_SUBSCRIPTION_DATE,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_FACT,
        provenance=[
            FactProvenance(
                reference=f"fixture/kakao-26w/{fact_id}",
                description="Human-reviewed KakaoBank regression fact",
            )
        ],
        collected_at=_COLLECTED_AT,
        confidence=1.0,
    )


def kakao_future_intent(
    value: bool = True,
    *,
    target: int = 26,
) -> UserFact:
    if target not in {7, 26}:
        raise ValueError("target must be 7 or 26")
    fact_type = (
        SEVEN_WEEK_INTENT_FACT_TYPE
        if target == 7
        else TWENTY_SIX_WEEK_INTENT_FACT_TYPE
    )
    return UserFact(
        fact_id=f"K26-F-INTENT-{target}-{str(value).upper()}",
        user_id=USER_ID,
        fact_type=fact_type,
        value=value,
        valid_from=_SUBSCRIPTION_DATE - timedelta(days=1),
        source_type=FactSourceType.USER_DECLARED,
        semantic_type=FactSemanticType.FUTURE_INTENT,
        provenance=[FactProvenance(reference="fixture/kakao-26w/user-intent")],
        collected_at=datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc),
    )


def _occurrence(
    sequence_no: int,
    status: ScheduledOccurrenceStatus,
    *,
    method: ScheduledOccurrenceMethod = ScheduledOccurrenceMethod.AUTO_TRANSFER,
    suffix: str = "AUTO",
) -> ScheduledOccurrence:
    scheduled_at = datetime.combine(
        _SUBSCRIPTION_DATE + timedelta(weeks=sequence_no),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    return ScheduledOccurrence(
        occurrence_id=f"K26-O-{sequence_no:02d}-{suffix}",
        user_id=USER_ID,
        schedule_id=SCHEDULE_ID,
        sequence_no=sequence_no,
        scheduled_at=scheduled_at,
        method=method,
        status=status,
        observed_event_id=f"K26-E-{sequence_no:02d}-{suffix}",
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_EVENT,
        provenance=[
            FactProvenance(
                reference=f"virtual-kakaobank/auto-transfer/{sequence_no}/{suffix}",
                attributes={
                    "manual_fill_does_not_change_auto_transfer_status": True
                },
            )
        ],
    )


def _coverage() -> DataCoverage:
    return DataCoverage(
        coverage_id="K26-COVERAGE-FULL",
        user_id=USER_ID,
        fact_domain=COVERAGE_DOMAIN,
        institution="KAKAOBANK",
        covered_from=_SUBSCRIPTION_DATE,
        covered_to=_MATURITY_DATE,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        provenance=[FactProvenance(reference="virtual-kakaobank/schedule-sync")],
    )


def kakao_pre_subscription_user(*, intent: bool | None = None) -> UserFactStore:
    facts = [_fact("K26-F-AMOUNT", "KAKAO_26W_INITIAL_AMOUNT_KRW", 1000)]
    if intent is not None:
        # Legacy convenience means "I will attempt the Kakao auto-transfer goals"
        # and therefore populates both independently answerable targets.
        facts.extend(
            [
                kakao_future_intent(intent, target=7),
                kakao_future_intent(intent, target=26),
            ]
        )
    return UserFactStore(user_id=USER_ID, facts=facts)


def kakao_user_from_statuses(
    statuses: Iterable[ScheduledOccurrenceStatus],
    *,
    manual_recovery_sequences: Iterable[int] = (),
) -> UserFactStore:
    status_list = list(statuses)
    occurrences = [
        _occurrence(sequence, status)
        for sequence, status in enumerate(status_list, start=1)
    ]
    for sequence in manual_recovery_sequences:
        occurrences.append(
            _occurrence(
                sequence,
                ScheduledOccurrenceStatus.SUCCESS,
                method=ScheduledOccurrenceMethod.MANUAL_TRANSFER,
                suffix="MANUAL-FILL",
            )
        )
    return UserFactStore(
        user_id=USER_ID,
        facts=[
            _fact("K26-F-AMOUNT", "KAKAO_26W_INITIAL_AMOUNT_KRW", 1000),
            _fact("K26-F-TERM", "TERMINATION_TYPE", "MATURED"),
        ],
        scheduled_occurrences=occurrences,
        data_coverages=[_coverage()],
    )


def kakao_user_all_success() -> UserFactStore:
    return kakao_user_from_statuses(
        [ScheduledOccurrenceStatus.SUCCESS] * 26
    )


def kakao_user_with_failure(*, manual_recovery: bool = False) -> UserFactStore:
    statuses = [ScheduledOccurrenceStatus.SUCCESS] * 26
    statuses[3] = ScheduledOccurrenceStatus.FAILED
    return kakao_user_from_statuses(
        statuses,
        manual_recovery_sequences=[4] if manual_recovery else [],
    )
