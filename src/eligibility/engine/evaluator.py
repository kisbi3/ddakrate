from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import date
from typing import Any

from eligibility.audit import AuditEventType, AuditSession

from eligibility.engine.aggregation import (
    count_consecutive_occurrences,
    distinct_periods,
    distinct_record_periods,
    facts_matching_predicates,
    records_matching_predicates,
    values_match_predicates,
)
from eligibility.engine.comparison import compare_values, get_value_by_path
from eligibility.engine.fact_acceptance import combine_verification_levels
from eligibility.engine.fact_resolver import FactResolver
from eligibility.engine.interest_engine import InterestEngine
from eligibility.engine.logical import combine_and, combine_or, negate_status
from eligibility.engine.rate_engine import RateEngine
from eligibility.engine.temporal import (
    available_period_keys,
    calculate_age,
    coverage_intervals_fully_cover,
    interval_contains,
    intervals_overlap,
    project_remaining_opportunities,
    resolve_date_expression,
    resolve_window,
)
from eligibility.schema.enums import (
    ComparisonOperator,
    EntityType,
    EvaluationStatus,
    EvaluationTrustMode,
    FactSemanticType,
    FactSourceType,
    PeriodUnit,
    ResolutionStatus,
    ResolutionStrategy,
    VerificationLevel,
)
from eligibility.schema.evaluation import (
    EvaluationContext,
    MissingFactRequest,
    ProductEvaluation,
    Progress,
    ProvenanceRecord,
    RuleEvaluation,
)
from eligibility.schema.product import (
    ContributionSchedulePlan,
    MonthlyContributionPlan,
    ProductDefinition,
)
from eligibility.schema.rule import (
    AndRule,
    CountConsecutiveRule,
    CountDistinctMonthsRule,
    CountDistinctPeriodsRule,
    CoverageRequirement,
    DerivedComparisonRule,
    EntityCountRule,
    ExistsRule,
    FactComparisonRule,
    FactSubjectSelector,
    NotExistsRule,
    NotRule,
    OrRule,
    RuleNode,
    SourceReference,
)
from eligibility.schema.user_fact import (
    AccountHoldingInterval,
    DataCoverage,
    PersonRelationship,
    RecurringPaymentEvent,
    ScheduledOccurrence,
    UserFact,
    UserFactStore,
)


class UnsupportedRuleError(NotImplementedError):
    """Raised when a validated AST node has no evaluator implementation yet."""


