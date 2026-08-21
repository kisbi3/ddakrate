from __future__ import annotations

from decimal import Decimal

from eligibility.fx.models import (
    FxEstimate,
    FxThresholdPolicy,
    PlannedMonetaryAmount,
)
from eligibility.fx.provider import FxQuoteProvider
from eligibility.schema.enums import FxThresholdClassification


class FxPlanningService:
    """Deterministic planning conversion using a supplied quote provider."""

    def __init__(
        self,
        quote_provider: FxQuoteProvider,
        *,
        threshold_policy: FxThresholdPolicy | None = None,
    ) -> None:
        self.quote_provider = quote_provider
        self.threshold_policy = threshold_policy or FxThresholdPolicy()

    def estimate_threshold(
        self,
        planned: PlannedMonetaryAmount,
        *,
        target_currency: str,
        target_threshold: Decimal,
    ) -> FxEstimate:
        target = target_currency.strip().upper()
        threshold = Decimal(target_threshold)
        if threshold < 0:
            raise ValueError("target_threshold must not be negative")

        quote = self.quote_provider.get_quote(planned.currency, target)
        if (
            quote.base_currency != planned.currency
            or quote.quote_currency != target
        ):
            raise ValueError("FX provider returned a quote for the wrong currency pair")

        estimated = planned.amount * quote.rate
        distance = estimated - threshold
        classification, warning = self.classify(
            estimated_amount=estimated,
            target_threshold=threshold,
        )
        return FxEstimate(
            original_amount=planned.amount,
            original_currency=planned.currency,
            estimated_amount=estimated,
            target_currency=target,
            quote_reference=quote.quote_id,
            target_threshold=threshold,
            distance_from_threshold=distance,
            classification=classification,
            warning=warning,
            quoted_at=quote.quoted_at,
            provider=quote.provider,
        )

    def classify(
        self,
        *,
        estimated_amount: Decimal,
        target_threshold: Decimal,
    ) -> tuple[FxThresholdClassification, str | None]:
        estimated = Decimal(estimated_amount)
        threshold = Decimal(target_threshold)
        if estimated < threshold:
            return (
                FxThresholdClassification.BELOW_THRESHOLD,
                "BELOW_REQUIRED_THRESHOLD",
            )
        required_buffer = self.threshold_policy.required_buffer(threshold)
        if estimated - threshold < required_buffer:
            return (
                FxThresholdClassification.NEAR_THRESHOLD,
                "FX_VOLATILITY_WARNING",
            )
        return FxThresholdClassification.CLEARLY_ABOVE, None
