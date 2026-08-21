from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from eligibility.schema.enums import (
    ContributionFrequency,
    ContributionMode,
    InterestPaymentMethod,
    SaleStatus,
    SubscriptionChannel,
    TermUnit,
)
from eligibility.schema.product import (
    ContractTerm,
    ContributionPolicy,
    ProductMetadata,
)
from eligibility.schema.rule import SourceReference


def test_monthly_flexible_product_metadata_serialization():
    metadata = ProductMetadata(
        institution_id="SHINHAN_BANK",
        product_id="MONTHLY-FLEX",
        product_name="월 자유적립 상품",
        product_type="INSTALLMENT_SAVINGS",
        sale_status=SaleStatus.ON_SALE,
        min_term=ContractTerm(value=6, unit=TermUnit.MONTH),
        max_term=ContractTerm(value=12, unit=TermUnit.MONTH),
        available_terms=[
            ContractTerm(value=6, unit=TermUnit.MONTH),
            ContractTerm(value=12, unit=TermUnit.MONTH),
        ],
        contribution_policy=ContributionPolicy(
            periodic_amount_min=Decimal("1000"),
            periodic_amount_max=Decimal("300000"),
            contribution_frequency=ContributionFrequency.MONTHLY,
            contribution_mode=ContributionMode.FLEXIBLE,
        ),
        base_rate=Decimal("3.0"),
        advertised_max_rate=Decimal("5.0"),
        preferential_rate_cap=Decimal("2.0"),
        allowed_channels=[
            SubscriptionChannel.MOBILE,
            SubscriptionChannel.BRANCH,
        ],
        target_customer_summary="실명의 개인",
        one_account_per_person=True,
        sale_start=date(2026, 1, 1),
        sale_end=date(2026, 12, 31),
        early_termination_policy="중도해지이율 적용",
        partial_withdrawal_policy="불가",
        interest_payment_method=InterestPaymentMethod.AT_MATURITY,
        tax_treatment={"default": "GENERAL_TAX"},
        effective_from=date(2026, 1, 1),
        source_reference=SourceReference(
            document="공식 상품설명서", page=1, section="상품개요"
        ),
    )

    payload = metadata.model_dump(mode="json")

    assert payload["contribution_policy"]["contribution_frequency"] == "MONTHLY"
    assert payload["contribution_policy"]["contribution_mode"] == "FLEXIBLE"
    assert payload["available_terms"][1] == {"value": 12, "unit": "MONTH"}
    assert payload["allowed_channels"] == ["MOBILE", "BRANCH"]


def test_weekly_incremental_product_and_amount_options():
    policy = ContributionPolicy(
        initial_amount_min=Decimal("1000"),
        initial_amount_max=Decimal("10000"),
        initial_amount_options=[
            Decimal("1000"),
            Decimal("2000"),
            Decimal("3000"),
            Decimal("5000"),
            Decimal("10000"),
        ],
        contribution_frequency=ContributionFrequency.WEEKLY,
        contribution_mode=ContributionMode.INCREMENTAL,
        increment_amount=Decimal("1000"),
        total_principal_limit=Decimal("351000"),
    )
    metadata = ProductMetadata(
        institution_id="KAKAOBANK",
        product_id="K26-META",
        product_name="26주적금",
        product_type="INSTALLMENT_SAVINGS",
        sale_status=SaleStatus.ON_SALE,
        min_term=ContractTerm(value=26, unit=TermUnit.WEEK),
        max_term=ContractTerm(value=26, unit=TermUnit.WEEK),
        available_terms=[ContractTerm(value=26, unit=TermUnit.WEEK)],
        contribution_policy=policy,
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("5.0"),
        preferential_rate_cap=Decimal("3.0"),
        allowed_channels=[SubscriptionChannel.MOBILE],
    )

    round_trip = ProductMetadata.model_validate(metadata.model_dump(mode="json"))

    assert round_trip.contribution_policy is not None
    assert round_trip.contribution_policy.increment_amount == Decimal("1000")
    assert round_trip.contribution_policy.initial_amount_options[-1] == Decimal("10000")
    assert round_trip.available_terms[0].unit == TermUnit.WEEK


def test_incremental_contribution_requires_increment_amount():
    with pytest.raises(ValidationError):
        ContributionPolicy(
            contribution_frequency=ContributionFrequency.WEEKLY,
            contribution_mode=ContributionMode.INCREMENTAL,
        )