class RuleEvaluator:
    def __init__(
        self,
        fact_store: UserFactStore,
        context: EvaluationContext,
        audit: AuditSession | None = None,
        trust_mode: EvaluationTrustMode | None = None,
    ):
        self.fact_store = fact_store
        self.context = context
        self.audit = audit
        # Deprecated compatibility input only. Per-Fact evidence drives v0.3.2.
        self.trust_mode = trust_mode
        self.fact_resolver = FactResolver(
            fact_store, audit=audit, trust_mode=trust_mode
        )

    def evaluate(self, rule: RuleNode) -> RuleEvaluation:
        if self.audit is None:
            return self._evaluate_and_finalize(rule)
        with self.audit.span("RULE_EVALUATOR"):
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.RULE_LOADED,
                entity_refs={"rule_id": rule.rule_id},
                input_data=rule,
                payload={
                    "rule_id": rule.rule_id,
                    "rule_name": rule.name,
                    "rule_type": rule.type,
                    "purpose": rule.purpose.value,
                    "evaluation_phase": rule.evaluation_phase.value,
                },
            )
            result = self._evaluate_and_finalize(rule)
            for missing in result.missing_facts:
                self.audit.emit(
                    "RULE_EVALUATOR",
                    AuditEventType.MISSING_FACT_CREATED,
                    entity_refs={
                        "rule_id": rule.rule_id,
                        "fact_type": missing.fact_type,
                    },
                    output_data=missing,
                    payload={
                        "resolution_strategy": missing.resolution_strategy.value,
                        "required_source": missing.required_source,
                        "rate_impact_pp": (
                            str(missing.impact.rate_pp)
                            if missing.impact is not None
                            and missing.impact.rate_pp is not None
                            else None
                        ),
                    },
                )
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.STATUS_DERIVED,
                entity_refs={"rule_id": rule.rule_id},
                input_data=rule,
                output_data=result,
                payload={
                    "status": result.status.value,
                    "reason_code": result.reason_code,
                    "progress": (
                        result.progress.model_dump(mode="json")
                        if result.progress is not None
                        else None
                    ),
                },
            )
            return result

    def _evaluate_and_finalize(self, rule: RuleNode) -> RuleEvaluation:
        if isinstance(rule, AndRule):
            result = self._evaluate_and(rule)
        elif isinstance(rule, OrRule):
            result = self._evaluate_or(rule)
        elif isinstance(rule, NotRule):
            result = self._evaluate_not(rule)
        elif isinstance(rule, FactComparisonRule):
            result = self._evaluate_fact_comparison(rule)
        elif isinstance(rule, DerivedComparisonRule):
            result = self._evaluate_derived_comparison(rule)
        elif isinstance(rule, EntityCountRule):
            result = self._evaluate_entity_count(rule)
        elif isinstance(rule, ExistsRule):
            result = self._evaluate_existence(rule, should_exist=True)
        elif isinstance(rule, NotExistsRule):
            result = self._evaluate_existence(rule, should_exist=False)
        elif isinstance(rule, CountDistinctPeriodsRule):
            result = self._evaluate_count_distinct_periods(rule)
        elif isinstance(rule, CountDistinctMonthsRule):
            result = self._evaluate_count_distinct_months(rule)
        elif isinstance(rule, CountConsecutiveRule):
            result = self._evaluate_count_consecutive(rule)
        else:
            raise UnsupportedRuleError(type(rule).__name__)
        # The trace records the evaluated AST node type. Evidence identity is
        # normalized here so every evaluator path exposes the same contract.
        levels = list(result.evidence_levels)
        if not levels and result.children:
            for child in result.children:
                child_levels = child.evidence_levels or (
                    [child.verification_level]
                    if child.verification_level != VerificationLevel.UNKNOWN
                    else []
                )
                for level in child_levels:
                    if level not in levels:
                        levels.append(level)
        if (
            result.verification_level != VerificationLevel.UNKNOWN
            and result.verification_level not in levels
        ):
            levels.append(result.verification_level)
        verification = (
            combine_verification_levels(levels)
            if levels
            else result.verification_level
        )
        return result.model_copy(
            update={
                "rule_type": rule.type,
                "evidence_levels": levels,
                "verification_level": verification,
                "is_provisional": VerificationLevel.SELF_REPORTED in levels,
            },
            deep=True,
        )

    def _evaluate_and(self, rule: AndRule) -> RuleEvaluation:
        children: list[RuleEvaluation] = []
        for child_rule in rule.children:
            child = self.evaluate(child_rule)
            children.append(child)
            if child.status == EvaluationStatus.UNSATISFIABLE:
                break

        status, reason = combine_and([child.status for child in children])
        missing = (
            self._dedupe_missing(children)
            if status == EvaluationStatus.UNKNOWN
            else []
        )
        is_provisional, verification_level = self._verification_from_children(children)
        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=status,
            reason_code=reason,
            missing_facts=missing,
            evidence={
                "evaluated_children": len(children),
                "declared_children": len(rule.children),
                "child_statuses": {
                    child.rule_id: child.status.value for child in children
                },
            },
            source_provenance=self._merge_provenance(
                self._rule_provenance(rule.source),
                *(child.source_provenance for child in children),
            ),
            children=children,
            is_provisional=is_provisional,
            verification_level=verification_level,
        )

    def _evaluate_or(self, rule: OrRule) -> RuleEvaluation:
        children: list[RuleEvaluation] = []
        for child_rule in rule.children:
            child = self.evaluate(child_rule)
            children.append(child)
            if child.status == EvaluationStatus.SATISFIED:
                break

        status, reason = combine_or([child.status for child in children])
        # If a branch is already achievable, unresolved alternative branches are
        # not required to establish rate attainability for this OR rule.
        missing = (
            self._dedupe_missing(children)
            if status == EvaluationStatus.UNKNOWN
            else []
        )
        is_provisional, verification_level = self._verification_from_children(children)
        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=status,
            reason_code=reason,
            missing_facts=missing,
            evidence={
                "evaluated_children": len(children),
                "declared_children": len(rule.children),
                "child_statuses": {
                    child.rule_id: child.status.value for child in children
                },
            },
            source_provenance=self._merge_provenance(
                self._rule_provenance(rule.source),
                *(child.source_provenance for child in children),
            ),
            children=children,
            is_provisional=is_provisional,
            verification_level=verification_level,
        )

    def _evaluate_not(self, rule: NotRule) -> RuleEvaluation:
        child = self.evaluate(rule.child)
        status, reason = negate_status(child.status)
        missing = child.missing_facts if status == EvaluationStatus.UNKNOWN else []
        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=status,
            reason_code=reason,
            missing_facts=missing,
            evidence={"negated_rule_id": child.rule_id},
            source_provenance=self._merge_provenance(
                self._rule_provenance(rule.source), child.source_provenance
            ),
            children=[child],
            is_provisional=child.is_provisional,
            verification_level=child.verification_level,
        )

    def _evaluate_fact_comparison(self, rule: FactComparisonRule) -> RuleEvaluation:
        effective_at = (
            resolve_date_expression(rule.effective_at, self.context)
            if rule.effective_at is not None
            else self.context.as_of
        )
        if self.audit is not None and rule.effective_at is not None:
            self.audit.emit(
                "TEMPORAL_ENGINE",
                AuditEventType.DATE_EXPRESSION_RESOLVED,
                entity_refs={"rule_id": rule.rule_id},
                input_data=rule.effective_at,
                output_data=effective_at,
                payload={"label": "effective_at", "resolved": effective_at.isoformat()},
            )
        subject_ids, relationship_provenance, subject_error = (
            self._resolve_subject_selector(
                rule.subject_selector,
                effective_at=effective_at,
                rule_id=rule.rule_id,
                rule_name=rule.name,
                source=rule.source,
            )
        )
        if subject_error is not None:
            return subject_error

        resolution = self.fact_resolver.resolve(
            rule.fact_type,
            effective_at,
            subject_person_ids=subject_ids,
            required_semantic_type=rule.required_semantic_type,
            acceptance_policy=rule.fact_acceptance_policy,
        )

        if resolution.status == ResolutionStatus.CONFLICT:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNKNOWN,
                reason_code="FACT_SOURCE_CONFLICT",
                missing_facts=self._missing_for_rule(rule),
                evidence={
                    "fact_type": rule.fact_type,
                    "effective_at": effective_at.isoformat(),
                    "competing_values": [
                        fact.value for fact in resolution.competing_facts
                    ],
                    "legacy_trust_mode_ignored": (
                        self.trust_mode.value if self.trust_mode is not None else None
                    ),
                },
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    *(
                        self._fact_provenance(fact)
                        for fact in resolution.competing_facts
                    ),
                ),
                verification_level=VerificationLevel.UNKNOWN,
            )

        if resolution.status == ResolutionStatus.UNRESOLVED or resolution.fact is None:
            authority_rejected = bool(resolution.rejected_facts)
            missing = (
                self._missing_for_rule(rule)
                if rule.on_missing_status == EvaluationStatus.UNKNOWN
                else []
            )
            if authority_rejected and not missing:
                missing = [self._authority_missing_request(rule.fact_type, rule.rule_id)]
            evidence: dict[str, Any] = {
                "fact_type": rule.fact_type,
                "effective_at": effective_at.isoformat(),
                "expected": rule.expected,
                "operator": rule.operator.value,
                "legacy_trust_mode_ignored": (
                self.trust_mode.value if self.trust_mode is not None else None
            ),
                "rejected_fact_ids": [
                    fact.fact_id for fact in resolution.rejected_facts
                ],
                "rejected_fact_semantics": [
                    fact.semantic_type.value for fact in resolution.rejected_facts
                ],
                "rejected_fact_sources": [
                    fact.source_type.value for fact in resolution.rejected_facts
                ],
            }
            if rule.action is not None:
                evidence["action"] = rule.action.model_dump(mode="json")
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=(
                    EvaluationStatus.UNKNOWN
                    if authority_rejected
                    else rule.on_missing_status
                ),
                reason_code=(
                    "FACT_AUTHORITY_INSUFFICIENT"
                    if authority_rejected
                    else rule.missing_reason_code
                ),
                missing_facts=missing,
                evidence=evidence,
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    *(self._fact_provenance(fact) for fact in resolution.rejected_facts),
                ),
                verification_level=VerificationLevel.UNKNOWN,
            )

        fact = resolution.fact
        try:
            actual = get_value_by_path(fact.value, rule.value_path)
        except KeyError:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNKNOWN,
                reason_code="FACT_VALUE_PATH_MISSING",
                missing_facts=self._missing_for_rule(rule),
                evidence={
                    "fact_type": rule.fact_type,
                    "value_path": rule.value_path,
                    "fact_id": fact.fact_id,
                },
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    self._fact_provenance(fact),
                ),
                verification_level=resolution.verification_level,
                is_provisional=resolution.is_provisional,
            )

        matched = compare_values(actual, rule.operator, rule.expected)
        if self.audit is not None:
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.COMPARISON_EXECUTED,
                entity_refs={"rule_id": rule.rule_id, "fact_id": fact.fact_id},
                input_data={
                    "actual": actual,
                    "operator": rule.operator.value,
                    "expected": rule.expected,
                },
                output_data={"matched": matched},
                payload={
                    "operator": rule.operator.value,
                    "matched": matched,
                    "fact_type": rule.fact_type,
                    "verification_level": resolution.verification_level.value,
                    "is_provisional": resolution.is_provisional,
                },
            )
        status = rule.on_true_status if matched else rule.on_false_status
        reason = rule.true_reason_code if matched else rule.false_reason_code
        evidence = {
            "fact_type": rule.fact_type,
            "fact_id": fact.fact_id,
            "actual": actual,
            "expected": rule.expected,
            "operator": rule.operator.value,
            "effective_at": effective_at.isoformat(),
            "source_type": fact.source_type.value,
            "semantic_type": fact.semantic_type.value,
            "legacy_trust_mode_ignored": (
                self.trust_mode.value if self.trust_mode is not None else None
            ),
            "verification_level": resolution.verification_level.value,
            "is_provisional": resolution.is_provisional,
        }
        if rule.action is not None:
            evidence["action"] = rule.action.model_dump(mode="json")

        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=status,
            reason_code=reason,
            evidence=evidence,
            source_provenance=self._merge_provenance(
                self._rule_provenance(rule.source),
                relationship_provenance,
                self._fact_provenance(fact),
            ),
            is_provisional=resolution.is_provisional,
            verification_level=resolution.verification_level,
        )

    def _evaluate_derived_comparison(
        self,
        rule: DerivedComparisonRule,
    ) -> RuleEvaluation:
        resolution = self.fact_resolver.resolve(rule.input_fact_type, self.context.as_of)
        if resolution.status != ResolutionStatus.RESOLVED or resolution.fact is None:
            missing: list[MissingFactRequest] = []
            if rule.missing_fact is not None:
                missing = [
                    self.fact_resolver.missing_request(
                        rule.input_fact_type, rule.missing_fact, rule.rule_id
                    )
                ]
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNKNOWN,
                reason_code=rule.missing_reason_code,
                missing_facts=missing,
                evidence={"input_fact_type": rule.input_fact_type},
                source_provenance=self._rule_provenance(rule.source),
            )

        fact = resolution.fact
        if rule.derivation == "AGE_AT":
            birth_date = fact.value
            if isinstance(birth_date, str):
                birth_date = date.fromisoformat(birth_date)
            if not isinstance(birth_date, date):
                raise TypeError("AGE_AT input must be an ISO date or date")
            reference_date = resolve_date_expression(rule.reference_date, self.context)
            actual = calculate_age(birth_date, reference_date)
        else:  # pragma: no cover - schema currently restricts this branch.
            raise UnsupportedRuleError(rule.derivation)

        matched = compare_values(actual, rule.operator, rule.expected)
        if self.audit is not None:
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.COMPARISON_EXECUTED,
                entity_refs={"rule_id": rule.rule_id},
                input_data={
                    "actual": actual,
                    "operator": rule.operator.value,
                    "expected": rule.expected,
                },
                output_data={"matched": matched},
                payload={"derivation": rule.derivation, "matched": matched},
            )
        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=(
                EvaluationStatus.SATISFIED
                if matched
                else EvaluationStatus.UNSATISFIABLE
            ),
            reason_code=(
                rule.true_reason_code if matched else rule.false_reason_code
            ),
            evidence={
                "derivation": rule.derivation,
                "input_fact_id": fact.fact_id,
                "actual": actual,
                "expected": rule.expected,
                "operator": rule.operator.value,
                "reference_date": reference_date.isoformat(),
            },
            source_provenance=self._merge_provenance(
                self._rule_provenance(rule.source),
                self._fact_provenance(fact),
            ),
            verification_level=VerificationLevel.DERIVED,
            evidence_levels=[VerificationLevel.DERIVED],
        )

    def _evaluate_entity_count(self, rule: EntityCountRule) -> RuleEvaluation:
        if rule.entity != EntityType.ACCOUNT_HOLDING_INTERVAL:
            raise UnsupportedRuleError(rule.entity)

        active_at = (
            resolve_date_expression(rule.active_at, self.context)
            if rule.active_at is not None
            else None
        )
        holdings = [
            holding
            for holding in self.fact_store.account_holdings
            if self._holding_matches_filters(holding, rule.filters)
            and (
                active_at is None
                or interval_contains(holding.held_from, holding.held_to, active_at)
            )
        ]
        count = len(holdings)
        matched = compare_values(count, rule.operator, rule.expected)
        if self.audit is not None:
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.COMPARISON_EXECUTED,
                entity_refs={"rule_id": rule.rule_id},
                input_data={
                    "actual": count,
                    "operator": rule.operator.value,
                    "expected": rule.expected,
                },
                output_data={"matched": matched},
                payload={"entity": rule.entity.value, "matched": matched},
            )
        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=(
                EvaluationStatus.SATISFIED
                if matched
                else EvaluationStatus.UNSATISFIABLE
            ),
            reason_code=(
                rule.true_reason_code if matched else rule.false_reason_code
            ),
            evidence={
                "entity": rule.entity.value,
                "filters": rule.filters,
                "active_at": active_at.isoformat() if active_at else None,
                "actual_count": count,
                "expected": rule.expected,
                "operator": rule.operator.value,
                "matched_account_ids": [holding.account_id for holding in holdings],
            },
            source_provenance=self._merge_provenance(
                self._rule_provenance(rule.source),
                *(self._holding_provenance(holding) for holding in holdings),
            ),
            verification_level=combine_verification_levels(
                self._verification_from_source(holding.source_type)
                for holding in holdings
            ) if holdings else VerificationLevel.UNKNOWN,
            evidence_levels=list(dict.fromkeys(
                self._verification_from_source(holding.source_type)
                for holding in holdings
                if self._verification_from_source(holding.source_type) != VerificationLevel.UNKNOWN
            )),
        )

    def _evaluate_existence(
        self,
        rule: ExistsRule | NotExistsRule,
        *,
        should_exist: bool,
    ) -> RuleEvaluation:
        if rule.entity != EntityType.ACCOUNT_HOLDING_INTERVAL:
            raise UnsupportedRuleError(rule.entity)

        window_start: date | None = None
        window_end: date | None = None
        if rule.overlaps is not None:
            window_start, window_end = resolve_window(rule.overlaps, self.context)
            if self.audit is not None:
                self.audit.emit(
                    "TEMPORAL_ENGINE",
                    AuditEventType.DATE_EXPRESSION_RESOLVED,
                    entity_refs={"rule_id": rule.rule_id},
                    input_data=rule.overlaps,
                    output_data={"start": window_start, "end": window_end},
                    payload={
                        "label": "existence_window",
                        "start": window_start.isoformat(),
                        "end": window_end.isoformat(),
                    },
                )

        matching: list[AccountHoldingInterval] = []
        for holding in self.fact_store.account_holdings:
            if not self._holding_matches_filters(holding, rule.filters):
                continue
            if window_start is not None and window_end is not None:
                if not intervals_overlap(
                    holding.held_from,
                    holding.held_to,
                    window_start,
                    window_end,
                    inclusive=rule.overlaps.inclusive,
                ):
                    continue
            matching.append(holding)

        coverage_provenance: list[ProvenanceRecord] = []
        coverage_evidence: dict[str, Any] = {}
        coverage_complete: bool | None = None
        coverage_levels: list[VerificationLevel] = []

        if not matching and rule.coverage is not None:
            if window_start is None or window_end is None:
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNKNOWN,
                    reason_code="ABSENCE_QUERY_COVERAGE_WINDOW_REQUIRED",
                    evidence={
                        "entity": rule.entity.value,
                        "filters": rule.filters,
                        "coverage_domain": rule.coverage.fact_domain,
                    },
                    source_provenance=self._rule_provenance(rule.source),
                )
            accepted_sources = set(rule.coverage.accepted_source_types)
            coverages = [
                coverage
                for coverage in self.fact_store.data_coverages
                if coverage.fact_domain == rule.coverage.fact_domain
                and (
                    rule.coverage.institution is None
                    or coverage.institution == rule.coverage.institution
                )
                and coverage.source_type in accepted_sources
            ]
            coverage_complete = coverage_intervals_fully_cover(
                ((coverage.covered_from, coverage.covered_to) for coverage in coverages),
                window_start,
                window_end,
            )
            coverage_levels = [
                self._verification_from_source(item.source_type) for item in coverages
            ]
            coverage_evidence = {
                "coverage_domain": rule.coverage.fact_domain,
                "coverage_institution": rule.coverage.institution,
                "required_coverage_from": window_start.isoformat(),
                "required_coverage_to": window_end.isoformat(),
                "coverage_complete": coverage_complete,
                "coverage_intervals": [
                    {
                        "coverage_id": coverage.coverage_id,
                        "covered_from": coverage.covered_from.isoformat(),
                        "covered_to": coverage.covered_to.isoformat(),
                        "source_type": coverage.source_type.value,
                    }
                    for coverage in sorted(coverages, key=lambda item: item.coverage_id)
                ],
            }
            coverage_provenance = self._merge_provenance(
                *(self._coverage_provenance(coverage) for coverage in coverages)
            )
            if self.audit is not None:
                self.audit.emit(
                    "RULE_EVALUATOR",
                    AuditEventType.DATA_COVERAGE_CHECKED,
                    entity_refs={"rule_id": rule.rule_id},
                    input_data=coverages,
                    output_data={"coverage_complete": coverage_complete},
                    payload=coverage_evidence,
                )

        elif not matching and rule.coverage_fact_type is not None:
            coverage_resolution = self.fact_resolver.resolve(
                rule.coverage_fact_type, self.context.as_of
            )
            authoritative_levels = {
                VerificationLevel.INSTITUTION_VERIFIED,
                VerificationLevel.MYDATA_VERIFIED,
                VerificationLevel.DERIVED,
                VerificationLevel.VERIFIED,
            }
            coverage_complete = bool(
                coverage_resolution.status == ResolutionStatus.RESOLVED
                and coverage_resolution.fact is not None
                and coverage_resolution.fact.value is True
                and coverage_resolution.verification_level in authoritative_levels
            )
            if coverage_resolution.fact is not None:
                coverage_evidence = {
                    "coverage_fact_type": rule.coverage_fact_type,
                    "coverage_fact_id": coverage_resolution.fact.fact_id,
                    "coverage_complete": coverage_complete,
                    "coverage_verification": coverage_resolution.verification_level.value,
                }
                coverage_provenance = self._fact_provenance(coverage_resolution.fact)
                coverage_levels = [coverage_resolution.verification_level]

        # Authoritative evidence has priority. A matching entity proves EXISTS
        # without needing absence coverage. Complete coverage proves the negative.
        if matching or coverage_complete is True or (
            not matching and rule.coverage is None and rule.coverage_fact_type is None
        ):
            exists = bool(matching)
            condition_true = exists if should_exist else not exists
            status = (
                EvaluationStatus.SATISFIED
                if condition_true
                else EvaluationStatus.UNSATISFIABLE
            )
            reason = rule.true_reason_code if condition_true else rule.false_reason_code
            holding_levels = [
                self._verification_from_source(item.source_type) for item in matching
            ]
            evidence_levels = [
                level
                for level in dict.fromkeys(holding_levels or coverage_levels)
                if level != VerificationLevel.UNKNOWN
            ]
            evidence: dict[str, Any] = {
                "entity": rule.entity.value,
                "filters": rule.filters,
                "matched_count": len(matching),
                "matched_account_ids": [holding.account_id for holding in matching],
                "window": self._window_evidence(window_start, window_end),
                "evidence_origin": "AUTHORITATIVE" if evidence_levels else "UNSCOPED_ENTITY_STORE",
                **coverage_evidence,
            }
            if matching and window_start is not None and window_end is not None:
                first = matching[0]
                evidence.update(
                    {
                        "holding_from": first.held_from.isoformat(),
                        "holding_to": first.held_to.isoformat() if first.held_to else None,
                        "lookback_from": window_start.isoformat(),
                        "lookback_to": window_end.isoformat(),
                        "overlap": True,
                    }
                )
            if self.audit is not None:
                self.audit.emit(
                    "RULE_EVALUATOR",
                    AuditEventType.COMPARISON_EXECUTED,
                    entity_refs={"rule_id": rule.rule_id},
                    input_data={
                        "exists": exists,
                        "should_exist": should_exist,
                        "matched_count": len(matching),
                    },
                    output_data={"matched": condition_true},
                    payload={
                        "operator": "EXISTS" if should_exist else "NOT_EXISTS",
                        "matched": condition_true,
                        "evidence_origin": evidence["evidence_origin"],
                    },
                )
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=status,
                reason_code=reason,
                evidence=evidence,
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    coverage_provenance,
                    *(self._holding_provenance(holding) for holding in matching),
                ),
                verification_level=combine_verification_levels(evidence_levels),
                evidence_levels=evidence_levels,
            )

        # Coverage is insufficient. A Web answer may still produce a deterministic
        # personalized result, but it remains SELF_REPORTED and never becomes
        # DataCoverage. The fallback value means "a matching entity existed".
        fallback = rule.self_report_fallback
        if fallback is not None:
            assertion = self.fact_resolver.resolve(
                fallback.fact_type,
                self.context.as_of,
                required_semantic_type=fallback.expected_semantic_type,
            )
            if assertion.status == ResolutionStatus.RESOLVED and assertion.fact is not None:
                if not isinstance(assertion.fact.value, bool):
                    return RuleEvaluation(
                        rule_id=rule.rule_id,
                        rule_name=rule.name,
                        status=EvaluationStatus.UNKNOWN,
                        reason_code="SELF_REPORTED_EXISTENCE_ASSERTION_INVALID",
                        evidence={
                            "fact_type": fallback.fact_type,
                            "value": assertion.fact.value,
                            **coverage_evidence,
                        },
                        source_provenance=self._merge_provenance(
                            self._rule_provenance(rule.source),
                            self._fact_provenance(assertion.fact),
                        ),
                        verification_level=VerificationLevel.SELF_REPORTED,
                        evidence_levels=[VerificationLevel.SELF_REPORTED],
                    )
                exists = assertion.fact.value
                condition_true = exists if should_exist else not exists
                status = (
                    EvaluationStatus.SATISFIED
                    if condition_true
                    else EvaluationStatus.UNSATISFIABLE
                )
                reason = rule.true_reason_code if condition_true else rule.false_reason_code
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=status,
                    reason_code=reason,
                    evidence={
                        "entity": rule.entity.value,
                        "filters": rule.filters,
                        "window": self._window_evidence(window_start, window_end),
                        "self_report_fact_type": fallback.fact_type,
                        "self_report_fact_id": assertion.fact.fact_id,
                        "self_reported_exists": exists,
                        "evidence_origin": "SELF_REPORTED",
                        "authoritative_coverage_fabricated": False,
                        **coverage_evidence,
                    },
                    source_provenance=self._merge_provenance(
                        self._rule_provenance(rule.source),
                        coverage_provenance,
                        self._fact_provenance(assertion.fact),
                    ),
                    verification_level=VerificationLevel.SELF_REPORTED,
                    evidence_levels=[VerificationLevel.SELF_REPORTED],
                    is_provisional=True,
                )

        missing: list[MissingFactRequest] = []
        if fallback is not None and fallback.missing_fact is not None:
            missing = [
                self.fact_resolver.missing_request(
                    fallback.fact_type, fallback.missing_fact, rule.rule_id
                )
            ]
        elif rule.coverage_missing_fact is not None:
            missing = [
                self.fact_resolver.missing_request(
                    (
                        f"DATA_COVERAGE:{rule.coverage.fact_domain}"
                        if rule.coverage is not None
                        else rule.coverage_fact_type or "DATA_COVERAGE"
                    ),
                    rule.coverage_missing_fact,
                    rule.rule_id,
                )
            ]

        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=EvaluationStatus.UNKNOWN,
            reason_code="ABSENCE_QUERY_COVERAGE_INCOMPLETE",
            missing_facts=missing,
            evidence={
                "entity": rule.entity.value,
                "filters": rule.filters,
                "window": self._window_evidence(window_start, window_end),
                "self_report_fallback_available": fallback is not None,
                **coverage_evidence,
            },
            source_provenance=self._merge_provenance(
                self._rule_provenance(rule.source), coverage_provenance
            ),
            verification_level=VerificationLevel.UNKNOWN,
        )

    def _evaluate_count_distinct_months(
        self,
        rule: CountDistinctMonthsRule,
    ) -> RuleEvaluation:
        return self._evaluate_period_aggregation(
            rule,
            period=PeriodUnit.MONTH,
            legacy_month_alias=True,
        )

    def _evaluate_count_distinct_periods(
        self,
        rule: CountDistinctPeriodsRule,
    ) -> RuleEvaluation:
        return self._evaluate_period_aggregation(
            rule,
            period=rule.period,
            legacy_month_alias=False,
        )

    def _evaluate_period_aggregation(
        self,
        rule: CountDistinctMonthsRule | CountDistinctPeriodsRule,
        *,
        period: PeriodUnit,
        legacy_month_alias: bool,
    ) -> RuleEvaluation:
        window_start, window_end = resolve_window(rule.window, self.context)
        if self.audit is not None:
            self.audit.emit(
                "TEMPORAL_ENGINE",
                AuditEventType.DATE_EXPRESSION_RESOLVED,
                entity_refs={"rule_id": rule.rule_id},
                input_data=rule.window,
                output_data={"start": window_start, "end": window_end},
                payload={
                    "label": "aggregation_window",
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                    "period": period.value,
                },
            )
        subject_ids, relationship_provenance, subject_error = (
            self._resolve_subject_selector(
                rule.subject_selector,
                effective_at=self.context.as_of,
                rule_id=rule.rule_id,
                rule_name=rule.name,
                source=rule.source,
            )
        )
        if subject_error is not None:
            return subject_error

        raw_records: list[Any]
        accepted_records: list[Any]
        record_ids: list[str]
        record_provenance: list[ProvenanceRecord]
        if rule.event_entity == EntityType.RECURRING_PAYMENT_EVENT:
            raw_records = [
                event
                for event in self.fact_store.recurring_payment_events
                if event.event_type == rule.fact_type
                and event.effective_subject_person_id in subject_ids
            ]
            acceptance = self.fact_resolver.accept_all(
                raw_records, policy=rule.fact_acceptance_policy
            )
            accepted_records = records_matching_predicates(
                acceptance.accepted, rule.filters
            )
            qualifying_periods = distinct_record_periods(
                accepted_records,
                period=period,
                date_path=rule.date_path,
                default_path="occurred_at",
                start=window_start,
                end=window_end,
                as_of=self.context.as_of,
            )
            record_ids = [event.event_id for event in accepted_records]
            record_provenance = self._merge_provenance(
                *(self._recurring_payment_provenance(event) for event in accepted_records)
            )
        else:
            raw_records = self.fact_resolver.all_facts(
                rule.fact_type, subject_person_ids=subject_ids
            )
            acceptance = self.fact_resolver.accept_all(
                raw_records, policy=rule.fact_acceptance_policy
            )
            accepted_records = facts_matching_predicates(
                acceptance.accepted, rule.filters
            )
            qualifying_periods = distinct_periods(
                accepted_records,
                period=period,
                date_path=rule.date_path,
                start=window_start,
                end=window_end,
                as_of=self.context.as_of,
            )
            record_ids = [fact.fact_id for fact in accepted_records]
            record_provenance = self._merge_provenance(
                *(self._fact_provenance(fact) for fact in accepted_records)
            )

        rejected_ids = [
            self._authority_record_id(record) for record in acceptance.rejected
        ]
        verification_level = (
            acceptance.verification_level
            if accepted_records
            else VerificationLevel.UNKNOWN
        )
        is_provisional = acceptance.is_provisional and bool(accepted_records)

        coverage_complete, coverage_evidence, coverage_provenance = (
            self._check_observation_coverage(
                rule_id=rule.rule_id,
                requirement=rule.coverage,
                window_start=window_start,
                window_end=window_end,
            )
        )
        if not coverage_complete:
            missing = [
                self.fact_resolver.missing_request(
                    f"DATA_COVERAGE:{rule.coverage.fact_domain}",
                    rule.coverage_missing_fact,
                    rule.rule_id,
                )
            ] if rule.coverage is not None and rule.coverage_missing_fact is not None else [
                self._coverage_missing_request(rule.coverage, rule.rule_id)
            ]
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNKNOWN,
                reason_code="DATA_SYNC_REQUIRED",
                progress=Progress(
                    current=len(qualifying_periods), required=rule.expected, unit=period.value
                ),
                missing_facts=missing,
                evidence={
                    "fact_type": rule.fact_type,
                    "period": period.value,
                    "current": len(qualifying_periods),
                    "required": rule.expected,
                    "observation_state": "NOT_OBSERVED_OR_SYNC_UNAVAILABLE",
                    **coverage_evidence,
                },
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    record_provenance,
                    coverage_provenance,
                ),
                is_provisional=is_provisional,
                verification_level=verification_level,
            )

        if acceptance.rejected and not acceptance.accepted:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNKNOWN,
                reason_code="FACT_AUTHORITY_INSUFFICIENT",
                progress=Progress(current=0, required=rule.expected, unit=period.value),
                missing_facts=[self._authority_missing_request(rule.fact_type, rule.rule_id)],
                evidence={
                    "fact_type": rule.fact_type,
                    "period": period.value,
                    "legacy_trust_mode_ignored": (
                        self.trust_mode.value if self.trust_mode is not None else None
                    ),
                    "rejected_record_ids": rejected_ids,
                    "observation_state": "EVIDENCE_NOT_ACCEPTED_BY_RULE_POLICY",
                    **coverage_evidence,
                },
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    *(self._generic_authority_provenance(item) for item in acceptance.rejected),
                    coverage_provenance,
                ),
                verification_level=VerificationLevel.UNKNOWN,
            )

        current = len(qualifying_periods)
        reached = compare_values(current, rule.operator, rule.expected)
        progress = Progress(current=current, required=rule.expected, unit=period.value)
        base_evidence: dict[str, Any] = {
            "fact_type": rule.fact_type,
            "event_entity": rule.event_entity.value if rule.event_entity else "USER_FACT",
            "period": period.value,
            "subject_person_ids": sorted(subject_ids),
            "window_from": window_start.isoformat(),
            "window_to": window_end.isoformat(),
            "qualifying_periods": qualifying_periods,
            "current": current,
            "required": rule.expected,
            "operator": rule.operator.value,
            "evaluation_phase": rule.evaluation_phase.value,
            "legacy_trust_mode_ignored": (
                self.trust_mode.value if self.trust_mode is not None else None
            ),
            "verification_level": verification_level.value,
            "is_provisional": is_provisional,
            "accepted_record_ids": record_ids,
            "rejected_record_ids": rejected_ids,
            "observation_state": "OBSERVED",
            **coverage_evidence,
        }
        if legacy_month_alias:
            base_evidence["qualifying_months"] = qualifying_periods

        if self.audit is not None:
            self.audit.emit(
                "AGGREGATION_ENGINE",
                AuditEventType.AGGREGATION_EXECUTED,
                entity_refs={"rule_id": rule.rule_id},
                input_data={
                    "record_ids": record_ids,
                    "period": period.value,
                    "window_start": window_start,
                    "window_end": window_end,
                    "filters": [item.model_dump(mode="json") for item in rule.filters],
                },
                output_data={
                    "qualifying_periods": qualifying_periods,
                    "count": current,
                },
                payload={
                    "aggregation": "COUNT_DISTINCT_PERIODS",
                    "period": period.value,
                    "fact_type": rule.fact_type,
                    "input_record_count": len(accepted_records),
                    "rejected_authority_count": len(acceptance.rejected),
                    "result_count": current,
                    "verification_level": verification_level.value,
                    "is_provisional": is_provisional,
                },
            )
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.COMPARISON_EXECUTED,
                entity_refs={"rule_id": rule.rule_id},
                input_data={
                    "actual": current,
                    "operator": rule.operator.value,
                    "expected": rule.expected,
                },
                output_data={"matched": reached},
                payload={
                    "operator": rule.operator.value,
                    "matched": reached,
                    "aggregation": "COUNT_DISTINCT_PERIODS",
                },
            )

        if reached:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.SATISFIED,
                reason_code=rule.true_reason_code,
                progress=progress,
                evidence=base_evidence,
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    record_provenance,
                    coverage_provenance,
                ),
                is_provisional=is_provisional,
                verification_level=verification_level,
            )

        if rule.future_achievement is None:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNSATISFIABLE,
                reason_code=rule.false_reason_code,
                progress=progress,
                evidence=base_evidence,
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    record_provenance,
                    coverage_provenance,
                ),
                is_provisional=is_provisional,
                verification_level=verification_level,
            )

        future = rule.future_achievement
        projection = project_remaining_opportunities(
            window_start=window_start,
            deadline=window_end,
            as_of=self.context.as_of,
            subscription_date=self.context.subscription_date,
            period=period,
            qualifying_periods=qualifying_periods,
        )
        slots = list(projection.future_periods)
        remaining_required = max(0, rule.expected - current)
        available_capacity = len(slots) * future.max_qualifying_units_per_opportunity
        enough_time = available_capacity >= remaining_required
        buffer = available_capacity - remaining_required
        base_evidence.update(
            {
                "achievement_mode": future.achievement_mode.value,
                "remaining_required": remaining_required,
                "available_future_periods": slots,
                "remaining_opportunities": len(slots),
                "max_qualifying_units_per_opportunity": (
                    future.max_qualifying_units_per_opportunity
                ),
                "available_capacity": available_capacity,
                "buffer": buffer,
                "time_feasible": enough_time,
                "opportunity_start": projection.opportunity_start.isoformat(),
            }
        )
        if legacy_month_alias:
            base_evidence["available_future_months"] = slots
        if future.action is not None:
            base_evidence["action"] = future.action.model_dump(mode="json")
        if future.action_paths:
            base_evidence["action_paths"] = [
                item.model_dump(mode="json") for item in future.action_paths
            ]
        if future.goal_template is not None:
            base_evidence["goal_template"] = future.goal_template.model_dump(mode="json")

        if self.audit is not None:
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.FUTURE_CAPACITY_CHECKED,
                entity_refs={"rule_id": rule.rule_id},
                input_data={
                    "window_start": projection.opportunity_start,
                    "window_end": window_end,
                    "qualifying_periods": qualifying_periods,
                    "required": rule.expected,
                },
                output_data={
                    "remaining_required": remaining_required,
                    "remaining_opportunities": len(slots),
                    "available_capacity": available_capacity,
                    "buffer": buffer,
                    "time_feasible": enough_time,
                },
                payload={
                    "period": period.value,
                    "remaining_required": remaining_required,
                    "remaining_opportunities": len(slots),
                    "available_capacity": available_capacity,
                    "buffer": buffer,
                    "time_feasible": enough_time,
                },
            )

        if not enough_time:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNSATISFIABLE,
                reason_code=future.insufficient_time_reason_code,
                progress=progress,
                evidence=base_evidence,
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    record_provenance,
                    coverage_provenance,
                ),
                is_provisional=is_provisional,
                verification_level=verification_level,
            )

        capability: RuleEvaluation | None = None
        children: list[RuleEvaluation] = []
        capability_provenance: list[ProvenanceRecord] = []
        if future.capability_rule is not None:
            capability = self.evaluate(future.capability_rule)
            children.append(capability)
            capability_provenance = capability.source_provenance
            base_evidence["capability_status"] = capability.status.value
            if capability.status == EvaluationStatus.UNKNOWN:
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNKNOWN,
                    reason_code="FUTURE_CAPABILITY_UNKNOWN",
                    progress=progress,
                    missing_facts=capability.missing_facts,
                    evidence=base_evidence,
                    source_provenance=self._merge_provenance(
                        self._rule_provenance(rule.source),
                        relationship_provenance,
                        record_provenance,
                        coverage_provenance,
                        capability_provenance,
                    ),
                    children=children,
                    is_provisional=is_provisional or capability.is_provisional,
                    verification_level=self._combine_verification_levels(
                        verification_level, capability.verification_level
                    ),
                )
            if capability.status == EvaluationStatus.UNSATISFIABLE:
                if not future.alternative_action_paths_complete:
                    base_evidence["unmodeled_alternative_action_paths"] = True
                    return RuleEvaluation(
                        rule_id=rule.rule_id,
                        rule_name=rule.name,
                        status=EvaluationStatus.UNKNOWN,
                        reason_code=future.unmodeled_alternative_reason_code,
                        progress=progress,
                        evidence=base_evidence,
                        source_provenance=self._merge_provenance(
                            self._rule_provenance(rule.source),
                            relationship_provenance,
                            record_provenance,
                            coverage_provenance,
                            capability_provenance,
                        ),
                        children=children,
                        is_provisional=is_provisional or capability.is_provisional,
                        verification_level=self._combine_verification_levels(
                            verification_level, capability.verification_level
                        ),
                    )
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNSATISFIABLE,
                    reason_code=capability.reason_code,
                    progress=progress,
                    evidence=base_evidence,
                    source_provenance=self._merge_provenance(
                        self._rule_provenance(rule.source),
                        relationship_provenance,
                        record_provenance,
                        coverage_provenance,
                        capability_provenance,
                    ),
                    children=children,
                    is_provisional=is_provisional or capability.is_provisional,
                    verification_level=self._combine_verification_levels(
                        verification_level, capability.verification_level
                    ),
                )

        intent_provenance: list[ProvenanceRecord] = []
        if future.intent_fact_type is not None:
            intent_resolution = self.fact_resolver.resolve(
                future.intent_fact_type,
                self.context.as_of,
                subject_person_ids=subject_ids,
                required_semantic_type=FactSemanticType.FUTURE_INTENT,
            )
            if (
                intent_resolution.status == ResolutionStatus.CONFLICT
                or intent_resolution.fact is None
            ):
                missing = []
                if future.missing_fact is not None:
                    missing = [
                        self.fact_resolver.missing_request(
                            future.intent_fact_type,
                            future.missing_fact,
                            rule.rule_id,
                        )
                    ]
                reason = (
                    "FUTURE_INTENT_CONFLICT"
                    if intent_resolution.status == ResolutionStatus.CONFLICT
                    else "FUTURE_INTENT_REQUIRED"
                )
                base_evidence.update(
                    {
                        "intent_fact_type": future.intent_fact_type,
                        "intent_status": "UNKNOWN",
                    }
                )
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNKNOWN,
                    reason_code=reason,
                    progress=progress,
                    missing_facts=missing,
                    evidence=base_evidence,
                    source_provenance=self._merge_provenance(
                        self._rule_provenance(rule.source),
                        relationship_provenance,
                        record_provenance,
                        coverage_provenance,
                        capability_provenance,
                    ),
                    children=children,
                    is_provisional=is_provisional,
                    verification_level=verification_level,
                )

            intent_fact = intent_resolution.fact
            intent_provenance = self._fact_provenance(intent_fact)
            base_evidence.update(
                {
                    "intent_fact_type": future.intent_fact_type,
                    "intent_fact_id": intent_fact.fact_id,
                    "intent_semantic_type": intent_fact.semantic_type.value,
                    "intent_source_type": intent_fact.source_type.value,
                    "intent_value": intent_fact.value,
                }
            )
            if intent_fact.value is False:
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNSATISFIABLE,
                    reason_code=future.user_declined_reason_code,
                    progress=progress,
                    evidence={**base_evidence, "ui_disposition": "NOT_SELECTED"},
                    source_provenance=self._merge_provenance(
                        self._rule_provenance(rule.source),
                        relationship_provenance,
                        record_provenance,
                        coverage_provenance,
                        capability_provenance,
                        intent_provenance,
                    ),
                    children=children,
                    verification_level=combine_verification_levels(
                        self._collect_evidence_levels(
                            verification_level, VerificationLevel.USER_INTENT, children=children
                        )
                    ),
                    evidence_levels=self._collect_evidence_levels(
                        verification_level, VerificationLevel.USER_INTENT, children=children
                    ),
                )
            if intent_fact.value is not True:
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNKNOWN,
                    reason_code="INVALID_FUTURE_INTENT_VALUE",
                    progress=progress,
                    evidence=base_evidence,
                    source_provenance=self._merge_provenance(
                        self._rule_provenance(rule.source),
                        relationship_provenance,
                        record_provenance,
                        coverage_provenance,
                        capability_provenance,
                        intent_provenance,
                    ),
                    children=children,
                    is_provisional=is_provisional,
                    verification_level=verification_level,
                )

        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=EvaluationStatus.ACHIEVABLE,
            reason_code=future.achievable_reason_code,
            progress=progress,
            evidence=base_evidence,
            source_provenance=self._merge_provenance(
                self._rule_provenance(rule.source),
                relationship_provenance,
                record_provenance,
                coverage_provenance,
                capability_provenance,
                intent_provenance,
            ),
            children=children,
            verification_level=combine_verification_levels(
                self._collect_evidence_levels(
                    verification_level, VerificationLevel.USER_INTENT, children=children
                )
            ) if future.intent_fact_type is not None else combine_verification_levels(
                self._collect_evidence_levels(verification_level, children=children)
            ),
            evidence_levels=(
                self._collect_evidence_levels(
                    verification_level, VerificationLevel.USER_INTENT, children=children
                )
                if future.intent_fact_type is not None
                else self._collect_evidence_levels(verification_level, children=children)
            ),
        )

    def _evaluate_count_consecutive(
        self,
        rule: CountConsecutiveRule,
    ) -> RuleEvaluation:
        subject_ids, relationship_provenance, subject_error = (
            self._resolve_subject_selector(
                rule.subject_selector,
                effective_at=self.context.as_of,
                rule_id=rule.rule_id,
                rule_name=rule.name,
                source=rule.source,
            )
        )
        if subject_error is not None:
            return subject_error

        window_start: date | None = None
        window_end: date | None = None
        if rule.window is not None:
            window_start, window_end = resolve_window(rule.window, self.context)
            if self.audit is not None:
                self.audit.emit(
                    "TEMPORAL_ENGINE",
                    AuditEventType.DATE_EXPRESSION_RESOLVED,
                    entity_refs={"rule_id": rule.rule_id},
                    input_data=rule.window,
                    output_data={"start": window_start, "end": window_end},
                    payload={
                        "label": "consecutive_window",
                        "start": window_start.isoformat(),
                        "end": window_end.isoformat(),
                    },
                )

        raw_occurrences = [
            occurrence
            for occurrence in self.fact_store.scheduled_occurrences
            if occurrence.effective_subject_person_id in subject_ids
            and (rule.schedule_id is None or occurrence.schedule_id == rule.schedule_id)
            and (
                window_start is None
                or window_end is None
                or window_start <= occurrence.scheduled_at.date() <= window_end
            )
            and occurrence.scheduled_at.date() <= self.context.as_of
        ]
        acceptance = self.fact_resolver.accept_all(
            raw_occurrences, policy=rule.fact_acceptance_policy
        )
        occurrences = list(acceptance.accepted)
        verification_level = (
            acceptance.verification_level
            if occurrences
            else VerificationLevel.UNKNOWN
        )
        is_provisional = acceptance.is_provisional and bool(occurrences)

        if window_start is not None and window_end is not None:
            coverage_complete, coverage_evidence, coverage_provenance = (
                self._check_observation_coverage(
                    rule_id=rule.rule_id,
                    requirement=rule.coverage,
                    window_start=window_start,
                    window_end=window_end,
                )
            )
        else:
            coverage_complete, coverage_evidence, coverage_provenance = True, {}, []
        if not coverage_complete:
            missing = [
                self.fact_resolver.missing_request(
                    f"DATA_COVERAGE:{rule.coverage.fact_domain}",
                    rule.coverage_missing_fact,
                    rule.rule_id,
                )
            ] if rule.coverage is not None and rule.coverage_missing_fact is not None else [
                self._coverage_missing_request(rule.coverage, rule.rule_id)
            ]
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNKNOWN,
                reason_code="DATA_SYNC_REQUIRED",
                progress=Progress(current=0, required=rule.expected, unit="OCCURRENCE"),
                missing_facts=missing,
                evidence={
                    "schedule_id": rule.schedule_id,
                    "observation_state": "NOT_OBSERVED_OR_SYNC_UNAVAILABLE",
                    **coverage_evidence,
                },
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    coverage_provenance,
                ),
                is_provisional=is_provisional,
                verification_level=verification_level,
            )

        if acceptance.rejected and not acceptance.accepted:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNKNOWN,
                reason_code="FACT_AUTHORITY_INSUFFICIENT",
                progress=Progress(current=0, required=rule.expected, unit="OCCURRENCE"),
                missing_facts=[
                    self._authority_missing_request(
                        f"SCHEDULED_OCCURRENCE:{rule.schedule_id or 'ANY'}",
                        rule.rule_id,
                    )
                ],
                evidence={
                    "schedule_id": rule.schedule_id,
                    "legacy_trust_mode_ignored": (
                        self.trust_mode.value if self.trust_mode is not None else None
                    ),
                    "rejected_record_ids": [
                        self._authority_record_id(item) for item in acceptance.rejected
                    ],
                },
                source_provenance=self._merge_provenance(
                    self._rule_provenance(rule.source),
                    relationship_provenance,
                    *(self._generic_authority_provenance(item) for item in acceptance.rejected),
                ),
                verification_level=VerificationLevel.UNKNOWN,
            )

        consecutive = count_consecutive_occurrences(
            occurrences,
            predicates=rule.filters,
            from_sequence=rule.from_sequence,
        )
        current = consecutive.count
        reached = compare_values(current, rule.operator, rule.expected)
        if self.audit is not None:
            self.audit.emit(
                "AGGREGATION_ENGINE",
                AuditEventType.AGGREGATION_EXECUTED,
                entity_refs={"rule_id": rule.rule_id},
                input_data={
                    "occurrence_ids": [item.occurrence_id for item in occurrences],
                    "from_sequence": rule.from_sequence,
                    "filters": [item.model_dump(mode="json") for item in rule.filters],
                },
                output_data={
                    "count": current,
                    "matched_sequence_nos": list(consecutive.matched_sequence_nos),
                    "first_break_sequence": consecutive.first_break_sequence,
                },
                payload={
                    "aggregation": "COUNT_CONSECUTIVE",
                    "result_count": current,
                    "input_occurrence_count": len(occurrences),
                    "streak_scope": (
                        "FIXED_START" if rule.from_sequence is not None else "ANYWHERE"
                    ),
                    "verification_level": verification_level.value,
                    "is_provisional": is_provisional,
                },
            )
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.COMPARISON_EXECUTED,
                entity_refs={"rule_id": rule.rule_id},
                input_data={
                    "actual": current,
                    "operator": rule.operator.value,
                    "expected": rule.expected,
                },
                output_data={"matched": reached},
                payload={
                    "operator": rule.operator.value,
                    "matched": reached,
                    "aggregation": "COUNT_CONSECUTIVE",
                },
            )
        qualifying_occurrence_count = sum(
            1
            for occurrence in occurrences
            if values_match_predicates(occurrence, rule.filters)
        )
        evidence: dict[str, Any] = {
            "entity": rule.entity.value,
            "schedule_id": rule.schedule_id,
            "subject_person_ids": sorted(subject_ids),
            "from_sequence": rule.from_sequence,
            "streak_scope": ("FIXED_START" if rule.from_sequence is not None else "ANYWHERE"),
            "matched_sequence_nos": list(consecutive.matched_sequence_nos),
            "streak_start_sequence": consecutive.start_sequence,
            "streak_end_sequence": consecutive.end_sequence,
            "consecutive_count": current,
            "trailing_consecutive_count": consecutive.trailing_count,
            "latest_observed_sequence": consecutive.latest_observed_sequence,
            "qualifying_occurrence_count": qualifying_occurrence_count,
            "first_break_sequence": consecutive.first_break_sequence,
            "break_reason": consecutive.break_reason,
            "break_occurrence_ids": list(consecutive.break_occurrence_ids),
            "expected": rule.expected,
            "operator": rule.operator.value,
            "window": self._window_evidence(window_start, window_end),
            "legacy_trust_mode_ignored": (
                self.trust_mode.value if self.trust_mode is not None else None
            ),
            "verification_level": verification_level.value,
            "is_provisional": is_provisional,
            **coverage_evidence,
        }
        progress = Progress(current=current, required=rule.expected, unit="OCCURRENCE")
        provenance = self._merge_provenance(
            self._rule_provenance(rule.source),
            relationship_provenance,
            coverage_provenance,
            *(self._occurrence_provenance(item) for item in occurrences),
        )
        if reached:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.SATISFIED,
                reason_code=rule.true_reason_code,
                progress=progress,
                evidence=evidence,
                source_provenance=provenance,
                is_provisional=is_provisional,
                verification_level=verification_level,
            )

        if rule.future_achievement is None:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNSATISFIABLE,
                reason_code=rule.false_reason_code,
                progress=progress,
                evidence=evidence,
                source_provenance=provenance,
                is_provisional=is_provisional,
                verification_level=verification_level,
            )

        future = rule.future_achievement
        expected_total = rule.expected_occurrence_count
        if expected_total is None and window_start is not None and window_end is not None:
            expected_total = len(
                available_period_keys(
                    window_start,
                    window_end,
                    period=rule.opportunity_period,
                )
            )
        if expected_total is None:
            expected_total = max(rule.expected, consecutive.latest_observed_sequence)
        future_occurrences = max(
            0, expected_total - consecutive.latest_observed_sequence
        )
        if rule.from_sequence is None:
            maximum_possible_streak = consecutive.trailing_count + future_occurrences
        else:
            fixed_break_is_failure = (
                consecutive.first_break_sequence is not None
                and consecutive.break_reason == "SEQUENCE_PREDICATE_FAILED"
            )
            maximum_possible_streak = (
                current if fixed_break_is_failure else current + future_occurrences
            )
        remaining_required = max(0, rule.expected - current)
        enough_time = maximum_possible_streak >= rule.expected
        evidence.update(
            {
                "achievement_mode": future.achievement_mode.value,
                "expected_occurrence_count": expected_total,
                "remaining_opportunities": future_occurrences,
                "remaining_required": remaining_required,
                "maximum_possible_streak": maximum_possible_streak,
                "time_feasible": enough_time,
                "buffer": maximum_possible_streak - rule.expected,
            }
        )
        if future.action is not None:
            evidence["action"] = future.action.model_dump(mode="json")
        if future.action_paths:
            evidence["action_paths"] = [
                item.model_dump(mode="json") for item in future.action_paths
            ]
        if future.goal_template is not None:
            evidence["goal_template"] = future.goal_template.model_dump(mode="json")
        if self.audit is not None:
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.FUTURE_CAPACITY_CHECKED,
                entity_refs={"rule_id": rule.rule_id},
                input_data={
                    "current_streak": current,
                    "trailing_streak": consecutive.trailing_count,
                    "expected_occurrence_count": expected_total,
                },
                output_data={
                    "future_occurrences": future_occurrences,
                    "maximum_possible_streak": maximum_possible_streak,
                    "time_feasible": enough_time,
                },
                payload={
                    "remaining_required": remaining_required,
                    "remaining_opportunities": future_occurrences,
                    "maximum_possible_streak": maximum_possible_streak,
                    "time_feasible": enough_time,
                },
            )
        if not enough_time:
            return RuleEvaluation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=EvaluationStatus.UNSATISFIABLE,
                reason_code=future.insufficient_time_reason_code,
                progress=progress,
                evidence=evidence,
                source_provenance=provenance,
                is_provisional=is_provisional,
                verification_level=verification_level,
            )

        capability_provenance: list[ProvenanceRecord] = []
        children: list[RuleEvaluation] = []
        if future.capability_rule is not None:
            capability = self.evaluate(future.capability_rule)
            children.append(capability)
            capability_provenance = capability.source_provenance
            evidence["capability_status"] = capability.status.value
            if capability.status == EvaluationStatus.UNKNOWN:
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNKNOWN,
                    reason_code="FUTURE_CAPABILITY_UNKNOWN",
                    progress=progress,
                    missing_facts=capability.missing_facts,
                    evidence=evidence,
                    source_provenance=self._merge_provenance(provenance, capability_provenance),
                    children=children,
                    is_provisional=is_provisional or capability.is_provisional,
                    verification_level=self._combine_verification_levels(
                        verification_level, capability.verification_level
                    ),
                )
            if capability.status == EvaluationStatus.UNSATISFIABLE:
                if not future.alternative_action_paths_complete:
                    evidence["unmodeled_alternative_action_paths"] = True
                    return RuleEvaluation(
                        rule_id=rule.rule_id,
                        rule_name=rule.name,
                        status=EvaluationStatus.UNKNOWN,
                        reason_code=future.unmodeled_alternative_reason_code,
                        progress=progress,
                        evidence=evidence,
                        source_provenance=self._merge_provenance(
                            provenance, capability_provenance
                        ),
                        children=children,
                        is_provisional=is_provisional or capability.is_provisional,
                        verification_level=self._combine_verification_levels(
                            verification_level, capability.verification_level
                        ),
                    )
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNSATISFIABLE,
                    reason_code=capability.reason_code,
                    progress=progress,
                    evidence=evidence,
                    source_provenance=self._merge_provenance(provenance, capability_provenance),
                    children=children,
                    is_provisional=is_provisional or capability.is_provisional,
                    verification_level=self._combine_verification_levels(
                        verification_level, capability.verification_level
                    ),
                )

        intent_provenance: list[ProvenanceRecord] = []
        if future.intent_fact_type is not None:
            intent_resolution = self.fact_resolver.resolve(
                future.intent_fact_type,
                self.context.as_of,
                subject_person_ids=subject_ids,
                required_semantic_type=FactSemanticType.FUTURE_INTENT,
            )
            if intent_resolution.status == ResolutionStatus.CONFLICT or intent_resolution.fact is None:
                missing = [
                    self.fact_resolver.missing_request(
                        future.intent_fact_type, future.missing_fact, rule.rule_id
                    )
                ] if future.missing_fact is not None else []
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNKNOWN,
                    reason_code=(
                        "FUTURE_INTENT_CONFLICT"
                        if intent_resolution.status == ResolutionStatus.CONFLICT
                        else "FUTURE_INTENT_REQUIRED"
                    ),
                    progress=progress,
                    missing_facts=missing,
                    evidence={
                        **evidence,
                        "intent_fact_type": future.intent_fact_type,
                        "intent_status": "UNKNOWN",
                    },
                    source_provenance=self._merge_provenance(provenance, capability_provenance),
                    children=children,
                    is_provisional=is_provisional,
                    verification_level=verification_level,
                )
            intent_fact = intent_resolution.fact
            intent_provenance = self._fact_provenance(intent_fact)
            evidence.update(
                {
                    "intent_fact_type": future.intent_fact_type,
                    "intent_fact_id": intent_fact.fact_id,
                    "intent_value": intent_fact.value,
                }
            )
            if intent_fact.value is False:
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNSATISFIABLE,
                    reason_code=future.user_declined_reason_code,
                    progress=progress,
                    evidence={**evidence, "ui_disposition": "NOT_SELECTED"},
                    source_provenance=self._merge_provenance(
                        provenance, capability_provenance, intent_provenance
                    ),
                    children=children,
                    verification_level=combine_verification_levels(
                        self._collect_evidence_levels(
                            verification_level, VerificationLevel.USER_INTENT, children=children
                        )
                    ),
                    evidence_levels=self._collect_evidence_levels(
                        verification_level, VerificationLevel.USER_INTENT, children=children
                    ),
                )
            if intent_fact.value is not True:
                return RuleEvaluation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    status=EvaluationStatus.UNKNOWN,
                    reason_code="INVALID_FUTURE_INTENT_VALUE",
                    progress=progress,
                    evidence=evidence,
                    source_provenance=self._merge_provenance(
                        provenance, capability_provenance, intent_provenance
                    ),
                    children=children,
                    is_provisional=is_provisional,
                    verification_level=verification_level,
                )

        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            status=EvaluationStatus.ACHIEVABLE,
            reason_code=future.achievable_reason_code,
            progress=progress,
            evidence=evidence,
            source_provenance=self._merge_provenance(
                provenance, capability_provenance, intent_provenance
            ),
            children=children,
            verification_level=combine_verification_levels(
                self._collect_evidence_levels(
                    verification_level, VerificationLevel.USER_INTENT, children=children
                )
            ) if future.intent_fact_type is not None else combine_verification_levels(
                self._collect_evidence_levels(verification_level, children=children)
            ),
            evidence_levels=(
                self._collect_evidence_levels(
                    verification_level, VerificationLevel.USER_INTENT, children=children
                )
                if future.intent_fact_type is not None
                else self._collect_evidence_levels(verification_level, children=children)
            ),
        )

    def _check_observation_coverage(
        self,
        *,
        rule_id: str,
        requirement: CoverageRequirement | None,
        window_start: date,
        window_end: date,
    ) -> tuple[bool, dict[str, Any], list[ProvenanceRecord]]:
        if requirement is None:
            return True, {}, []
        observed_from = max(window_start, self.context.subscription_date)
        observed_to = min(window_end, self.context.as_of)
        if observed_to < observed_from:
            return True, {
                "coverage_domain": requirement.fact_domain,
                "coverage_complete": True,
                "coverage_not_yet_required": True,
            }, []
        accepted_sources = set(requirement.accepted_source_types)
        coverages = [
            coverage
            for coverage in self.fact_store.data_coverages
            if coverage.fact_domain == requirement.fact_domain
            and (
                requirement.institution is None
                or coverage.institution == requirement.institution
            )
            and coverage.source_type in accepted_sources
        ]
        complete = coverage_intervals_fully_cover(
            ((item.covered_from, item.covered_to) for item in coverages),
            observed_from,
            observed_to,
        )
        evidence = {
            "coverage_domain": requirement.fact_domain,
            "coverage_institution": requirement.institution,
            "required_coverage_from": observed_from.isoformat(),
            "required_coverage_to": observed_to.isoformat(),
            "coverage_complete": complete,
            "coverage_intervals": [
                {
                    "coverage_id": item.coverage_id,
                    "covered_from": item.covered_from.isoformat(),
                    "covered_to": item.covered_to.isoformat(),
                    "source_type": item.source_type.value,
                }
                for item in sorted(coverages, key=lambda value: value.coverage_id)
            ],
        }
        provenance = self._merge_provenance(
            *(self._coverage_provenance(item) for item in coverages)
        )
        if self.audit is not None:
            self.audit.emit(
                "RULE_EVALUATOR",
                AuditEventType.DATA_COVERAGE_CHECKED,
                entity_refs={"rule_id": rule_id},
                input_data=coverages,
                output_data={"coverage_complete": complete},
                payload=evidence,
            )
        return complete, evidence, provenance

    @staticmethod
    def _coverage_missing_request(
        requirement: CoverageRequirement | None,
        rule_id: str,
    ) -> MissingFactRequest:
        domain = requirement.fact_domain if requirement is not None else "UNKNOWN"
        return MissingFactRequest(
            fact_type=f"DATA_COVERAGE:{domain}",
            resolution_strategy=ResolutionStrategy.QUERY_INSTITUTION,
            required_source=(
                requirement.institution if requirement is not None else None
            ),
            requested_by_rule_id=rule_id,
        )

    @staticmethod
    def _authority_missing_request(fact_type: str, rule_id: str) -> MissingFactRequest:
        return MissingFactRequest(
            fact_type=fact_type,
            resolution_strategy=ResolutionStrategy.QUERY_INSTITUTION,
            required_source="INSTITUTION_OR_MYDATA_VERIFIED",
            requested_by_rule_id=rule_id,
        )

    @staticmethod
    def _authority_record_id(record: Any) -> str:
        for field in ("fact_id", "occurrence_id", "event_id"):
            value = getattr(record, field, None)
            if isinstance(value, str):
                return value
        return repr(record)

    def _generic_authority_provenance(self, record: Any) -> list[ProvenanceRecord]:
        if isinstance(record, UserFact):
            return self._fact_provenance(record)
        if isinstance(record, ScheduledOccurrence):
            return self._occurrence_provenance(record)
        if isinstance(record, RecurringPaymentEvent):
            return self._recurring_payment_provenance(record)
        return []

    @staticmethod
    def _combine_verification_levels(
        left: VerificationLevel,
        right: VerificationLevel,
    ) -> VerificationLevel:
        return combine_verification_levels([left, right])

    @classmethod
    def _verification_from_children(
        cls, children: list[RuleEvaluation]
    ) -> tuple[bool, VerificationLevel]:
        levels: list[VerificationLevel] = []
        for child in children:
            child_levels = child.evidence_levels or (
                [child.verification_level]
                if child.verification_level != VerificationLevel.UNKNOWN
                else []
            )
            for level in child_levels:
                if level not in levels:
                    levels.append(level)
        return (
            VerificationLevel.SELF_REPORTED in levels,
            combine_verification_levels(levels),
        )

    @staticmethod
    def _verification_from_source(source_type: FactSourceType) -> VerificationLevel:
        if source_type == FactSourceType.INSTITUTION_VERIFIED:
            return VerificationLevel.INSTITUTION_VERIFIED
        if source_type == FactSourceType.MYDATA_VERIFIED:
            return VerificationLevel.MYDATA_VERIFIED
        if source_type == FactSourceType.DERIVED:
            return VerificationLevel.DERIVED
        if source_type == FactSourceType.USER_DECLARED:
            return VerificationLevel.SELF_REPORTED
        return VerificationLevel.UNKNOWN

    @staticmethod
    def _collect_evidence_levels(
        *levels: VerificationLevel,
        children: list[RuleEvaluation] | None = None,
    ) -> list[VerificationLevel]:
        merged: list[VerificationLevel] = []
        for level in levels:
            if level not in {VerificationLevel.UNKNOWN, VerificationLevel.MIXED} and level not in merged:
                merged.append(level)
        for child in children or []:
            for level in (child.evidence_levels or [child.verification_level]):
                if level not in {VerificationLevel.UNKNOWN, VerificationLevel.MIXED} and level not in merged:
                    merged.append(level)
        return merged

    def _resolve_subject_selector(
        self,
        selector: FactSubjectSelector | None,
        *,
        effective_at: date,
        rule_id: str,
        rule_name: str,
        source: SourceReference | None,
    ) -> tuple[set[str], list[ProvenanceRecord], RuleEvaluation | None]:
        resolution = self.fact_resolver.resolve_subjects(selector, effective_at)
        if resolution.status == ResolutionStatus.RESOLVED:
            provenance = self._merge_provenance(
                *(
                    self._relationship_provenance(relationship)
                    for relationship in resolution.relationships
                )
            )
            return set(resolution.person_ids), provenance, None

        relationship_type = (
            selector.relationship_type
            if selector is not None and selector.relationship_type is not None
            else "UNSPECIFIED"
        )
        missing: list[MissingFactRequest] = []
        if selector is not None and selector.relationship_missing_fact is not None:
            missing = [
                self.fact_resolver.missing_request(
                    f"PERSON_RELATIONSHIP:{relationship_type}",
                    selector.relationship_missing_fact,
                    rule_id,
                )
            ]
        unverified = list(resolution.unverified_relationships)
        reason = (
            "RELATED_PERSON_RELATIONSHIP_UNVERIFIED"
            if unverified
            else "RELATED_PERSON_RELATIONSHIP_MISSING"
        )
        evidence = {
            "relationship_type": relationship_type,
            "unverified_relationship_ids": [
                relationship.relationship_id for relationship in unverified
            ],
            "unverified_source_types": [
                relationship.source_type.value for relationship in unverified
            ],
        }
        error = RuleEvaluation(
            rule_id=rule_id,
            rule_name=rule_name,
            status=EvaluationStatus.UNKNOWN,
            reason_code=reason,
            missing_facts=missing,
            evidence=evidence,
            source_provenance=self._merge_provenance(
                self._rule_provenance(source),
                *(
                    self._relationship_provenance(relationship)
                    for relationship in unverified
                ),
            ),
        )
        return set(), [], error

    def _missing_for_rule(self, rule: FactComparisonRule) -> list[MissingFactRequest]:
        if rule.missing_fact is None:
            return []
        return [
            self.fact_resolver.missing_request(
                rule.fact_type, rule.missing_fact, rule.rule_id
            )
        ]

    @staticmethod
    def _holding_matches_filters(
        holding: AccountHoldingInterval,
        filters: dict[str, Any],
    ) -> bool:
        for key, expected in filters.items():
            if not hasattr(holding, key):
                return False
            actual = getattr(holding, key)
            if isinstance(expected, (list, tuple, set, frozenset)):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _window_evidence(start: date | None, end: date | None) -> dict[str, Any] | None:
        if start is None or end is None:
            return None
        return {"start": start.isoformat(), "end": end.isoformat(), "inclusive": True}

    @staticmethod
    def _rule_provenance(source: SourceReference | None) -> list[ProvenanceRecord]:
        if source is None:
            return []
        return [
            ProvenanceRecord(
                source_type="PRODUCT_DOCUMENT",
                reference=source.document,
                details=source.model_dump(mode="json", exclude_none=True),
            )
        ]

    @staticmethod
    def _fact_provenance(fact: UserFact) -> list[ProvenanceRecord]:
        records = [
            ProvenanceRecord(
                source_type=fact.source_type.value,
                reference=fact.fact_id,
                details={
                    "fact_type": fact.fact_type,
                    "semantic_type": fact.semantic_type.value,
                    "subject_person_id": fact.effective_subject_person_id,
                    "valid_from": fact.valid_from.isoformat() if fact.valid_from else None,
                    "valid_to": fact.valid_to.isoformat() if fact.valid_to else None,
                },
            )
        ]
        records.extend(
            ProvenanceRecord(
                source_type=fact.source_type.value,
                reference=provenance.reference,
                details={
                    "description": provenance.description,
                    **provenance.attributes,
                },
            )
            for provenance in fact.provenance
        )
        return records

    @staticmethod
    def _holding_provenance(
        holding: AccountHoldingInterval,
    ) -> list[ProvenanceRecord]:
        records = [
            ProvenanceRecord(
                source_type=holding.source_type.value,
                reference=holding.account_id,
                details={
                    "institution": holding.institution,
                    "product_type": holding.product_type,
                },
            )
        ]
        records.extend(
            ProvenanceRecord(
                source_type=holding.source_type.value,
                reference=provenance.reference,
                details={
                    "description": provenance.description,
                    **provenance.attributes,
                },
            )
            for provenance in holding.provenance
        )
        return records

    @staticmethod
    def _relationship_provenance(
        relationship: PersonRelationship,
    ) -> list[ProvenanceRecord]:
        records = [
            ProvenanceRecord(
                source_type=relationship.source_type.value,
                reference=relationship.source_reference or relationship.relationship_id,
                details={
                    "record_type": "PERSON_RELATIONSHIP",
                    "relationship_id": relationship.relationship_id,
                    "person_a": relationship.person_a,
                    "person_b": relationship.person_b,
                    "relationship_type": relationship.relationship_type,
                },
            )
        ]
        records.extend(
            ProvenanceRecord(
                source_type=relationship.source_type.value,
                reference=provenance.reference,
                details={
                    "description": provenance.description,
                    **provenance.attributes,
                },
            )
            for provenance in relationship.provenance
        )
        return records

    @staticmethod
    def _coverage_provenance(coverage: DataCoverage) -> list[ProvenanceRecord]:
        records = [
            ProvenanceRecord(
                source_type=coverage.source_type.value,
                reference=coverage.coverage_id,
                details={
                    "record_type": "DATA_COVERAGE",
                    "fact_domain": coverage.fact_domain,
                    "institution": coverage.institution,
                    "covered_from": coverage.covered_from.isoformat(),
                    "covered_to": coverage.covered_to.isoformat(),
                },
            )
        ]
        records.extend(
            ProvenanceRecord(
                source_type=coverage.source_type.value,
                reference=provenance.reference,
                details={
                    "description": provenance.description,
                    **provenance.attributes,
                },
            )
            for provenance in coverage.provenance
        )
        return records

    @staticmethod
    def _occurrence_provenance(
        occurrence: ScheduledOccurrence,
    ) -> list[ProvenanceRecord]:
        records = [
            ProvenanceRecord(
                source_type=occurrence.source_type.value,
                reference=occurrence.occurrence_id,
                details={
                    "record_type": "SCHEDULED_OCCURRENCE",
                    "schedule_id": occurrence.schedule_id,
                    "sequence_no": occurrence.sequence_no,
                    "method": occurrence.method.value,
                    "status": occurrence.status.value,
                },
            )
        ]
        records.extend(
            ProvenanceRecord(
                source_type=occurrence.source_type.value,
                reference=provenance.reference,
                details={
                    "description": provenance.description,
                    **provenance.attributes,
                },
            )
            for provenance in occurrence.provenance
        )
        return records

    @staticmethod
    def _recurring_payment_provenance(
        event: RecurringPaymentEvent,
    ) -> list[ProvenanceRecord]:
        records = [
            ProvenanceRecord(
                source_type=event.source_type.value,
                reference=event.event_id,
                details={
                    "record_type": "RECURRING_PAYMENT_EVENT",
                    "event_type": event.event_type,
                    "category": event.category.value,
                    "payment_method": event.payment_method.value,
                    "institution": event.institution,
                    "settlement_bank": event.settlement_bank,
                    "semantic_type": event.semantic_type.value,
                },
            )
        ]
        records.extend(
            ProvenanceRecord(
                source_type=event.source_type.value,
                reference=provenance.reference,
                details={
                    "description": provenance.description,
                    **provenance.attributes,
                },
            )
            for provenance in event.provenance
        )
        return records

    @staticmethod
    def _merge_provenance(
        *groups: list[ProvenanceRecord],
    ) -> list[ProvenanceRecord]:
        merged: list[ProvenanceRecord] = []
        seen: set[tuple[str, str | None, str]] = set()
        for group in groups:
            for record in group:
                key = (
                    record.source_type,
                    record.reference,
                    json.dumps(record.details, sort_keys=True, default=str),
                )
                if key not in seen:
                    merged.append(record)
                    seen.add(key)
        return merged

    @staticmethod
    def _dedupe_missing(children: list[RuleEvaluation]) -> list[MissingFactRequest]:
        merged: list[MissingFactRequest] = []
        seen: set[tuple[str, str]] = set()
        for child in children:
            for missing in child.missing_facts:
                key = (missing.fact_type, missing.requested_by_rule_id)
                if key not in seen:
                    merged.append(missing)
                    seen.add(key)
        return merged


