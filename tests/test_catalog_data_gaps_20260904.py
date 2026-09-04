from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.audit_catalog_gaps_20260904 import audit


ROOT = Path(__file__).resolve().parents[1]


KNOWN_PRODUCTS_WITHOUT_A_WEB_LINK = {
    "INST-KR-000028-3-0001",
    "INST-KR-000028-3-0002",
    "INST-KR-000055-2-0007",
    "INST-KR-000055-2-0008",
    "INST-KR-000055-2-0009",
    "INST-KR-000055-2-0010",
    "INST-KR-000076-4-0002",
    "INST-KR-000076-4-0003",
    "INST-KR-000084-4-0002",
    "INST-KR-000092-4-0003",
    "INST-KR-000148-4-0001",
    "INST-KR-000148-4-0003",
    "INST-KR-000175-4-0002",
    "INST-KR-000175-4-0003",
    "INST-KR-000244-4-0001",
    "INST-KR-000259-4-0001",
    "INST-KR-000259-4-0002",
    "INST-KR-000259-4-0003",
    "INST-KR-000408-1-0001",
    "INST-KR-000408-1-0004",
    "INST-KR-000408-2-0001",
    "INST-KR-000408-2-0002",
    "INST-KR-000408-2-0003",
    "INST-KR-000408-2-0004",
    "INST-KR-000408-2-0010",
    "INST-KR-000408-2-0011",
    "INST-KR-000408-2-0012",
    "INST-KR-000408-2-0013",
    "INST-KR-000408-2-0014",
    "INST-KR-000408-2-0019",
    "INST-KR-000459-4-0001",
    "INST-KR-000459-4-0002",
    "INST-KR-000459-4-0003",
    "INST-KR-000830-1-0001",
    "INST-KR-000830-1-0002",
    "INST-KR-000830-1-0003",
    "INST-KR-000830-1-0004",
    "INST-KR-000830-1-0005",
}


def _read(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_catalog_data_gap_repair_counts_and_exception_sets() -> None:
    report = audit()
    counts = report["counts"]
    index = _read("data/financial_products/normalized/index.json")

    assert counts["catalog_products"] == index["product_count"]
    assert counts["missing_web_link"] == len(KNOWN_PRODUCTS_WITHOUT_A_WEB_LINK)
    assert {
        item["product_code"] for item in report["missing_web_link_products"]
    } == KNOWN_PRODUCTS_WITHOUT_A_WEB_LINK
    assert counts["no_rate_entries"] == 66
    assert counts["no_rate_entries_performance_observations"] == 11
    assert counts["no_rate_entries_ranking_ineligible"] == 3
    assert counts["no_rate_entries_declared_rate_gap"] == 52
    assert counts["no_rate_entries_silent"] == 0
    assert report["no_rate_entries_silent_products"] == []
    assert counts["missing_document_type"] == 0
    assert report["missing_document_type_products"] == []
    assert counts["data_gap_schema_count"] == 14
    assert counts["field_schema_gap_items"] == 107
    assert counts["unresolved_source_ref_products"] == 0


def test_rate_gap_repairs_preserve_unknown_values_and_kakao_official_rates() -> None:
    yuanta = _read(
        "data/financial_products/normalized/products/cma/"
        "INST-KR-000092/INST-KR-000092-4-0004/v003.json"
    )
    tariff_selection = _read(
        "data/financial_products/normalized/products/parking_account/"
        "INST-KR-000736/INST-KR-000736-3-C1C2DCAD7BA/v004.json"
    )
    kakao = _read(
        "data/financial_products/normalized/products/time_deposit/"
        "INST-KR-000830/INST-KR-000830-2-0001/v004.json"
    )

    assert yuanta["return_policy"]["rate_entries"] == []
    assert any(
        gap.get("path") == "return_policy.rate_entries"
        for gap in yuanta["version_metadata"]["data_gaps"]
    )
    assert tariff_selection["return_policy"]["rate_entries"] == []
    assert any(
        gap.get("path") == "return_policy.rate_entries"
        for gap in tariff_selection["version_metadata"]["data_gaps"]
    )

    base_rates = [
        entry["calculation"]["value"]
        for entry in kakao["return_policy"]["rate_entries"]
        if entry.get("role") == "BASE"
    ]
    assert base_rates == ["2.9", "3.3", "3.4", "3.6", "3", "3"]
    assert (
        kakao["official_site_link"]["url"]
        == "https://www.kakaobank.com/products/deposit"
    )
    assert not any(
        gap.get("path") == "official_research"
        for gap in kakao["version_metadata"]["data_gaps"]
    )


def test_active_manifest_and_index_hashes_are_current() -> None:
    index = _read("data/financial_products/normalized/index.json")
    manifest_path = (
        ROOT
        / "data/financial_products/normalized/manifests"
        / f"{index['manifest_batch']}.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for item in manifest["files"]:
        path = ROOT / item["path"]
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == item["sha256"], item["path"]

    for item in index["products"]:
        path = ROOT / item["path"]
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == item["sha256"], item["product_code"]


def test_all_nested_product_source_refs_resolve_through_evidence_to_source() -> None:
    index = _read("data/financial_products/normalized/index.json")
    manifest = _read(
        f"data/financial_products/normalized/manifests/"
        f"{index['manifest_batch']}.json"
    )
    evidence = {
        row["evidence_ref_id"]: row
        for row in _read(manifest["evidence_ref_registry"])["evidence_refs"]
    }
    sources = {
        row["source_id"]: row
        for row in _read(manifest["source_document_registry"])["source_documents"]
    }

    def refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "source_ref_ids" and isinstance(child, list):
                    yield from (ref for ref in child if isinstance(ref, str))
                else:
                    yield from refs(child)
        elif isinstance(value, list):
            for child in value:
                yield from refs(child)

    for row in index["products"]:
        product = _read(row["path"])
        for ref_id in refs(product):
            assert ref_id in evidence, (row["product_code"], ref_id)
            assert evidence[ref_id].get("source_id") in sources


def test_source_bridge_preserves_legacy_locator_and_excerpt() -> None:
    index = _read("data/financial_products/normalized/index.json")
    manifest = _read(
        f"data/financial_products/normalized/manifests/"
        f"{index['manifest_batch']}.json"
    )
    evidence_rows = _read(manifest["evidence_ref_registry"])["evidence_refs"]
    source_rows = _read(manifest["source_document_registry"])["source_documents"]
    evidence = next(
        row
        for row in evidence_rows
        if row.get("evidence_ref_id")
        == "EVD-SOURCE-BRIDGE-EVD-TD-ROW0462-INST-KR-000424-2-CF0A625DD97"
    )
    source = next(
        row
        for row in source_rows
        if row.get("source_id")
        == "EVD-TD-ROW0462-INST-KR-000424-2-CF0A625DD97"
    )
    assert evidence["source_id"] == source["source_id"]
    assert evidence["locator"] == source["locator"]
    assert evidence["source_text"] == source["excerpt"]
