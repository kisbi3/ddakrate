from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eligibility.schema.application_input import (
    Capability,
    HardConstraint,
    NumericPreference,
    Preference,
)
from eligibility.schema.enums import (
    ContributionFrequency,
    EligibilityBadge,
    EvaluationStatus,
    IntentConflictType,
    RankingComparability,
    RankingInputStatus,
    RankingObjective,
    PreSearchAnswerStatus,
    SearchSessionStatus,
    TermUnit,
    UserConditionStatus,
    VerificationBadge,
    VerificationLevel,
)
from eligibility.schema.condition_requirement import QuestionSpec, UserConditionState
from eligibility.schema.evaluation import MissingFactRequest, ProductEvaluation
from eligibility.schema.rule import SourceReference


class StrictSearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContributionPlan(StrictSearchModel):
    """Global user contribution preference shared across the search session.

    Product-specific ranking choices (for example a Kakao 26-week start amount)
    are stored separately as :class:`ProductContributionChoice` and overlaid only
    for the matching product during cashflow construction.  The legacy
    ``preferred_start_amount`` / ``incremental_amount`` fields remain for
    backward compatibility with direct evaluator callers, but ApplicationService
    ranking-input answers do not mutate them.
    """

    desired_periodic_amount: Decimal | None = Field(default=None, gt=0)
    maximum_affordable_periodic_amount: Decimal | None = Field(default=None, gt=0)
    frequency: ContributionFrequency | None = None
    selected_term_value: int | None = Field(default=None, gt=0)
    selected_term_unit: TermUnit | None = None
    term_strictness: Literal[
        "PREFERRED", "EXACT", "MAXIMUM", "MINIMUM", "ANY"
    ] | None = None
    preferred_start_amount: Decimal | None = Field(default=None, gt=0)
    incremental_amount: Decimal | None = Field(default=None, gt=0)
    currency: str = "KRW"
    tax_rate: Decimal = Field(default=Decimal("0.154"), ge=0, lt=1)

    @model_validator(mode="after")
    def validate_plan(self) -> "ContributionPlan":
        if (self.selected_term_value is None) != (self.selected_term_unit is None):
            raise ValueError("selected_term_value and selected_term_unit must be provided together")
        if self.term_strictness == "ANY" and self.selected_term_value is not None:
            raise ValueError("term_strictness ANY cannot be combined with a selected term")
        if (
            self.desired_periodic_amount is not None
            and self.maximum_affordable_periodic_amount is not None
            and self.desired_periodic_amount > self.maximum_affordable_periodic_amount
        ):
            raise ValueError(
                "desired_periodic_amount cannot exceed maximum_affordable_periodic_amount"
            )
        return self


class ProductContributionChoice(StrictSearchModel):
    """One product-scoped answer required to complete that product's cashflow.

    Identity is semantic and product scoped: ``(product_id, field)``.  This is
    deliberately not part of ``ProductSearchIntent.contribution_plan`` because a
    start amount or product-specific term selection must never leak into other
    candidates.  v0.4.4 adds append-only revision metadata while SearchSession
    continues to expose only the latest ACTIVE choice for each semantic key.
    """

    choice_id: str
    product_id: str
    field: str
    value: Any
    unit: str | None = None
    source_question_id: str
    answered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)
    record_status: Literal["ACTIVE", "SUPERSEDED"] = "ACTIVE"
    supersedes_choice_id: str | None = None


class ContributionPlanPatch(StrictSearchModel):
    desired_periodic_amount: Decimal | None = Field(default=None, gt=0)
    maximum_affordable_periodic_amount: Decimal | None = Field(default=None, gt=0)
    frequency: ContributionFrequency | None = None
    selected_term_value: int | None = Field(default=None, gt=0)
    selected_term_unit: TermUnit | None = None
    term_strictness: Literal[
        "PREFERRED", "EXACT", "MAXIMUM", "MINIMUM", "ANY"
    ] | None = None
    preferred_start_amount: Decimal | None = Field(default=None, gt=0)
    incremental_amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = None
    tax_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    remove_fields: list[str] = Field(default_factory=list)


