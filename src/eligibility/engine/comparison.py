from __future__ import annotations

from collections.abc import Collection
from typing import Any

from eligibility.schema.enums import ComparisonOperator


class ComparisonError(ValueError):
    """Raised when a comparison cannot be evaluated deterministically."""


def compare_values(actual: Any, operator: ComparisonOperator, expected: Any) -> bool:
    """Evaluate the comparison subset used by Rule DSL v0.1."""
    if operator == ComparisonOperator.EQ:
        return actual == expected
    if operator == ComparisonOperator.NEQ:
        return actual != expected
    if operator == ComparisonOperator.GT:
        return actual > expected
    if operator == ComparisonOperator.GTE:
        return actual >= expected
    if operator == ComparisonOperator.LT:
        return actual < expected
    if operator == ComparisonOperator.LTE:
        return actual <= expected
    if operator == ComparisonOperator.IN:
        if not isinstance(expected, Collection) or isinstance(expected, (str, bytes)):
            raise ComparisonError("IN expects a non-string collection as expected value")
        return actual in expected
    if operator == ComparisonOperator.NOT_IN:
        if not isinstance(expected, Collection) or isinstance(expected, (str, bytes)):
            raise ComparisonError("NOT_IN expects a non-string collection as expected value")
        return actual not in expected
    if operator == ComparisonOperator.BETWEEN_INCLUSIVE:
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            raise ComparisonError("BETWEEN_INCLUSIVE expects [lower, upper]")
        lower, upper = expected
        return lower <= actual <= upper
    raise ComparisonError(f"Unsupported comparison operator: {operator}")


def get_value_by_path(value: Any, path: str | None) -> Any:
    """Read a dotted path from dictionaries or object attributes."""
    if path is None or path == "":
        return value

    current = value
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                raise KeyError(path)
            current = current[segment]
        else:
            if not hasattr(current, segment):
                raise KeyError(path)
            current = getattr(current, segment)
    return current
