from __future__ import annotations

from datetime import date
from enum import StrEnum
from decimal import Decimal
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eligibility.schema.application_input import ApplicationScalar
from eligibility.schema.enums import UserConditionStatus
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
    PROPOSE_ACTIVE_QUESTION_ANSWER = "PROPOSE_ACTIVE_QUESTION_ANSWER"
    SKIP_ACTIVE_QUESTION = "SKIP_ACTIVE_QUESTION"
    EXPLAIN_ACTIVE_QUESTION = "EXPLAIN_ACTIVE_QUESTION"
    EXCLUDE_PRODUCT = "EXCLUDE_PRODUCT"
    NO_OP = "NO_OP"
    SUBMIT_PRE_SEARCH_ANSWER = "SUBMIT_PRE_SEARCH_ANSWER"
    ACKNOWLEDGE_PRE_SEARCH_UNKNOWN = "ACKNOWLEDGE_PRE_SEARCH_UNKNOWN"
    SET_PRODUCT_FEATURE_POLICY = "SET_PRODUCT_FEATURE_POLICY"
    CLEAR_PRODUCT_FEATURE_POLICY = "CLEAR_PRODUCT_FEATURE_POLICY"
    REVERT_DECISIONS = "REVERT_DECISIONS"


class ProductFeaturePolicy(StrEnum):
    EXCLUDE = "EXCLUDE"
    PREFER_ABSENT = "PREFER_ABSENT"
    ALLOW = "ALLOW"


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


class QuantitativeConditionValue(BaseModel):
    """Strict amount value for threshold-based user conditions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal = Field(ge=0)
    currency: Literal["KRW"] = "KRW"
    period: Literal["DAY", "WEEK", "MONTH", "YEAR"] | None = None


ConditionAnswerValue: TypeAlias = ApplicationScalar | QuantitativeConditionValue


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
    confirmation_question: str | None = None
    assistant_message: str | None = None
    feature_id: str | None = None
    feature_policy: ProductFeaturePolicy | None = None
    decision_ids: list[str] = Field(default_factory=list)

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
        if op == ConversationOperation.PROPOSE_ACTIVE_QUESTION_ANSWER:
            if not isinstance(self.answer, bool) or not self.confirmation_question:
                raise ValueError(
                    "PROPOSE_ACTIVE_QUESTION_ANSWER requires a boolean answer and confirmation_question"
                )
        # Some structured-model responses redundantly put the turn-level
        # assistant message on a mutating action.  The backend never exposes
        # action-level text unless this is EXPLAIN_ACTIVE_QUESTION, so accept
        # and safely ignore that redundant field instead of rejecting an
        # otherwise valid user command.
        if op == ConversationOperation.EXCLUDE_PRODUCT and not self.product_id:
            raise ValueError("EXCLUDE_PRODUCT requires product_id")
        if op == ConversationOperation.SET_PRODUCT_FEATURE_POLICY:
            if not self.feature_id or self.feature_policy is None:
                raise ValueError(
                    "SET_PRODUCT_FEATURE_POLICY requires feature_id and feature_policy"
                )
        if op == ConversationOperation.CLEAR_PRODUCT_FEATURE_POLICY and not self.feature_id:
            raise ValueError("CLEAR_PRODUCT_FEATURE_POLICY requires feature_id")
        if op == ConversationOperation.REVERT_DECISIONS and not self.decision_ids:
            raise ValueError("REVERT_DECISIONS requires decision_ids")
        return self


class ConversationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actions: list[ConversationAction] = Field(min_length=1)


class ActiveFinancialFactPlan(BaseModel):
    """Small OpenAI boundary for a reply to the currently displayed fact question.

    The active question already fixes the target fact and allowed operation, so
    sending the universal ConversationPlan schema for every yes/no-style reply is
    unnecessary. ApplicationService converts this classification back into the
    regular domain ConversationPlan before changing any state.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resolution: Literal[
        "ANSWER_TRUE",
        "ANSWER_FALSE",
        "PROPOSE_TRUE",
        "PROPOSE_FALSE",
        "ACKNOWLEDGED_UNKNOWN",
        "EXPLAIN",
        "NOT_AN_ANSWER",
    ]
    rationale: str | None = None
    confirmation_question: str | None = None
    assistant_message: str | None = None

    @model_validator(mode="after")
    def validate_confirmation_question(self) -> "ActiveFinancialFactPlan":
        proposing = self.resolution in {"PROPOSE_TRUE", "PROPOSE_FALSE"}
        if proposing and not self.confirmation_question:
            raise ValueError(
                "PROPOSE_TRUE/PROPOSE_FALSE requires confirmation_question"
            )
        if not proposing and self.confirmation_question is not None:
            raise ValueError(
                "confirmation_question is only valid for a proposed answer"
            )
        explaining = self.resolution == "EXPLAIN"
        if explaining and not self.assistant_message:
            raise ValueError("EXPLAIN requires assistant_message")
        if not explaining and self.assistant_message is not None:
            raise ValueError("assistant_message is only valid for EXPLAIN")
        return self