class IntentPatch(StrictSearchModel):
    """Patch semantics for follow-up user utterances.

    Upserts affect only matching keys; omitted categories remain untouched.
    Explicit removal is represented separately so a short follow-up does not
    silently erase unrelated intent state.
    """

    upsert_product_types: list[Literal["INSTALLMENT_SAVINGS", "TIME_DEPOSIT", "PARKING_ACCOUNT", "CMA"]] = Field(
        default_factory=list
    )
    remove_product_types: list[Literal["INSTALLMENT_SAVINGS", "TIME_DEPOSIT", "PARKING_ACCOUNT", "CMA"]] = Field(
        default_factory=list
    )
    # Institution selection is an explicit catalog-level operation.  It is kept
    # separate from INSTITUTION_SECTOR: the latter means a sector such as BANK,
    # whereas this field means a concrete institution such as BK_SH.
    upsert_excluded_institution_ids: list[str] = Field(default_factory=list)
    remove_excluded_institution_ids: list[str] = Field(default_factory=list)
    upsert_hard_constraints: list[HardConstraint] = Field(default_factory=list)
    remove_hard_constraint_keys: list[str] = Field(default_factory=list)
    upsert_preferences: list[Preference] = Field(default_factory=list)
    remove_preference_keys: list[str] = Field(default_factory=list)
    upsert_capabilities: list[Capability] = Field(default_factory=list)
    remove_capability_keys: list[str] = Field(default_factory=list)
    upsert_numeric_preferences: list[NumericPreference] = Field(default_factory=list)
    remove_numeric_preference_keys: list[str] = Field(default_factory=list)
    contribution_plan_patch: ContributionPlanPatch | None = None
    ranking_objective_patch: RankingObjective | None = None
    requested_top_k_patch: int | None = Field(default=None, ge=1, le=20)
    application_capacity_patch: Literal[
        "INDIVIDUAL", "SOLE_PROPRIETOR", "CORPORATION"
    ] | None = None


class ProductSearchIntent(StrictSearchModel):
    search_intent_id: str
    user_id: str
    product_types: list[Literal["INSTALLMENT_SAVINGS", "TIME_DEPOSIT", "PARKING_ACCOUNT", "CMA"]] = Field(
        default_factory=list
    )
    excluded_institution_ids: list[str] = Field(default_factory=list)
    ranking_objective: RankingObjective = RankingObjective.MAX_REALIZABLE_RATE
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    numeric_preferences: list[NumericPreference] = Field(default_factory=list)
    contribution_plan: ContributionPlan | None = None
    application_capacity: Literal[
        "INDIVIDUAL", "SOLE_PROPRIETOR", "CORPORATION"
    ] | None = None
    requested_top_k: int = Field(default=5, ge=1, le=20)
    source_utterances: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProductSearchFacts(StrictSearchModel):
    """Stable search projection shared by every financial-product family.

    Category-specific canonical policies remain lossless in ``NormalizedProductData``.
    This projection exposes only typed facts that deterministic retrieval may safely
    compare; missing facts stay ``None`` and never become negative evidence.
    """

    product_id: str
    product_family: str
    product_subtype: str | None = None
    institution_sector: str | None = None
    sale_status: str | None = None
    open_ended: bool | None = None
    customer_scopes: list[str] = Field(default_factory=list)
    subscription_channels: list[str] = Field(default_factory=list)
    funding_type: str | None = None
    contribution_frequency: str | None = None
    return_kind: str | None = None
    protection_status: str | None = None
    reinvestment_mode: str | None = None
    balance_tier_method: str | None = None
    fee_waiver_available: bool | None = None
    interest_payment_method: str | None = None
    classifications: list[str] = Field(default_factory=list)


