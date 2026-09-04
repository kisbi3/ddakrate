from __future__ import annotations

import pytest

from eligibility.catalog.preferential_quality import (
    PreferentialQualityError,
    assert_preferential_rate_quality,
    inspect_preferential_rate_quality,
)


def _product(*, maximum: str = "5.0", gap: bool = False) -> dict:
    gaps = (
        [{"path": "return_policy.rate_entries[PREFERENTIAL]", "reason": "조건 미확인"}]
        if gap
        else []
    )
    return {
        "product_code": "TEST-001",
        "return_policy": {
            "rate_entries": [
                {
                    "rate_id": "BASE",
                    "role": "BASE",
                    "calculation": {"value": "2.0", "unit": "PERCENT"},
                },
                {
                    "rate_id": "MAX",
                    "role": "ADVERTISED_MAXIMUM",
                    "calculation": {"value": maximum, "unit": "PERCENT"},
                },
            ],
            "advertised_max_rate": {"value": maximum, "unit": "PERCENT"},
        },
        "standard_conditions": [],
        "custom_bindings": [],
        "version_metadata": {"data_gaps": gaps},
    }


def _add_preferential(product: dict, *, amount: str = "3.0", reward_ref: str = "PREF") -> None:
    product["return_policy"]["rate_entries"].append(
        {
            "rate_id": "PREF",
            "role": "PREFERENTIAL",
            "calculation": {"value": amount, "unit": "PERCENTAGE_POINT"},
        }
    )
    product["return_policy"]["preferential_application"] = {"mode": "SUM"}
    product["standard_conditions"].append(
        {
            "condition_id": "COND-PREF",
            "purpose": "PREFERENTIAL_RETURN",
            "reward_refs": [reward_ref],
        }
    )


def test_rate_uplift_requires_linked_preferential_rate_or_explicit_gap() -> None:
    product = _product()

    issues = inspect_preferential_rate_quality(product)

    assert [issue.code for issue in issues] == ["UNEXPLAINED_ADVERTISED_RATE_UPLIFT"]
    with pytest.raises(PreferentialQualityError, match="TEST-001"):
        assert_preferential_rate_quality(product)

    assert inspect_preferential_rate_quality(_product(gap=True)) == ()


def test_legacy_field_message_gap_explains_rate_uplift() -> None:
    product = _product(maximum="5.0")
    product["version_metadata"]["data_gaps"] = [
        {
            "field": "return_policy.preferential_policy.rules.PREF-B01-01",
            "reason_code": "REQUIRES_EXTERNAL_VALUE",
            "message": "쿠폰별 외부 결정값이라 확인 필요",
        }
    ]

    assert inspect_preferential_rate_quality(product) == ()


def test_canonical_path_reason_gap_remains_supported() -> None:
    product = _product(maximum="5.0", gap=True)

    assert inspect_preferential_rate_quality(product) == ()


def test_every_structured_reward_ref_must_resolve_to_a_rate_entry() -> None:
    product = _product()
    _add_preferential(product, reward_ref="MISSING")

    codes = {issue.code for issue in inspect_preferential_rate_quality(product)}

    assert "UNRESOLVED_REWARD_REF" in codes
    assert "UNEXPLAINED_ADVERTISED_RATE_UPLIFT" in codes


def test_preferential_condition_must_link_to_preferential_role() -> None:
    product = _product(maximum="2.0")
    product["standard_conditions"].append(
        {
            "condition_id": "BAD",
            "purpose": "PREFERENTIAL_RETURN",
            "reward_refs": ["BASE"],
        }
    )

    assert [issue.code for issue in inspect_preferential_rate_quality(product)] == [
        "PREFERENTIAL_REWARD_ROLE_MISMATCH"
    ]


def test_verifiable_sum_must_equal_advertised_maximum() -> None:
    valid = _product(maximum="5.0")
    _add_preferential(valid)
    assert inspect_preferential_rate_quality(valid) == ()

    invalid = _product(maximum="5.1")
    _add_preferential(invalid)
    issues = inspect_preferential_rate_quality(invalid)
    assert [issue.code for issue in issues] == [
        "ADVERTISED_MAXIMUM_ARITHMETIC_MISMATCH"
    ]


def test_sum_cap_is_applied_when_arithmetic_is_verifiable() -> None:
    product = _product(maximum="4.0")
    _add_preferential(product, amount="3.0")
    product["return_policy"]["preferential_application"].update(
        {"cap_value": "2.0", "cap_unit": "PERCENTAGE_POINT"}
    )

    assert inspect_preferential_rate_quality(product) == ()


def test_canonical_numeric_fact_cannot_compare_to_boolean() -> None:
    product = _product(maximum="2.0")
    product["return_policy"]["preferential_policy"] = {
        "fact_definitions": [
            {"fact_key": "ACCOUNT.AVERAGE_BALANCE", "type": "NUMBER", "unit": "KRW"}
        ],
        "rules": [
            {
                "rule_id": "BAD-NUMERIC-PREDICATE",
                "condition": {
                    "predicate": {
                        "fact_key": "ACCOUNT.AVERAGE_BALANCE",
                        "operator": "EQUALS",
                        "expected_value": True,
                    }
                },
                "reward": {"kind": "ADD_RATE", "value": "0.1"},
            }
        ],
    }

    issues = inspect_preferential_rate_quality(product)

    assert [issue.code for issue in issues] == ["CANONICAL_PREDICATE_TYPE_MISMATCH"]


def test_canonical_numeric_threshold_is_type_safe() -> None:
    product = _product(maximum="2.0")
    product["return_policy"]["preferential_policy"] = {
        "fact_definitions": [
            {"fact_key": "ACCOUNT.AVERAGE_BALANCE", "type": "NUMBER", "unit": "KRW"}
        ],
        "rules": [
            {
                "rule_id": "VALID-NUMERIC-PREDICATE",
                "condition": {
                    "predicate": {
                        "fact_key": "ACCOUNT.AVERAGE_BALANCE",
                        "operator": "GTE",
                        "expected_value": 300000,
                        "unit": "KRW",
                    }
                },
                "reward": {"kind": "ADD_RATE", "value": "0.1"},
            }
        ],
    }

    assert inspect_preferential_rate_quality(product) == ()
