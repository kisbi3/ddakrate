from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from decimal import Decimal

from eligibility.catalog.normalized_loader import select_base_rate
from eligibility.schema.product import ContractTerm
from eligibility.schema.enums import TermUnit


ROOT = Path(__file__).resolve().parents[1]
PUBLISH_SCRIPT = ROOT / "scripts/publish_naver_first_catalog_20260828.py"

# The 20260828 naver-first rebuild inputs live under data/financial_products/rebuild/,
# which .gitignore excludes as a build artifact (structuring_inputs.jsonl and
# source_priority_ledger.jsonl are 19MB each). These tests are about the publishing
# logic, not about the bulk inputs, so only the seven product rows they actually
# exercise are committed as a fixture. Regenerate with:
#
#   jq -c 'select(.canonical_product_code | IN("INST-KR-000830-1-CD19BB23CD9", ...))' \
#     data/financial_products/rebuild/20260828-naver-first-01/structuring_inputs.jsonl
#
# Two tests that used to live here were removed rather than fixtured:
#   - test_naver_priority_ledger_has_lossless_source_coverage checked the ledger and
#     coverage report of a one-off migration that has already run and been published;
#     it needs the whole 19MB ledger to assert a row count and cannot be subset.
#   - test_clean_rebuild_inputs_keep_only_the_selected_source_material re-ran
#     build_inputs() over the raw naver capture, which is also gitignored and far
#     larger. It tested the input builder, not anything the runtime depends on.
# Both verified a build that is now frozen in the published catalog; what they were
# guarding is covered by the published-artifact assertions in
# tests/test_normalized_runtime_catalog.py.
INPUTS = ROOT / "tests/fixtures/naver_first_20260828/structuring_inputs.subset.jsonl"


def _inputs() -> list[dict]:
    return [
        json.loads(line)
        for line in INPUTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _input_for(product_code: str) -> dict:
    return next(row for row in _inputs() if row["canonical_product_code"] == product_code)


def test_day_term_and_daily_contribution_are_not_coerced_to_monthly() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher")
    kakao = _input_for("INST-KR-000830-1-CD19BB23CD9")
    product, _, _, _, snapshot = publisher.product_from_input(kakao, {})

    assert product["term_policy"]["unit"] == "DAY"
    assert product["term_policy"]["fixed_value"] == 31
    assert product["cash_flow_policy"]["contribution_frequency"] == "DAILY"
    assert product["cash_flow_policy"]["amount_rules"] == [{
        "scope": "PER_PERIOD", "min_value": "100", "max_value": "30000", "currency": "KRW",
    }]
    assert snapshot["product_facts"]["term_unit"] == "DAY"


def test_time_deposit_period_rate_table_is_scoped_to_elapsed_term() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher_period_rates")
    expected = {
        "INST-KR-000448-2-C56B3F966A0": "3.10",  # KDB
        "INST-KR-000830-2-C0A3C28D4F5": "3.40",  # KakaoBank
        "INST-KR-000865-2-0001": "3.40",  # K-Bank code K
        "INST-KR-000856-2-C0A841598BE": "3.40",  # Sh수협 Hey
    }
    for code, value in expected.items():
        product, *_ = publisher.product_from_input(_input_for(code), {})
        entries = product["return_policy"]["rate_entries"]
        assert entries and all(entry.get("applies_to") for entry in entries if entry["role"] == "BASE")
        assert select_base_rate(
            product["return_policy"], ContractTerm(value=6, unit=TermUnit.MONTH)
        ) == Decimal(value)


def test_generic_individual_eligibility_is_not_published_as_a_data_gap() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher_eligibility")
    kakao = _input_for("INST-KR-000830-2-C0A3C28D4F5")
    product, *_ = publisher.product_from_input(kakao, {})
    assert product["eligibility_policy"]["allowed_customer_types"] == ["INDIVIDUAL"]
    assert product["eligibility_policy"]["age_range"] == {"min_age": 14}
    assert not any(
        gap["path"] == "eligibility_policy"
        for gap in product["version_metadata"]["data_gaps"]
    )


def test_mixed_daily_and_monthly_contracts_keep_both_options() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher_mixed")
    mixed = _input_for("INST-KR-000125-1-C5CD66E5D9E")
    product, _, _, _, snapshot = publisher.product_from_input(mixed, {})

    options = product["cash_flow_policy"]["contribution_options"]
    assert {option["contribution_frequency"] for option in options} == {"DAILY", "MONTHLY"}
    assert snapshot["product_facts"]["contribution_frequency"] == "IRREGULAR"


def test_internal_text_fallback_cannot_invent_a_rankable_rate() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher_fallback")
    fallback = _input_for("INST-KR-000830-2-0001")
    product, _, _, listing, _ = publisher.product_from_input(fallback, {})

    assert product["return_policy"]["rate_entries"] == []
    assert listing["base_rate"] is None
    assert listing["advertised_max_rate"] is None


def _module_for(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
