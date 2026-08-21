from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from eligibility.schema.enums import (
    AchievementMode,
    ComparisonOperator,
    ContextDateField,
    EvaluationPhase,
    FactSemanticType,
    FactSourceType,
    PeriodUnit,
    ResolutionStrategy,
    RulePurpose,
    TermUnit,
)
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.product import PreferentialRateRule, ProductDefinition, Reward
from eligibility.schema.rule import (
    ActionDefinition,
    ActionDuration,
    ActionPath,
    CountDistinctPeriodsRule,
    CoverageRequirement,
    DateExpression,
    FactComparisonRule,
    FactAcceptancePolicy,
    FutureAchievementSpec,
    GoalTemplate,
    MissingFactSpec,
    RateImpact,
    SourceReference,
    TimeWindow,
    ValuePredicate,
)
from eligibility.schema.user_fact import FactProvenance, UserFact, UserFactStore


USER_ID = "U-FUTURE-001"
SALARY_INTENT_FACT_TYPE = "WILL_TRACK_SHINHAN_SALARY_ENVELOPE_6M"
STEP_INTENT_FACT_TYPE = "WILL_TRACK_STEP_30000_300D"


def _source(document: str, page: int, section: str) -> SourceReference:
    return SourceReference(
        document=document,
        version_date=date(2026, 7, 22),
        page=page,
        section=section,
    )


def _eligibility_rule(fact_type: str, name: str) -> FactComparisonRule:
    return FactComparisonRule(
        rule_id=f"ELIG-{fact_type}",
        name=name,
        purpose=RulePurpose.ELIGIBILITY,
        evaluation_phase=EvaluationPhase.PRE_SUBSCRIPTION,
        fact_type=fact_type,
        operator=ComparisonOperator.EQ,
        expected=True,
        true_reason_code="PRODUCT_ELIGIBLE",
        false_reason_code="PRODUCT_INELIGIBLE",
    )


