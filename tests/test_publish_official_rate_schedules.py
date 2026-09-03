from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

from eligibility.catalog.preferential_quality import inspect_preferential_rate_quality


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/publish_official_rate_schedules_20260827.py"
SPEC = importlib.util.spec_from_file_location("publish_official_rate_schedules", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("code", sorted(MODULE.SCHEDULES))
def test_balance_schedule_patch_uses_scoped_base_rates(code: str) -> None:
    spec = MODULE.SCHEDULES[code]
    source = {
        "version": 1,
        "product_code": code,
        "name": spec["name"],
        "return_policy": {
            "rate_entries": [
                {
                    "rate_id": "OLD-BASE",
                    "role": "BASE",
                    "application_event": "ANY",
                    "calculation": {"type": "VARIABLE_POSTED", "value": "0.1", "unit": "PERCENT"},
                },
                {
                    "rate_id": "OLD-MAX",
                    "role": "ADVERTISED_MAXIMUM",
                    "application_event": "ANY",
                    "calculation": {"type": "DISPLAYED_MAXIMUM", "value": spec["maximum"], "unit": "PERCENT"},
                },
            ],
            "advertised_max_rate": {"value": spec["maximum"], "unit": "PERCENT"},
        },
        "standard_conditions": [],
        "custom_bindings": [],
        "version_metadata": {},
    }
    original = copy.deepcopy(source)

    patched, _ = MODULE.patch_product(source)

    assert source == original
    assert patched["version"] == 2
    assert all(row["role"] == "BASE" for row in patched["return_policy"]["rate_entries"])
    assert all(row["applies_to"][0]["basis"] == "BALANCE" for row in patched["return_policy"]["rate_entries"])
    assert not inspect_preferential_rate_quality(patched)


def test_kakao_coupon_is_not_a_deterministic_preferential_reward() -> None:
    source = {
        "version": 1,
        "product_code": MODULE.KAKAO_CODE,
        "name": MODULE.KAKAO_NAME,
        "return_policy": {
            "rate_entries": [
                {
                    "rate_id": "BASE",
                    "role": "BASE",
                    "application_event": "ANY",
                    "calculation": {"type": "VARIABLE_POSTED", "value": "2.00", "unit": "PERCENT"},
                }
            ],
            "advertised_max_rate": {"value": "2.00", "unit": "PERCENT"},
        },
        "standard_conditions": [],
        "custom_bindings": [
            {
                "product_version": 1,
                "custom_code": "INST-KR-000830-VATBOX-COUPON",
                "purpose": "PREFERENTIAL_RETURN",
                "reward_refs": [],
            }
        ],
        "version_metadata": {},
    }

    patched, _ = MODULE.patch_product(source)

    assert patched["custom_bindings"][0]["purpose"] == "INFORMATIONAL_BENEFIT"
    assert patched["custom_bindings"][0]["product_version"] == 2
    assert not inspect_preferential_rate_quality(patched)
    assert any("금리쿠폰" in gap["reason"] for gap in patched["version_metadata"]["data_gaps"])