class ActiveQuestionAnswer(BaseModel):
    """Answer to the exact backend-owned active condition question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    state: UserConditionStatus
    value: ConditionAnswerValue | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "ActiveQuestionAnswer":
        if self.state == UserConditionStatus.VERIFIED:
            raise ValueError("AnswerPlan cannot create VERIFIED state")
        if self.state in {
            UserConditionStatus.DECLARED_FEASIBLE,
        } and self.value is None:
            raise ValueError(f"{self.state.value} requires value")
        if self.state == UserConditionStatus.NOT_ASKED:
            raise ValueError("AnswerPlan cannot submit NOT_ASKED")
        return self


class AnswerPlanUpdate(BaseModel):
    """One allowlisted side effect extracted alongside the active answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["INSTITUTION_EXCLUSION", "USER_CONDITION"]
    institution_id: str | None = None
    excluded: bool | None = None
    variable_id: str | None = None
    state: UserConditionStatus | None = None
    value: ConditionAnswerValue | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "AnswerPlanUpdate":
        if self.kind == "INSTITUTION_EXCLUSION":
            if self.institution_id is None or self.excluded is None:
                raise ValueError(
                    "INSTITUTION_EXCLUSION requires institution_id and excluded"
                )
            if self.variable_id is not None or self.state is not None:
                raise ValueError("INSTITUTION_EXCLUSION cannot update a condition")
        else:
            if self.variable_id is None or self.state is None:
                raise ValueError("USER_CONDITION requires variable_id and state")
            if self.institution_id is not None or self.excluded is not None:
                raise ValueError("USER_CONDITION cannot update an institution")
            if self.state in {
                UserConditionStatus.DECLARED_FEASIBLE,
            } and self.value is None:
                raise ValueError(f"{self.state.value} requires value")
            if self.state == UserConditionStatus.VERIFIED:
                raise ValueError("AnswerPlan cannot create VERIFIED state")
            if self.state == UserConditionStatus.NOT_ASKED:
                raise ValueError("AnswerPlan cannot submit NOT_ASKED")
        return self


