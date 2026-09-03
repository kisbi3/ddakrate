"""Small read-only query contracts for the LLM boundary.

These helpers deliberately expose a projection-oriented interface rather than
SQL.  The caller can filter and order known catalog fields, but cannot submit a
query string, mutate a product/session, or obtain arbitrary raw storage data.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from enum import Enum
from functools import cmp_to_key
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from eligibility.schema.condition_requirement import ConditionRequirement, UserConditionState


PRODUCT_FIELDS = frozenset(
    {
        "product_id",
        "institution_id",
        "institution_name",
        "name",
        "product_type",
        "product_subtype",
        "institution_sector",
        "sale_status",
        "base_rate",
        "advertised_max_rate",
        "return_kind",
        "protection_status",
        "interest_payment_method",
    }
)
FILTER_FIELDS = frozenset(
    {
        "product_id",
        "institution_id",
        "institution_name",
        "name",
        "product_type",
        "product_subtype",
        "institution_sector",
        "sale_status",
        "base_rate",
        "advertised_max_rate",
        "return_kind",
        "protection_status",
    }
)
ORDER_FIELDS = PRODUCT_FIELDS
SUPPORTED_OPERATORS = frozenset({"EQ", "NEQ", "IN", "NOT_IN", "CONTAINS", "GT", "GTE", "LT", "LTE"})
STRING_OPERATORS = frozenset({"EQ", "NEQ", "IN", "NOT_IN", "CONTAINS"})
NUMERIC_OPERATORS = frozenset({"EQ", "NEQ", "GT", "GTE", "LT", "LTE"})
NUMERIC_FIELDS = frozenset({"base_rate", "advertised_max_rate"})
DETAIL_FIELDS = PRODUCT_FIELDS | {
    "term_policy",
    "cash_flow_policy",
    "return_policy",
    "fee_policy",
    "tax_policy",
    "liquidity_policy",
    "protection_policy",
    "preferential_conditions",
    "official_sources",
    "data_gaps",
}


class QueryContractError(ValueError):
    """Raised when a query requests an unsupported operation or field."""


class ProductFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    operator: str
    value: Any

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        if value not in FILTER_FIELDS:
            raise ValueError(f"unsupported filter field: {value}")
        return value

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in SUPPORTED_OPERATORS:
            raise ValueError(f"unsupported filter operator: {value}")
        return normalized


class ProductOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    direction: Literal["ASC", "DESC"] = "ASC"

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        if value not in ORDER_FIELDS:
            raise ValueError(f"unsupported order field: {value}")
        return value


class ProductQuerySpec(BaseModel):
    """Allowlisted query shape; no SQL or expression fields are accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: list[str] = Field(default_factory=lambda: ["product_id", "name"])
    filters: list[ProductFilter] = Field(default_factory=list)
    order: list[ProductOrder] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=100)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("at least one projection field is required")
        unknown = sorted(set(values) - PRODUCT_FIELDS)
        if unknown:
            raise ValueError(f"unsupported projection field: {unknown[0]}")
        if len(values) != len(set(values)):
            raise ValueError("projection fields must be unique")
        return values


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _raw_product(product: Any) -> Mapping[str, Any]:
    normalized = getattr(product, "normalized", None)
    raw = getattr(normalized, "raw_product", None)
    if isinstance(raw, Mapping) and raw:
        return raw
    return _mapping(product)


def _value(product: Any, field: str) -> Any:
    raw = _raw_product(product)
    metadata = getattr(product, "metadata", None)
    normalized = getattr(product, "normalized", None)
    if field == "product_id":
        return raw.get("product_code") or raw.get("product_id") or getattr(product, "product_id", None)
    if field == "institution_id":
        return raw.get("institution_id") or getattr(product, "institution_id", None)
    if field == "institution_name":
        return raw.get("institution_name") or getattr(metadata, "institution_name", None)
    if field == "name":
        return raw.get("name") or getattr(product, "name", None)
    if field == "product_type":
        return raw.get("product_family") or getattr(product, "product_type", None)
    if field == "product_subtype":
        return raw.get("product_subtype") or getattr(metadata, "product_subtype", None)
    if field == "institution_sector":
        return getattr(metadata, "institution_sector", None)
    if field == "sale_status":
        return (raw.get("sale_policy") or {}).get("status") or getattr(metadata, "sale_status", None)
    if field in {"base_rate", "advertised_max_rate"}:
        direct = raw.get(field)
        if direct is not None:
            return direct
        value = getattr(product, field, None)
        if value is not None:
            return value
        # Canonical normalized products keep rates in the return policy.  A
        # query may inspect the published maximum without knowing storage
        # layout, but must not receive the raw policy as a side channel.
        entries = (raw.get("return_policy") or {}).get("rate_entries") or []
        target_kind = "BASE" if field == "base_rate" else "ADVERTISED_MAXIMUM"
        for entry in entries:
            if not isinstance(entry, Mapping) or entry.get("kind") != target_kind:
                continue
            value = entry.get("rate")
            if value is None:
                value = entry.get("value")
            if value is not None:
                return value
        return None
    policy = (raw.get("return_policy") or {}) if raw else {}
    if field == "return_kind":
        return policy.get("return_kind") or (
            normalized.return_policy.get("return_kind") if normalized is not None else None
        )
    if field == "protection_status":
        protection = raw.get("protection_policy") or {}
        return protection.get("status") or protection.get("coverage")
    if field == "interest_payment_method":
        return getattr(metadata, "interest_payment_method", None)
    raise QueryContractError(f"unsupported product field: {field}")


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, Enum):
        return value.value
    return deepcopy(value)