class RateEvaluationResult(StrictSearchModel):
    """Family-neutral financial result consumed by search and ranking.

    The legacy flattened fields on :class:`CandidateEvaluation` remain available
    for API compatibility. New search/ranking code should use this contract so a
    deposit, savings account, parking account, and CMA expose the same outcome
    without pretending that their internal calculations are identical.
    """

    schema_version: Literal["1.0"] = "1.0"
    product_id: str
    product_family: str
    calculation_mode: Literal[
        "INSTALLMENT_CASHFLOW",
        "LUMP_SUM_TERM",
        "ON_DEMAND_BALANCE",
        "CMA_POSTED_YIELD",
        "CMA_PERFORMANCE_LINKED",
        "GENERIC_RATE",
    ]
    return_kind: str | None = None
    calculation_status: Literal["CALCULATED", "UNSUPPORTED", "UNKNOWN"]
    calculation_reason: str | None = None
    eligibility_status: EvaluationStatus
    confirmed_rate: Decimal | None = None
    realizable_rate: Decimal | None = None
    user_specific_conditional_upper_rate: Decimal | None = None
    advertised_max_rate: Decimal | None = None
    rate_as_of: date | None = None
    selected_term_value: int | None = Field(default=None, gt=0)
    selected_term_unit: TermUnit | None = None
    scenario_principal: Decimal | None = Field(default=None, ge=0)
    customer_scope: str | None = None
    comparison_basis: Literal[
        "ANNUAL_PERCENT_RATE",
        "PERFORMANCE_LINKED_RETURN",
        "UNKNOWN",
    ] = "UNKNOWN"
    rate_comparable: bool = False
    interest_comparable: bool = False
    ranking_comparability: RankingComparability = RankingComparability.NOT_COMPARABLE
    applied_condition_ids: list[str] = Field(default_factory=list)
    missing_fact_ids: list[str] = Field(default_factory=list)


class IntentConflict(StrictSearchModel):
    conflict_id: str
    field: str
    conflict_type: IntentConflictType
    left_input: dict[str, Any]
    right_input: dict[str, Any]
    severity: Literal["ERROR", "WARNING"] = "ERROR"
    resolution_options: list[str] = Field(default_factory=list)


class ClarificationRequest(StrictSearchModel):
    clarification_id: str
    conflict_ids: list[str]
    allowed_resolutions: list[str]
    question_payload: dict[str, Any]
    question: str
    status: Literal["PENDING", "RESOLVED"] = "PENDING"


class ClarificationResolution(StrictSearchModel):
    clarification_id: str
    resolution: str
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SearchSession(StrictSearchModel):
    search_session_id: str
    user_id: str
    intent_version: int = Field(default=1, ge=1)
    status: SearchSessionStatus = SearchSessionStatus.INTENT_COLLECTION
    candidate_product_ids: list[str] = Field(default_factory=list)
    active_question_id: str | None = None
    answered_question_ids: list[str] = Field(default_factory=list)
    ranking_run_id: str | None = None
    recommendation_id: str | None = None
    product_contribution_choices: list[ProductContributionChoice] = Field(default_factory=list)
    excluded_product_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=20)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    condition_states: list[UserConditionState] = Field(default_factory=list)
    completion_reason: Literal[
        "STABLE_TOP3",
        "PROVISIONAL_USER_STOPPED",
        "PROVISIONAL_DATA_INCOMPLETE",
        "INSUFFICIENT_ELIGIBLE_PRODUCTS",
    ] | None = None


class CandidateFilterDecision(StrictSearchModel):
    product_id: str
    retained: bool
    reason_code: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class PlannedCashflow(StrictSearchModel):
    sequence_no: int = Field(ge=1)
    amount: Decimal = Field(gt=0)
    days_held: int = Field(ge=0)
    contribution_date: date | None = None


