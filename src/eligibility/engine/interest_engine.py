from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from eligibility.schema.evaluation import InterestEstimate
from eligibility.schema.product import ContributionSchedulePlan, MonthlyContributionPlan


_ONE_WON = Decimal("1")


class InterestEngine:
    """Deterministic simple monthly approximation for the MVP."""

    @staticmethod
    def estimate_installment_savings(
        plan: MonthlyContributionPlan,
        annual_rate_percent: Decimal,
    ) -> InterestEstimate:
        # For monthly contributions at the start of each simplified period:
        # amount * annual_rate * (months + ... + 1) / 12.
        month_weight_sum = Decimal(plan.months * (plan.months + 1) // 2)
        rate_ratio = annual_rate_percent / Decimal("100")
        pre_tax_exact = (
            plan.monthly_amount * rate_ratio * month_weight_sum / Decimal("12")
        )
        pre_tax = pre_tax_exact.quantize(_ONE_WON, rounding=ROUND_HALF_UP)
        tax = (pre_tax * plan.tax_rate).quantize(_ONE_WON, rounding=ROUND_HALF_UP)
        after_tax = pre_tax - tax
        total_principal = plan.monthly_amount * plan.months

        return InterestEstimate(
            annual_rate=annual_rate_percent,
            total_principal=total_principal,
            pre_tax_interest=pre_tax,
            tax=tax,
            after_tax_interest=after_tax,
            assumptions=[
                f"Monthly contribution: {plan.monthly_amount} KRW",
                f"Contribution count: {plan.months}",
                f"General tax rate: {plan.tax_rate}",
                "Actual bank interest may differ because banks use deposit dates, day counts, and rounding rules.",
            ],
        )

    @staticmethod
    def estimate_contribution_schedule(
        plan: ContributionSchedulePlan,
        annual_rate_percent: Decimal,
    ) -> InterestEstimate:
        """Estimate interest from explicit principal cashflows and holding days."""

        rate_ratio = annual_rate_percent / Decimal("100")
        pre_tax_exact = sum(
            cashflow.amount * rate_ratio * Decimal(cashflow.days_held) / Decimal("365")
            for cashflow in plan.cashflows
        )
        pre_tax = pre_tax_exact.quantize(_ONE_WON, rounding=ROUND_HALF_UP)
        tax = (pre_tax * plan.tax_rate).quantize(_ONE_WON, rounding=ROUND_HALF_UP)
        after_tax = pre_tax - tax
        total_principal = sum(cashflow.amount for cashflow in plan.cashflows)
        return InterestEstimate(
            annual_rate=annual_rate_percent,
            total_principal=total_principal,
            pre_tax_interest=pre_tax,
            tax=tax,
            after_tax_interest=after_tax,
            method="DAY_COUNT_SIMPLE_APPROXIMATION",
            assumptions=[
                f"Cashflow count: {len(plan.cashflows)}",
                f"Schedule: {plan.schedule_label}",
                f"General tax rate: {plan.tax_rate}",
                "Simple actual-day/365 approximation; bank day-count and rounding rules may differ.",
            ],
        )
