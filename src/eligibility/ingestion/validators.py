from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, ValidationError

from eligibility.audit import AuditEventType, AuditSession
from eligibility.ingestion.knowledge_draft import ProductKnowledgeDraft, RuleDraft
from eligibility.schema.enums import (
    AchievementMode,
    EvaluationPhase,
    RulePurpose,
)
from eligibility.schema.rule import RULE_NODE_ADAPTER


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: ValidationSeverity
    code: str
    message: str
    rule_id: str | None = None
    path: str | None = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    issues: list[ValidationIssue]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == ValidationSeverity.WARNING]


class ProductKnowledgeSchemaValidator:
    @staticmethod
    def validate(raw: Any) -> tuple[ProductKnowledgeDraft | None, ValidationReport]:
        try:
            draft = ProductKnowledgeDraft.model_validate(raw)
        except ValidationError as exc:
            issues = [
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="SCHEMA_VALIDATION_ERROR",
                    message=error["msg"],
                    path=".".join(str(item) for item in error["loc"]),
                )
                for error in exc.errors()
            ]
            return None, ValidationReport(valid=False, issues=issues)
        return draft, ValidationReport(valid=True, issues=[])


_ALLOWED_DSL_OPERATORS = {
    "AND",
    "OR",
    "NOT",
    "FACT_COMPARE",
    "DERIVED_COMPARE",
    "ENTITY_COUNT",
    "EXISTS",
    "NOT_EXISTS",
    "COUNT_DISTINCT_PERIODS",
    "COUNT_DISTINCT_MONTHS",
    "COUNT_CONSECUTIVE",
}
_INSTITUTION_MARKERS = (
    "SHINHAN",
    "HANA",
    "IBK",
    "KAKAO",
    "TOSS",
    "KB_",
    "SUPERSOL",
    "SALARY_ENVELOPE",
)