class ContributionProjection(StrictSearchModel):
    frequency: ContributionFrequency | None = None
    contribution_count: int = Field(default=0, ge=0)
    requested_periodic_amount: Decimal | None = None
    planned_periodic_amount: Decimal | None = None
    monthly_equivalent_amount: Decimal | None = None
    amount_difference: Decimal | None = None
    amount_difference_ratio: Decimal | None = None
    amount_match_status: Literal[
        "NOT_SPECIFIED",
        "EXACT",
        "WITHIN_TOLERANCE",
        "OUTSIDE_TOLERANCE",
        "INPUT_REQUIRED",
    ] = "NOT_SPECIFIED"
    product_choice_override: bool = False
    term_match_status: Literal[
        "NOT_SPECIFIED",
        "EXACT",
        "SELECTABLE_EXACT",
        "WITHIN_BOUNDS",
        "ALTERNATIVE_SHORTER",
        "MISMATCH",
    ] = "NOT_SPECIFIED"
    maximum_periodic_amount: Decimal | None = None
    initial_amount: Decimal | None = None
    increment_amount: Decimal | None = None
    estimated_total_principal: Decimal | None = None
    term_summary: str
    contribution_summary: str
    maximum_deposit_summary: str
    planned_contribution_summary: str
    cashflows: list[PlannedCashflow] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class MissingRankingInput(StrictSearchModel):
    input_id: str
    input_type: Literal["CONTRIBUTION_PLAN"] = "CONTRIBUTION_PLAN"
    product_id: str
    required_field: str
    allowed_options: list[Any] = Field(default_factory=list)
    reason: str
    affected_metric: Literal["ESTIMATED_AFTER_TAX_INTEREST"] = (
        "ESTIMATED_AFTER_TAX_INTEREST"
    )
    status: RankingInputStatus = RankingInputStatus.UNRESOLVED
    question: str




class ContributionOptionFeasibility(StrictSearchModel):
    """Deterministic affordability result for one product-scoped option."""

    product_id: str
    field: str
    option_value: Any
    status: Literal["FEASIBLE", "INFEASIBLE"]
    max_bucket_amount: Decimal | None = None
    affordability_limit: Decimal | None = None
    affordability_frequency: ContributionFrequency | None = None
    violation_bucket: str | None = None
    reason_code: str


class ContributionFeasibilityClarification(StrictSearchModel):
    """User decision required when contribution state conflicts with affordability.

    For a newly unresolved product input ``feasible_options`` may be empty and the
    user can adjust the global limit or exclude the product.  When a previously
    committed product-specific choice becomes stale after a global-state revision,
    v0.4.4 also exposes the currently feasible replacement options and allows
    ``CHANGE_PRODUCT_CONTRIBUTION_CHOICE``.
    """

    clarification_id: str
    product_id: str
    current_affordability_amount: Decimal
    current_affordability_frequency: ContributionFrequency
    field: str | None = None
    current_choice_id: str | None = None
    current_choice_value: Any | None = None
    feasible_options: list[Any] = Field(default_factory=list)
    minimum_required_affordability: Decimal | None = None
    reason_code: str = "ALL_CONTRIBUTION_OPTIONS_INFEASIBLE"
    allowed_resolutions: list[Literal[
        "CHANGE_PRODUCT_CONTRIBUTION_CHOICE",
        "ADJUST_GLOBAL_AFFORDABILITY",
        "EXCLUDE_PRODUCT",
    ]] = Field(
        default_factory=lambda: ["ADJUST_GLOBAL_AFFORDABILITY", "EXCLUDE_PRODUCT"]
    )
    question: str


class RankInterval(StrictSearchModel):
    best_possible_rank: int = Field(ge=1)
    worst_possible_rank: int = Field(ge=1)


