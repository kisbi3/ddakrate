from __future__ import annotations

from datetime import date
from decimal import Decimal

from eligibility.schema.enums import (
    ComparisonOperator,
    ContextDateField,
    ContributionFrequency,
    ContributionMode,
    EntityType,
    EvaluationStatus,
    FactSemanticType,
    ResolutionStrategy,
    RulePurpose,
    SaleStatus,
    TermUnit,
)
from eligibility.schema.product import (
    ContractTerm,
    ContributionPolicy,
    MonthlyContributionPlan,
    PreferentialRateRule,
    ProductDefinition,
    ProductFeature,
    ProductMetadata,
    Reward,
)
from eligibility.schema.rule import (
    ActionDefinition,
    AndRule,
    CountDistinctMonthsRule,
    DateExpression,
    DerivedComparisonRule,
    ExistenceAssertionFallback,
    FactComparisonRule,
    FutureAchievementSpec,
    MissingFactSpec,
    NotExistsRule,
    OrRule,
    RateImpact,
    SourceReference,
    TimeWindow,
)
from eligibility.schema.evaluation import EvaluationContext


PRODUCT_ID = "SHINHAN_YOUTH_FIRST_SAVINGS_20260722"
DOCUMENT_URL = (
    "https://img.shinhan.com/sbank2016/seol/"
    "20240202000000990012LC000030.PDF"
)


def _source(page: int, section: str) -> SourceReference:
    return SourceReference(
        document="신한은행 청년 처음적금 상품설명서",
        version_date=date(2026, 7, 22),
        page=page,
        section=section,
        source_url=DOCUMENT_URL,
    )


