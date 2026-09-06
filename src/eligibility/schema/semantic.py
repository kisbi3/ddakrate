"""Bounded, source-linked semantic condition/answer contracts.

Question slots and condition structure are separate. The compiler may describe
card, salary, merchant, holding and consent predicates. It cannot supply
rewards, executable code, free-form fact identifiers, user facts or ranks.
UNKNOWN is a first-class expression leaf.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

SemanticVariable = Literal["CHILDREN", "MARRIAGE_DATE", "PREGNANT_SELF"]
Comparator = Literal["EQ", "NE", "GT", "GTE", "LT", "LTE"]
ConditionKind = Literal[
    "CARD_PAYMENT",
    "SALARY_DEPOSIT",
    "MERCHANT_SETTLEMENT",
    "HOLDING_HISTORY",
    "ABSENCE_HISTORY",
    "CONSENT",
    "CHILD_COUNT",
    "CHILD_EXISTS",
    "MARRIAGE_DATE",
    "PREGNANT_SELF",
    "OTHER",
]
SubjectRole = Literal["SELF", "CHILD", "SPOUSE", "HOUSEHOLD", "UNSPECIFIED"]
ScopeKind = Literal[
    "THIS_INSTITUTION",
    "THIS_PRODUCT",
    "PRODUCT_KIND",
    "ACCOUNT",
    "CARD_NETWORK",
    "UNSPECIFIED",
]
MetricKind = Literal["AMOUNT", "COUNT", "PERIOD", "RATIO", "BOOLEAN", "UNSPECIFIED"]
AmountUnit = Literal[
    "KRW", "CHEON_WON", "MAN_WON", "COUNT", "MONTH", "YEAR", "DAY", "BUSINESS_DAY",
    "PERCENT", "PERCENTAGE_POINT", "TERM_RATIO",
]
RewardRelationType = Literal["TIER_MAX", "INDEPENDENT_ADD"]
OfficialBasis = Literal["SOURCE_TEXT", "CATALOG_POLICY"]


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChildBirth(SemanticModel):
    birth_date: date | None = None
    birth_year: int | None = Field(default=None, ge=1900, le=2200)

    @model_validator(mode="after")
    def consistent(self):
        if self.birth_date and self.birth_year and self.birth_date.year != self.birth_year:
            raise ValueError("Child birth date and year disagree")
        return self


class ChildrenAnswer(SemanticModel):
    """A roster is complete only when the user explicitly supplied all children.

    Unknown/missing information is not the same as an empty family. Identifying
    names, registration numbers and documents are deliberately not accepted.
    """
    count: StrictInt | None = Field(default=None, ge=0, le=30)
    children: list[ChildBirth] = Field(default_factory=list, max_length=30)
    complete: StrictBool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.count is not None and self.count < len(self.children):
            raise ValueError("Child count is smaller than the supplied roster")
        if self.complete and self.count != len(self.children):
            raise ValueError("A complete roster requires the explicit matching count")
        return self


class ChildFilter(SemanticModel):
    reference: Literal["SUBSCRIPTION_DATE", "AS_OF"] = "SUBSCRIPTION_DATE"
    min_age: StrictInt | None = Field(default=None, ge=0, le=120)
    max_age: StrictInt | None = Field(default=None, ge=0, le=120)
    min_inclusive: StrictBool = True
    max_inclusive: StrictBool = True
    born_from: date | None = None
    born_to: date | None = None

    @model_validator(mode="after")
    def interval(self):
        if self.min_age is not None and self.max_age is not None and self.min_age > self.max_age:
            raise ValueError("Inverted age interval")
        if self.born_from and self.born_to and self.born_from > self.born_to:
            raise ValueError("Inverted birth interval")
        return self


class PeriodWindow(SemanticModel):
    """Aggregation window copied from source. Missing pieces stay None."""

    length: StrictInt | None = Field(default=None, ge=0, le=1200)
    unit: Literal["DAY", "MONTH", "YEAR", "BUSINESS_DAY"] | None = None
    basis: Literal[
        "SUBSCRIPTION_MONTH",
        "MATURITY_PREV_PREV_MONTH_END",
        "SALARY_DESIGNATED_DATE",
        "TERM_RATIO",
        "AS_OF",
        "UNSPECIFIED",
    ] | None = None
    start_quote: str | None = Field(default=None, max_length=500)
    end_quote: str | None = Field(default=None, max_length=500)
    before_offset: StrictInt | None = Field(default=None, ge=0, le=365)
    after_offset: StrictInt | None = Field(default=None, ge=0, le=365)
    term_ratio: str | None = Field(default=None, max_length=32)
    inclusive_start: StrictBool | None = None
    inclusive_end: StrictBool | None = None


class ConditionScope(SemanticModel):
    institution: ScopeKind = "UNSPECIFIED"
    product_kind: str | None = Field(default=None, max_length=80)
    account_or_product: str | None = Field(default=None, max_length=200)
    networks: list[str] = Field(default_factory=list, max_length=12)


class RewardRelation(SemanticModel):
    relation: RewardRelationType
    rule_ids: list[str] = Field(min_length=2, max_length=12)
    source_quote: str = Field(min_length=1, max_length=8000)


class SemanticExpression(SemanticModel):
    op: Literal["ALL", "ANY", "NOT", "CHILD_COUNT", "CHILD_EXISTS", "COMPARE", "PREDICATE", "UNKNOWN"]
    source_quote: str = Field(min_length=1, max_length=8000)
    children: list["SemanticExpression"] = Field(default_factory=list, max_length=12)
    variable: Literal["MARRIAGE_DATE", "PREGNANT_SELF"] | None = None
    comparator: Comparator | None = None
    expected: StrictInt | StrictBool | str | None = None
    child_filter: ChildFilter | None = None
    kind: ConditionKind | None = None
    subject: SubjectRole | None = None
    scope: ConditionScope | None = None
    metric: MetricKind | None = None
    expected_literal: str | None = Field(default=None, max_length=80)
    expected_unit: AmountUnit | None = None
    period: PeriodWindow | None = None
    existing_rule_id: str | None = Field(default=None, max_length=200)
    required_facts: list[str] = Field(default_factory=list, max_length=12)
    official_confirmation_required: StrictBool | None = None
    official_confirmation_basis: OfficialBasis | None = None

    @model_validator(mode="after")
    def shape(self):
        predicate_extras = (
            self.kind, self.subject, self.scope, self.metric, self.expected_literal,
            self.expected_unit, self.period, self.existing_rule_id,
            self.official_confirmation_required, self.official_confirmation_basis,
        )
        if self.op in {"ALL", "ANY", "NOT"}:
            if not self.children or (self.op == "NOT" and len(self.children) != 1):
                raise ValueError("Invalid logical expression arity")
            if any(x is not None for x in (self.variable, self.comparator, self.expected, self.child_filter)):
                raise ValueError("Logical nodes cannot carry a predicate")
            if any(x is not None for x in predicate_extras) or self.required_facts:
                raise ValueError("Logical nodes cannot carry a structured predicate")
        elif self.op == "UNKNOWN":
            if self.children or any(x is not None for x in (self.variable, self.comparator, self.expected, self.child_filter)):
                raise ValueError("UNKNOWN is an unresolved leaf")
            if any(x is not None for x in predicate_extras) or self.required_facts:
                raise ValueError("UNKNOWN is an unresolved leaf")
        elif self.op == "CHILD_EXISTS":
            if self.children or self.variable is not None or self.comparator is not None or self.expected is not None:
                raise ValueError("Existence is a non-numeric child predicate")
        elif self.op == "PREDICATE":
            if self.children or self.variable is not None or self.child_filter is not None:
                raise ValueError("PREDICATE is a structured leaf")
            if self.kind is None:
                raise ValueError("PREDICATE requires a condition kind")
        else:
            if self.children or self.comparator is None or self.expected is None:
                raise ValueError("Predicate requires a comparator and expected value")
            if self.op == "CHILD_COUNT":
                if type(self.expected) is not int or not 0 <= self.expected <= 30 or self.variable:
                    raise ValueError("Child count requires a bounded integer")
            elif self.child_filter is not None or self.variable is None:
                raise ValueError("Invalid scalar predicate")
            elif self.variable == "PREGNANT_SELF":
                if type(self.expected) is not bool or self.comparator not in {"EQ", "NE"}:
                    raise ValueError("Pregnancy predicate must compare booleans")
            else:
                if not isinstance(self.expected, str):
                    raise ValueError("Marriage predicate requires an ISO date")
                date.fromisoformat(self.expected)
        return self


class ClauseInterpretation(SemanticModel):
    clause_id: str
    source_hash: str
    source_quote: str = Field(min_length=1, max_length=8000)
    expression: SemanticExpression | None = None
    unresolved_reason: str | None = Field(default=None, max_length=1000)
    relations: list[RewardRelation] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def explained(self):
        if self.expression is None and not self.unresolved_reason:
            raise ValueError("An uncompiled clause needs an unresolved reason")
        return self


class SemanticCompilation(SemanticModel):
    clauses: list[ClauseInterpretation] = Field(max_length=48)


class LoosePeriodWindow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    length: StrictInt | None = None
    unit: str | None = None
    basis: str | None = None
    start_quote: str | None = None
    end_quote: str | None = None
    before_offset: StrictInt | None = None
    after_offset: StrictInt | None = None
    term_ratio: str | None = None
    inclusive_start: StrictBool | None = None
    inclusive_end: StrictBool | None = None


class LooseConditionScope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    institution: str | None = None
    product_kind: str | None = None
    account_or_product: str | None = None
    networks: list[str] = Field(default_factory=list)


class LooseRewardRelation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relation: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    source_quote: str | None = None


class LooseSemanticExpression(BaseModel):
    """Transport envelope. Invalid leaves are coerced to UNKNOWN, not applied."""

    model_config = ConfigDict(extra="ignore")

    op: str | None = None
    source_quote: str = ""
    children: list["LooseSemanticExpression"] = Field(default_factory=list)
    variable: str | None = None
    comparator: str | None = None
    expected: StrictInt | StrictBool | str | None = None
    child_filter: ChildFilter | None = None
    kind: str | None = None
    subject: str | None = None
    scope: LooseConditionScope | None = None
    metric: str | None = None
    expected_literal: str | None = None
    expected_unit: str | None = None
    period: LoosePeriodWindow | None = None
    existing_rule_id: str | None = None
    required_facts: list[str] = Field(default_factory=list)
    official_confirmation_required: StrictBool | None = None
    official_confirmation_basis: str | None = None


class LooseClauseInterpretation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clause_id: str | None = None
    source_hash: str | None = None
    source_quote: str | None = None
    expression: LooseSemanticExpression | None = None
    unresolved_reason: str | None = None
    relations: list[LooseRewardRelation] = Field(default_factory=list)


class LooseSemanticCompilation(BaseModel):
    """Lenient LLM envelope. Per-clause validation stays strict."""

    model_config = ConfigDict(extra="ignore")

    clauses: list[LooseClauseInterpretation] = Field(default_factory=list, max_length=48)


class SemanticReviewState(SemanticModel):
    """COMPLETE means challenger packets were read, not that every condition is judged."""

    status: Literal["DISABLED", "DEFERRED", "PENDING", "PARTIAL", "COMPLETE", "FAILED"] = "DISABLED"
    reviewed_product_ids: list[str] = Field(default_factory=list)
    pending_product_count: int = 0
    unresolved_clause_count: int = 0
    ai_interpreted: bool = False
    calls_this_turn: int = 0
    error_code: str | None = None