class CandidateEvaluation(StrictSearchModel):
    product_id: str
    product_evaluation: ProductEvaluation
    confirmed_rate: Decimal | None = None
    realizable_rate: Decimal | None = None
    user_specific_conditional_upper_rate: Decimal | None = None
    confirmed_after_tax_interest: Decimal | None = None
    realizable_after_tax_interest: Decimal | None = None
    conditional_upper_after_tax_interest: Decimal | None = None
    confirmed_pre_tax_interest: Decimal | None = None
    conditional_upper_pre_tax_interest: Decimal | None = None
    estimated_total_principal: Decimal | None = None
    realizable_pre_tax_interest: Decimal | None = None
    preference_score: int = 0
    action_burden_score: int = Field(default=0, ge=0)
    material_unknown_count: int = Field(default=0, ge=0)
    unresolved_material_fact_ids: list[str] = Field(default_factory=list)
    contribution_projection: ContributionProjection
    ranking_comparability: RankingComparability = RankingComparability.COMPARABLE
    missing_ranking_inputs: list[MissingRankingInput] = Field(default_factory=list)
    contribution_option_feasibilities: list[ContributionOptionFeasibility] = Field(default_factory=list)
    contribution_feasibility_clarification: ContributionFeasibilityClarification | None = None
    rate_evaluation: RateEvaluationResult | None = None
    rank_interval: RankInterval | None = None
    eligibility_text_review_status: EvaluationStatus | None = None
    eligibility_text_review_reason_code: str | None = None
    eligibility_text_review_fingerprint: str | None = None
    semantic_interpretations: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def eligibility_status(self) -> EvaluationStatus:
        return self.product_evaluation.eligibility_status

    def model_copy(self, *, update=None, deep: bool = False):
        """Keep transitional flattened rate fields aligned with the common result.

        ``CandidateEvaluation`` predates ``RateEvaluationResult`` and remains a
        public compatibility DTO.  Callers that revise a legacy rate field via
        Pydantic's copy API should not leave the canonical nested result stale.
        """

        updates = dict(update or {})
        rate_result = self.rate_evaluation
        if rate_result is not None and "rate_evaluation" not in updates:
            rate_updates = {}
            for field in (
                "confirmed_rate",
                "realizable_rate",
                "user_specific_conditional_upper_rate",
            ):
                if field in updates:
                    rate_updates[field] = updates[field]
            if "ranking_comparability" in updates:
                comparability = updates["ranking_comparability"]
                rate_updates.update(
                    {
                        "ranking_comparability": comparability,
                        "interest_comparable": (
                            comparability == RankingComparability.COMPARABLE
                        ),
                    }
                )
            if rate_updates:
                updates["rate_evaluation"] = rate_result.model_copy(
                    update=rate_updates,
                    deep=deep,
                )
        return super().model_copy(update=updates, deep=deep)


class QuestionCandidate(StrictSearchModel):
    question_id: str
    fact_type: str
    # Canonical cross-product variable used for grouping and repeat
    # suppression. ``fact_type`` remains the concrete evaluator input for
    # backward-compatible consumers.
    family_id: str | None = None
    question_kind: Literal["FINANCIAL_FACT", "RANKING_INPUT", "CONTRIBUTION_FEASIBILITY"] = "FINANCIAL_FACT"
    request: MissingFactRequest | None = None
    ranking_input: MissingRankingInput | None = None
    feasibility_clarification: ContributionFeasibilityClarification | None = None
    affected_product_ids: list[str]
    affected_request_ids: list[str]
    ranking_impact: Decimal = Decimal("0")
    candidate_coverage: int = Field(default=0, ge=0)
    eligibility_impact: int = Field(default=0, ge=0)
    top3_eligibility_impact: int = Field(default=0, ge=0)
    reward_or_benefit_impact: Decimal = Decimal("0")
    branch_short_circuit_value: int = Field(default=0, ge=0)
    score: Decimal = Decimal("0")


class PlannedQuestion(StrictSearchModel):
    question_id: str
    question_kind: Literal[
        "PRE_SEARCH_PROFILE",
        "FINANCIAL_FACT",
        "RANKING_INPUT",
        "CONTRIBUTION_FEASIBILITY",
        "PROMOTION_RISK_PREFERENCE",
    ] = "FINANCIAL_FACT"
    request: MissingFactRequest | None = None
    ranking_input: MissingRankingInput | None = None
    feasibility_clarification: ContributionFeasibilityClarification | None = None
    question: str
    affected_product_ids: list[str]
    score: Decimal
    product_context: str | None = None
    explanation: str | None = None
    explanation_details: dict[str, Any] = Field(default_factory=dict)
    question_stage: Literal["PRE_SEARCH"] | None = None
    pre_search_key: str | None = None
    answer_mode: Literal["BINARY", "FREE_TEXT", "OPTIONS"] = "BINARY"
    confirmation_required: bool = False
    answer_examples: list[str] = Field(default_factory=list)
    question_spec: QuestionSpec | None = None


class PreSearchProfileEntry(StrictSearchModel):
    """Current answer state for one reusable pre-search profile dimension."""

    question_key: str
    answer_status: PreSearchAnswerStatus = PreSearchAnswerStatus.NOT_ASKED
    value: dict[str, Any] = Field(default_factory=dict)
    source_text: str | None = None
    rationale: str | None = None
    answered_at: datetime | None = None


