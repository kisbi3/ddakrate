"""Compile published condition data into a safe, session-queryable index.

This module is deliberately pure.  It accepts either a normalized JSON mapping
or a runtime ``ProductDefinition`` and returns typed requirements plus coverage
metrics; it does not write catalog files and it never promotes a heuristic
classification into a financial decision.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from eligibility.schema.condition_requirement import (
    ConditionRequirement,
    RequirementCompilationMetrics,
    RequirementCompilationResult,
)


COMPILER_VERSION = "requirement-compiler.v1"

# Only reviewed canonical user-variable families may be activated.  The list is
# intentionally conservative; adding a family is a review/release decision, not
# an LLM or heuristic side effect.
VERIFIED_VARIABLE_FAMILIES = frozenset(
    {
        "AGE_YEARS",
        "AUTO_TRANSFER",
        "CARD_MONTHLY_SPEND_LIMIT",
        "CARD_SETTLEMENT_ACCOUNT",
        "CUSTOMER_TYPE",
        "INCOME_CREDIT",
        "MARKETING_CONSENT",
        "NEW_CARD_ISSUANCE",
        "PRODUCT_HOLDING",
        "PRODUCT_HOLDING_INSTITUTIONS",
        "SALARY_TRANSFER",
        "SUBSCRIPTION_CHANNEL",
    }
)

KNOWN_VARIABLE_FAMILIES = VERIFIED_VARIABLE_FAMILIES | frozenset(
    {
        "ACCOUNT_BALANCE",
        "AGE",
        "AUTOMATIC_TRANSFER",
        "BENEFIT_OR_SCHOOL_CREDIT",
        "BILL_PAYMENT",
        "CARD_ACTIVITY",
        "CHANNEL_OR_ACCOUNT_FEATURE",
        "CONTRIBUTION_EVENT",
        "CONTRIBUTION_OR_TRANSFER",
        "CUSTOMER_STATUS",
        "DEMOGRAPHIC_OR_LIFE_EVENT",
        "MERCHANT_SETTLEMENT",
        "MATURITY_OR_RENEWAL",
        "OTHER_SOURCE_CONDITION",
        "PROMOTION_COUPON",
        "SERVICE_ENROLLMENT_OR_USE",
        "SUBSCRIPTION_OR_ACCOUNT_AMOUNT",
    }
)

_OPERATOR_MAP = {
    "EQUALS": "EQ",
    "EQ": "EQ",
    "NOT_EQUALS": "NEQ",
    "NEQ": "NEQ",
    "GREATER_THAN": "GT",
    "GT": "GT",
    "GREATER_THAN_OR_EQUAL": "GTE",
    "GREATER_THAN_OR_EQUALS": "GTE",
    "GTE": "GTE",
    "LESS_THAN": "LT",
    "LT": "LT",
    "LESS_THAN_OR_EQUAL": "LTE",
    "LESS_THAN_OR_EQUALS": "LTE",
    "LTE": "LTE",
    "IN": "IN",
    "NOT_IN": "NOT_IN",
    "BETWEEN_INCLUSIVE": "BETWEEN_INCLUSIVE",
}

_INFO_MARKERS = (
    "FEE",
    "FEE_WAIVER",
    "CHARGE",
    "PRIZE",
    "GIFT",
    "REWARD_EVENT",
    "SERVICE_BENEFIT",
    "BENEFIT_ONLY",
    "EVENT",
)


@dataclass(frozen=True)
class _SourceCondition:
    product_id: str
    condition_id: str
    payload: Mapping[str, Any]
    default_scope: str
    source_kind: str


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _product_mapping(product: Any) -> Mapping[str, Any]:
    """Preserve raw normalized data when a runtime product wraps it."""

    raw = getattr(getattr(product, "normalized", None), "raw_product", None)
    if isinstance(raw, Mapping) and raw:
        return raw
    return _as_mapping(product)


def _product_id(product: Any, raw: Mapping[str, Any]) -> str:
    return str(
        raw.get("product_code")
        or raw.get("product_id")
        or getattr(product, "product_id", "")
    )


def _institution_id(product: Any, raw: Mapping[str, Any]) -> str | None:
    value = raw.get("institution_id") or getattr(product, "institution_id", None)
    return str(value) if value else None


def _walk_predicates(condition: Any) -> list[Mapping[str, Any]]:
    if not isinstance(condition, Mapping):
        return []
    result: list[Mapping[str, Any]] = []
    predicate = condition.get("predicate")
    if isinstance(predicate, Mapping):
        result.append(predicate)
    for key in ("children", "operands", "conditions"):
        children = condition.get(key)
        if isinstance(children, list):
            for child in children:
                result.extend(_walk_predicates(child))
    for key in ("condition", "operand"):
        child = condition.get(key)
        if isinstance(child, Mapping):
            result.extend(_walk_predicates(child))
    return result


def _all_strings(value: Any, keys: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in keys and isinstance(child, list):
                found.extend(str(item) for item in child if item is not None)
            elif key in keys and child is not None and not isinstance(child, (dict, list)):
                found.append(str(child))
            else:
                found.extend(_all_strings(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(_all_strings(child, keys))
    return list(dict.fromkeys(found))


def _slug(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(".", "_").replace("-", "_").replace(" ", "_")
    return text.upper()


def _semantic_family(payload: Mapping[str, Any], predicates: list[Mapping[str, Any]]) -> tuple[str | None, bool]:
    """Return a canonical variable candidate and whether it was unmapped."""

    for key in ("variable_id", "semantic_domain"):
        value = _slug(payload.get(key))
        if value:
            return value, value not in KNOWN_VARIABLE_FAMILIES
    for key in ("action_id", "fact_type", "fact_key"):
            value = _slug(payload.get(key))
            if value:
                value = value.removeprefix("ACTION_")
                return value, value not in KNOWN_VARIABLE_FAMILIES
    for predicate in predicates:
        semantic = _slug(predicate.get("semantic_domain"))
        if semantic:
            return semantic, semantic not in KNOWN_VARIABLE_FAMILIES
        for key in ("variable_id", "action_id", "fact_type", "fact_key"):
            value = _slug(predicate.get(key))
            if value:
                value = value.removeprefix("ACTION_")
                return value, value not in KNOWN_VARIABLE_FAMILIES
    return None, True


def _scope(payload: Mapping[str, Any], default_scope: str) -> str:
    explicit = str(
        payload.get("scope")
        or payload.get("condition_scope")
        or payload.get("purpose")
        or ""
    ).upper()
    if explicit in {"ELIGIBILITY", "ELIGIBILITY_GATE", "JOIN_ELIGIBILITY"}:
        return "ELIGIBILITY"
    if explicit in {"INFORMATION_ONLY", "SUPPORTING", "FEE", "BENEFIT"}:
        return "INFORMATION_ONLY"
    if explicit in {"PREFERENTIAL_RETURN", "RATE_BENEFIT", "PREFERENTIAL_RATE"}:
        return "RATE_BENEFIT"
    text = " ".join(
        str(payload.get(key, "")) for key in ("condition_type", "benefit_type", "type")
    ).upper()
    if any(marker in text for marker in _INFO_MARKERS):
        return "INFORMATION_ONLY"
    return default_scope


def _coverage(payload: Mapping[str, Any], product_gaps: list[Any]) -> str:
    explicit = str(
        payload.get("coverage_status")
        or payload.get("data_status")
        or payload.get("coverage")
        or ""
    ).upper()
    if explicit in {"UNAVAILABLE", "PARTIAL", "COMPLETE"}:
        return explicit
    for gap in product_gaps:
        text = str(gap if not isinstance(gap, Mapping) else {**gap}).upper()
        if any(marker in text for marker in ("UNAVAILABLE", "NOT_AVAILABLE", "MISSING")):
            return "UNAVAILABLE"
        if any(marker in text for marker in ("PARTIAL", "GAP", "INCOMPLETE")):
            return "PARTIAL"
    # No gap is positive evidence that the source is complete; it does not
    # imply that the rule is reviewed or safe to activate.
    return "COMPLETE"


def _review_status(payload: Mapping[str, Any], variable_id: str | None) -> str:
    explicit = str(
        payload.get("review_status")
        or payload.get("verification_status")
        or payload.get("activation_status")
        or payload.get("structuring_status")
        or ""
    ).upper()
    if explicit == "VERIFIED" and variable_id in VERIFIED_VARIABLE_FAMILIES:
        return "VERIFIED"
    # SUPPORTED, AI_STRUCTURED, heuristic, and absent statuses are deliberately
    # not equivalent to human verification.
    if explicit == "REJECTED":
        return "REJECTED"
    return "REVIEW_REQUIRED"


def _relationship(condition: Mapping[str, Any], predicates: list[Mapping[str, Any]]) -> dict[str, Any]:
    op = str(condition.get("op") or condition.get("operator") or "NONE").upper()
    variables = [
        _slug(predicate.get("variable_id") or predicate.get("action_id") or predicate.get("fact_type") or predicate.get("fact_key"))
        for predicate in predicates
    ]
    return {
        "logic": op,
        "variable_ids": [item for item in variables if item],
    }


def _threshold(condition: Mapping[str, Any], predicates: list[Mapping[str, Any]]) -> dict[str, Any]:
    if len(predicates) == 1:
        predicate = predicates[0]
        return {
            key: predicate[key]
            for key in ("expected_value", "unit", "min_value", "max_value", "min_inclusive", "max_inclusive")
            if key in predicate
        }
    return {"predicates": [dict(predicate) for predicate in predicates]}


def _rate_effect(payload: Mapping[str, Any]) -> dict[str, Any]:
    reward = payload.get("reward")
    if not isinstance(reward, Mapping):
        return {}
    result = {key: reward[key] for key in ("value", "unit", "kind") if key in reward}
    if "percentage_point" in reward:
        result["percentage_point"] = reward["percentage_point"]
    return result


def _source_conditions(product: Any) -> list[_SourceCondition]:
    raw = _product_mapping(product)
    product_id = _product_id(product, raw)
    normalized = getattr(product, "normalized", None)
    if normalized is not None:
        nr = _as_mapping(normalized)
        if nr:
            raw = nr.get("raw_product") if isinstance(nr.get("raw_product"), Mapping) else raw
    return_policy = raw.get("return_policy") or {}
    preferential = return_policy.get("preferential_policy") or {}
    rows: list[_SourceCondition] = []
    for rule in preferential.get("rules") or []:
        if isinstance(rule, Mapping):
            identifier = str(rule.get("rule_id") or f"rule-{len(rows) + 1}")
            rows.append(_SourceCondition(product_id, identifier, rule, "RATE_BENEFIT", "preferential_rule"))

    eligibility = raw.get("eligibility_policy") or {}
    if eligibility and str(eligibility.get("mode", "")).upper() != "UNRESTRICTED":
        rows.append(_SourceCondition(product_id, f"{product_id}:eligibility", eligibility, "ELIGIBILITY", "eligibility_policy"))

    for key in ("standard_conditions", "custom_bindings"):
        for index, condition in enumerate(raw.get(key) or [], start=1):
            if isinstance(condition, Mapping):
                identifier = str(condition.get("condition_id") or condition.get("binding_id") or f"{key}-{index}")
                rows.append(_SourceCondition(product_id, identifier, condition, "INFORMATION_ONLY", key))

    # Runtime legacy products may not carry raw normalized rules. Their typed
    # MissingFactSpec still provides a safe source-preserving fallback.
    if not rows:
        for item in getattr(product, "preferential_rules", []) or []:
            rule = getattr(item, "rule", None)
            missing = getattr(rule, "missing_fact", None)
            payload = _as_mapping(missing)
            payload = {
                **payload,
                "rule_id": getattr(rule, "rule_id", None),
                "fact_type": getattr(rule, "fact_type", None),
                "action_id": getattr(missing, "action_id", None),
                "reward": {"value": getattr(getattr(item, "reward", None), "value", None), "unit": "PERCENTAGE_POINT"},
                "source_ref_ids": _all_strings(_as_mapping(rule), {"source_ref_ids", "evidence_ref_ids"}),
                "review_status": "REVIEW_REQUIRED",
            }
            rows.append(_SourceCondition(product_id, str(getattr(rule, "rule_id", f"rule-{len(rows) + 1}")), payload, "RATE_BENEFIT", "typed_rule"))
    return rows


def _compile_one(product: Any, source: _SourceCondition) -> tuple[ConditionRequirement, bool]:
    payload = source.payload
    condition = payload.get("condition") if isinstance(payload.get("condition"), Mapping) else payload
    predicates = _walk_predicates(condition)
    variable_id, unmapped = _semantic_family(payload, predicates)
    if source.source_kind == "eligibility_policy":
        variable_id, unmapped = "ELIGIBILITY_POLICY", False
    if variable_id is None:
        variable_id = f"UNMAPPED:{source.condition_id}"
    scope = _scope(payload, source.default_scope)
    raw = _product_mapping(product)
    gaps = ((raw.get("version_metadata") or {}).get("data_gaps") or [])
    refs = _all_strings(payload, {"source_ref_ids", "evidence_ref_ids"})
    relationship = _relationship(condition, predicates)
    relationship["source_kind"] = source.source_kind
    operator = condition.get("op") or condition.get("operator")
    if operator is None and len(predicates) == 1:
        operator = predicates[0].get("operator")
    requirement = ConditionRequirement(
        requirement_id=f"req:{source.product_id}:{source.condition_id}",
        product_id=source.product_id,
        scope=scope,
        variable_id=variable_id,
        subject_scope="USER",
        institution_id=_institution_id(product, raw),
        operator=str(operator or "STRUCTURED").upper(),
        threshold=_threshold(condition, predicates),
        rate_effect=_rate_effect(payload),
        relationship=relationship,
        evidence_refs=refs,
        coverage_status=_coverage(payload, gaps),
        review_status=_review_status(payload, variable_id),
    )
    return requirement, unmapped


class RequirementCompiler:
    """Compile conditions without mutating products or publishing results."""

    compiler_version = COMPILER_VERSION
    verified_variable_families = VERIFIED_VARIABLE_FAMILIES

    def compile_product(self, product: Any) -> list[ConditionRequirement]:
        return [
            requirement
            for requirement, _ in (
                _compile_one(product, source) for source in _source_conditions(product)
            )
        ]

    def compile(self, products: Iterable[Any]) -> RequirementCompilationResult:
        requirements: list[ConditionRequirement] = []
        unmapped_ids: list[str] = []
        source_count = 0
        product_count = 0
        for product in products:
            product_count += 1
            sources = _source_conditions(product)
            source_count += len(sources)
            for source in sources:
                requirement, unmapped = _compile_one(product, source)
                requirements.append(requirement)
                if unmapped:
                    unmapped_ids.append(requirement.requirement_id)

        scope_counts = Counter(item.scope for item in requirements)
        variable_counts = Counter(item.variable_id for item in requirements)
        metrics = RequirementCompilationMetrics(
            product_count=product_count,
            source_condition_count=source_count,
            compiled_requirement_count=len(requirements),
            verified_count=sum(item.review_status == "VERIFIED" for item in requirements),
            review_required_count=sum(item.review_status == "REVIEW_REQUIRED" for item in requirements),
            rejected_count=sum(item.review_status == "REJECTED" for item in requirements),
            complete_count=sum(item.coverage_status == "COMPLETE" for item in requirements),
            partial_count=sum(item.coverage_status == "PARTIAL" for item in requirements),
            unavailable_count=sum(item.coverage_status == "UNAVAILABLE" for item in requirements),
            unmapped_count=len(unmapped_ids),
            unmapped_requirement_ids=sorted(unmapped_ids),
            scope_counts=dict(sorted(scope_counts.items())),
            variable_counts=dict(sorted(variable_counts.items())),
        )
        return RequirementCompilationResult(
            compiler_version=self.compiler_version,
            requirements=requirements,
            metrics=metrics,
        )


def compile_requirements(products: Iterable[Any]) -> RequirementCompilationResult:
    """Convenience function for audits and tests."""

    return RequirementCompiler().compile(products)


__all__ = [
    "COMPILER_VERSION",
    "VERIFIED_VARIABLE_FAMILIES",
    "KNOWN_VARIABLE_FAMILIES",
    "RequirementCompiler",
    "compile_requirements",
]
