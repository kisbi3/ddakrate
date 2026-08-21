from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from eligibility.schema.enums import (
    ComparisonOperator,
    ContextDateField,
    ContributionFrequency,
    EvaluationStatus,
    FactSourceType,
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
    DateExpression,
    DerivedComparisonRule,
    FactComparisonRule,
    SourceReference,
)
from eligibility.schema.user_fact import FactProvenance, UserFact, UserFactStore


PRODUCT_ID = "HANA_RUN_SAVINGS_20260429"
USER_ID = "HANA_U001"
DOCUMENT_URL = (
    "https://hanabank.com/cont/mall/mall08/mall0801/mall080102/"
    "1524394_115157.jsp"
)
_SUBSCRIPTION_DATE = date(2026, 5, 1)
_MATURITY_DATE = date(2027, 5, 1)
_COLLECTED_AT = datetime(2027, 4, 15, 9, 0, tzinfo=timezone.utc)


def _source(section: str) -> SourceReference:
    return SourceReference(
        document="하나은행 달려라 하나 적금 공식 상품페이지",
        version_date=date(2026, 4, 29),
        section=section,
        source_url=DOCUMENT_URL,
    )


def hana_health_asset_management_service() -> InstitutionServiceDefinition:
    return InstitutionServiceDefinition(
        institution_id="HANA_BANK",
        service_id="HANA_HEALTH_ASSET_MANAGEMENT",
        name="하나 건강자산관리",
        description=(
            "건강데이터 동의와 허용된 달리기 기록 연동을 관리하고, "
            "하나 서비스 화면에서 인정된 누적거리를 권위 있는 Fact로 제공"
        ),
        effective_from=date(2026, 4, 29),
        facts=[
            ServiceFactDefinition(
                fact_type="HANA_HEALTH_ASSET_MANAGEMENT_ACTIVE",
                description="건강자산관리 가입 및 필수 동의가 유효함",
                authoritative_source="Hana Health Asset Management service",
                derivation_chain=[
                    "SERVICE_ENROLLED",
                    "HEALTH_DATA_CONSENT_ACTIVE",
                    "MEASUREMENT_PERMISSION_ACTIVE",
                ],
            ),
            ServiceFactDefinition(
                fact_type="HANA_RUNNING_DATA_LINKED",
                description="허용된 앱·기기에서 생성된 달리기 기록이 연동됨",
                authoritative_source="Hana Health Asset Management service",
                derivation_chain=[
                    "SUPPORTED_MEASUREMENT_SOURCE",
                    "NOT_MANUAL_INPUT",
                    "RUNNING_RECORD_LINKED",
                ],
            ),
            ServiceFactDefinition(
                fact_type="HANA_CONFIRMED_RUNNING_DISTANCE_KM",
                description="하나 건강자산관리 화면에서 상품 실적으로 인정된 누적 달리기 거리",
                authoritative_source="Hana Health Asset Management service",
                derivation_chain=[
                    "HEALTH_DATA_CONSENT_ACTIVE",
                    "RUNNING_RECORD_LINKED",
                    "SUPPORTED_MEASUREMENT_SOURCE",
                    "HANA_SERVICE_CONFIRMATION_BEFORE_DEADLINE",
                    "CONFIRMED_RUNNING_DISTANCE_KM",
                ],
            ),
        ],
        source_provenance=[_source("건강자산관리 및 달리기 누적거리 우대")],
    )


def _distance_increment_rule(
    *,
    rule_id: str,
    name: str,
    threshold_km: int,
) -> AndRule:
    return AndRule(
        rule_id=rule_id,
        name=name,
        purpose=RulePurpose.PREFERENTIAL_RATE,
        children=[
            FactComparisonRule(
                rule_id=f"{rule_id}_SERVICE",
                name="하나 건강자산관리 서비스 활성",
                fact_type="HANA_HEALTH_ASSET_MANAGEMENT_ACTIVE",
                operator=ComparisonOperator.EQ,
                expected=True,
                true_reason_code="HANA_HEALTH_SERVICE_ACTIVE",
                false_reason_code="HANA_HEALTH_SERVICE_INACTIVE",
            ),
            FactComparisonRule(
                rule_id=f"{rule_id}_DISTANCE",
                name=f"하나 인정 누적거리 {threshold_km}km 이상",
                fact_type="HANA_CONFIRMED_RUNNING_DISTANCE_KM",
                operator=ComparisonOperator.GTE,
                expected=threshold_km,
                true_reason_code="HANA_CONFIRMED_DISTANCE_THRESHOLD_REACHED",
                false_reason_code="HANA_CONFIRMED_DISTANCE_THRESHOLD_NOT_REACHED",
            ),
        ],
        source=_source(f"누적거리 {threshold_km}km 구간"),
    )