def shinhan_youth_first_product() -> ProductDefinition:
    subscription_date = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)
    maturity_date = DateExpression.context(ContextDateField.MATURITY_DATE)

    age_rule = DerivedComparisonRule(
        rule_id="ELIG_AGE",
        name="가입연령 18~39세",
        purpose=RulePurpose.ELIGIBILITY,
        derivation="AGE_AT",
        input_fact_type="BIRTH_DATE",
        reference_date=subscription_date,
        operator=ComparisonOperator.BETWEEN_INCLUSIVE,
        expected=[18, 39],
        true_reason_code="AGE_WITHIN_ALLOWED_RANGE",
        false_reason_code="AGE_OUTSIDE_ALLOWED_RANGE",
        source=_source(1, "거래조건/가입대상"),
    )

    one_account_rule = FactComparisonRule(
        rule_id="ELIG_ONE_ACCOUNT",
        name="1인 1계좌",
        purpose=RulePurpose.ELIGIBILITY,
        fact_type="SHINHAN_YOUTH_FIRST_ACTIVE_ACCOUNT_COUNT",
        effective_at=subscription_date,
        operator=ComparisonOperator.EQ,
        expected=0,
        true_reason_code="NO_EXISTING_SAME_PRODUCT_ACCOUNT",
        false_reason_code="SAME_PRODUCT_ACCOUNT_ALREADY_EXISTS",
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.QUERY_MYDATA,
            required_source="SHINHAN_ACCOUNT_HOLDING_HISTORY",
        ),
        source=_source(1, "거래조건/가입대상"),
    )

    eligibility = AndRule(
        rule_id="ELIGIBILITY",
        name="청년 처음적금 가입자격",
        purpose=RulePurpose.ELIGIBILITY,
        children=[age_rule, one_account_rule],
    )

    recurring_window = TimeWindow(
        start=DateExpression.start_of_month(subscription_date),
        end=DateExpression.end_of_month(
            DateExpression.add_months(maturity_date, -2)
        ),
    )

    salary_capability = FactComparisonRule(
        rule_id="CAP_SALARY_ACCOUNT_CHANGE",
        name="급여 수령계좌 변경 가능",
        purpose=RulePurpose.SUPPORTING,
        fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
        operator=ComparisonOperator.EQ,
        expected=True,
        required_semantic_type=FactSemanticType.FUTURE_INTENT,
        on_true_status=EvaluationStatus.ACHIEVABLE,
        on_false_status=EvaluationStatus.UNSATISFIABLE,
        true_reason_code="SALARY_ACCOUNT_CAN_BE_CHANGED",
        false_reason_code="SALARY_ACCOUNT_CANNOT_BE_CHANGED",
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.ASK_USER,
            question="가입 후 급여 수령계좌를 신한은행으로 변경할 수 있나요?",
            expected_semantic_type=FactSemanticType.FUTURE_INTENT,
            action_id="CHANGE_SALARY_ACCOUNT",
            reward_id="REWARD_RATE_SALARY",
            grounding_terms=["급여", "변경"],
        ),
    )
    salary_rule = CountDistinctMonthsRule(
        rule_id="RATE_SALARY",
        name="주거래 우대",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        fact_type="SHINHAN_QUALIFYING_SALARY_MONTH",
        window=recurring_window,
        operator=ComparisonOperator.GTE,
        expected=6,
        future_achievement=FutureAchievementSpec(
            capability_rule=salary_capability,
            action=ActionDefinition(
                action_id="CHANGE_SALARY_ACCOUNT",
                description="급여 수령계좌를 신한은행으로 변경하고 6개월의 인정 실적을 만든다.",
                burden_score=2,
            ),
            achievable_reason_code="SALARY_MONTHS_CAN_BE_COMPLETED",
            alternative_action_paths_complete=False,
        ),
        source=_source(2, "[1] 주거래 우대"),
    )

    card_capability = AndRule(
        rule_id="CAP_SHINHAN_CARD_SETTLEMENT",
        name="신한카드 결제계좌 변경 가능",
        purpose=RulePurpose.SUPPORTING,
        children=[
            FactComparisonRule(
                rule_id="CAP_SHINHAN_CARD_HELD",
                name="신한카드 보유",
                fact_type="SHINHAN_CARD_HELD",
                operator=ComparisonOperator.EQ,
                expected=True,
                true_reason_code="SHINHAN_CARD_ALREADY_HELD",
                false_reason_code="NO_SHINHAN_CARD",
                missing_fact=MissingFactSpec(
                    resolution_strategy=ResolutionStrategy.QUERY_MYDATA,
                    required_source="CARD_HOLDING_DATA",
                ),
            ),
            FactComparisonRule(
                rule_id="CAP_CARD_SETTLEMENT_CHANGE",
                name="결제계좌 변경 가능",
                fact_type="CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE",
                operator=ComparisonOperator.EQ,
                expected=True,
                required_semantic_type=FactSemanticType.FUTURE_INTENT,
                on_true_status=EvaluationStatus.ACHIEVABLE,
                on_false_status=EvaluationStatus.UNSATISFIABLE,
                true_reason_code="CARD_SETTLEMENT_ACCOUNT_CAN_BE_CHANGED",
                false_reason_code="CARD_SETTLEMENT_ACCOUNT_CANNOT_BE_CHANGED",
                missing_fact=MissingFactSpec(
                    resolution_strategy=ResolutionStrategy.ASK_USER,
                    question="가입 후 신한카드 결제계좌를 본인 신한은행 계좌로 변경할 수 있나요?",
                    expected_semantic_type=FactSemanticType.FUTURE_INTENT,
                    action_id="CHANGE_CARD_SETTLEMENT_ACCOUNT",
                    reward_id="REWARD_RATE_CARD",
                    grounding_terms=["신한카드", "결제계좌", "변경"],
                ),
            ),
        ],
    )
    card_rule = CountDistinctMonthsRule(
        rule_id="RATE_CARD",
        name="신한카드 결제 우대",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        fact_type="SHINHAN_CARD_QUALIFYING_MONTH",
        window=recurring_window,
        operator=ComparisonOperator.GTE,
        expected=6,
        future_achievement=FutureAchievementSpec(
            capability_rule=card_capability,
            action=ActionDefinition(
                action_id="CHANGE_CARD_SETTLEMENT_ACCOUNT",
                description="신한카드 결제계좌를 본인 신한은행 계좌로 변경하고 6개월에 1원 이상 결제한다.",
                burden_score=1,
            ),
            achievable_reason_code="CARD_MONTHS_CAN_BE_COMPLETED",
        ),
        source=_source(2, "[2] 신한카드 결제 우대"),
    )

    supersol_rule = FactComparisonRule(
        rule_id="RATE_SUPERSOL",
        name="신한 슈퍼SOL 우대",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        fact_type="SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING",
        operator=ComparisonOperator.EQ,
        expected=True,
        required_semantic_type=FactSemanticType.FUTURE_INTENT,
        on_true_status=EvaluationStatus.ACHIEVABLE,
        on_false_status=EvaluationStatus.UNSATISFIABLE,
        true_reason_code="SUPER_SOL_ACTION_COMMITTED",
        false_reason_code="SUPER_SOL_ACTION_DECLINED",
        missing_reason_code="SUPER_SOL_INTENT_UNRESOLVED",
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.ASK_USER,
            required_source="USER_INTENT_OR_SHINHAN_SERVICE_DATA",
            question="SuperSOL 정회원 가입·최초 로그인·만기 전전영업일까지 회원 유지를 진행할 수 있나요?",
            impact=RateImpact(rate_pp=Decimal("0.5")),
            expected_semantic_type=FactSemanticType.FUTURE_INTENT,
            action_id="JOIN_AND_MAINTAIN_SUPERSOL",
            reward_id="REWARD_RATE_SUPERSOL",
            grounding_terms=["SuperSOL", "유지"],
        ),
        action=ActionDefinition(
            action_id="JOIN_AND_MAINTAIN_SUPERSOL",
            description="SuperSOL 정회원 가입, 최초 로그인, 회원 유지를 완료한다.",
            burden_score=1,
        ),
        source=_source(2, "[3] 신한 슈퍼SOL 우대"),
    )

    lookback_window = TimeWindow(
        start=DateExpression.add_years(subscription_date, -1),
        end=DateExpression.add_days(subscription_date, -1),
    )
    first_transaction_branch = NotExistsRule(
        rule_id="RATE_FIRST_TRANSACTION_BRANCH",
        name="첫거래 branch",
        purpose=RulePurpose.SUPPORTING,
        entity=EntityType.ACCOUNT_HOLDING_INTERVAL,
        filters={
            "institution": "SHINHAN_BANK",
            "product_type": [
                "TIME_DEPOSIT",
                "INSTALLMENT_SAVINGS",
                "HOUSING_SUBSCRIPTION",
            ],
        },
        overlaps=lookback_window,
        self_report_fallback=ExistenceAssertionFallback(
            fact_type="SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y",
            missing_fact=MissingFactSpec(
                resolution_strategy=ResolutionStrategy.ASK_USER,
                required_source="USER_DECLARED_HISTORY",
                question="최근 1년간 신한은행 정기예금·정기적금·주택청약을 보유한 적이 있나요?",
                impact=RateImpact(rate_pp=Decimal("1.0")),
                expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                reward_id="REWARD_RATE_FIRST_OR_EVENT",
                grounding_terms=["최근 1년", "신한은행"],
            ),
        ),
        coverage_fact_type="SHINHAN_RELEVANT_HOLDING_HISTORY_COMPLETE",
        coverage_missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.QUERY_MYDATA,
            required_source="SHINHAN_ACCOUNT_HOLDING_HISTORY",
            impact=RateImpact(rate_pp=Decimal("1.0")),
        ),
        true_reason_code="NO_RELEVANT_HOLDING_IN_LOOKBACK",
        false_reason_code="PRIOR_HOLDING_OVERLAPS_LOOKBACK",
        source=_source(3, "[4] 첫거래 또는 이벤트 우대"),
    )
    event_branch = FactComparisonRule(
        rule_id="RATE_EVENT_BRANCH",
        name="이벤트 branch",
        purpose=RulePurpose.SUPPORTING,
        fact_type="SPECIAL_RATE_COUPON_VALID",
        operator=ComparisonOperator.EQ,
        expected=True,
        true_reason_code="VALID_EVENT_COUPON_CONFIRMED",
        false_reason_code="NO_VALID_EVENT_COUPON",
        missing_reason_code="EVENT_COUPON_STATUS_UNRESOLVED",
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.ASK_USER,
            required_source="SHINHAN_EVENT_ENTITLEMENT",
            question="청년 처음적금에 적용되는 특별금리 우대쿠폰을 보유하고 있나요?",
            impact=RateImpact(rate_pp=Decimal("1.0")),
            expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            reward_id="REWARD_RATE_FIRST_OR_EVENT",
            grounding_terms=["특별금리", "쿠폰"],
        ),
        source=_source(3, "[4] 첫거래 또는 이벤트 우대"),
    )
    first_or_event_rule = OrRule(
        rule_id="RATE_FIRST_OR_EVENT",
        name="첫거래 또는 이벤트 우대",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        children=[first_transaction_branch, event_branch],
        source=_source(3, "[4] 첫거래 또는 이벤트 우대"),
    )

    maturity_guard = FactComparisonRule(
        rule_id="GUARD_MATURITY",
        name="만기해지 guard",
        purpose=RulePurpose.GLOBAL_GUARD,
        fact_type="TERMINATION_TYPE",
        effective_at=maturity_date,
        operator=ComparisonOperator.EQ,
        expected="MATURED",
        on_missing_status=EvaluationStatus.ACHIEVABLE,
        true_reason_code="MATURED_NORMALLY",
        false_reason_code="EARLY_TERMINATION_BLOCKS_PREFERENTIAL_RATE",
        missing_reason_code="MATURITY_GUARD_PENDING",
        action=ActionDefinition(
            action_id="HOLD_UNTIL_MATURITY",
            description="중도해지하지 않고 계약 만기까지 유지한다.",
            burden_score=2,
        ),
        source=_source(3, "중도해지 우대이자율 미적용"),
    )

    metadata = ProductMetadata(
        institution_id="SHINHAN_BANK",
        product_id=PRODUCT_ID,
        product_name="청년 처음적금",
        product_type="INSTALLMENT_SAVINGS",
        sale_status=SaleStatus.ON_SALE,
        min_term=ContractTerm(value=12, unit=TermUnit.MONTH),
        max_term=ContractTerm(value=12, unit=TermUnit.MONTH),
        available_terms=[ContractTerm(value=12, unit=TermUnit.MONTH)],
        contribution_policy=ContributionPolicy(
            periodic_amount_min=Decimal("1000"),
            periodic_amount_max=Decimal("300000"),
            contribution_frequency=ContributionFrequency.MONTHLY,
            contribution_mode=ContributionMode.FLEXIBLE,
        ),
        base_rate=Decimal("3.05"),
        advertised_max_rate=Decimal("6.05"),
        preferential_rate_cap=Decimal("3.0"),
        target_customer_summary="가입일 현재 만 18세 이상 만 39세 이하 실명의 개인 및 개인사업자",
        one_account_per_person=True,
        features=[
            ProductFeature(
                feature_id="FIRST_TRANSACTION_BENEFIT",
                source_reference=_source(3, "[4] 첫거래 또는 이벤트 우대"),
            ),
            ProductFeature(
                feature_id="CARD_BENEFIT",
                source_reference=_source(2, "[2] 신한카드 결제 우대"),
            ),
            ProductFeature(
                feature_id="APP_SERVICE_BENEFIT",
                source_reference=_source(2, "[3] 신한 슈퍼SOL 우대"),
            ),
        ],
        effective_from=date(2026, 7, 22),
        source_reference=_source(1, "거래조건/가입대상"),
    )

    return ProductDefinition(
        product_id=PRODUCT_ID,
        institution_id="SHINHAN_BANK",
        name="청년 처음적금",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=12,
        base_rate=Decimal("3.05"),
        advertised_max_rate=Decimal("6.05"),
        preferential_rate_cap=Decimal("3.0"),
        eligibility_rule=eligibility,
        preferential_rules=[
            PreferentialRateRule(
                rule=salary_rule,
                reward=Reward(value=Decimal("1.0")),
            ),
            PreferentialRateRule(
                rule=card_rule,
                reward=Reward(value=Decimal("0.5")),
            ),
            PreferentialRateRule(
                rule=supersol_rule,
                reward=Reward(value=Decimal("0.5")),
            ),
            PreferentialRateRule(
                rule=first_or_event_rule,
                reward=Reward(value=Decimal("1.0")),
            ),
        ],
        global_guards=[maturity_guard],
        metadata=metadata,
    )


def golden_context() -> EvaluationContext:
    return EvaluationContext(
        as_of=date(2026, 8, 19),
        subscription_date=date(2026, 8, 20),
        maturity_date=date(2027, 8, 20),
    )


def golden_contribution_plan() -> MonthlyContributionPlan:
    return MonthlyContributionPlan(
        monthly_amount=Decimal("300000"),
        months=12,
        tax_rate=Decimal("0.154"),
    )