def salary_envelope_6m_product() -> ProductDefinition:
    subscription = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)
    maturity = DateExpression.context(ContextDateField.MATURITY_DATE)
    rule = CountDistinctPeriodsRule(
        rule_id="RATE_SALARY_ENVELOPE_6M",
        name="가입 후 월급봉투 인정월 6개월",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        evaluation_phase=EvaluationPhase.POST_SUBSCRIPTION,
        period=PeriodUnit.MONTH,
        fact_type="SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED",
        window=TimeWindow(
            start=subscription,
            # Twelve monthly opportunities for the focused golden fixture.
            end=DateExpression.add_months(maturity, -1),
        ),
        operator=ComparisonOperator.GTE,
        expected=6,
        fact_acceptance_policy=FactAcceptancePolicy(
            strict_semantic_types=[FactSemanticType.OBSERVED_EVENT],
            strict_source_types=[
                FactSourceType.INSTITUTION_VERIFIED,
                FactSourceType.MYDATA_VERIFIED,
                FactSourceType.DERIVED,
            ],
            provisional_semantic_types=[FactSemanticType.SELF_REPORTED_FACT],
            provisional_source_types=[FactSourceType.USER_DECLARED],
        ),
        coverage=CoverageRequirement(
            fact_domain="SHINHAN_SALARY_CLUB_ENVELOPE_EVENT",
            institution="SHINHAN_BANK",
        ),
        future_achievement=FutureAchievementSpec(
            intent_fact_type=SALARY_INTENT_FACT_TYPE,
            missing_fact=MissingFactSpec(
                resolution_strategy=ResolutionStrategy.ASK_USER,
                required_source="USER_DECLARED_FUTURE_INTENT",
                question=(
                    "이 상품의 +1.0%p 우대를 받으려면 가입 후 월급봉투 "
                    "인정조건을 6개월 이상 달성해야 합니다. 이 조건을 목표로 관리할까요?"
                ),
                impact=RateImpact(rate_pp=Decimal("1.0")),
                expected_semantic_type=FactSemanticType.FUTURE_INTENT,
                action_id="TRACK_SALARY_ENVELOPE_6M",
                reward_id="REWARD_RATE_SALARY_ENVELOPE_1P0",
                grounding_terms=["월급봉투", "6개월"],
            ),
            achievement_mode=AchievementMode.ACCUMULATIVE,
            max_qualifying_units_per_opportunity=1,
            action=ActionDefinition(
                action_id="TRACK_SALARY_ENVELOPE_6M",
                description="가입 후 월급봉투 인정월을 6개월 이상 만든다.",
                burden_score=2,
            ),
            action_paths=[
                ActionPath(
                    action_path_id="SHINHAN_SALARY_CLUB_ENVELOPE_PATH",
                    rule_id="RATE_SALARY_ENVELOPE_6M",
                    source_rule_node_id="RATE_SALARY_ENVELOPE_6M",
                    label="급여클럽 월급봉투 인정 경로",
                    required_capabilities=[
                        "JOIN_BANK_SERVICE",
                        "ACCEPT_MARKETING_CONSENT",
                        "MANAGE_QUALIFYING_INCOME_CREDIT",
                        "MAINTAIN_RECURRING_CONDITION",
                    ],
                    one_time_actions=[
                        ActionDefinition(
                            action_id="JOIN_SHINHAN_SALARY_CLUB",
                            description="공식 급여클럽 서비스 가입 요건을 충족한다.",
                            burden_score=1,
                        ),
                        ActionDefinition(
                            action_id="ACCEPT_REQUIRED_SALARY_CLUB_CONSENTS",
                            description="공식 서비스가 요구하는 동의 항목을 확인한다.",
                            burden_score=1,
                        ),
                    ],
                    recurring_actions=[
                        ActionDefinition(
                            action_id="RECEIVE_QUALIFIED_SALARY_ENVELOPE",
                            description="공식 인정기준에 따른 월급봉투를 인정월마다 받는다.",
                            burden_score=2,
                        )
                    ],
                    required_duration=ActionDuration(value=6, unit=TermUnit.MONTH),
                    required_service_refs=["SHINHAN_SALARY_CLUB"],
                    required_fact_types=[
                        "SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED"
                    ],
                    provenance=[
                        _source(
                            "신한은행 청년 처음적금 상품설명서",
                            2,
                            "[1] 주거래 우대",
                        )
                    ],
                )
            ],
            goal_template=GoalTemplate(
                metric="SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED_MONTH",
                unit="MONTH",
                tracking_mode=AchievementMode.ACCUMULATIVE,
                safety_threshold=1,
                description="월급봉투 인정월 6개월",
                coverage_fact_domain="SHINHAN_SALARY_CLUB_ENVELOPE_EVENT",
                coverage_institution="SHINHAN_BANK",
            ),
            achievable_reason_code="FUTURE_INTENT_CONFIRMED_AND_TIME_FEASIBLE",
            insufficient_time_reason_code="INSUFFICIENT_FUTURE_OPPORTUNITIES",
        ),
        source=_source("신한은행 청년 처음적금 상품설명서", 2, "[1] 주거래 우대"),
    )
    return ProductDefinition(
        product_id="SHINHAN_SALARY_ENVELOPE_6M_GOLDEN",
        institution_id="SHINHAN_BANK",
        name="청년 처음적금 — 월급봉투 6개월 Golden Flow",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=12,
        base_rate=Decimal("3.05"),
        advertised_max_rate=Decimal("4.05"),
        preferential_rate_cap=Decimal("1.0"),
        eligibility_rule=_eligibility_rule(
            "SHINHAN_GOLDEN_ELIGIBLE", "Golden fixture 가입 가능"
        ),
        preferential_rules=[
            PreferentialRateRule(rule=rule, reward=Reward(value=Decimal("1.0")))
        ],
    )


def salary_envelope_context() -> EvaluationContext:
    return EvaluationContext(
        as_of=date(2026, 8, 19),
        subscription_date=date(2026, 8, 20),
        maturity_date=date(2027, 8, 19),
    )


def salary_envelope_user() -> UserFactStore:
    return UserFactStore(
        user_id=USER_ID,
        facts=[
            UserFact(
                fact_id="F-SHINHAN-GOLDEN-ELIGIBLE",
                user_id=USER_ID,
                fact_type="SHINHAN_GOLDEN_ELIGIBLE",
                value=True,
                source_type=FactSourceType.INSTITUTION_VERIFIED,
                semantic_type=FactSemanticType.OBSERVED_FACT,
                valid_from=date(2026, 8, 19),
                provenance=[
                    FactProvenance(reference="fixture/shinhan/golden-eligibility")
                ],
                collected_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
            )
        ],
    )