def _project(product: Any, fields: Iterable[str]) -> dict[str, Any]:
    return {field: _json_value(_value(product, field)) for field in fields}


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _compare(left: Any, right: Any) -> int:
    left, right = _plain(left), _plain(right)
    if left is None and right is None:
        return 0
    if left is None:
        return 1
    if right is None:
        return -1
    if isinstance(left, Decimal) or isinstance(right, Decimal):
        try:
            left, right = Decimal(str(left)), Decimal(str(right))
        except Exception:
            pass
    try:
        return (left > right) - (left < right)
    except TypeError:
        left, right = str(left), str(right)
        return (left > right) - (left < right)


def _matches(actual: Any, query: ProductFilter) -> bool:
    operator, expected = query.operator, query.value
    _validate_filter_type(query)
    if actual is None:
        return operator == "EQ" and expected is None or operator == "NEQ" and expected is not None
    if query.field in NUMERIC_FIELDS:
        actual = Decimal(str(actual))
        if operator in NUMERIC_OPERATORS:
            expected = Decimal(str(expected))
    if operator == "CONTAINS":
        if not isinstance(actual, str) or not isinstance(expected, str):
            raise QueryContractError("CONTAINS requires string field and value")
        return expected.casefold() in actual.casefold()
    if operator in {"IN", "NOT_IN"}:
        if not isinstance(expected, (list, tuple, set, frozenset)):
            raise QueryContractError(f"{operator} requires a list value")
        if query.field in NUMERIC_FIELDS:
            expected = [Decimal(str(item)) for item in expected]
        result = any(_compare(actual, item) == 0 for item in expected)
        return result if operator == "IN" else not result
    comparison = _compare(actual, expected)
    return {
        "EQ": comparison == 0,
        "NEQ": comparison != 0,
        "GT": comparison > 0,
        "GTE": comparison >= 0,
        "LT": comparison < 0,
        "LTE": comparison <= 0,
    }[operator]


def _validate_filter_type(query: ProductFilter) -> None:
    if query.field in NUMERIC_FIELDS:
        if query.operator not in NUMERIC_OPERATORS | {"IN", "NOT_IN"}:
            raise QueryContractError(f"operator {query.operator} requires a numeric field: {query.field}")
        if query.operator in {"EQ", "NEQ", "GT", "GTE", "LT", "LTE"}:
            if isinstance(query.value, bool):
                raise QueryContractError(f"numeric filter value required: {query.field}")
            try:
                Decimal(str(query.value))
            except (TypeError, ValueError, ArithmeticError) as exc:
                raise QueryContractError(f"numeric filter value required: {query.field}") from exc
    elif query.operator in NUMERIC_OPERATORS - {"EQ", "NEQ"}:
        raise QueryContractError(f"numeric operator is unsupported for field: {query.field}")
    if query.operator == "CONTAINS" and not isinstance(query.value, str):
        raise QueryContractError("CONTAINS requires a string value")
    if query.operator in {"IN", "NOT_IN"} and not isinstance(query.value, (list, tuple, set, frozenset)):
        raise QueryContractError(f"{query.operator} requires a list value")


def search_products(products: Iterable[Any], query: ProductQuerySpec) -> list[dict[str, Any]]:
    """Return deterministic projections from an in-memory product iterable."""

    if not isinstance(query, ProductQuerySpec):
        query = ProductQuerySpec.model_validate(query)
    for item in query.filters:
        _validate_filter_type(item)
    rows: list[tuple[Any, dict[str, Any]]] = []
    for product in products:
        if all(_matches(_value(product, item.field), item) for item in query.filters):
            rows.append((product, _project(product, query.fields)))

    # Product ID is an unconditional deterministic tie-breaker. Null values
    # sort last in both directions, preventing unstable pagination.
    rows.sort(key=lambda item: str(_value(item[0], "product_id") or ""))
    for order in reversed(query.order):
        non_null = [row for row in rows if _value(row[0], order.field) is not None]
        nulls = [row for row in rows if _value(row[0], order.field) is None]
        non_null.sort(
            key=cmp_to_key(
                lambda left, right: _compare(
                    _value(left[0], order.field), _value(right[0], order.field)
                ) * (-1 if order.direction == "DESC" else 1)
            )
        )
        rows = non_null + nulls
    return [row[1] for row in rows[: query.limit]]