class TopKStabilityResult(StrictSearchModel):
    stable: bool = False
    stable_membership: bool
    current_kth_realizable_score: Decimal | None = None
    maximum_nonselected_optimistic_score: Decimal | None = None
    material_internal_question_remaining: bool = False
    reason_code: str


class SemanticConditionMemo(StrictSearchModel):
    code: Literal["RULED_OUT", "NEEDS_INPUT", "INSUFFICIENT_FACTS", "NEEDS_OFFICIAL", "UNRESOLVED"]
    text: str


class RecommendationListItem(StrictSearchModel):
    rank: int = Field(ge=1)
    product_id: str
    institution_id: str | None = None
    institution_name: str
    institution_sector: str = "UNKNOWN"
    product_name: str
    product_type: str
    realizable_rate: Decimal | None = None
    user_specific_conditional_upper_rate: Decimal | None = None
    base_rate: Decimal | None = None
    advertised_max_rate: Decimal | None = None
    return_kind: str | None = None
    published_rate_label: str | None = None
    published_rate_summary: str | None = None
    published_rate_as_of: str | None = None
    term_summary: str
    contribution_summary: str
    maximum_deposit_summary: str
    planned_contribution_summary: str
    requested_periodic_amount: Decimal | None = None
    planned_periodic_amount: Decimal | None = None
    monthly_equivalent_amount: Decimal | None = None
    amount_difference: Decimal | None = None
    amount_difference_ratio: Decimal | None = None
    amount_match_status: str = "NOT_SPECIFIED"
    product_choice_override: bool = False
    term_match_status: str = "NOT_SPECIFIED"
    estimated_total_principal: Decimal | None = None
    estimated_pre_tax_interest: Decimal | None = None
    estimated_after_tax_interest: Decimal | None = None
    conditional_upper_pre_tax_interest: Decimal | None = None
    conditional_upper_after_tax_interest: Decimal | None = None
    ranking_comparability: RankingComparability = RankingComparability.COMPARABLE
    missing_ranking_input_count: int = Field(default=0, ge=0)
    eligibility_badge: EligibilityBadge
    verification_badge: VerificationBadge
    material_unknown_count: int = Field(ge=0)
    eligibility_text_review_status: EvaluationStatus | None = None
    eligibility_text_review_reason_code: str | None = None
    eligibility_text_review_fingerprint: str | None = None
    semantic_memos: list[SemanticConditionMemo] = Field(default_factory=list)


class RateContribution(StrictSearchModel):
    rule_id: str
    nominal_reward_pp: Decimal
    status: EvaluationStatus
    evidence_bucket: str
    included_in_confirmed: bool
    included_in_realizable: bool
    included_in_conditional_upper: bool


class RateCapAdjustment(StrictSearchModel):
    cap_pp: Decimal | None = None
    pre_cap_total_pp: Decimal | None = None
    cap_reduction_pp: Decimal | None = None
    post_cap_total_pp: Decimal | None = None


class RateBreakdownItem(StrictSearchModel):
    rule_id: str
    rule_label: str
    nominal_reward_pp: Decimal | None = None
    status: EvaluationStatus
    verification_level: VerificationLevel
    evidence_basis: str
    action_summary: str | None = None
    reason_code: str
    source_reference: SourceReference | None = None
    disclosure_id: str | None = None
    presentation: dict[str, Any] | None = None
    disclosure_verification_status: str | None = None
    user_condition_status: UserConditionStatus | None = None
    display_status: Literal[
        "적용 예상", "확인 전", "적용 안 함", "확인 완료"
    ]
    children: list["RateBreakdownItem"] = Field(default_factory=list)


class RecommendationReasonEntry(StrictSearchModel):
    code: str
    text: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class PersonalizedRecommendationReason(StrictSearchModel):
    positives: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    positive_entries: list[RecommendationReasonEntry] = Field(default_factory=list)
    limitation_entries: list[RecommendationReasonEntry] = Field(default_factory=list)
    action_entries: list[RecommendationReasonEntry] = Field(default_factory=list)
    unknown_entries: list[RecommendationReasonEntry] = Field(default_factory=list)


