from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Protocol

from eligibility.fx.models import FxQuote


class FxQuoteProvider(Protocol):
    """Replaceable provider boundary; the deterministic core owns no live rate."""

    def get_quote(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        quoted_at: datetime | None = None,
    ) -> FxQuote:
        ...


class MockFxQuoteProvider:
    def __init__(
        self,
        rates: Mapping[tuple[str, str], Decimal | str | int | float],
        *,
        provider_name: str = "MOCK_FX",
        default_quoted_at: datetime | None = None,
    ) -> None:
        self._rates = {
            (base.upper(), quote.upper()): Decimal(str(rate))
            for (base, quote), rate in rates.items()
        }
        if any(rate <= 0 for rate in self._rates.values()):
            raise ValueError("FX rates must be positive")
        self.provider_name = provider_name
        self.default_quoted_at = default_quoted_at or datetime(
            2026, 8, 20, 0, 0, tzinfo=timezone.utc
        )

    def get_quote(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        quoted_at: datetime | None = None,
    ) -> FxQuote:
        base = base_currency.upper()
        quote = quote_currency.upper()
        key = (base, quote)
        if key not in self._rates:
            raise LookupError(f"No mock FX quote configured for {base}/{quote}")
        at = quoted_at or self.default_quoted_at
        return FxQuote(
            quote_id=(
                f"FXQ-{self.provider_name}-{base}-{quote}-"
                f"{at.isoformat()}"
            ),
            base_currency=base,
            quote_currency=quote,
            rate=self._rates[key],
            provider=self.provider_name,
            quoted_at=at,
        )
