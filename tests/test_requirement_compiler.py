from __future__ import annotations

from copy import deepcopy

from eligibility.catalog.requirement_compiler import (
    COMPILER_VERSION,
    RequirementCompiler,
)
from eligibility.schema.condition_requirement import ConditionRequirement


def _product(*, rules=None, standard_conditions=None):
    return {
        "product_code": "P-1",
        "institution_id": "BANK-1",
        "product_family": "INSTALLMENT_SAVINGS",
        "return_policy": {
            "preferential_policy": {"rules": rules or []},
        },
        "eligibility_policy": {"mode": "UNRESTRICTED"},
        "standard_conditions": standard_conditions or [],
        "custom_bindings": [],
        "version_metadata": {"data_gaps": []},
    }


def _rule(**overrides):
    row = {
        "rule_id": "R-1",
        "title": "카드 실적",
        "semantic_domain": "CARD_MONTHLY_SPEND_LIMIT",
        "condition": {
            "predicate": {
                "fact_key": "CUSTOMER.CARD_MONTHLY_SPEND",
                "operator": "GTE",
                "expected_value": 300000,
                "unit": "KRW_PER_MONTH",
            }
        },
        "reward": {
            "kind": "ADD_RATE",
            "value": "1.0",
            "unit": "PERCENTAGE_POINT",
        },
        "source_ref_ids": ["EVD-1"],
        "structuring_status": "SUPPORTED",
    }
    row.update(overrides)
    return row


def test_compiler_preserves_structure_and_requires_explicit_verification():
    verified = _rule(review_status="VERIFIED", coverage_status="PARTIAL")
    product = _product(rules=[verified])
    before = deepcopy(product)

    result = RequirementCompiler().compile([product])
    requirement = result.requirements[0]

    assert product == before
    assert requirement.scope == "RATE_BENEFIT"
    assert requirement.variable_id == "CARD_MONTHLY_SPEND_LIMIT"
    assert requirement.operator == "GTE"
    assert requirement.threshold["expected_value"] == 300000
    assert requirement.rate_effect["value"] == "1.0"
    assert requirement.review_status == "VERIFIED"
    assert requirement.coverage_status == "PARTIAL"
    assert requirement.evidence_refs == ["EVD-1"]


def test_supported_or_unlisted_heuristic_stays_review_required():
    supported = _rule(structuring_status="SUPPORTED")
    unlisted = _rule(
        rule_id="R-2",
        semantic_domain="SOME_AI_GUESS",
        review_status="VERIFIED",
    )
    result = RequirementCompiler().compile([_product(rules=[supported, unlisted])])

    assert all(item.review_status == "REVIEW_REQUIRED" for item in result.requirements)
    assert result.metrics.review_required_count == 2
    assert result.metrics.verified_count == 0


def test_fee_and_prize_conditions_are_information_only():
    conditions = [
        {
            "condition_id": "FEE-1",
            "condition_type": "FEE_WAIVER",
            "source_text": "수수료 면제",
            "source_ref_ids": ["EVD-FEE"],
        },
        {
            "condition_id": "PRIZE-1",
            "condition_type": "PRIZE_EVENT",
            "source_text": "경품 추첨",
            "source_ref_ids": ["EVD-PRIZE"],
        },
    ]
    result = RequirementCompiler().compile([_product(standard_conditions=conditions)])

    assert [item.scope for item in result.requirements] == [
        "INFORMATION_ONLY",
        "INFORMATION_ONLY",
    ]
    assert result.metrics.scope_counts == {"INFORMATION_ONLY": 2}


def test_unavailable_gap_and_unmapped_variable_are_reported():
    rule = _rule(
        rule_id="R-UNKNOWN",
        semantic_domain=None,
        condition={"op": "AND", "children": [{"expected": True}]},
        coverage_status="UNAVAILABLE",
    )
    result = RequirementCompiler().compile([_product(rules=[rule])])
    requirement = result.requirements[0]

    assert requirement.coverage_status == "UNAVAILABLE"
    assert requirement.variable_id == "UNMAPPED:R-UNKNOWN"
    assert result.metrics.unmapped_count == 1
    assert result.metrics.unmapped_requirement_ids == ["req:P-1:R-UNKNOWN"]
    assert result.metrics.unavailable_count == 1


def test_compiles_typed_legacy_product_without_normalized_raw_data():
    from tests.v04_helpers import make_product

    product = make_product(
        "LEGACY",
        reward_pp="1",
        bonus_fact_type="AUTO_TRANSFER",
    )
    result = RequirementCompiler().compile([product])

    assert result.compiler_version == COMPILER_VERSION
    assert result.metrics.product_count == 1
    assert result.requirements[0].variable_id == "AUTO_TRANSFER"
    assert result.requirements[0].review_status == "REVIEW_REQUIRED"


def test_condition_requirement_rejects_unknown_fields():
    try:
        ConditionRequirement(
            requirement_id="x",
            product_id="p",
            scope="RATE_BENEFIT",
            variable_id="X",
            operator="EQ",
            unexpected=True,
        )
    except Exception as exc:
        assert "unexpected" in str(exc)
    else:
        raise AssertionError("extra fields must be rejected")