class ProductRecommendationDetail(StrictSearchModel):
    semantic_interpretations: list[dict[str, Any]] = Field(default_factory=list)
    recommendation_id: str
    search_session_id: str
    product_id: str
    product_name: str
    institution_name: str
    product_type: str
    rank: int
    ranking_objective: RankingObjective = RankingObjective.MAX_REALIZABLE_RATE
    eligibility_status: EvaluationStatus = EvaluationStatus.UNKNOWN
    material_unknown_count: int = Field(default=0, ge=0)
    confirmed_or_achievable_products_ahead: int = Field(default=0, ge=0)
    realizable_rate: Decimal | None = None
    confirmed_rate: Decimal | None = None
    advertised_max_rate: Decimal | None = None
    additional_possible_rate_pp: Decimal | None = Field(default=None, ge=0)
    product_subtype: str | None = None
    sale_status: str | None = None
    return_kind: str | None = None
    published_rate_label: str | None = None
    published_rate_summary: str | None = None
    published_rate_as_of: str | None = None
    calculation_method: str | None = None
    rate_calculation_status: str | None = None
    rate_calculation_reason: str | None = None
    rate_as_of: str | None = None
    target_customer_summary: str | None = None
    protection_status: str | None = None
    search_facts: ProductSearchFacts | None = None
    rate_evaluation: RateEvaluationResult | None = None
    term_policy: dict[str, Any] = Field(default_factory=dict)
    cash_flow_policy: dict[str, Any] = Field(default_factory=dict)
    fee_policy: dict[str, Any] = Field(default_factory=dict)
    tax_policy: dict[str, Any] = Field(default_factory=dict)
    liquidity_policy: dict[str, Any] = Field(default_factory=dict)
    rate_entries: list[dict[str, Any]] = Field(default_factory=list)
    preferential_conditions: list[dict[str, Any]] = Field(default_factory=list)
    data_gaps: list[dict[str, Any]] = Field(default_factory=list)
    official_sources: list[dict[str, Any]] = Field(default_factory=list)
    term_summary: str
    contribution_summary: str
    maximum_deposit_summary: str
    planned_contribution_summary: str
    estimated_total_principal: Decimal | None = None
    estimated_pre_tax_interest: Decimal | None = None
    estimated_after_tax_interest: Decimal | None = None
    ranking_comparability: RankingComparability = RankingComparability.COMPARABLE
    missing_ranking_inputs: list[MissingRankingInput] = Field(default_factory=list)
    rate_breakdown: list[RateBreakdownItem]
    rate_contributions: list[RateContribution]
    rate_cap_adjustment: RateCapAdjustment
    recommendation_reason: PersonalizedRecommendationReason
    explanation: str | None = None


class RankingResult(StrictSearchModel):
    ranking_run_id: str
    ranking_objective: RankingObjective
    items: list[RecommendationListItem]
    display_items: list[RecommendationListItem] = Field(default_factory=list)
    ordered_product_ids: list[str]
    stability: TopKStabilityResult
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProductRecommendationResult(StrictSearchModel):
    semantic_review: dict[str, Any] = Field(default_factory=dict)
    recommendation_id: str
    search_session_id: str
    ranking_objective: RankingObjective
    generated_at: datetime
    top_products: list[RecommendationListItem]
    ranked_products: list[RecommendationListItem] = Field(default_factory=list)
    recommendation_status: Literal["PROVISIONAL", "CONFIRMED"] = "PROVISIONAL"
    provisional_candidates: list[RecommendationListItem] = Field(default_factory=list)
    confirmed_top_products: list[RecommendationListItem] = Field(default_factory=list)
    unresolved_question_count: int = Field(default=0, ge=0)
    acknowledged_unknown_count: int = Field(default=0, ge=0)
    unresolved_global_assumptions: list[str] = Field(default_factory=list)
    ranking_explanation_refs: list[str] = Field(default_factory=list)
    eligibility_text_review_state: Literal[
        "DISABLED", "DEFERRED", "COMPLETE", "PROVISIONAL", "FAILED"
    ] = "DISABLED"
    eligibility_text_review_rounds: int = Field(default=0, ge=0)
    eligibility_text_review_pending_count: int = Field(default=0, ge=0)


RateBreakdownItem.model_rebuild()