def step_30000_300d_product() -> ProductDefinition:
    subscription = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)
    maturity = DateExpression.context(ContextDateField.MATURITY_DATE)
    rule = CountDistinctPeriodsRule(
        rule_id="RATE_STEP_30000_300D",
        name="하루 30,000보 이상 300일",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        evaluation_phase=EvaluationPhase.POST_SUBSCRIPTION,
        period=PeriodUnit.DAY,
        fact_type="QUALIFIED_DAILY_STEP_METRIC",
        filters=[
            ValuePredicate(
                path="steps",
                operator=ComparisonOperator.GTE,
                expected=30000,
            )
        ],
        window=TimeWindow(start=subscription, end=maturity),
        operator=ComparisonOperator.GTE,
        expected=300,
        fact_acceptance_policy=FactAcceptancePolicy(
            strict_semantic_types=[FactSemanticType.OBSERVED_EVENT],
            strict_source_types=[
                FactSourceType.INSTITUTION_VERIFIED,
                FactSourceType.MYDATA_VERIFIED,
                FactSourceType.DERIVED,
            ],
            provisional_semantic_types=[FactSemanticType.SELF_REPORTED_FACT],
            provisional_source_types=[FactSourceType.USER_DECLARED],
        ),
        coverage=CoverageRequirement(
            fact_domain="QUALIFIED_DAILY_STEP_METRIC",
            institution="HEALTH_SERVICE",
        ),
        future_achievement=FutureAchievementSpec(
            intent_fact_type=STEP_INTENT_FACT_TYPE,
            missing_fact=MissingFactSpec(
                resolution_strategy=ResolutionStrategy.ASK_USER,
                required_source="USER_DECLARED_FUTURE_INTENT",
                question=(
                    "가입 후 하루 30,000보 이상 달성한 날을 300일 이상 "
                    "만들어야 합니다. 이 조건을 목표로 관리할까요?"
                ),
                impact=RateImpact(rate_pp=Decimal("2.0")),
                expected_semantic_type=FactSemanticType.FUTURE_INTENT,
                action_id="TRACK_STEP_30000_300D",
                reward_id="REWARD_RATE_STEP_2P0",
                grounding_terms=["30,000보", "300일"],
            ),
            achievement_mode=AchievementMode.ACCUMULATIVE,
            max_qualifying_units_per_opportunity=1,
            action=ActionDefinition(
                action_id="TRACK_STEP_30000_300D",
                description="하루 30,000보 이상 달성일을 300일 만든다.",
                burden_score=5,
            ),
            goal_template=GoalTemplate(
                metric="QUALIFIED_DAILY_STEP_METRIC",
                unit="DAY",
                tracking_mode=AchievementMode.ACCUMULATIVE,
                safety_threshold=10,
                description="30,000보 이상 300일",
                coverage_fact_domain="QUALIFIED_DAILY_STEP_METRIC",
                coverage_institution="HEALTH_SERVICE",
            ),
            achievable_reason_code="FUTURE_INTENT_CONFIRMED_AND_TIME_FEASIBLE",
            insufficient_time_reason_code="INSUFFICIENT_FUTURE_OPPORTUNITIES",
        ),
        source=SourceReference(
            document="Synthetic stress-test specification",
            version_date=date(2026, 8, 19),
            page=1,
            section="30,000 steps x 300 days",
        ),
    )
    return ProductDefinition(
        product_id="SYNTHETIC_STEP_30000_300D",
        institution_id="SYNTHETIC",
        name="30,000보 × 300일 Synthetic Stress Test",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=12,
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("4.0"),
        preferential_rate_cap=Decimal("2.0"),
        eligibility_rule=_eligibility_rule(
            "SYNTHETIC_STEP_PRODUCT_ELIGIBLE", "Synthetic fixture 가입 가능"
        ),
        preferential_rules=[
            PreferentialRateRule(rule=rule, reward=Reward(value=Decimal("2.0")))
        ],
    )


def step_30000_context(*, available_days: int = 365) -> EvaluationContext:
    subscription = date(2026, 8, 20)
    maturity = date.fromordinal(subscription.toordinal() + available_days - 1)
    return EvaluationContext(
        as_of=date(2026, 8, 19),
        subscription_date=subscription,
        maturity_date=maturity,
    )


def step_30000_user() -> UserFactStore:
    return UserFactStore(
        user_id=USER_ID,
        facts=[
            UserFact(
                fact_id="F-STEP-GOLDEN-ELIGIBLE",
                user_id=USER_ID,
                fact_type="SYNTHETIC_STEP_PRODUCT_ELIGIBLE",
                value=True,
                source_type=FactSourceType.DERIVED,
                semantic_type=FactSemanticType.DERIVED_FACT,
                valid_from=date(2026, 8, 19),
                provenance=[FactProvenance(reference="fixture/step/eligibility")],
                collected_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
            )
        ],
    )


def future_intent_fact(
    fact_type: str,
    value: bool,
    *,
    user_id: str = USER_ID,
    valid_from: date = date(2026, 8, 19),
) -> UserFact:
    return UserFact(
        fact_id=f"F-INTENT-{fact_type}-{str(value).upper()}",
        user_id=user_id,
        fact_type=fact_type,
        value=value,
        source_type=FactSourceType.USER_DECLARED,
        semantic_type=FactSemanticType.FUTURE_INTENT,
        valid_from=valid_from,
        provenance=[FactProvenance(reference="fixture/user-answer/future-intent")],
        collected_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
