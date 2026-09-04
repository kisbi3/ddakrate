import json
from pathlib import Path

from eligibility.application_service import ApplicationService
from eligibility.catalog.normalized_loader import load_normalized_product_catalog, load_product_aliases

ROOT = Path(__file__).resolve().parents[1]
PAIRS = {
    "INST-KR-000830-1-0001": "INST-KR-000830-1-C98752E9523",
    "INST-KR-000830-1-0002": "INST-KR-000830-1-CA7BF0C5E7B",
    "INST-KR-000830-1-0003": "INST-KR-000830-1-CD19BB23CD9",
    "INST-KR-000830-1-0004": "INST-KR-000830-1-CAAB9B11CC0",
    "INST-KR-000830-1-0005": "INST-KR-000830-1-C2AFF66ADCD",
    "INST-KR-000830-2-0001": "INST-KR-000830-2-C0A3C28D4F5",
    "INST-KR-000830-3-0004": "INST-KR-000830-3-C90225C0EC4",
    "INST-KR-000830-3-0005": "INST-KR-000830-3-C67C2196311",
    "INST-KR-000099-4-0001": "INST-KR-000099-4-C0D71CA0183",
    "INST-KR-000099-4-0002": "INST-KR-000099-4-C7C7C5DB97E",
    "INST-KR-000647-4-0001": "INST-KR-000647-4-C9A274A26D1",
}
EXPECTED_NAMES = {
    "INST-KR-000830-1-0001": "카카오뱅크 자유적금", "INST-KR-000830-1-0002": "카카오뱅크 26주적금", "INST-KR-000830-1-0003": "카카오뱅크 한달적금", "INST-KR-000830-1-0004": "카카오뱅크 우리아이적금", "INST-KR-000830-1-0005": "카카오뱅크 청년미래적금", "INST-KR-000830-2-0001": "카카오뱅크 정기예금", "INST-KR-000830-3-0004": "카카오뱅크 세이프박스", "INST-KR-000830-3-0005": "카카오뱅크 부가세박스", "INST-KR-000099-4-0001": "NH CMA (RP형)", "INST-KR-000099-4-0002": "NH CMA (발행어음형)", "INST-KR-000647-4-0001": "CAPE 오르다 CMA (RP형)",
}
RUNTIME_ID_KEYS = {"product_code", "rate_id", "rule_id", "relation_id", "event_id", "event_ref", "counter_event_ref", "fulfillment_id", "fulfillment_ref", "fact_key", "member_rule_ids", "members", "applies_to_rule_ids", "action_id"}


def _runtime_identifier_values(value, key=None):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _runtime_identifier_values(child, child_key)
    elif isinstance(value, list):
        for child in value:
            yield from _runtime_identifier_values(child, key)
    elif key in RUNTIME_ID_KEYS and isinstance(value, str):
        yield value


def test_only_confirmed_aliases_are_registered_and_hidden():
    aliases = load_product_aliases()
    assert aliases == {alias: canonical for canonical, alias in PAIRS.items()}
    index = json.loads((ROOT / "data/financial_products/normalized/index.json").read_text())
    active = {row["product_code"] for row in index["products"]}
    assert not active.intersection(aliases)
    assert set(aliases.values()) <= active

    manifest = json.loads((ROOT / "data/financial_products/normalized/manifests/20260905-confirmed-identity-merge-01.json").read_text())
    source = json.loads((ROOT / manifest["source_document_registry"]).read_text())
    evidence = json.loads((ROOT / manifest["evidence_ref_registry"]).read_text())
    assert manifest["counts"]["source_documents"] == len(source["source_documents"])
    assert manifest["counts"]["evidence_refs"] == len(evidence["evidence_refs"])

    old_listing = json.loads((ROOT / "data/financial_products/normalized/listing_snapshots/20260831-installment-audited-01.json").read_text())
    listing = json.loads((ROOT / manifest["listing_snapshot_file"]).read_text())
    old_condition = json.loads((ROOT / "data/financial_products/normalized/condition_snapshots/20260831-installment-audited-01.json").read_text())
    condition = json.loads((ROOT / manifest["condition_snapshot_file"]).read_text())
    assert listing["snapshot_id"] == "20260905-confirmed-identity-merge-01"
    assert listing["captured_at"] == "2026-09-05T00:00:00+09:00"
    assert listing["based_on_snapshot_id"] == "20260831-installment-audited-01"
    assert listing["record_count"] == len(listing["records"])
    assert condition["generated_at"] == "2026-09-05T00:00:00+09:00"
    assert condition["correction_batch"] == "20260905-confirmed-identity-merge-01"
    assert condition["record_count"] == len(condition["records"])
    def expected_count(old_records):
        old_codes = {row["product_code"] for row in old_records}
        return len(old_records) + sum(
            (1 if alias in old_codes and canonical not in old_codes else 0)
            - (1 if alias in old_codes else 0)
            for canonical, alias in aliases.items()
        )

    assert len(listing["records"]) == expected_count(old_listing["records"])
    assert len(condition["records"]) == expected_count(old_condition["records"])


def test_names_provenance_and_runtime_identifiers_are_canonical():
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    for canonical, alias in PAIRS.items():
        product = products[canonical]
        assert product.name == EXPECTED_NAMES[canonical]
        raw = product.normalized.raw_product
        assert raw["product_code"] == canonical
        identity = raw["version_metadata"]["identity_audit"]
        assert identity["related_input_product_codes"] == [alias]
        assert raw["version_metadata"]["identity_merge"]["alias_product_code"] == alias
        assert all(alias not in value for value in _runtime_identifier_values(raw))
        alias_path = sorted((ROOT / "data/financial_products/normalized/products").glob(f"**/{alias}/v*.json"))[-1]
        alias_refs = set(json.loads(alias_path.read_text())["source_ref_ids"])
        assert alias_refs <= set(raw["source_ref_ids"])

    old_term = json.loads((ROOT / "data/financial_products/normalized/products/time_deposit/INST-KR-000830/INST-KR-000830-2-0001/v004.json").read_text())
    new_term = products["INST-KR-000830-2-0001"].normalized.raw_product
    assert [x["calculation"]["value"] for x in new_term["return_policy"]["rate_entries"]] == [x["calculation"]["value"] for x in old_term["return_policy"]["rate_entries"]]
    assert new_term["official_site_link"] == old_term["official_site_link"]


def test_historical_alias_detail_lookup_resolves_to_canonical():
    aliases = load_product_aliases()
    canonical, alias = next(iter(aliases.items()))[1], next(iter(aliases.items()))[0]
    product = next(item for item in load_normalized_product_catalog() if item.product_id == canonical)
    service = ApplicationService([product], product_aliases={alias: canonical})
    assert service.resolve_product_id(alias) == canonical
    assert service.get_catalog_product(alias).product_id == canonical
