"""Model-facing vocabulary and per-leaf fault isolation share one contract."""
from decimal import Decimal

import pytest

from eligibility.llm.json_schema import openai_strict_json_schema
from eligibility.schema.semantic import (
    ConditionScope, LooseSemanticCompilation, PeriodWindow, RewardRelation,
    SemanticExpression,
)
from eligibility.search.semantic import SourceClause, coerce_expression


def string_values(field):
    branches = field.get("anyOf", [field])
    return next(branch["enum"] for branch in branches if branch.get("type") == "string")


@pytest.mark.parametrize("transport,strict,fields", [
    ("LooseSemanticExpression", SemanticExpression, [
        "op", "variable", "comparator", "kind", "subject", "metric",
        "expected_unit", "official_confirmation_basis",
    ]),
    ("LoosePeriodWindow", PeriodWindow, ["unit", "basis"]),
    ("LooseConditionScope", ConditionScope, ["institution"]),
    ("LooseRewardRelation", RewardRelation, ["relation"]),
])
def test_provider_advertises_receiving_vocabulary_and_preserves_nulls(transport, strict, fields):
    provider = openai_strict_json_schema(LooseSemanticCompilation.model_json_schema())
    properties = provider["$defs"][transport]["properties"]
    receiving_schema = strict.model_json_schema()
    if "$ref" in receiving_schema:
        receiving_schema = receiving_schema["$defs"][receiving_schema["$ref"].rsplit("/", 1)[-1]]
    receiving = receiving_schema["properties"]
    for name in fields:
        assert string_values(properties[name]) == string_values(receiving[name])
        assert {"type": "null"} in properties[name]["anyOf"]
        assert name in provider["$defs"][transport]["required"]


def test_unknown_enum_is_isolated_without_losing_a_valid_sibling():
    quote = "자녀 2명 이상 또는 자녀 3명 이상"
    envelope = LooseSemanticCompilation.model_validate({"clauses": [{
        "clause_id": "c1", "source_hash": "h1", "source_quote": quote,
        "expression": {
            "op": "ANY", "source_quote": quote, "children": [
                {"op": "CHILD_COUNT", "source_quote": "자녀 2명 이상",
                 "comparator": "GTE", "expected": 2},
                {"op": "CHILD_COUNT", "source_quote": "자녀 3명 이상",
                 "comparator": "GREATER_THAN_OR_EQUALS", "expected": 3},
            ],
        },
    }]})
    clause = SourceClause("c1", "r1", "h1", quote, {}, False, Decimal("1"))
    expression = coerce_expression(envelope.clauses[0].expression, clause)
    assert expression.op == "ANY"
    assert [child.op for child in expression.children] == ["CHILD_COUNT", "UNKNOWN"]
    assert expression.children[0].expected == 2


def test_product_scope_remains_source_text_not_a_condition_kind_enum():
    schema = LooseSemanticCompilation.model_json_schema()
    product_kind = schema["$defs"]["LooseConditionScope"]["properties"]["product_kind"]
    assert {"type": "string"} in product_kind["anyOf"]
