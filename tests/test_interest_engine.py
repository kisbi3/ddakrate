from __future__ import annotations

from decimal import Decimal

from eligibility.engine.interest_engine import InterestEngine
from eligibility.fixtures.shinhan_youth_first import golden_contribution_plan


def test_six_point_zero_five_percent_interest_matches_validation_example():
    estimate = InterestEngine.estimate_installment_savings(
        golden_contribution_plan(), Decimal("6.05")
    )

    assert estimate.total_principal == Decimal("3600000")
    assert estimate.pre_tax_interest == Decimal("117975")
    assert estimate.after_tax_interest == Decimal("99807")


def test_five_point_zero_five_percent_interest_matches_validation_example():
    estimate = InterestEngine.estimate_installment_savings(
        golden_contribution_plan(), Decimal("5.05")
    )

    assert estimate.pre_tax_interest == Decimal("98475")
    assert estimate.after_tax_interest == Decimal("83310")
