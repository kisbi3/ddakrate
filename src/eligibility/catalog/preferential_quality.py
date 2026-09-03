"""Quality checks for normalized preferential-rate data.

The checks in this module intentionally operate on the published JSON shape.  They
can therefore be used by publishers before a product is committed as well as by a
catalog-wide audit without constructing runtime ``ProductDefinition`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


PREFERENTIAL_GAP_PATH = "return_policy.rate_entries[PREFERENTIAL]"


@dataclass(frozen=True)
class PreferentialQualityIssue:
    code: str
    path: str
    message: str


class PreferentialQualityError(ValueError):
    """Raised when a product fails a preferential-rate publication gate."""

    def __init__(self, product_code: str, issues: Iterable[PreferentialQualityIssue]):
        self.product_code = product_code
        self.issues = tuple(issues)
        details = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(f"Preferential-rate quality gate failed for {product_code}: {details}")


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _rate_value(entry: Mapping[str, Any], unit: str) -> Decimal | None:
    calculation = entry.get("calculation") or {}
    if calculation.get("unit") != unit:
        return None
    return _decimal(calculation.get("value"))


def _advertised_maximum(policy: Mapping[str, Any]) -> Decimal | None:
    advertised = policy.get("advertised_max_rate") or {}
    if advertised.get("unit") == "PERCENT":
        value = _decimal(advertised.get("value"))
        if value is not None:
            return value

    values = [
        _rate_value(entry, "PERCENT")
        for entry in policy.get("rate_entries", [])
        if entry.get("role") == "ADVERTISED_MAXIMUM"
    ]
    resolved = [value for value in values if value is not None]
    return max(resolved) if resolved else None


def _has_preferential_gap(product: Mapping[str, Any]) -> bool:
    gaps = (product.get("version_metadata") or {}).get("data_gaps") or []
    for gap in gaps:
        if isinstance(gap, str):
            text = gap
            reason = gap
        elif isinstance(gap, Mapping):
            text = str(gap.get("path", ""))
            reason = str(gap.get("reason", ""))
        else:
            continue
        normalized = text.lower()
        if reason.strip() and any(
            marker in normalized
            for marker in ("preferential", "standard_conditions", "custom_bindings")
        ):
            return True
    return False


def _condition_predicates(condition: Any) -> Iterable[Mapping[str, Any]]:
    """Yield predicate nodes from either the canonical wrapper or a nested AST."""

    if not isinstance(condition, Mapping):
        return
    predicate = condition.get("predicate")
    if isinstance(predicate, Mapping):
        yield predicate
    for key in ("operands", "conditions"):
        children = condition.get(key) or []
        if isinstance(children, list):
            for child in children:
                yield from _condition_predicates(child)
    for key in ("condition", "operand"):
        child = condition.get(key)
        if isinstance(child, Mapping):
            yield from _condition_predicates(child)


def inspect_preferential_rate_quality(
    product: Mapping[str, Any],
) -> tuple[PreferentialQualityIssue, ...]:
    """Return deterministic publication issues for one normalized product.

    Arithmetic is checked only when the JSON explicitly describes one unscoped
    base rate and unscoped additive preferential rates under ``mode=SUM``.  This
    avoids guessing about tiered, mutually exclusive, or term-dependent rates.
    """

    issues: list[PreferentialQualityIssue] = []
    policy = product.get("return_policy") or {}
    canonical_policy = policy.get("preferential_policy") or {}
    fact_types = {
        str(row.get("fact_key")): str(row.get("type"))
        for row in canonical_policy.get("fact_definitions") or []
        if isinstance(row, Mapping) and row.get("fact_key") and row.get("type")
    }
    for rule in canonical_policy.get("rules") or []:
        if not isinstance(rule, Mapping):
            continue
        rule_id = str(rule.get("rule_id") or "<unknown>")
        for predicate in _condition_predicates(rule.get("condition") or {}):
            fact_key = str(predicate.get("fact_key") or "")
            fact_type = fact_types.get(fact_key)
            expected = predicate.get("expected_value")
            if (
                fact_type in {"NUMBER", "INTEGER"}
                and predicate.get("operator") in {"EQUALS", "NOT_EQUALS"}
                and isinstance(expected, bool)
            ):
                issues.append(
                    PreferentialQualityIssue(
                        code="CANONICAL_PREDICATE_TYPE_MISMATCH",
                        path=f"return_policy.preferential_policy.rules[{rule_id}].condition",
                        message=(
                            f"{fact_key}는 {fact_type} fact인데 boolean 값 "
                            f"{expected!r}와 비교하고 있습니다."
                        ),
                    )
                )
    entries = policy.get("rate_entries") or []
    entries_by_id = {
        str(entry.get("rate_id")): entry for entry in entries if entry.get("rate_id")
    }
    preferential_ids = {
        rate_id
        for rate_id, entry in entries_by_id.items()
        if entry.get("role") == "PREFERENTIAL"
    }
    conditions = [
        *list(product.get("standard_conditions") or []),
        *list(product.get("custom_bindings") or []),
    ]
    referenced_preferential_ids: set[str] = set()

    for condition in conditions:
        condition_id = str(
            condition.get("condition_id") or condition.get("binding_id") or "<unknown>"
        )
        reward_refs = condition.get("reward_refs") or []
        if condition.get("purpose") == "PREFERENTIAL_RETURN" and not reward_refs:
            issues.append(
                PreferentialQualityIssue(
                    code="PREFERENTIAL_CONDITION_WITHOUT_REWARD",
                    path=f"conditions[{condition_id}].reward_refs",
                    message="우대 조건에 reward_refs가 없습니다.",
                )
            )
        for reward_ref in reward_refs:
            reward_ref = str(reward_ref)
            target = entries_by_id.get(reward_ref)
            if target is None:
                issues.append(
                    PreferentialQualityIssue(
                        code="UNRESOLVED_REWARD_REF",
                        path=f"conditions[{condition_id}].reward_refs",
                        message=f"{reward_ref}가 rate_entries에 존재하지 않습니다.",
                    )
                )
            elif condition.get("purpose") == "PREFERENTIAL_RETURN" and target.get(
                "role"
            ) != "PREFERENTIAL":
                issues.append(
                    PreferentialQualityIssue(
                        code="PREFERENTIAL_REWARD_ROLE_MISMATCH",
                        path=f"conditions[{condition_id}].reward_refs",
                        message=f"{reward_ref}의 role이 PREFERENTIAL이 아닙니다.",
                    )
                )
            elif target.get("role") == "PREFERENTIAL":
                referenced_preferential_ids.add(reward_ref)

    base_values = [
        _rate_value(entry, "PERCENT")
        for entry in entries
        if entry.get("role") == "BASE"
    ]
    resolved_base_values = [value for value in base_values if value is not None]
    maximum = _advertised_maximum(policy)
    has_rate_uplift = bool(
        maximum is not None
        and resolved_base_values
        and maximum > max(resolved_base_values)
    )
    if (
        has_rate_uplift
        and not referenced_preferential_ids
        and not _has_preferential_gap(product)
    ):
        issues.append(
            PreferentialQualityIssue(
                code="UNEXPLAINED_ADVERTISED_RATE_UPLIFT",
                path=PREFERENTIAL_GAP_PATH,
                message=(
                    "최고금리가 기본금리보다 높지만 조건과 연결된 PREFERENTIAL "
                    "금리도, 이를 설명하는 data gap도 없습니다."
                ),
            )
        )

    preferential_entries = [
        entries_by_id[rate_id] for rate_id in sorted(preferential_ids)
    ]
    application = policy.get("preferential_application") or {}
    can_check_sum = bool(
        maximum is not None
        and len([entry for entry in entries if entry.get("role") == "BASE"]) == 1
        and preferential_entries
        and preferential_ids == referenced_preferential_ids
        and application.get("mode") == "SUM"
        and all(
            not entry.get("applies_to")
            for entry in [
                *[entry for entry in entries if entry.get("role") == "BASE"],
                *preferential_entries,
            ]
        )
    )
    if can_check_sum:
        base = _rate_value(
            next(entry for entry in entries if entry.get("role") == "BASE"), "PERCENT"
        )
        additions = [_rate_value(entry, "PERCENTAGE_POINT") for entry in preferential_entries]
        if base is not None and all(value is not None for value in additions):
            preferential_total = sum(
                (value for value in additions if value is not None), Decimal("0")
            )
            cap = _decimal(application.get("cap_value"))
            if application.get("cap_unit") not in (None, "PERCENTAGE_POINT"):
                cap = None
            if cap is not None:
                preferential_total = min(preferential_total, cap)
            expected = base + preferential_total
            if maximum != expected:
                issues.append(
                    PreferentialQualityIssue(
                        code="ADVERTISED_MAXIMUM_ARITHMETIC_MISMATCH",
                        path="return_policy.advertised_max_rate",
                        message=(
                            f"최고금리 {maximum}%가 검증 가능한 합계 "
                            f"{expected}%와 다릅니다."
                        ),
                    )
                )

    return tuple(issues)


def assert_preferential_rate_quality(product: Mapping[str, Any]) -> None:
    """Raise when ``product`` is unsafe to publish under the quality gate."""

    issues = inspect_preferential_rate_quality(product)
    if issues:
        raise PreferentialQualityError(
            str(product.get("product_code") or "<unknown>"), issues
        )