class SemanticValidator:
    def __init__(self, audit: AuditSession | None = None) -> None:
        self.audit = audit

    def validate(self, draft: ProductKnowledgeDraft) -> ValidationReport:
        issues: list[ValidationIssue] = []
        required_fact_types = {fact.fact_type for fact in draft.required_facts}
        rule_ids = {rule.rule_id for rule in draft.rules}

        if not draft.rules:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="EMPTY_DRAFT",
                    message="ProductKnowledgeDraft contains no rules",
                )
            )

        for rule in draft.rules:
            issues.extend(self._validate_rule(rule, required_fact_types))

        for fact in draft.required_facts:
            missing_rule_ids = set(fact.used_by_rule_ids) - rule_ids
            if missing_rule_ids:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="REQUIRED_FACT_UNKNOWN_RULE",
                        message=(
                            f"Required fact {fact.fact_type} refers to unknown rule IDs: "
                            f"{sorted(missing_rule_ids)}"
                        ),
                    )
                )

        for service in draft.service_references:
            if not service.provenance:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="SERVICE_REFERENCE_WITHOUT_PROVENANCE",
                        message=(
                            f"Service reference {service.service_name}/{service.concept_name} "
                            "has no provenance"
                        ),
                    )
                )
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="EXTERNAL_SERVICE_DEPENDENCY",
                    message=(
                        f"Institution service definition required: "
                        f"{service.service_name}/{service.concept_name}"
                    ),
                )
            )

        report = ValidationReport(
            valid=not any(
                issue.severity == ValidationSeverity.ERROR for issue in issues
            ),
            issues=issues,
        )
        if self.audit is not None:
            self.audit.emit(
                "SEMANTIC_VALIDATOR",
                AuditEventType.SEMANTIC_VALIDATION_COMPLETED,
                entity_refs={"document_id": draft.document_id},
                input_data=draft,
                output_data=report,
                payload={
                    "valid": report.valid,
                    "error_count": len(report.errors),
                    "warning_count": len(report.warnings),
                    "issue_codes": [issue.code for issue in report.issues],
                },
            )
        return report

    def _validate_rule(
        self,
        rule: RuleDraft,
        required_fact_types: set[str],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not rule.ast:
            return [
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="EMPTY_RULE",
                    message="Rule AST is empty",
                    rule_id=rule.rule_id,
                )
            ]
        if not rule.provenance:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="RULE_WITHOUT_PROVENANCE",
                    message="Rule has no page/section provenance",
                    rule_id=rule.rule_id,
                )
            )

        operator_values = list(self._operator_values(rule.ast))
        for operator in operator_values:
            upper = operator.upper()
            if upper not in _ALLOWED_DSL_OPERATORS:
                code = (
                    "SERVICE_SPECIFIC_OPERATOR_USAGE"
                    if any(marker in upper for marker in _INSTITUTION_MARKERS)
                    else "UNKNOWN_DSL_OPERATOR"
                )
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code=code,
                        message=f"Unsupported DSL operator: {operator}",
                        rule_id=rule.rule_id,
                    )
                )

        try:
            parsed = RULE_NODE_ADAPTER.validate_python(rule.ast)
        except ValidationError as exc:
            issues.extend(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code=self._classify_ast_error(error),
                    message=error["msg"],
                    rule_id=rule.rule_id,
                    path=".".join(str(item) for item in error["loc"]),
                )
                for error in exc.errors()
            )
            parsed = None

        if parsed is not None:
            if parsed.rule_id != rule.rule_id:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="RULE_IDENTITY_MISMATCH",
                        message=(
                            f"RuleDraft.rule_id={rule.rule_id} does not match "
                            f"RuleAST.rule_id={parsed.rule_id}"
                        ),
                        rule_id=rule.rule_id,
                        path="ast.rule_id",
                    )
                )
            if parsed.name != rule.name:
                # v0.3.1 uses an error rather than a warning: user traces, audit
                # events, and RequiredFact links must name the same executable rule.
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="RULE_NAME_IDENTITY_MISMATCH",
                        message=(
                            f"RuleDraft.name={rule.name!r} does not match "
                            f"RuleAST.name={parsed.name!r}"
                        ),
                        rule_id=rule.rule_id,
                        path="ast.name",
                    )
                )
            issues.extend(self._validate_provenance_identity(rule, parsed.source))

        if rule.purpose == RulePurpose.PREFERENTIAL_RATE and rule.reward is None:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_REWARD",
                    message="Preferential rate rule is missing its reward",
                    rule_id=rule.rule_id,
                )
            )
        if rule.purpose != RulePurpose.PREFERENTIAL_RATE and rule.reward is not None:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="REWARD_ON_NON_PREFERENTIAL_RULE",
                    message="Reward is attached to a non-preferential rule",
                    rule_id=rule.rule_id,
                )
            )

        referenced_facts = set(self._fact_references(rule.ast))
        missing_facts = referenced_facts - required_fact_types
        for fact_type in sorted(missing_facts):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="MISSING_FACT_REFERENCE",
                    message=(
                        f"Rule references {fact_type}, but ProductKnowledgeDraft.required_facts "
                        "does not declare it"
                    ),
                    rule_id=rule.rule_id,
                )
            )

        if rule.achievement_mode is not None:
            future = rule.ast.get("future_achievement")
            if future is None and rule.evaluation_phase in {
                EvaluationPhase.POST_SUBSCRIPTION,
                EvaluationPhase.BOTH,
            }:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="INVALID_ACHIEVEMENT_MODE",
                        message="Post-subscription achievement mode requires future_achievement",
                        rule_id=rule.rule_id,
                    )
                )
            elif isinstance(future, dict):
                ast_mode = future.get("achievement_mode", AchievementMode.ACCUMULATIVE.value)
                if ast_mode != rule.achievement_mode.value:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            code="INVALID_ACHIEVEMENT_MODE",
                            message=(
                                f"Rule draft mode {rule.achievement_mode.value} does not match "
                                f"AST mode {ast_mode}"
                            ),
                            rule_id=rule.rule_id,
                        )
                    )

        if "NOT" in operator_values or "NOT_EXISTS" in operator_values:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="ABSENCE_RULE_REVIEW_REQUIRED",
                    message="NOT/absence semantics require coverage and human review",
                    rule_id=rule.rule_id,
                )
            )
        if self._contains_nested_or(rule.ast):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="NESTED_OR_REVIEW_REQUIRED",
                    message="Nested OR scope requires human review",
                    rule_id=rule.rule_id,
                )
            )
        _ = parsed
        return self._dedupe(issues)

    @staticmethod
    def _validate_provenance_identity(
        rule: RuleDraft, ast_source: Any | None
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not rule.provenance:
            return issues
        keys = {
            (
                item.document_id,
                item.document_name,
                item.page,
                item.section,
                item.source_url,
                item.source_text,
            )
            for item in rule.provenance
        }
        if len(keys) > 1:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="CONFLICTING_RULE_PROVENANCE",
                    message=(
                        "A single executable RuleNode cannot silently collapse "
                        "multiple conflicting draft provenance locations"
                    ),
                    rule_id=rule.rule_id,
                    path="provenance",
                )
            )
            return issues
        if ast_source is None:
            return issues
        draft_source = rule.provenance[0]
        comparisons = {
            "document": (ast_source.document, draft_source.document_name),
            "document_id": (ast_source.document_id, draft_source.document_id),
            "page": (ast_source.page, draft_source.page),
            "section": (ast_source.section, draft_source.section),
            "source_url": (ast_source.source_url, draft_source.source_url),
            "source_text": (ast_source.source_text, draft_source.source_text),
        }
        conflicts = [
            field
            for field, (ast_value, draft_value) in comparisons.items()
            if ast_value is not None
            and draft_value is not None
            and ast_value != draft_value
        ]
        if conflicts:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="RULE_PROVENANCE_CONFLICT",
                    message=(
                        "RuleDraft provenance conflicts with AST source fields: "
                        f"{conflicts}"
                    ),
                    rule_id=rule.rule_id,
                    path="ast.source",
                )
            )
        return issues

    @staticmethod
    def _classify_ast_error(error: dict[str, Any]) -> str:
        path = ".".join(str(item) for item in error.get("loc", ())).lower()
        message = str(error.get("msg", "")).lower()
        if "date" in path or "window" in path or "expression" in path:
            return "INVALID_TIME_EXPRESSION"
        if "reward" in path:
            return "INVALID_REWARD"
        if "children" in path and "at least one" in message:
            return "EMPTY_RULE"
        return "INVALID_RULE_AST"

    @classmethod
    def _operator_values(cls, value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            operator = value.get("type")
            # Rule nodes always carry rule_id/name. Date expressions and other
            # typed helper objects also use a `type` field and must not be
            # mistaken for DSL operators.
            if (
                isinstance(operator, str)
                and "rule_id" in value
                and "name" in value
            ):
                yield operator
            for item in value.values():
                yield from cls._operator_values(item)
        elif isinstance(value, list):
            for item in value:
                yield from cls._operator_values(item)

    @classmethod
    def _fact_references(cls, value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            for key in ("fact_type", "input_fact_type", "intent_fact_type"):
                fact_type = value.get(key)
                if isinstance(fact_type, str):
                    yield fact_type
            for item in value.values():
                yield from cls._fact_references(item)
        elif isinstance(value, list):
            for item in value:
                yield from cls._fact_references(item)

    @classmethod
    def _contains_nested_or(cls, value: Any, inside_or: bool = False) -> bool:
        if isinstance(value, dict):
            current_or = value.get("type") == "OR"
            if inside_or and current_or:
                return True
            return any(
                cls._contains_nested_or(item, inside_or or current_or)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(cls._contains_nested_or(item, inside_or) for item in value)
        return False

    @staticmethod
    def _dedupe(issues: list[ValidationIssue]) -> list[ValidationIssue]:
        result: list[ValidationIssue] = []
        seen: set[tuple[str, str | None, str | None]] = set()
        for issue in issues:
            key = (issue.code, issue.rule_id, issue.path)
            if key not in seen:
                result.append(issue)
                seen.add(key)
        return result
