from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from decimal import Decimal

from eligibility.catalog.normalized_loader import select_base_rate
from eligibility.schema.product import ContractTerm
from eligibility.schema.enums import TermUnit


ROOT = Path(__file__).resolve().parents[1]
INPUT_SCRIPT = ROOT / "scripts/build_naver_first_rebuild_inputs_20260828.py"
PUBLISH_SCRIPT = ROOT / "scripts/publish_naver_first_catalog_20260828.py"
REBUILD_DIR = ROOT / "data/financial_products/rebuild/20260828-naver-first-01"


def test_naver_priority_ledger_has_lossless_source_coverage() -> None:
    ledger = [
        __import__("json").loads(line)
        for line in (REBUILD_DIR / "source_priority_ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = __import__("json").loads(
        (REBUILD_DIR / "coverage_report.json").read_text(encoding="utf-8")
    )

    assert len(ledger) == 4306
    assert report["counts"] == {
        "catalog_products": 4306,
        "naver_raw_records": 4233,
        "naver_primary_products": 4233,
        "internal_original_fallback_products": 73,
        "unresolved_naver_records": 0,
        "duplicate_source_assignments": 0,
    }
    assert all(report["invariants"].values())
    assert {row["source_priority"] for row in ledger} == {
        "NAVER_PRIMARY",
        "INTERNAL_ORIGINAL_FALLBACK",
    }
    assert all(row["processing_status"] == "SOURCE_READY_FOR_STRUCTURING" for row in ledger)


def test_clean_rebuild_inputs_keep_only_the_selected_source_material() -> None:
    input_module = _module_for(INPUT_SCRIPT, "naver_first_rebuild_inputs")
    rows, report = input_module.build_inputs()

    assert len(rows) == 4306
    assert all(report["invariants"].values())
    assert report["counts"]["naver_primary"] == 4233
    assert report["counts"]["internal_original_fallback"] == 73
    assert (
        report["counts"]["internal_fallback_original_text_ready"]
        + report["counts"]["internal_fallback_source_recollection_required"]
        == 73
    )
    assert all(
        "naver_raw_record" in row
        for row in rows
        if row["source_priority"] == "NAVER_PRIMARY"
    )
    assert all(
        "internal_original_text_evidence" in row
        for row in rows
        if row["source_priority"] == "INTERNAL_ORIGINAL_FALLBACK"
    )


def test_day_term_and_daily_contribution_are_not_coerced_to_monthly() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher")
    inputs = [
        __import__("json").loads(line)
        for line in (REBUILD_DIR / "structuring_inputs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kakao = next(
        row for row in inputs
        if row["canonical_product_code"] == "INST-KR-000830-1-CD19BB23CD9"
    )
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
    inputs = [
        json.loads(line)
        for line in (REBUILD_DIR / "structuring_inputs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expected = {
        "INST-KR-000448-2-C56B3F966A0": "3.10",  # KDB
        "INST-KR-000830-2-C0A3C28D4F5": "3.40",  # KakaoBank
        "INST-KR-000865-2-0001": "3.40",  # K-Bank code K
        "INST-KR-000856-2-C0A841598BE": "3.40",  # Sh수협 Hey
    }
    for code, value in expected.items():
        item = next(row for row in inputs if row["canonical_product_code"] == code)
        product, *_ = publisher.product_from_input(item, {})
        entries = product["return_policy"]["rate_entries"]
        assert entries and all(entry.get("applies_to") for entry in entries if entry["role"] == "BASE")
        assert select_base_rate(
            product["return_policy"], ContractTerm(value=6, unit=TermUnit.MONTH)
        ) == Decimal(value)


def test_generic_individual_eligibility_is_not_published_as_a_data_gap() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher_eligibility")
    inputs = [
        json.loads(line)
        for line in (REBUILD_DIR / "structuring_inputs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kakao = next(
        row for row in inputs
        if row["canonical_product_code"] == "INST-KR-000830-2-C0A3C28D4F5"
    )
    product, *_ = publisher.product_from_input(kakao, {})
    assert product["eligibility_policy"]["allowed_customer_types"] == ["INDIVIDUAL"]
    assert product["eligibility_policy"]["age_range"] == {"min_age": 14}
    assert not any(
        gap["path"] == "eligibility_policy"
        for gap in product["version_metadata"]["data_gaps"]
    )


def test_mixed_daily_and_monthly_contracts_keep_both_options() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher_mixed")
    inputs = [
        __import__("json").loads(line)
        for line in (REBUILD_DIR / "structuring_inputs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mixed = next(
        row for row in inputs
        if row["canonical_product_code"] == "INST-KR-000125-1-C5CD66E5D9E"
    )
    product, _, _, _, snapshot = publisher.product_from_input(mixed, {})

    options = product["cash_flow_policy"]["contribution_options"]
    assert {option["contribution_frequency"] for option in options} == {"DAILY", "MONTHLY"}
    assert snapshot["product_facts"]["contribution_frequency"] == "IRREGULAR"


def test_internal_text_fallback_cannot_invent_a_rankable_rate() -> None:
    publisher = _module_for(PUBLISH_SCRIPT, "naver_first_publisher_fallback")
    inputs = [
        __import__("json").loads(line)
        for line in (REBUILD_DIR / "structuring_inputs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fallback = next(
        row for row in inputs
        if row["canonical_product_code"] == "INST-KR-000830-2-0001"
    )
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