def hana_run_product() -> ProductDefinition:
    subscription_date = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)
    maturity_date = DateExpression.context(ContextDateField.MATURITY_DATE)
    eligibility = AndRule(
        rule_id="HANA_RUN_ELIGIBILITY",
        name="달려라 하나 적금 가입자격",
        purpose=RulePurpose.ELIGIBILITY,
        children=[
            DerivedComparisonRule(
                rule_id="HANA_RUN_AGE",
                name="만 19세 이상",
                derivation="AGE_AT",
                input_fact_type="BIRTH_DATE",
                reference_date=subscription_date,
                operator=ComparisonOperator.GTE,
                expected=19,
                true_reason_code="AGE_AT_LEAST_19",
                false_reason_code="AGE_UNDER_19",
                source=_source("가입대상"),
            ),
            FactComparisonRule(
                rule_id="HANA_RUN_ONE_ACCOUNT",
                name="1인 1계좌",
                fact_type="HANA_RUN_ACTIVE_ACCOUNT_COUNT",
                effective_at=subscription_date,
                operator=ComparisonOperator.EQ,
                expected=0,
                true_reason_code="NO_EXISTING_SAME_PRODUCT_ACCOUNT",
                false_reason_code="SAME_PRODUCT_ACCOUNT_ALREADY_EXISTS",
                source=_source("1인 1계좌"),
            ),
        ],
    )

    # Tiered rewards are represented as incremental generic thresholds:
    # 100km = +1.5, 200km adds +0.5, 500km adds +0.5, total +2.5.
    distance_100 = _distance_increment_rule(
        rule_id="HANA_RUN_RATE_100KM",
        name="누적거리 100km 우대",
        threshold_km=100,
    )
    distance_200_increment = _distance_increment_rule(
        rule_id="HANA_RUN_RATE_200KM_INCREMENT",
        name="누적거리 200km 추가 우대",
        threshold_km=200,
    )
    distance_500_increment = _distance_increment_rule(
        rule_id="HANA_RUN_RATE_500KM_INCREMENT",
        name="누적거리 500km 추가 우대",
        threshold_km=500,
    )
    maturity_guard = FactComparisonRule(
        rule_id="HANA_RUN_GUARD_MATURITY",
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

    # Focused regression fixture: it exercises the service-derived running
    # distance branch. First-transaction, National Fitness 100, and running
    # crew branches remain outside this fixture because supplied analysis does
    # not enumerate every exact reward amount needed for a full rate replica.
    metadata = ProductMetadata(
        institution_id="HANA_BANK",
        product_id=PRODUCT_ID,
        product_name="달려라 하나 적금",
        product_type="INSTALLMENT_SAVINGS",
        sale_status=SaleStatus.ON_SALE,
        min_term=ContractTerm(value=12, unit=TermUnit.MONTH),
        max_term=ContractTerm(value=12, unit=TermUnit.MONTH),
        available_terms=[ContractTerm(value=12, unit=TermUnit.MONTH)],
        contribution_policy=ContributionPolicy(
            periodic_amount_max=Decimal("300000"),
            contribution_frequency=ContributionFrequency.MONTHLY,
        ),
        base_rate=Decimal("1.8"),
        advertised_max_rate=Decimal("6.0"),
        preferential_rate_cap=Decimal("4.2"),
        target_customer_summary="만 19세 이상, 1인 1계좌",
        one_account_per_person=True,
        features=[
            ProductFeature(
                feature_id="HEALTH_DATA_BENEFIT",
                source_reference=_source("건강자산관리 달리기 실적"),
            ),
            ProductFeature(
                feature_id="MYDATA_SERVICE_BENEFIT",
                source_reference=_source("건강자산관리 MyData 동의"),
            ),
            ProductFeature(
                feature_id="APP_SERVICE_BENEFIT",
                source_reference=_source("하나원큐 건강자산관리"),
            ),
        ],
        effective_from=date(2026, 4, 29),
        source_reference=_source("상품 기본조건"),
    )

    return ProductDefinition(
        product_id=PRODUCT_ID,
        institution_id="HANA_BANK",
        name="달려라 하나 적금",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=12,
        base_rate=Decimal("1.8"),
        advertised_max_rate=Decimal("6.0"),
        preferential_rate_cap=Decimal("4.2"),
        eligibility_rule=eligibility,
        preferential_rules=[
            PreferentialRateRule(
                rule=distance_100,
                reward=Reward(value=Decimal("1.5")),
            ),
            PreferentialRateRule(
                rule=distance_200_increment,
                reward=Reward(value=Decimal("0.5")),
            ),
            PreferentialRateRule(
                rule=distance_500_increment,
                reward=Reward(value=Decimal("0.5")),
            ),
        ],
        global_guards=[maturity_guard],
        metadata=metadata,
    )


def hana_run_context() -> EvaluationContext:
    return EvaluationContext(
        as_of=date(2027, 4, 15),
        subscription_date=_SUBSCRIPTION_DATE,
        maturity_date=_MATURITY_DATE,
    )


def _fact(
    fact_id: str,
    fact_type: str,
    value: object,
    *,
    reference: str,
    provenance: list[FactProvenance] | None = None,
    valid_from: date | None = None,
) -> UserFact:
    return UserFact(
        fact_id=fact_id,
        user_id=USER_ID,
        fact_type=fact_type,
        value=value,
        valid_from=valid_from,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        provenance=[FactProvenance(reference=reference), *(provenance or [])],
        collected_at=_COLLECTED_AT,
        confidence=1.0,
    )


def hana_run_user(distance_km: int = 523) -> UserFactStore:
    service_chain = [
        FactProvenance(
            reference="hana-health/consent/active",
            description="건강데이터 및 측정 동의 유지",
            attributes={"layer": "INSTITUTION_SERVICE"},
        ),
        FactProvenance(
            reference="hana-health/running-data/linked",
            description="허용된 앱·기기 달리기 기록 연동",
            attributes={"manual_input": False, "layer": "INSTITUTION_SERVICE"},
        ),
        FactProvenance(
            reference="hana-health/confirmed-distance/snapshot-20270415",
            description="하나 건강자산관리 화면 인정 누적거리",
            attributes={
                "confirmed_km": distance_km,
                "layer": "INSTITUTION_SERVICE",
            },
        ),
    ]
    return UserFactStore(
        user_id=USER_ID,
        facts=[
            _fact(
                "HANA-F-BIRTH",
                "BIRTH_DATE",
                date(1992, 6, 10),
                reference="virtual-mydata/user-profile",
            ),
            _fact(
                "HANA-F-ACCOUNT-COUNT",
                "HANA_RUN_ACTIVE_ACCOUNT_COUNT",
                0,
                valid_from=_SUBSCRIPTION_DATE,
                reference="virtual-hana/account-snapshot",
            ),
            _fact(
                "HANA-F-HEALTH-ACTIVE",
                "HANA_HEALTH_ASSET_MANAGEMENT_ACTIVE",
                True,
                valid_from=_SUBSCRIPTION_DATE,
                reference="hana-health/service-status",
                provenance=service_chain[:1],
            ),
            _fact(
                "HANA-F-DISTANCE",
                "HANA_CONFIRMED_RUNNING_DISTANCE_KM",
                distance_km,
                valid_from=date(2027, 4, 15),
                reference="hana-health/confirmed-running-distance",
                provenance=service_chain,
            ),
            _fact(
                "HANA-F-TERM",
                "TERMINATION_TYPE",
                "MATURED",
                valid_from=_MATURITY_DATE,
                reference="virtual-hana/termination",
            ),
        ],
    )
