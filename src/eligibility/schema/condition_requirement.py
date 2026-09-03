"""Schemas for the reviewed condition index and session-local user state.

The product catalog still contains family-specific source data.  These small
schemas are the stable contract between that catalog, the evaluator and the
question planner; they intentionally do not contain any evaluation logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from eligibility.schema.enums import UserConditionStatus

class ConditionRequirement(BaseModel):
    """One reviewed, machine-evaluable condition attached to a product."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    product_id: str
    scope: Literal["ELIGIBILITY", "RATE_BENEFIT", "INFORMATION_ONLY"]
    variable_id: str
    subject_scope: Literal["USER", "ACCOUNT", "INSTITUTION", "PRODUCT"] = "USER"
    institution_id: str | None = None
    operator: str
    threshold: dict[str, Any] = Field(default_factory=dict)
    rate_effect: dict[str, Any] = Field(default_factory=dict)
    relationship: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    coverage_status: Literal["COMPLETE", "PARTIAL", "UNAVAILABLE"] = "COMPLETE"
    review_status: Literal["VERIFIED", "REVIEW_REQUIRED", "REJECTED"] = (
        "REVIEW_REQUIRED"
    )


class UserConditionState(BaseModel):
    """Latest session-local state for a canonical user variable.

    ``WILLING_UNSPECIFIED`` and ``ACKNOWLEDGED_UNKNOWN`` deliberately remain
    non-negative evidence.  The evaluator may use them for an optimistic bound,
    but never for the displayed/realizable rate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    variable_id: str
    status: UserConditionStatus = UserConditionStatus.NOT_ASKED
    value: Any | None = None
    source_turn_id: str | None = None
    question_id: str | None = None
    interpreter_version: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def applies_to_realizable(self) -> bool:
        return self.status in {
            UserConditionStatus.DECLARED_FEASIBLE,
            UserConditionStatus.VERIFIED,
        }

    @property
    def remains_optimistic(self) -> bool:
        return self.status is not UserConditionStatus.DECLINED


class QuestionSpec(BaseModel):
    """Backend-owned question plan consumed by a renderer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    family_id: str
    variable_id: str
    value_schema: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, str] = Field(default_factory=dict)
    bound_requirement_ids: list[str] = Field(default_factory=list)
    affected_product_ids: list[str] = Field(default_factory=list)
    options: list[Any] = Field(default_factory=list)
    prompt_template_id: str


class CompletionResult(BaseModel):
    """Deterministic result returned when the planner has no next question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Literal[
        "STABLE_TOP3",
        "PROVISIONAL_USER_STOPPED",
        "PROVISIONAL_DATA_INCOMPLETE",
        "INSUFFICIENT_ELIGIBLE_PRODUCTS",
    ]
    visible_top3_product_ids: list[str] = Field(default_factory=list)
    frontier_product_ids: list[str] = Field(default_factory=list)
    unresolved_question_count: int = Field(default=0, ge=0)


class RequirementCompilationMetrics(BaseModel):
    """Auditable coverage counters emitted by :mod:`requirement_compiler`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_count: int = Field(default=0, ge=0)
    source_condition_count: int = Field(default=0, ge=0)
    compiled_requirement_count: int = Field(default=0, ge=0)
    verified_count: int = Field(default=0, ge=0)
    review_required_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    complete_count: int = Field(default=0, ge=0)
    partial_count: int = Field(default=0, ge=0)
    unavailable_count: int = Field(default=0, ge=0)
    unmapped_count: int = Field(default=0, ge=0)
    unmapped_requirement_ids: list[str] = Field(default_factory=list)
    scope_counts: dict[str, int] = Field(default_factory=dict)
    variable_counts: dict[str, int] = Field(default_factory=dict)


class RequirementCompilationResult(BaseModel):
    """Pure compiler output; no catalog files are written by the compiler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    compiler_version: str
    requirements: list[ConditionRequirement] = Field(default_factory=list)
    metrics: RequirementCompilationMetrics
