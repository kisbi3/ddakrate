from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eligibility.schema.enums import (
    FactSemanticType,
    FactSourceType,
    FxThresholdClassification,
    TermUnit,
)


class StrictFxModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannedMonetaryAmount(StrictFxModel):
    """User-entered planning amount, never authoritative settlement evidence."""

    amount: Decimal = Field(ge=0)
    currency: str
    period: TermUnit
    source: FactSourceType = FactSourceType.USER_DECLARED
    semantic_type: FactSemanticType

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO-style code")
        return normalized

    @model_validator(mode="after")
    def validate_planning_semantics(self) -> "PlannedMonetaryAmount":
        if self.semantic_type not in {
            FactSemanticType.FUTURE_INTENT,
            FactSemanticType.SELF_REPORTED_FACT,
        }:
            raise ValueError(
                "PlannedMonetaryAmount must be FUTURE_INTENT or SELF_REPORTED_FACT"
            )
        if self.source != FactSourceType.USER_DECLARED:
            raise ValueError("Planning amounts must remain USER_DECLARED")
        return self


class FxQuote(StrictFxModel):
    quote_id: str
    base_currency: str
    quote_currency: str
    rate: Decimal = Field(gt=0)
    provider: str
    quoted_at: datetime

    @field_validator("base_currency", "quote_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO-style code")
        return normalized


class FxThresholdPolicy(StrictFxModel):
    """Configurable safety band above a threshold.

    A planning estimate below the threshold is always BELOW_THRESHOLD. An
    estimate at or above the threshold but inside this safety band is
    NEAR_THRESHOLD because adverse FX movement can erase the buffer.
    """

    near_threshold_margin_ratio: Decimal = Field(default=Decimal("0.03"), ge=0)
    near_threshold_margin_absolute: Decimal = Field(default=Decimal("0"), ge=0)

    def required_buffer(self, threshold: Decimal) -> Decimal:
        return max(
            threshold * self.near_threshold_margin_ratio,
            self.near_threshold_margin_absolute,
        )


class FxEstimate(StrictFxModel):
    original_amount: Decimal
    original_currency: str
    estimated_amount: Decimal
    target_currency: str
    quote_reference: str
    target_threshold: Decimal
    distance_from_threshold: Decimal
    classification: FxThresholdClassification
    warning: str | None = None
    quoted_at: datetime
    provider: str
    is_planning_estimate: bool = True
    authoritative_for_final_reward: bool = False

    @field_validator("original_currency", "target_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()
