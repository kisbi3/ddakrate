from __future__ import annotations

import pytest
from pydantic import ValidationError

from eligibility.schema.rule import AndRule, RULE_NODE_ADAPTER


def test_rule_ast_schema_contains_discriminated_rule_definitions():
    schema = RULE_NODE_ADAPTER.json_schema()

    assert "$defs" in schema
    assert "FactComparisonRule" in schema["$defs"]
    assert "CountDistinctMonthsRule" in schema["$defs"]
    assert "NotExistsRule" in schema["$defs"]


def test_empty_and_rule_is_rejected():
    with pytest.raises(ValidationError):
        AndRule(rule_id="EMPTY", name="empty", children=[])
