"""Strict contracts for the top-ranked eligibility-text review call."""
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from eligibility.schema.enums import EvaluationStatus, FactSemanticType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EligibilityTextReviewReasonCode(StrEnum):
    TEXT_ALL_MANDATORY_CONDITIONS_SUPPORTED = "TEXT_ALL_MANDATORY_CONDITIONS_SUPPORTED"
    TEXT_EXPLICIT_REQUIRED_CONDITION_CONFLICT = "TEXT_EXPLICIT_REQUIRED_CONDITION_CONFLICT"
    TEXT_REQUIRED_USER_FACT_MISSING = "TEXT_REQUIRED_USER_FACT_MISSING"
    TEXT_ALLOWED_FUTURE_ACTION_CAN_RESOLVE = "TEXT_ALLOWED_FUTURE_ACTION_CAN_RESOLVE"
    TEXT_AMBIGUOUS_OR_INSUFFICIENT_EVIDENCE = "TEXT_AMBIGUOUS_OR_INSUFFICIENT_EVIDENCE"
    TEXT_NO_EXECUTABLE_ELIGIBILITY_CLAIM = "TEXT_NO_EXECUTABLE_ELIGIBILITY_CLAIM"


class EligibilityEvidenceSource(StrictModel):
    source_ref_id: str
    source_text: str


class EligibilityEvidenceBinding(StrictModel):
    source_ref_id: str
    source_text: str
    claim_role: Literal[
        "MANDATORY_ELIGIBILITY",
        "NON_ELIGIBILITY_CONTEXT",
        "NO_EXECUTABLE_ELIGIBILITY_CLAIM",
    ]
    applied_user_fact_ids: list[str] = Field(default_factory=list)


class EligibilityTextMissingFactDraft(StrictModel):
    fact_type: str
    semantic_type: FactSemanticType
    answer_mode: Literal["BINARY_OR_EXPLANATION", "FREE_TEXT", "OPTIONS"]
    question: str = Field(min_length=1)
    grounding_evidence_indexes: list[int] = Field(default_factory=list, min_length=1)


class EligibilityTextStructuredEvaluation(StrictModel):
    status: EvaluationStatus
    reason_codes: list[str] = Field(default_factory=list)


class EligibilityTextUserFactSnapshot(StrictModel):
    facts: list[dict] = Field(default_factory=list)
    pre_search_profile: dict = Field(default_factory=dict)


class EligibilityTextReviewProductInput(StrictModel):
    rank: int = Field(ge=1)
    product_id: str
    product_name: str
    product_version: int = Field(ge=1)
    eligibility_text_fingerprint: str
    eligibility_text: str = Field(min_length=1)
    structured_evaluation: EligibilityTextStructuredEvaluation
    source_refs: list[EligibilityEvidenceSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sources(self) -> "EligibilityTextReviewProductInput":
        if not self.source_refs:
            raise ValueError("eligibility text review requires at least one source ref")
        if any(source.source_text != self.eligibility_text for source in self.source_refs):
            raise ValueError("source ref text must exactly match eligibility_text")
        return self


class EligibilityTextReviewBatchInput(StrictModel):
    review_id: str
    as_of: date
    subscription_date: date
    global_policy_invariants: dict[str, str] = Field(default_factory=dict)
    user_fact_snapshot: EligibilityTextUserFactSnapshot
    products: list[EligibilityTextReviewProductInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_products(self) -> "EligibilityTextReviewBatchInput":
        ids = [p.product_id for p in self.products]
        if len(ids) != len(set(ids)):
            raise ValueError("products must contain unique product_id values")
        if any(p.rank > 3 for p in self.products):
            raise ValueError("eligibility text review input is limited to rank <= 3")
        return self


class ProductEligibilityTextReview(StrictModel):
    product_id: str
    product_version: int = Field(ge=1)
    eligibility_text_fingerprint: str
    status: EvaluationStatus
    reason_code: EligibilityTextReviewReasonCode
    evidence_bindings: list[EligibilityEvidenceBinding] = Field(default_factory=list)
    missing_facts: list[EligibilityTextMissingFactDraft] = Field(default_factory=list)


class EligibilityTextReviewBatch(StrictModel):
    review_id: str
    product_reviews: list[ProductEligibilityTextReview] = Field(min_length=1)
    assistant_message: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reviews(self) -> "EligibilityTextReviewBatch":
        if len({r.product_id for r in self.product_reviews}) != len(self.product_reviews):
            raise ValueError("product_reviews must contain unique product_id values")
        for review in self.product_reviews:
            if review.status == EvaluationStatus.SATISFIED and review.missing_facts:
                raise ValueError("SATISFIED review cannot contain missing_facts")
        return self


# Concise aliases used by adapters that refer to the envelope as a review
# request rather than an input batch.
EligibilityTextReviewInput = EligibilityTextReviewBatchInput
EligibilityTextReviewOutput = EligibilityTextReviewBatch
