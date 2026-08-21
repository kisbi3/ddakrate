from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from eligibility.schema.enums import (
    EvaluationStatus,
    FactSemanticType,
    ResolutionStatus,
    ResolutionStrategy,
    VerificationLevel,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationContext(StrictModel):
    as_of: date
    subscription_date: date
    maturity_date: date
    business_holidays: set[date] = Field(default_factory=set)


class Progress(StrictModel):
    current: int | Decimal
    required: int | Decimal
    unit: str


class MissingFactImpact(StrictModel):
    rate_pp: Decimal | None = None


class MissingFactRequest(StrictModel):
    fact_type: str
    status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    resolution_strategy: ResolutionStrategy
    impact: MissingFactImpact | None = None
    required_source: str | None = None
    question: str | None = None
    requested_by_rule_id: str
    expected_semantic_type: FactSemanticType | None = None
    action_id: str | None = None
    reward_id: str | None = None
    grounding_terms: list[str] = Field(default_factory=list)
    # Stable Application Layer reference. Existing fixtures may omit it; the
    # UserAnswerMapper deterministically derives a reference in that case.
    missing_fact_id: str | None = None


class ProvenanceRecord(StrictModel):
    source_type: str
    reference: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RuleEvaluation(StrictModel):
    rule_id: str
    rule_name: str
    rule_type: str | None = None
    status: EvaluationStatus
    reason_code: str
    progress: Progress | None = None
    missing_facts: list[MissingFactRequest] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    source_provenance: list[ProvenanceRecord] = Field(default_factory=list)
    children: list["RuleEvaluation"] = Field(default_factory=list)
    # Kept as a compatibility/readability flag: it means self-reported evidence
    # participated, not that the whole evaluation ran in a provisional mode.
    is_provisional: bool = False
    verification_level: VerificationLevel = VerificationLevel.UNKNOWN
    evidence_levels: list[VerificationLevel] = Field(default_factory=list)


class AppliedReward(StrictModel):
    rule_id: str
    rule_name: str
    status: EvaluationStatus
    reward_pp: Decimal
    verification_level: VerificationLevel = VerificationLevel.UNKNOWN
    evidence_levels: list[VerificationLevel] = Field(default_factory=list)
    evidence_bucket: Literal[
        "VERIFIED",
        "SELF_REPORTED",
        "FUTURE_ACTION",
        "UNKNOWN",
        "UNSATISFIABLE",
    ] = "UNKNOWN"
    included_in_confirmed: bool
    included_in_realizable: bool
    included_in_user_specific_conditional_upper: bool

    @property
    def included_in_conditional_upper(self) -> bool:
        """v0.1 compatibility accessor; serialization uses the explicit name."""

        return self.included_in_user_specific_conditional_upper


class RateEvidenceBreakdown(StrictModel):
    base_rate: Decimal
    verified_reward_pp: Decimal = Decimal("0")
    self_reported_reward_pp: Decimal = Decimal("0")
    future_action_reward_pp: Decimal = Decimal("0")
    unknown_conditional_reward_pp: Decimal = Decimal("0")


class RateSummary(StrictModel):
    advertised_max_rate: Decimal
    confirmed_rate: Decimal
    realizable_rate: Decimal
    user_specific_conditional_upper_rate: Decimal
    preferential_cap: Decimal
    guard_status: EvaluationStatus
    applied_rewards: list[AppliedReward] = Field(default_factory=list)
    evidence_breakdown: RateEvidenceBreakdown

    @property
    def conditional_upper_rate(self) -> Decimal:
        """v0.1 compatibility accessor."""

        return self.user_specific_conditional_upper_rate


class InterestEstimate(StrictModel):
    annual_rate: Decimal
    total_principal: Decimal
    pre_tax_interest: Decimal
    tax: Decimal
    after_tax_interest: Decimal
    method: Literal[
        "MONTHLY_SIMPLE_APPROXIMATION",
        "DAY_COUNT_SIMPLE_APPROXIMATION",
    ] = "MONTHLY_SIMPLE_APPROXIMATION"
    assumptions: list[str] = Field(default_factory=list)


class ProductEvaluation(StrictModel):
    schema_version: Literal["0.3.2"] = "0.3.2"
    evaluation_id: str
    user_id: str
    product_id: str
    product_name: str
    as_of: date
    subscription_date: date
    eligibility_status: EvaluationStatus
    eligibility: RuleEvaluation
    preferential_rule_results: list[RuleEvaluation]
    global_guard_results: list[RuleEvaluation]
    rates: RateSummary
    missing_facts: list[MissingFactRequest] = Field(default_factory=list)
    interest_estimates: dict[str, InterestEstimate] = Field(default_factory=dict)
    is_provisional: bool = False
    verification_level: VerificationLevel = VerificationLevel.UNKNOWN
    evidence_levels: list[VerificationLevel] = Field(default_factory=list)
    self_reported_rule_ids: list[str] = Field(default_factory=list)
    user_intent_rule_ids: list[str] = Field(default_factory=list)
    # Compatibility alias for v0.3.1 clients. This is exactly the set of rules
    # that used SELF_REPORTED evidence and is no longer tied to a global mode.
    provisional_rule_ids: list[str] = Field(default_factory=list)
