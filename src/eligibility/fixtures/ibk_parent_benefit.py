from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from eligibility.schema.enums import (
    ComparisonOperator,
    ContextDateField,
    ContributionFrequency,
    EvaluationStatus,
    FactSourceType,
    FactSubjectMode,
    PeriodUnit,
    ResolutionStrategy,
    RulePurpose,
    SaleStatus,
    TermUnit,
)
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.institution_service import (
    InstitutionServiceDefinition,
    ServiceFactDefinition,
)
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
    AndRule,
    CountDistinctPeriodsRule,
    DateExpression,
    FactComparisonRule,
    FactSubjectSelector,
    MissingFactSpec,
    OrRule,
    SourceReference,
    TimeWindow,
)
from eligibility.schema.user_fact import (
    FactProvenance,
    PersonRelationship,
    UserFact,
    UserFactStore,
)


PRODUCT_ID = "IBK_PARENT_BENEFIT_SAVINGS_20260220"
USER_ID = "PARENT_01"
CHILD_ID = "CHILD_01"
DOCUMENT_URL = (
    "https://mybank.ibk.co.kr/uib/jsp/guest/ntr/ntr70/ntr7010/"
    "PNTR701000_i2.jsp?grcd=21&lncd=01&pdcd=0124&tmcd=121"
)
_SUBSCRIPTION_DATE = date(2026, 3, 1)
_MATURITY_DATE = date(2027, 3, 1)
_COLLECTED_AT = datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)


def _source(section: str) -> SourceReference:
    return SourceReference(
        document="IBK부모급여우대적금 공식 상품페이지",
        version_date=date(2026, 2, 20),
        section=section,
        source_url=DOCUMENT_URL,
    )


def ibk_family_aggregation_service() -> InstitutionServiceDefinition:
    return InstitutionServiceDefinition(
        institution_id="IBK_BANK",
        service_id="IBK_PARENT_BENEFIT_FAMILY_AGGREGATION",
        name="부모급여우대적금 가족실적 합산 등록",
        description="영업점에서 부모-자녀 1:1 관계를 등록한 뒤 계약기간 실적을 합산하는 IBK 고유 서비스",
        effective_from=date(2026, 2, 20),
        facts=[
            ServiceFactDefinition(
                fact_type="IBK_FAMILY_AGGREGATION_REGISTERED",
                description="해당 적금에 대해 가족실적 합산 등록이 유효함",
                authoritative_source="IBK family-registration service",
                derivation_chain=[
                    "PARENT_CHILD_RELATIONSHIP_VERIFIED",
                    "ONE_TO_ONE_FAMILY_REGISTERED_AT_BRANCH",
                    "AGGREGATION_EFFECTIVE_FOR_PRODUCT",
                ],
            )
        ],
        source_provenance=[_source("가족실적 합산")],
    )


def _performance_count(
    *,
    rule_id: str,
    name: str,
    subject_selector: FactSubjectSelector | None,
) -> CountDistinctPeriodsRule:
    subscription_date = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)
    maturity_date = DateExpression.context(ContextDateField.MATURITY_DATE)
    return CountDistinctPeriodsRule(
        rule_id=rule_id,
        name=name,
        purpose=RulePurpose.SUPPORTING,
        period=PeriodUnit.MONTH,
        fact_type="IBK_PARENT_BENEFIT_QUALIFIED_CREDIT",
        date_path="occurred_at",
        subject_selector=subject_selector,
        window=TimeWindow(start=subscription_date, end=maturity_date),
        operator=ComparisonOperator.GTE,
        expected=6,
        true_reason_code="PARENT_BENEFIT_SIX_MONTHS_REACHED",
        false_reason_code="PARENT_BENEFIT_SIX_MONTHS_NOT_REACHED",
        source=_source("부모급여·아동수당 6개월 이상"),
    )


