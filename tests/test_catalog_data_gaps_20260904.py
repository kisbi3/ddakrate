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
    assert counts["no_rate_entries"] == 61
    assert counts["no_rate_entries_performance_observations"] == 11
    assert counts["no_rate_entries_ranking_ineligible"] == 3
    assert counts["no_rate_entries_declared_rate_gap"] == 47
    assert counts["no_rate_entries_silent"] == 0
    assert report["no_rate_entries_silent_products"] == []
    assert counts["missing_document_type"] == 0
    assert report["missing_document_type_products"] == []
    assert counts["data_gap_schema_count"] == 14
    assert counts["field_schema_gap_items"] == 98
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


def test_nh_term_deposit_is_distinct_from_big_satisfaction_deposit() -> None:
    index = _read("data/financial_products/normalized/index.json")
    by_code = {row["product_code"]: _read(row["path"]) for row in index["products"]}
    term_deposit = by_code["INST-KR-000739-2-C4C876638DA"]
    big_satisfaction = by_code["INST-KR-000739-2-C87539C2B27"]

    assert term_deposit["name"] == "정기예금"
    assert term_deposit["version"] == 6
    assert term_deposit["return_policy"]["advertised_max_rate"]["value"] == "2.4"
    assert term_deposit["eligibility_policy"]["mode"] == "UNRESTRICTED"
    assert term_deposit["sale_policy"]["subscription_channels"] == ["BRANCH"]
    assert term_deposit["interest_payment_policy"]["default_context_id"] == (
        "MATURITY_PAYMENT"
    )

    assert big_satisfaction["name"] == "큰만족실세예금"
    assert big_satisfaction["return_policy"]["advertised_max_rate"]["value"] == (
        "2.45"
    )
    assert big_satisfaction["eligibility_policy"]["mode"] == "RESTRICTED"

    serialized = json.dumps(term_deposit, ensure_ascii=False)
    assert "EVD-TD-ROW0512-NH-PDF" not in serialized
    assert term_deposit["liquidity_policy"]["automatic_renewal_allowed"] is False
    assert term_deposit["liquidity_policy"]["after_maturity_policy"]
    assert term_deposit["liquidity_policy"]["partial_withdrawal"]["max_events_including_maturity"] == 3
    assert not any(
        (gap.get("path") or gap.get("field")) == "eligibility_policy"
        for gap in term_deposit["version_metadata"]["data_gaps"]
    )


def test_nh_identity_correction_preserves_user_evidence_and_repairs_pdf_owner() -> None:
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

    pdf_evidence = evidence["EVD-TD-ROW0512-NH-PDF"]
    pdf_source = sources[pdf_evidence["source_id"]]
    assert pdf_evidence["product_code"] == "INST-KR-000739-2-C87539C2B27"
    assert pdf_source["product_code"] == "INST-KR-000739-2-C87539C2B27"

    expected_hashes = {
        "EVD-USER-20260904-NAVER-NH-DEPOSIT-LIST": (
            "sha256:1cbb62b6045d15ac701d64744bd97fdad6ca75bb2e02368b452c1fb7553ae05d"
        ),
        "EVD-USER-20260904-NH-OFFICIAL-DEPOSIT-LIST": (
            "sha256:544b268519c9b904f14638b7517841625a61ee584a03f7cdcb9fde11ee7f610e"
        ),
    }
    for evidence_id, expected_hash in expected_hashes.items():
        row = evidence[evidence_id]
        source = sources[row["source_id"]]
        assert row["locator"]["sha256"] == expected_hash
        assert source["locator"]["sha256"] == expected_hash
        assert row["related_product_codes"] == [
            "INST-KR-000739-2-C4C876638DA",
            "INST-KR-000739-2-C87539C2B27",
        ]


def test_nh_official_terms_v006_preserve_current_rate_and_resolve_gaps() -> None:
    index = _read("data/financial_products/normalized/index.json")
    row = next(x for x in index["products"] if x["product_code"] == "INST-KR-000739-2-C4C876638DA")
    product = _read(row["path"])
    assert row["version"] == 6
    assert product["return_policy"]["advertised_max_rate"]["value"] == "2.4"
    assert product["official_site_link"]["url"] == "https://smartmarket.nonghyup.com/servlet/BFDCW1021R.view"
    manifest = _read(f"data/financial_products/normalized/manifests/{index['manifest_batch']}.json")
    evidence = _read(manifest["evidence_ref_registry"])["evidence_refs"]
    pdf = next(x for x in evidence if x["evidence_ref_id"] == "EVD-USER-20260904-NH-TERM-DEPOSIT-PDF")
    assert pdf["locator"]["sha256"] == "sha256:00fd5752d6be9549a8b49959074b280f0f96af0c4b254af62ebe583637fe4d58"
    terms = next(x for x in evidence if x["evidence_ref_id"].endswith("PDF-TERMS"))
    auto = next(x for x in evidence if x["evidence_ref_id"].endswith("PDF-AUTORENEWAL-DECISION"))
    assert terms["locator"]["page"] == 2
    assert "만기후" not in terms["source_text"]
    assert auto["locator"]["page_range"] == "1-5"
    assert "자동재예치 조항 없음" in auto["source_text"]
    assert product["data_gaps"] == []
    assert product["version_metadata"]["data_gaps"] == []
    assert all(
        "TERM-DEPOSIT-PDF" not in ref and "TERM-DEPOSIT-DETAIL" not in ref
        for entry in product["return_policy"]["rate_entries"]
        for ref in entry.get("source_ref_ids", [])
    )
    liquidity = product["liquidity_policy"]
    assert liquidity["automatic_renewal_allowed"] is False
    assert liquidity["partial_withdrawal"]["max_events_including_maturity"] == 3
    assert liquidity["partial_withdrawal"]["remaining_balance_min_krw"] == "1000000"
    assert liquidity["early_termination_policy"]["tiers"][-1]["rate"]["minimum_percent"] == "0.10"
    assert liquidity["early_termination_policy"]["tiers"][-1]["rate"]["formula"] == (
        "EARLY_BASE_RATE*80%*ELAPSED_MONTHS/CONTRACT_MONTHS"
    )
    assert liquidity["after_maturity_policy"]["tiers"][0]["rate"]["formula"].endswith("50%")


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