def get_product_details(
    products: Iterable[Any],
    product_ids: Iterable[str],
    fields: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return allowlisted product details without exposing arbitrary raw JSON."""

    requested = list(fields) if fields is not None else sorted(PRODUCT_FIELDS)
    unknown = sorted(set(requested) - DETAIL_FIELDS)
    if unknown:
        raise QueryContractError(f"unsupported detail field: {unknown[0]}")
    ids = {str(item) for item in product_ids}
    result: list[dict[str, Any]] = []
    for product in products:
        if str(_value(product, "product_id")) not in ids:
            continue
        row = _project(product, [field for field in requested if field in PRODUCT_FIELDS])
        raw = _raw_product(product)
        normalized = getattr(product, "normalized", None)
        for field in requested:
            if field in PRODUCT_FIELDS:
                continue
            source = normalized if normalized is not None else raw
            source_map = _mapping(source)
            if field == "preferential_conditions":
                row[field] = deepcopy(
                    (source_map.get("return_policy") or {}).get("preferential_policy", {}).get("rules", [])
                )
            elif field == "official_sources":
                row[field] = deepcopy(source_map.get("official_sources", []))
            elif field == "data_gaps":
                row[field] = deepcopy(source_map.get("data_gaps", []))
            else:
                row[field] = deepcopy(source_map.get(field, {}))
        result.append(row)
    return result


def get_requirement_index(
    requirements: Iterable[ConditionRequirement | Mapping[str, Any]],
    *,
    product_ids: Iterable[str] | None = None,
    variable_ids: Iterable[str] | None = None,
    scopes: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Read the compiled requirement index, retaining REVIEW_REQUIRED exactly."""

    products = {str(item) for item in product_ids} if product_ids is not None else None
    variables = {str(item) for item in variable_ids} if variable_ids is not None else None
    scope_values = {str(item) for item in scopes} if scopes is not None else None
    result: list[dict[str, Any]] = []
    for item in requirements:
        row = item.model_dump(mode="python") if isinstance(item, ConditionRequirement) else deepcopy(dict(item))
        if products is not None and str(row.get("product_id")) not in products:
            continue
        if variables is not None and str(row.get("variable_id")) not in variables:
            continue
        if scope_values is not None and str(row.get("scope")) not in scope_values:
            continue
        result.append(row)
    return sorted(result, key=lambda row: (str(row.get("product_id", "")), str(row.get("requirement_id", ""))))


def get_session_condition_state(
    states: Iterable[UserConditionState | Mapping[str, Any]] | Mapping[str, UserConditionState | Mapping[str, Any]],
    *,
    variable_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Read session-local condition state without mutating the supplied ledger."""

    values = states.values() if isinstance(states, Mapping) else states
    wanted = {str(item) for item in variable_ids} if variable_ids is not None else None
    result: list[dict[str, Any]] = []
    for item in values:
        row = item.model_dump(mode="python") if isinstance(item, UserConditionState) else deepcopy(dict(item))
        if wanted is None or str(row.get("variable_id")) in wanted:
            result.append(row)
    return sorted(result, key=lambda row: str(row.get("variable_id", "")))


class ReadOnlyProductQueryTools:
    """Convenience facade over the pure module-level contracts."""

    def __init__(
        self,
        products: Iterable[Any],
        *,
        requirements: Iterable[ConditionRequirement | Mapping[str, Any]] = (),
        condition_states: Iterable[UserConditionState | Mapping[str, Any]] | Mapping[str, UserConditionState | Mapping[str, Any]] = (),
    ) -> None:
        self._products = tuple(products)
        self._requirements = tuple(requirements)
        self._condition_states = deepcopy(condition_states)

    def search_products(self, query: ProductQuerySpec) -> list[dict[str, Any]]:
        return search_products(self._products, query)

    def get_product_details(self, product_ids: Iterable[str], fields: Iterable[str] | None = None) -> list[dict[str, Any]]:
        return get_product_details(self._products, product_ids, fields)

    def get_requirement_index(self, **kwargs: Any) -> list[dict[str, Any]]:
        return get_requirement_index(self._requirements, **kwargs)

    def get_session_condition_state(self, **kwargs: Any) -> list[dict[str, Any]]:
        return get_session_condition_state(self._condition_states, **kwargs)


__all__ = [
    "PRODUCT_FIELDS",
    "FILTER_FIELDS",
    "ORDER_FIELDS",
    "SUPPORTED_OPERATORS",
    "QueryContractError",
    "ProductFilter",
    "ProductOrder",
    "ProductQuerySpec",
    "search_products",
    "get_product_details",
    "get_requirement_index",
    "get_session_condition_state",
    "ReadOnlyProductQueryTools",
]
