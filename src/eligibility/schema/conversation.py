from __future__ import annotations

from enum import StrEnum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eligibility.schema.application_input import ApplicationScalar
from eligibility.schema.search import (
    IntentPatch,
    PlannedQuestion,
    ProductRecommendationResult,
    SearchSession,
)


class ConversationOperation(StrEnum):
    """Narrow domain operations an LLM may select for a follow-up utterance.

    This is deliberately *not* a universal user-state patch language.  Each action
    maps to an existing ApplicationService domain operation with deterministic
    validation and state ownership.
    """

    UPDATE_SEARCH_INTENT = "UPDATE_SEARCH_INTENT"
    SET_PRODUCT_CONTRIBUTION_CHOICE = "SET_PRODUCT_CONTRIBUTION_CHOICE"
    SET_PRODUCT_EXCLUSION = "SET_PRODUCT_EXCLUSION"
    CLEAR_USER_DECLARED_FACT = "CLEAR_USER_DECLARED_FACT"
    CLEAR_PRODUCT_CONTRIBUTION_CHOICE = "CLEAR_PRODUCT_CONTRIBUTION_CHOICE"
    SHOW_CURRENT_RESULTS = "SHOW_CURRENT_RESULTS"
    # Backward-compatible v0.4.4 operation names remain valid for typed clients.
    REVISE_PRODUCT_CONTRIBUTION_CHOICE = "REVISE_PRODUCT_CONTRIBUTION_CHOICE"
    REVISE_USER_ANSWER = "REVISE_USER_ANSWER"
    REVISE_USER_DECLARED_FACT = "REVISE_USER_DECLARED_FACT"
    SUBMIT_ACTIVE_QUESTION_ANSWER = "SUBMIT_ACTIVE_QUESTION_ANSWER"
    SKIP_ACTIVE_QUESTION = "SKIP_ACTIVE_QUESTION"
    EXPLAIN_ACTIVE_QUESTION = "EXPLAIN_ACTIVE_QUESTION"
    EXCLUDE_PRODUCT = "EXCLUDE_PRODUCT"
    NO_OP = "NO_OP"


class ConversationStructuredAnswer(BaseModel):
    """Typed shape for active questions that need a resolution object.

    Free-form JSON is intentionally not allowed through the LLM boundary. The
    deterministic ApplicationService still validates the resolution against the
    active question and allowed options.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resolution: Literal[
        "CHANGE_PRODUCT_CONTRIBUTION_CHOICE",
        "ADJUST_GLOBAL_AFFORDABILITY",
        "EXCLUDE_PRODUCT",
    ]
    new_value: ApplicationScalar | None = None
    maximum_affordable_periodic_amount: ApplicationScalar | None = None


ConversationAnswer: TypeAlias = ApplicationScalar | ConversationStructuredAnswer


class ConversationAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: ConversationOperation
    intent_patch: IntentPatch | None = None
    product_id: str | None = None
    field: str | None = None
    new_value: ApplicationScalar | None = None
    request_reference: str | None = None
    fact_type: str | None = None
    answer: ConversationAnswer | None = None
    excluded: bool | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "ConversationAction":
        op = self.operation
        if op == ConversationOperation.UPDATE_SEARCH_INTENT and self.intent_patch is None:
            raise ValueError("UPDATE_SEARCH_INTENT requires intent_patch")
        if op in {
            ConversationOperation.SET_PRODUCT_CONTRIBUTION_CHOICE,
            ConversationOperation.REVISE_PRODUCT_CONTRIBUTION_CHOICE,
        }:
            if not self.product_id or not self.field or self.new_value is None:
                raise ValueError(
                    f"{op.value} requires product_id, field, new_value"
                )
        if op == ConversationOperation.SET_PRODUCT_EXCLUSION:
            if not self.product_id or self.excluded is None:
                raise ValueError("SET_PRODUCT_EXCLUSION requires product_id and excluded")
        if op == ConversationOperation.REVISE_USER_ANSWER:
            if not self.request_reference or self.new_value is None:
                raise ValueError("REVISE_USER_ANSWER requires request_reference and new_value")
        if op == ConversationOperation.REVISE_USER_DECLARED_FACT:
            if not self.fact_type or self.new_value is None:
                raise ValueError("REVISE_USER_DECLARED_FACT requires fact_type and new_value")
        if op == ConversationOperation.CLEAR_USER_DECLARED_FACT and not self.fact_type:
            raise ValueError("CLEAR_USER_DECLARED_FACT requires fact_type")
        if op == ConversationOperation.CLEAR_PRODUCT_CONTRIBUTION_CHOICE:
            if not self.product_id or not self.field:
                raise ValueError("CLEAR_PRODUCT_CONTRIBUTION_CHOICE requires product_id and field")
        if op == ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER and self.answer is None:
            raise ValueError("SUBMIT_ACTIVE_QUESTION_ANSWER requires answer")
        if op == ConversationOperation.EXCLUDE_PRODUCT and not self.product_id:
            raise ValueError("EXCLUDE_PRODUCT requires product_id")
        return self


class ConversationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actions: list[ConversationAction] = Field(min_length=1)


class ConversationTurnResult(BaseModel):
    """Structured Web handoff result for one natural-language follow-up turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    search_session_id: str
    message: str
    operations_executed: list[ConversationOperation] = Field(default_factory=list)
    session: SearchSession
    next_question: PlannedQuestion | None = None
    recommendations: ProductRecommendationResult | None = None
    current_results_requested: bool = False
    unresolved_warning: str | None = None
    assistant_message: str | None = None
