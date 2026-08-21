from __future__ import annotations

from copy import deepcopy
from typing import Any


_UNSUPPORTED_REGEX_LOOKAROUNDS = ("(?=", "(?!", "(?<=", "(?<!")


def _has_unsupported_regex_lookaround(value: Any) -> bool:
    return isinstance(value, str) and any(
        token in value for token in _UNSUPPORTED_REGEX_LOOKAROUNDS
    )


def openai_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Pydantic JSON Schema for OpenAI strict Structured Outputs.

    Pydantic omits defaulted fields from ``required``. OpenAI strict JSON schema
    requires object properties to be explicit and objects to reject undeclared
    keys. Optionality therefore stays in the property's ``null`` union, while the
    property name itself becomes required in the generated JSON.

    This function deliberately changes only transport-level schema requirements;
    the Pydantic response model remains the final validation contract.
    """

    normalized = deepcopy(schema)

    def visit(node: Any) -> Any:
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node

        # Defaults are a Pydantic/runtime concern and are not needed by the model.
        node.pop("default", None)

        # Pydantic represents Decimal as either a JSON number or a string guarded
        # by a lookaround regex. OpenAI Structured Outputs does not support regex
        # lookarounds. Prefer the numeric branch so the transport contract stays
        # strict; Pydantic remains the final response validator.
        any_of = node.get("anyOf")
        if isinstance(any_of, list) and any(
            isinstance(branch, dict) and branch.get("type") == "number"
            for branch in any_of
        ):
            node["anyOf"] = [
                branch
                for branch in any_of
                if not (
                    isinstance(branch, dict)
                    and branch.get("type") == "string"
                    and _has_unsupported_regex_lookaround(branch.get("pattern"))
                )
            ]

        # Other model schemas may also contain a Pydantic-generated lookaround.
        # Dropping only the unsupported transport keyword avoids provider-side
        # schema rejection while preserving final validation in Pydantic.
        if _has_unsupported_regex_lookaround(node.get("pattern")):
            node.pop("pattern", None)

        for key, value in list(node.items()):
            node[key] = visit(value)

        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
                node["additionalProperties"] = False
        return node

    return visit(normalized)
