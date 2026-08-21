from __future__ import annotations

from decimal import Decimal

from eligibility.fx import (
    FxPlanningService,
    FxThresholdPolicy,
    MockFxQuoteProvider,
    PlannedMonetaryAmount,
)
from eligibility.schema.enums import (
    FactSemanticType,
    FactSourceType,
    FxThresholdClassification,
    TermUnit,
)


def _plan(amount: str) -> PlannedMonetaryAmount:
    return PlannedMonetaryAmount(
        amount=Decimal(amount),
        currency="KRW",
        period=TermUnit.YEAR,
        source=FactSourceType.USER_DECLARED,
        semantic_type=FactSemanticType.FUTURE_INTENT,
    )


def test_mock_fx_quote_conversion():
    service = FxPlanningService(
        MockFxQuoteProvider({("KRW", "USD"): Decimal("0.00075")})
    )

    estimate = service.estimate_threshold(
        _plan("1000000"),
        target_currency="USD",
        target_threshold=Decimal("500"),
    )

    assert estimate.estimated_amount == Decimal("750.00000")
    assert estimate.original_currency == "KRW"
    assert estimate.target_currency == "USD"
    assert estimate.provider == "MOCK_FX"
    assert estimate.is_planning_estimate is True
    assert estimate.authoritative_for_final_reward is False


def test_fx_threshold_classification_clearly_above():
    service = FxPlanningService(
        MockFxQuoteProvider({("KRW", "USD"): Decimal("0.00075")}),
        threshold_policy=FxThresholdPolicy(
            near_threshold_margin_ratio=Decimal("0.03")
        ),
    )

    estimate = service.estimate_threshold(
        _plan("14000000"),
        target_currency="USD",
        target_threshold=Decimal("10000"),
    )

    assert estimate.estimated_amount == Decimal("10500.00000")
    assert estimate.classification == FxThresholdClassification.CLEARLY_ABOVE
    assert estimate.warning is None


def test_fx_threshold_classification_near_threshold_has_warning():
    service = FxPlanningService(
        MockFxQuoteProvider({("KRW", "USD"): Decimal("0.000723")}),
        threshold_policy=FxThresholdPolicy(
            near_threshold_margin_ratio=Decimal("0.03")
        ),
    )

    estimate = service.estimate_threshold(
        _plan("14000000"),
        target_currency="USD",
        target_threshold=Decimal("10000"),
    )

    assert estimate.estimated_amount == Decimal("10122.000000")
    assert estimate.classification == FxThresholdClassification.NEAR_THRESHOLD
    assert estimate.warning == "FX_VOLATILITY_WARNING"
    assert estimate.distance_from_threshold == Decimal("122.000000")


def test_fx_threshold_classification_below_threshold():
    service = FxPlanningService(
        MockFxQuoteProvider({("KRW", "USD"): Decimal("0.00070")})
    )

    estimate = service.estimate_threshold(
        _plan("14000000"),
        target_currency="USD",
        target_threshold=Decimal("10000"),
    )

    assert estimate.estimated_amount == Decimal("9800.00000")
    assert estimate.classification == FxThresholdClassification.BELOW_THRESHOLD
    assert estimate.warning == "BELOW_REQUIRED_THRESHOLD"
