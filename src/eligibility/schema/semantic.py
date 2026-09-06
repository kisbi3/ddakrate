"""Bounded, source-linked semantic condition/answer contracts.

No reward, executable code, free-form fact identifier or authoritative user fact
can be supplied by the compiler. UNKNOWN is a first-class expression leaf.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

SemanticVariable = Literal["CHILDREN", "MARRIAGE_DATE", "PREGNANT_SELF"]
Comparator = Literal["EQ", "NE", "GT", "GTE", "LT", "LTE"]


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


class SemanticExpression(SemanticModel):
    op: Literal["ALL", "ANY", "NOT", "CHILD_COUNT", "CHILD_EXISTS", "COMPARE", "UNKNOWN"]
    source_quote: str = Field(min_length=1, max_length=8000)
    children: list["SemanticExpression"] = Field(default_factory=list, max_length=12)
    variable: Literal["MARRIAGE_DATE", "PREGNANT_SELF"] | None = None
    comparator: Comparator | None = None
    expected: StrictInt | StrictBool | str | None = None
    child_filter: ChildFilter | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.op in {"ALL", "ANY", "NOT"}:
            if not self.children or (self.op == "NOT" and len(self.children) != 1):
                raise ValueError("Invalid logical expression arity")
            if any(x is not None for x in (self.variable, self.comparator, self.expected, self.child_filter)):
                raise ValueError("Logical nodes cannot carry a predicate")
        elif self.op == "UNKNOWN":
            if self.children or any(x is not None for x in (self.variable, self.comparator, self.expected, self.child_filter)):
                raise ValueError("UNKNOWN is an unresolved leaf")
        elif self.op == "CHILD_EXISTS":
            if self.children or self.variable is not None or self.comparator is not None or self.expected is not None:
                raise ValueError("Existence is a non-numeric child predicate")
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

    @model_validator(mode="after")
    def explained(self):
        if self.expression is None and not self.unresolved_reason:
            raise ValueError("An uncompiled clause needs an unresolved reason")
        return self


class SemanticCompilation(SemanticModel):
    clauses: list[ClauseInterpretation] = Field(max_length=48)


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


class LooseClauseInterpretation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clause_id: str | None = None
    source_hash: str | None = None
    source_quote: str | None = None
    expression: LooseSemanticExpression | None = None
    unresolved_reason: str | None = None


class LooseSemanticCompilation(BaseModel):
    """Lenient LLM envelope. Per-clause validation stays strict."""

    model_config = ConfigDict(extra="ignore")

    clauses: list[LooseClauseInterpretation] = Field(default_factory=list, max_length=48)


class SemanticReviewState(SemanticModel):
    status: Literal["DISABLED", "DEFERRED", "PENDING", "PARTIAL", "COMPLETE", "FAILED"] = "DISABLED"
    reviewed_product_ids: list[str] = Field(default_factory=list)
    pending_product_count: int = 0
    unresolved_clause_count: int = 0
    ai_interpreted: bool = False
    calls_this_turn: int = 0
    error_code: str | None = None
