from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from eligibility.schema.enums import (
    AchievementMode,
    ComparisonOperator,
    ContextDateField,
    EntityType,
    EvaluationPhase,
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    FactSubjectMode,
    PeriodUnit,
    ResolutionStrategy,
    RulePurpose,
    TermUnit,
    TimeExpressionType,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceReference(StrictModel):
    document: str
    document_id: str | None = None
    version_date: date | None = None
    page: int | None = None
    section: str | None = None
    source_url: str | None = None
    source_text: str | None = None


class RateImpact(StrictModel):
    rate_pp: Decimal


class MissingFactSpec(StrictModel):
    resolution_strategy: ResolutionStrategy
    impact: RateImpact | None = None
    required_source: str | None = None
    question: str | None = None
    expected_semantic_type: FactSemanticType | None = None
    action_id: str | None = None
    reward_id: str | None = None
    grounding_terms: list[str] = Field(default_factory=list)


class ActionDefinition(StrictModel):
    action_id: str
    description: str
    burden_score: int | None = Field(default=None, ge=0, le=5)


class ActionAmountRequirement(StrictModel):
    field: str
    amount: Decimal = Field(ge=0)
    currency: str = "KRW"
    description: str | None = None


class ActionDuration(StrictModel):
    value: int = Field(gt=0)
    unit: TermUnit


class ActionPath(StrictModel):
    """Human-action view of an existing AST branch; not a second evaluator."""

    action_path_id: str
    rule_id: str
    label: str
    source_rule_node_id: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    one_time_actions: list[ActionDefinition] = Field(default_factory=list)
    recurring_actions: list[ActionDefinition] = Field(default_factory=list)
    required_amounts: list[ActionAmountRequirement] = Field(default_factory=list)
    required_duration: ActionDuration | None = None
    requires_external_party: bool = False
    required_service_refs: list[str] = Field(default_factory=list)
    required_fact_types: list[str] = Field(default_factory=list)
    provenance: list[SourceReference] = Field(default_factory=list)


class DateExpression(StrictModel):
    type: TimeExpressionType
    value: date | None = None
    context_field: ContextDateField | None = None
    expression: "DateExpression | None" = None
    amount: int | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "DateExpression":
        if self.type == TimeExpressionType.LITERAL:
            if self.value is None:
                raise ValueError("LITERAL date expression requires value")
        elif self.type == TimeExpressionType.CONTEXT:
            if self.context_field is None:
                raise ValueError("CONTEXT date expression requires context_field")
        else:
            if self.expression is None:
                raise ValueError(f"{self.type} requires nested expression")
            if self.type in {
                TimeExpressionType.ADD_DAYS,
                TimeExpressionType.ADD_MONTHS,
                TimeExpressionType.ADD_YEARS,
                TimeExpressionType.BUSINESS_DAY_OFFSET,
            } and self.amount is None:
                raise ValueError(f"{self.type} requires amount")
        return self

    @classmethod
    def context(cls, field: ContextDateField) -> "DateExpression":
        return cls(type=TimeExpressionType.CONTEXT, context_field=field)

    @classmethod
    def literal(cls, value: date) -> "DateExpression":
        return cls(type=TimeExpressionType.LITERAL, value=value)

    @classmethod
    def start_of_month(cls, expression: "DateExpression") -> "DateExpression":
        return cls(type=TimeExpressionType.START_OF_MONTH, expression=expression)

    @classmethod
    def end_of_month(cls, expression: "DateExpression") -> "DateExpression":
        return cls(type=TimeExpressionType.END_OF_MONTH, expression=expression)

    @classmethod
    def add_days(cls, expression: "DateExpression", amount: int) -> "DateExpression":
        return cls(type=TimeExpressionType.ADD_DAYS, expression=expression, amount=amount)

    @classmethod
    def add_months(cls, expression: "DateExpression", amount: int) -> "DateExpression":
        return cls(type=TimeExpressionType.ADD_MONTHS, expression=expression, amount=amount)

    @classmethod
    def add_years(cls, expression: "DateExpression", amount: int) -> "DateExpression":
        return cls(type=TimeExpressionType.ADD_YEARS, expression=expression, amount=amount)

    @classmethod
    def business_day_offset(
        cls, expression: "DateExpression", amount: int
    ) -> "DateExpression":
        return cls(
            type=TimeExpressionType.BUSINESS_DAY_OFFSET,
            expression=expression,
            amount=amount,
        )


class TimeWindow(StrictModel):
    start: DateExpression
    end: DateExpression
    inclusive: bool = True


class ValuePredicate(StrictModel):
    path: str
    operator: ComparisonOperator
    expected: Any


class CoverageRequirement(StrictModel):
    """Describe the data domain that must fully cover an observation window."""

    fact_domain: str
    institution: str | None = None
    accepted_source_types: list[FactSourceType] = Field(
        default_factory=lambda: [
            FactSourceType.INSTITUTION_VERIFIED,
            FactSourceType.MYDATA_VERIFIED,
            FactSourceType.DERIVED,
        ]
    )


class FactAcceptancePolicy(StrictModel):
    """Reusable per-Fact semantic/authority gate.

    The legacy ``strict_*`` / ``provisional_*`` field names remain for schema
    compatibility.  In v0.3.2 there is no global trust-mode switch: the first
    pair denotes authoritative evidence and the second pair denotes explicitly
    allowed self-reported evidence.
    """

    strict_semantic_types: list[FactSemanticType] = Field(
        default_factory=lambda: [
            FactSemanticType.OBSERVED_FACT,
            FactSemanticType.OBSERVED_EVENT,
            FactSemanticType.DERIVED_FACT,
        ]
    )
    strict_source_types: list[FactSourceType] = Field(
        default_factory=lambda: [
            FactSourceType.INSTITUTION_VERIFIED,
            FactSourceType.MYDATA_VERIFIED,
            FactSourceType.DERIVED,
        ]
    )
    provisional_semantic_types: list[FactSemanticType] = Field(
        default_factory=lambda: [FactSemanticType.SELF_REPORTED_FACT]
    )
    provisional_source_types: list[FactSourceType] = Field(
        default_factory=lambda: [FactSourceType.USER_DECLARED]
    )
    allow_provisional: bool = False


class ExistenceAssertionFallback(StrictModel):
    """User answer fallback for EXISTS/NOT_EXISTS when coverage is unavailable.

    The referenced fact stores whether a matching entity existed.  It remains a
    SELF_REPORTED_FACT and is never converted into authoritative DataCoverage.
    """

    fact_type: str
    missing_fact: MissingFactSpec | None = None
    expected_semantic_type: FactSemanticType = FactSemanticType.SELF_REPORTED_FACT


class FactSubjectSelector(StrictModel):
    """Select whose facts a generic rule evaluates."""

    mode: FactSubjectMode = FactSubjectMode.STORE_USER
    person_id: str | None = None
    relationship_type: str | None = None
    include_store_user: bool = False
    allowed_relationship_source_types: list[FactSourceType] = Field(
        default_factory=lambda: [
            FactSourceType.INSTITUTION_VERIFIED,
            FactSourceType.MYDATA_VERIFIED,
            FactSourceType.DERIVED,
        ]
    )
    relationship_missing_fact: MissingFactSpec | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> "FactSubjectSelector":
        if self.mode == FactSubjectMode.EXPLICIT and self.person_id is None:
            raise ValueError("EXPLICIT subject selector requires person_id")
        if self.mode == FactSubjectMode.RELATED_PERSON and self.relationship_type is None:
            raise ValueError("RELATED_PERSON selector requires relationship_type")
        return self


class RuleBase(StrictModel):
    rule_id: str
    name: str
    purpose: RulePurpose = RulePurpose.SUPPORTING
    evaluation_phase: EvaluationPhase = EvaluationPhase.PRE_SUBSCRIPTION
    description: str | None = None
    source: SourceReference | None = None


class AndRule(RuleBase):
    type: Literal["AND"] = "AND"
    children: list["RuleNode"]

    @model_validator(mode="after")
    def require_children(self) -> "AndRule":
        if not self.children:
            raise ValueError("AND requires at least one child")
        return self


class OrRule(RuleBase):
    type: Literal["OR"] = "OR"
    children: list["RuleNode"]

    @model_validator(mode="after")
    def require_children(self) -> "OrRule":
        if not self.children:
            raise ValueError("OR requires at least one child")
        return self


class NotRule(RuleBase):
    type: Literal["NOT"] = "NOT"
    child: "RuleNode"


class FactComparisonRule(RuleBase):
    type: Literal["FACT_COMPARE"] = "FACT_COMPARE"
    fact_type: str
    value_path: str | None = None
    effective_at: DateExpression | None = None
    subject_selector: FactSubjectSelector | None = None
    operator: ComparisonOperator
    expected: Any
    on_true_status: EvaluationStatus = EvaluationStatus.SATISFIED
    on_false_status: EvaluationStatus = EvaluationStatus.UNSATISFIABLE
    on_missing_status: EvaluationStatus = EvaluationStatus.UNKNOWN
    true_reason_code: str = "FACT_COMPARISON_MATCH"
    false_reason_code: str = "FACT_COMPARISON_MISMATCH"
    missing_reason_code: str = "REQUIRED_FACT_MISSING"
    missing_fact: MissingFactSpec | None = None
    action: ActionDefinition | None = None
    # v0.3 compatibility. New authority-sensitive rules use the policy.
    required_semantic_type: FactSemanticType | None = None
    fact_acceptance_policy: FactAcceptancePolicy | None = None


class DerivedComparisonRule(RuleBase):
    type: Literal["DERIVED_COMPARE"] = "DERIVED_COMPARE"
    derivation: Literal["AGE_AT"]
    input_fact_type: str
    reference_date: DateExpression
    operator: ComparisonOperator
    expected: Any
    true_reason_code: str = "DERIVED_COMPARISON_MATCH"
    false_reason_code: str = "DERIVED_COMPARISON_MISMATCH"
    missing_reason_code: str = "DERIVATION_INPUT_MISSING"
    missing_fact: MissingFactSpec | None = None


class EntityCountRule(RuleBase):
    type: Literal["ENTITY_COUNT"] = "ENTITY_COUNT"
    entity: EntityType
    filters: dict[str, Any] = Field(default_factory=dict)
    active_at: DateExpression | None = None
    operator: ComparisonOperator
    expected: int
    true_reason_code: str = "ENTITY_COUNT_MATCH"
    false_reason_code: str = "ENTITY_COUNT_MISMATCH"


class ExistsRule(RuleBase):
    type: Literal["EXISTS"] = "EXISTS"
    entity: EntityType
    filters: dict[str, Any] = Field(default_factory=dict)
    overlaps: TimeWindow | None = None
    coverage: CoverageRequirement | None = None
    self_report_fallback: ExistenceAssertionFallback | None = None
    # v0.1 compatibility. New rules should use coverage intervals above.
    coverage_fact_type: str | None = None
    coverage_missing_fact: MissingFactSpec | None = None
    true_reason_code: str = "MATCHING_ENTITY_EXISTS"
    false_reason_code: str = "MATCHING_ENTITY_NOT_FOUND"


class NotExistsRule(RuleBase):
    type: Literal["NOT_EXISTS"] = "NOT_EXISTS"
    entity: EntityType
    filters: dict[str, Any] = Field(default_factory=dict)
    overlaps: TimeWindow | None = None
    coverage: CoverageRequirement | None = None
    self_report_fallback: ExistenceAssertionFallback | None = None
    # v0.1 compatibility. New rules should use coverage intervals above.
    coverage_fact_type: str | None = None
    coverage_missing_fact: MissingFactSpec | None = None
    true_reason_code: str = "NO_MATCHING_ENTITY"
    false_reason_code: str = "MATCHING_ENTITY_EXISTS"


class GoalTemplate(StrictModel):
    metric: str
    unit: str
    tracking_mode: AchievementMode = AchievementMode.ACCUMULATIVE
    safety_threshold: int = Field(default=0, ge=0)
    description: str | None = None
    coverage_fact_domain: str | None = None
    coverage_institution: str | None = None


class FutureAchievementSpec(StrictModel):
    capability_rule: "RuleNode | None" = None
    intent_fact_type: str | None = None
    missing_fact: MissingFactSpec | None = None
    achievement_mode: AchievementMode = AchievementMode.ACCUMULATIVE
    max_qualifying_units_per_opportunity: int = Field(default=1, ge=1)
    # Backward-compatible v0.2 field. New code uses the generic field above.
    max_new_qualifying_months_per_month: int = Field(default=1, ge=1)
    action: ActionDefinition | None = None
    action_paths: list[ActionPath] = Field(default_factory=list)
    goal_template: GoalTemplate | None = None
    achievable_reason_code: str = "RECURRING_CONDITION_CAN_BE_COMPLETED"
    insufficient_time_reason_code: str = "INSUFFICIENT_REMAINING_MONTHS"
    user_declined_reason_code: str = "USER_DECLINED"
    # False means the modeled capability represents only one of several
    # source-backed action paths. Failure of that one path must not close the
    # entire financial rule.
    alternative_action_paths_complete: bool = True
    unmodeled_alternative_reason_code: str = "ALTERNATIVE_ACTION_PATH_NOT_EVALUATED"

    @model_validator(mode="after")
    def require_resolution_path(self) -> "FutureAchievementSpec":
        if self.capability_rule is None and self.intent_fact_type is None:
            raise ValueError(
                "FutureAchievementSpec requires capability_rule or intent_fact_type"
            )
        if self.intent_fact_type is not None and self.missing_fact is None:
            raise ValueError("intent_fact_type requires missing_fact")
        if (
            self.goal_template is not None
            and self.goal_template.tracking_mode != self.achievement_mode
        ):
            raise ValueError("goal_template tracking_mode must match achievement_mode")
        return self


class CountDistinctPeriodsRule(RuleBase):
    type: Literal["COUNT_DISTINCT_PERIODS"] = "COUNT_DISTINCT_PERIODS"
    period: PeriodUnit
    fact_type: str
    # None means UserFact. RECURRING_PAYMENT_EVENT consumes the typed event store.
    event_entity: Literal[EntityType.RECURRING_PAYMENT_EVENT] | None = None
    date_path: str | None = None
    subject_selector: FactSubjectSelector | None = None
    filters: list[ValuePredicate] = Field(default_factory=list)
    window: TimeWindow
    operator: ComparisonOperator
    expected: int = Field(ge=0)
    fact_acceptance_policy: FactAcceptancePolicy = Field(
        default_factory=FactAcceptancePolicy
    )
    coverage: CoverageRequirement | None = None
    coverage_missing_fact: MissingFactSpec | None = None
    future_achievement: FutureAchievementSpec | None = None
    true_reason_code: str = "DISTINCT_PERIOD_COUNT_REACHED"
    false_reason_code: str = "DISTINCT_PERIOD_COUNT_NOT_REACHED"


class CountDistinctMonthsRule(RuleBase):
    """Backward-compatible v0.1 alias of COUNT_DISTINCT_PERIODS(MONTH)."""

    type: Literal["COUNT_DISTINCT_MONTHS"] = "COUNT_DISTINCT_MONTHS"
    fact_type: str
    event_entity: Literal[EntityType.RECURRING_PAYMENT_EVENT] | None = None
    date_path: str | None = None
    subject_selector: FactSubjectSelector | None = None
    filters: list[ValuePredicate] = Field(default_factory=list)
    window: TimeWindow
    operator: ComparisonOperator
    expected: int = Field(ge=0)
    fact_acceptance_policy: FactAcceptancePolicy = Field(
        default_factory=FactAcceptancePolicy
    )
    coverage: CoverageRequirement | None = None
    coverage_missing_fact: MissingFactSpec | None = None
    future_achievement: FutureAchievementSpec | None = None
    true_reason_code: str = "DISTINCT_MONTH_COUNT_REACHED"
    false_reason_code: str = "DISTINCT_MONTH_COUNT_NOT_REACHED"


class CountConsecutiveRule(RuleBase):
    type: Literal["COUNT_CONSECUTIVE"] = "COUNT_CONSECUTIVE"
    entity: Literal[EntityType.SCHEDULED_OCCURRENCE] = EntityType.SCHEDULED_OCCURRENCE
    schedule_id: str | None = None
    subject_selector: FactSubjectSelector | None = None
    filters: list[ValuePredicate] = Field(default_factory=list)
    # None means the longest qualifying run anywhere in the ordered sequence.
    from_sequence: int | None = Field(default=None, ge=1)
    window: TimeWindow | None = None
    operator: ComparisonOperator
    expected: int = Field(ge=0)
    expected_occurrence_count: int | None = Field(default=None, ge=0)
    opportunity_period: PeriodUnit = PeriodUnit.WEEK
    fact_acceptance_policy: FactAcceptancePolicy = Field(
        default_factory=FactAcceptancePolicy
    )
    coverage: CoverageRequirement | None = None
    coverage_missing_fact: MissingFactSpec | None = None
    future_achievement: FutureAchievementSpec | None = None
    true_reason_code: str = "CONSECUTIVE_COUNT_REACHED"
    false_reason_code: str = "CONSECUTIVE_COUNT_NOT_REACHED"


RuleNode = Annotated[
    Union[
        AndRule,
        OrRule,
        NotRule,
        FactComparisonRule,
        DerivedComparisonRule,
        EntityCountRule,
        ExistsRule,
        NotExistsRule,
        CountDistinctPeriodsRule,
        CountDistinctMonthsRule,
        CountConsecutiveRule,
    ],
    Field(discriminator="type"),
]


# Resolve recursive annotations after the discriminated union exists.
_REBUILD_NAMESPACE = {"RuleNode": RuleNode}
for _model in (
    AndRule,
    OrRule,
    NotRule,
    FutureAchievementSpec,
    CountDistinctPeriodsRule,
    CountDistinctMonthsRule,
    CountConsecutiveRule,
):
    _model.model_rebuild(_types_namespace=_REBUILD_NAMESPACE)

RULE_NODE_ADAPTER = TypeAdapter(RuleNode)