class FinancialEligibilityEngine:
    """End-to-end deterministic product evaluation facade."""

    def evaluate_product(
        self,
        product: ProductDefinition,
        fact_store: UserFactStore,
        context: EvaluationContext,
        contribution_plan: MonthlyContributionPlan | ContributionSchedulePlan | None = None,
        *,
        audit: AuditSession | None = None,
        trust_mode: EvaluationTrustMode | None = None,
    ) -> ProductEvaluation:
        # ``trust_mode`` is accepted only as a deprecated source-compatible
        # argument. It does not change evaluation semantics or identity.
        evaluation_id = self._evaluation_id(
            product, fact_store, context, contribution_plan
        )
        if audit is not None:
            audit.bind_evaluation_id(evaluation_id)
        scope = audit.span("FINANCIAL_ELIGIBILITY_ENGINE") if audit is not None else nullcontext()
        with scope:
            if audit is not None:
                audit.emit(
                    "FINANCIAL_ELIGIBILITY_ENGINE",
                    AuditEventType.REQUEST_RECEIVED,
                    entity_refs={
                        "product_id": product.product_id,
                        "user_id": fact_store.user_id,
                    },
                    input_data={
                        "product": product,
                        "fact_store": fact_store,
                        "context": context,
                        "contribution_plan": contribution_plan,
                        "legacy_trust_mode_ignored": trust_mode,
                    },
                    payload={
                        "product_id": product.product_id,
                        "user_id": fact_store.user_id,
                        "as_of": context.as_of.isoformat(),
                        "subscription_date": context.subscription_date.isoformat(),
                        "legacy_trust_mode_ignored": (
                            trust_mode.value if trust_mode is not None else None
                        ),
                        "contribution_plan_included": contribution_plan is not None,
                    },
                )

            evaluator = RuleEvaluator(
                fact_store, context, audit=audit, trust_mode=trust_mode
            )
            eligibility = evaluator.evaluate(product.eligibility_rule)
            preferential_results = [
                evaluator.evaluate(preferential.rule)
                for preferential in product.preferential_rules
            ]
            guard_results = [
                evaluator.evaluate(rule) for rule in product.global_guards
            ]
            rates = RateEngine.calculate(product, preferential_results, guard_results)

            if audit is not None:
                for applied in rates.applied_rewards:
                    audit.emit(
                        "RATE_ENGINE",
                        AuditEventType.REWARD_APPLIED,
                        entity_refs={
                            "product_id": product.product_id,
                            "rule_id": applied.rule_id,
                        },
                        input_data=applied,
                        output_data={
                            "included_in_confirmed": applied.included_in_confirmed,
                            "included_in_realizable": applied.included_in_realizable,
                            "included_in_user_specific_conditional_upper": (
                                applied.included_in_user_specific_conditional_upper
                            ),
                        },
                        payload={
                            "status": applied.status.value,
                            "reward_pp": str(applied.reward_pp),
                            "verification_level": applied.verification_level.value,
                            "evidence_bucket": applied.evidence_bucket,
                            "included_in_confirmed": applied.included_in_confirmed,
                            "included_in_realizable": applied.included_in_realizable,
                            "included_in_user_specific_conditional_upper": (
                                applied.included_in_user_specific_conditional_upper
                            ),
                        },
                    )
                audit.emit(
                    "RATE_ENGINE",
                    AuditEventType.RATE_SUMMARY_CREATED,
                    entity_refs={"product_id": product.product_id},
                    input_data={
                        "preferential_results": preferential_results,
                        "guard_results": guard_results,
                    },
                    output_data=rates,
                    payload={
                        "advertised_max_rate": str(rates.advertised_max_rate),
                        "confirmed_rate": str(rates.confirmed_rate),
                        "realizable_rate": str(rates.realizable_rate),
                        "user_specific_conditional_upper_rate": str(
                            rates.user_specific_conditional_upper_rate
                        ),
                        "guard_status": rates.guard_status.value,
                    },
                )

            missing = self._dedupe_top_level_missing(
                [eligibility, *preferential_results, *guard_results]
            )
            interest_estimates = {}
            if contribution_plan is not None:
                estimator = (
                    InterestEngine.estimate_installment_savings
                    if isinstance(contribution_plan, MonthlyContributionPlan)
                    else InterestEngine.estimate_contribution_schedule
                )
                interest_estimates = {
                    "confirmed": estimator(
                        contribution_plan, rates.confirmed_rate
                    ),
                    "realizable": estimator(
                        contribution_plan, rates.realizable_rate
                    ),
                    "user_specific_conditional_upper": estimator(
                        contribution_plan, rates.user_specific_conditional_upper_rate
                    ),
                }

            all_results = [eligibility, *preferential_results, *guard_results]
            self_reported_rule_ids = sorted(
                result.rule_id
                for result in all_results
                if VerificationLevel.SELF_REPORTED in result.evidence_levels
            )
            user_intent_rule_ids = sorted(
                result.rule_id
                for result in all_results
                if VerificationLevel.USER_INTENT in result.evidence_levels
            )
            evaluation_levels = self._evaluation_evidence_levels(all_results)
            verification_level = combine_verification_levels(evaluation_levels)

            return ProductEvaluation(
                evaluation_id=evaluation_id,
                user_id=fact_store.user_id,
                product_id=product.product_id,
                product_name=product.name,
                as_of=context.as_of,
                subscription_date=context.subscription_date,
                eligibility_status=eligibility.status,
                eligibility=eligibility,
                preferential_rule_results=preferential_results,
                global_guard_results=guard_results,
                rates=rates,
                missing_facts=missing,
                interest_estimates=interest_estimates,
                is_provisional=bool(self_reported_rule_ids),
                verification_level=verification_level,
                evidence_levels=evaluation_levels,
                self_reported_rule_ids=self_reported_rule_ids,
                user_intent_rule_ids=user_intent_rule_ids,
                provisional_rule_ids=self_reported_rule_ids,
            )

    @staticmethod
    def _evaluation_evidence_levels(
        results: list[RuleEvaluation],
    ) -> list[VerificationLevel]:
        levels: list[VerificationLevel] = []
        for result in results:
            candidates = result.evidence_levels or (
                [result.verification_level]
                if result.verification_level != VerificationLevel.UNKNOWN
                else []
            )
            for level in candidates:
                if level not in {VerificationLevel.UNKNOWN, VerificationLevel.MIXED} and level not in levels:
                    levels.append(level)
        return levels

    @staticmethod
    def _dedupe_top_level_missing(
        results: list[RuleEvaluation],
    ) -> list[MissingFactRequest]:
        merged: list[MissingFactRequest] = []
        seen: set[tuple[str, str]] = set()
        for result in results:
            for missing in result.missing_facts:
                key = (missing.fact_type, missing.requested_by_rule_id)
                if key not in seen:
                    merged.append(missing)
                    seen.add(key)
        return merged

    @staticmethod
    def _evaluation_id(
        product: ProductDefinition,
        fact_store: UserFactStore,
        context: EvaluationContext,
        contribution_plan: MonthlyContributionPlan | ContributionSchedulePlan | None,
    ) -> str:
        payload = {
            "product": product.model_dump(mode="json"),
            "fact_store": fact_store.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "contribution_plan": (
                contribution_plan.model_dump(mode="json")
                if contribution_plan is not None
                else None
            ),
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"EVAL-{digest}"