class AnswerPlan(BaseModel):
    """Narrow LLM output; backend validates IDs and performs every mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    active_question_answer: ActiveQuestionAnswer | None = None
    additional_updates: list[AnswerPlanUpdate] = Field(default_factory=list)
    unresolved_fragments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_updates(self) -> "AnswerPlan":
        keys: set[tuple[str, str]] = set()
        for update in self.additional_updates:
            identifier = update.institution_id or update.variable_id or ""
            key = (update.kind, identifier)
            if key in keys:
                raise ValueError(f"Duplicate AnswerPlan update: {key}")
            keys.add(key)
        return self


class PreSearchAnswerPlan(BaseModel):
    """Difference-only interpretation of one deterministic pre-search answer.

    The backend owns which question is active and its wording.  The model may
    only extract values supplied in the current answer; omitted fields leave the
    existing profile untouched.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resolution: Literal[
        "ANSWER",
        "ACKNOWLEDGED_UNKNOWN",
        "NOT_APPLICABLE",
        "EXPLAIN",
        "NOT_AN_ANSWER",
    ]
    product_types: list[Literal["INSTALLMENT_SAVINGS", "TIME_DEPOSIT", "PARKING_ACCOUNT", "CMA"]] = Field(
        default_factory=list
    )
    application_capacity: Literal[
        "INDIVIDUAL", "SOLE_PROPRIETOR", "CORPORATION"
    ] | None = None
    foreign_national: bool | None = None
    birth_date: date | None = None
    youth_policy_account_held: bool | None = None
    soldier_tomorrow_savings_eligible: bool | None = None
    desired_amount_krw: int | None = Field(default=None, gt=0)
    maximum_amount_krw: int | None = Field(default=None, gt=0)
    contribution_frequency: Literal["DAILY", "WEEKLY", "MONTHLY", "FLEXIBLE"] | None = None
    term_value: int | None = Field(default=None, gt=0)
    term_unit: Literal["DAY", "WEEK", "MONTH", "YEAR"] | None = None
    term_strictness: Literal[
        "PREFERRED", "EXACT", "MAXIMUM", "MINIMUM", "ANY"
    ] | None = None
    institution_scope: Literal[
        "FIRST_SECTOR_ONLY",
        "PREFER_FIRST_SECTOR",
        "BANKS_AND_SAVINGS_BANKS",
        "BANKS_AND_SECURITIES",
        "SECURITIES_ONLY",
        "ANY",
    ] | None = None
    liquid_product_scope: Literal[
        "PARKING_ONLY",
        "INCLUDE_CMA",
        "CMA_ONLY",
    ] | None = None
    willingness: Literal["WILLING", "UNWILLING", "CONDITIONAL"] | None = None
    first_transaction_willingness: Literal[
        "WILLING", "UNWILLING", "CONDITIONAL", "NOT_APPLICABLE"
    ] | None = None
    salary_transfer_willingness: Literal[
        "WILLING", "UNWILLING", "CONDITIONAL", "NOT_APPLICABLE"
    ] | None = None
    card_willingness: Literal[
        "WILLING", "UNWILLING", "CONDITIONAL", "NOT_APPLICABLE"
    ] | None = None
    prior_product_holding_institutions: list[str] = Field(default_factory=list)
    no_prior_product_holding_institutions: bool | None = None
    current_institution: str | None = None
    liquidity_preference: Literal[
        "CAN_HOLD_TO_MATURITY",
        "MAY_NEED_WITHDRAWAL",
    ] | None = None
    rationale: str | None = None
    assistant_message: str | None = None

    @model_validator(mode="after")
    def validate_term_pair(self) -> "PreSearchAnswerPlan":
        if (self.term_value is None) != (self.term_unit is None):
            raise ValueError("term_value and term_unit must be supplied together")
        explaining = self.resolution == "EXPLAIN"
        if explaining and not self.assistant_message:
            raise ValueError("EXPLAIN requires assistant_message")
        if not explaining and self.assistant_message is not None:
            raise ValueError("assistant_message is only valid for EXPLAIN")
        return self


class PreSearchProfileUpdate(PreSearchAnswerPlan):
    """One explicit profile fact extracted independently of question order."""

    question_key: str
    resolution: Literal[
        "ANSWER",
        "ACKNOWLEDGED_UNKNOWN",
        "NOT_APPLICABLE",
    ]
    assistant_message: None = None


class FlexibleConversationTurnPlan(BaseModel):
    """Single-call contract for a complete natural-language user turn.

    The model interprets user intent only. Deterministic workflow question
    selection and presentation remain backend responsibilities.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_updates: list[PreSearchProfileUpdate] = Field(default_factory=list)
    actions: list[ConversationAction] = Field(default_factory=list)
    assistant_message: str | None = None

    @model_validator(mode="after")
    def validate_turn_plan(self) -> "FlexibleConversationTurnPlan":
        seen: dict[str, dict] = {}
        for update in self.profile_updates:
            payload = update.model_dump(mode="json", exclude={"rationale"})
            prior = seen.get(update.question_key)
            if prior is not None and prior != payload:
                raise ValueError(
                    f"Conflicting profile_updates for {update.question_key}"
                )
            seen[update.question_key] = payload
        if self.assistant_message is not None and not self.assistant_message.strip():
            raise ValueError("assistant_message must not be blank")
        return self


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
