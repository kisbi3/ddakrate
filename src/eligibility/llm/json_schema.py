from __future__ import annotations

from copy import deepcopy
from typing import Any


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

        for key, value in list(node.items()):
            node[key] = visit(value)

        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
                node["additionalProperties"] = False
        return node

    return visit(normalized)