def ibk_parent_benefit_product() -> ProductDefinition:
    maturity_date = DateExpression.context(ContextDateField.MATURITY_DATE)
    eligibility = FactComparisonRule(
        rule_id="IBK_ELIGIBILITY",
        name="기본 가입대상 확인",
        purpose=RulePurpose.ELIGIBILITY,
        fact_type="IBK_PARENT_BENEFIT_BASE_ELIGIBLE",
        operator=ComparisonOperator.EQ,
        expected=True,
        true_reason_code="IBK_BASE_ELIGIBILITY_CONFIRMED",
        false_reason_code="IBK_BASE_ELIGIBILITY_FAILED",
        source=_source("가입대상 및 1인 1계좌"),
    )

    self_performance = _performance_count(
        rule_id="IBK_RATE_SELF_PERFORMANCE",
        name="가입자 본인 부모급여 실적",
        subject_selector=None,
    )
    family_registration = FactComparisonRule(
        rule_id="IBK_FAMILY_REGISTRATION",
        name="IBK 가족실적 합산 등록",
        purpose=RulePurpose.SUPPORTING,
        fact_type="IBK_FAMILY_AGGREGATION_REGISTERED",
        operator=ComparisonOperator.EQ,
        expected=True,
        true_reason_code="IBK_FAMILY_AGGREGATION_ACTIVE",
        false_reason_code="IBK_FAMILY_AGGREGATION_NOT_ACTIVE",
        source=_source("가족 1명 영업점 등록 및 실적 합산"),
    )
    family_performance = _performance_count(
        rule_id="IBK_RATE_RELATED_PERFORMANCE",
        name="부모·자녀 합산 부모급여 실적",
        subject_selector=FactSubjectSelector(
            mode=FactSubjectMode.RELATED_PERSON,
            relationship_type="PARENT_CHILD",
            include_store_user=True,
            relationship_missing_fact=MissingFactSpec(
                resolution_strategy=ResolutionStrategy.QUERY_INSTITUTION,
                required_source="IBK_OR_PUBLIC_PARENT_CHILD_VERIFICATION",
            ),
        ),
    )
    family_branch = AndRule(
        rule_id="IBK_RATE_FAMILY_BRANCH",
        name="가족등록 후 부모·자녀 실적 합산",
        purpose=RulePurpose.SUPPORTING,
        children=[family_registration, family_performance],
        source=_source("가족실적 합산"),
    )
    parent_benefit_rule = OrRule(
        rule_id="IBK_RATE_PARENT_BENEFIT",
        name="부모급여·아동수당 6개월 우대",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        children=[self_performance, family_branch],
        source=_source("부모급여·아동수당 6개월 이상"),
    )
    maturity_guard = FactComparisonRule(
        rule_id="IBK_GUARD_MATURITY",
        name="만기해지",
        purpose=RulePurpose.GLOBAL_GUARD,
        fact_type="TERMINATION_TYPE",
        effective_at=maturity_date,
        operator=ComparisonOperator.EQ,
        expected="MATURED",
        on_missing_status=EvaluationStatus.ACHIEVABLE,
        true_reason_code="MATURED_NORMALLY",
        false_reason_code="EARLY_TERMINATION_BLOCKS_PREFERENTIAL_RATE",
        missing_reason_code="MATURITY_GUARD_PENDING",
        source=_source("만기해지"),
    )

    # This fixture intentionally models the related-person pressure slice. The
    # official advertised maximum remains product metadata; other official
    # preferential branches are not invented when their exact reward amounts
    # are outside the supplied stress-test decomposition.
    metadata = ProductMetadata(
        institution_id="IBK_BANK",
        product_id=PRODUCT_ID,
        product_name="IBK부모급여우대적금",
        product_type="INSTALLMENT_SAVINGS",
        sale_status=SaleStatus.ON_SALE,
        min_term=ContractTerm(value=12, unit=TermUnit.MONTH),
        max_term=ContractTerm(value=12, unit=TermUnit.MONTH),
        available_terms=[ContractTerm(value=12, unit=TermUnit.MONTH)],
        contribution_policy=ContributionPolicy(
            periodic_amount_max=Decimal("500000"),
            contribution_frequency=ContributionFrequency.MONTHLY,
            # The supplied source confirms a monthly limit but does not state
            # FIXED vs FLEXIBLE contribution semantics; leave mode unspecified.
        ),
        base_rate=Decimal("2.5"),
        advertised_max_rate=Decimal("6.5"),
        preferential_rate_cap=Decimal("4.0"),
        target_customer_summary="실명의 개인, 1인 1계좌(일부 대상 제외)",
        one_account_per_person=True,
        features=[
            ProductFeature(
                feature_id="RELATED_PERSON_PERFORMANCE",
                source_reference=_source("부모·자녀 명의 실적 인정"),
            ),
            ProductFeature(
                feature_id="BRANCH_FAMILY_REGISTRATION_PATH",
                required_for_subscription=False,
                source_reference=_source("가족실적합산 등록"),
            ),
        ],
        effective_from=date(2026, 2, 20),
        source_reference=_source("상품 기본조건"),
    )

    return ProductDefinition(
        product_id=PRODUCT_ID,
        institution_id="IBK_BANK",
        name="IBK부모급여우대적금",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=12,
        base_rate=Decimal("2.5"),
        advertised_max_rate=Decimal("6.5"),
        preferential_rate_cap=Decimal("4.0"),
        eligibility_rule=eligibility,
        preferential_rules=[
            PreferentialRateRule(
                rule=parent_benefit_rule,
                reward=Reward(value=Decimal("2.0")),
            )
        ],
        global_guards=[maturity_guard],
        metadata=metadata,
    )


def ibk_parent_benefit_context() -> EvaluationContext:
    return EvaluationContext(
        as_of=_MATURITY_DATE,
        subscription_date=_SUBSCRIPTION_DATE,
        maturity_date=_MATURITY_DATE,
    )


def _fact(
    fact_id: str,
    fact_type: str,
    value: object,
    *,
    subject_person_id: str = USER_ID,
    related_person_id: str | None = None,
    valid_from: date | None = None,
    source_type: FactSourceType = FactSourceType.INSTITUTION_VERIFIED,
    reference: str,
) -> UserFact:
    return UserFact(
        fact_id=fact_id,
        user_id=USER_ID,
        subject_person_id=subject_person_id,
        related_person_id=related_person_id,
        fact_type=fact_type,
        value=value,
        valid_from=valid_from,
        source_type=source_type,
        provenance=[FactProvenance(reference=reference)],
        collected_at=_COLLECTED_AT,
        confidence=1.0,
    )


def ibk_parent_benefit_user(
    *,
    relationship_source: FactSourceType = FactSourceType.INSTITUTION_VERIFIED,
) -> UserFactStore:
    months = [date(2026, month, 20) for month in range(3, 9)]
    performance_facts = []
    for index, occurred_at in enumerate(months, start=1):
        subject = USER_ID if index <= 3 else CHILD_ID
        related = CHILD_ID if subject == USER_ID else USER_ID
        performance_facts.append(
            _fact(
                f"IBK-F-PERF-{index}",
                "IBK_PARENT_BENEFIT_QUALIFIED_CREDIT",
                {
                    "occurred_at": occurred_at.isoformat(),
                    "benefit_type": "PARENT_BENEFIT",
                    "amount_krw": 100000,
                    "qualifier_resolver": "IBK_PARENT_BENEFIT_TRANSACTION_RESOLVER",
                },
                subject_person_id=subject,
                related_person_id=related,
                valid_from=occurred_at,
                source_type=FactSourceType.DERIVED,
                reference=f"resolver/ibk-parent-benefit/{index}",
            )
        )

    relationship = PersonRelationship(
        relationship_id="IBK-REL-PARENT-CHILD-01",
        person_a=USER_ID,
        person_b=CHILD_ID,
        relationship_type="PARENT_CHILD",
        valid_from=date(2026, 2, 20),
        source_type=relationship_source,
        source_reference="ibk/family-registration/PARENT_01-CHILD_01",
        provenance=[
            FactProvenance(
                reference="ibk/family-registration/one-to-one",
                attributes={"registered_for_product": PRODUCT_ID},
            )
        ],
    )
    return UserFactStore(
        user_id=USER_ID,
        facts=[
            _fact(
                "IBK-F-ELIG",
                "IBK_PARENT_BENEFIT_BASE_ELIGIBLE",
                True,
                reference="virtual-ibk/eligibility",
            ),
            _fact(
                "IBK-F-FAMILY-REG",
                "IBK_FAMILY_AGGREGATION_REGISTERED",
                True,
                reference="ibk/family-registration/service",
            ),
            _fact(
                "IBK-F-TERM",
                "TERMINATION_TYPE",
                "MATURED",
                valid_from=_MATURITY_DATE,
                reference="virtual-ibk/termination",
            ),
            *performance_facts,
        ],
        relationships=[relationship],
    )
