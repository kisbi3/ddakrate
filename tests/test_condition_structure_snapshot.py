import json
from pathlib import Path

from eligibility.catalog.normalized_loader import load_normalized_product_catalog


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/financial_products/normalized/condition_snapshots/eligibility_preferential_20260828.json"


def test_condition_snapshot_covers_every_published_product_and_keeps_text_only_safe():
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    products = load_normalized_product_catalog(verify_hashes=False)
    active_codes = {product.product_id for product in products}
    snapshot_by_code = {row["product_code"]: row for row in payload["records"]}

    assert payload["snapshot_kind"] == "ELIGIBILITY_AND_PREFERENTIAL_STRUCTURE"
    assert payload["record_count"] == len(snapshot_by_code)
    assert active_codes <= snapshot_by_code.keys()
    for row in snapshot_by_code.values():
        for condition in row["preferential_rate"]["conditions"]:
            if condition["evaluation_status"] == "TEXT_ONLY":
                assert condition["reward_value"] is None
                assert condition["reward_rate_id"] is None


def test_known_high_rate_savings_keep_structured_conditions_separate_from_eligibility():
    products = {product.name: product for product in load_normalized_product_catalog()}

    ok = products["OK얼리버드적금"].normalized.condition_snapshot
    assert ok["eligibility"]["status"] == "TEXT_ONLY"
    assert {row["reward_value"] for row in ok["preferential_rate"]["conditions"] if row["evaluation_status"] == "STRUCTURED_LINKED"} == {"3", "7"}

    kb = products["KB카드쓰담적금"].normalized.condition_snapshot
    assert kb["preferential_rate"]["status"] == "PARTIALLY_STRUCTURED"
    assert any(row["reward_value"] == "6" for row in kb["preferential_rate"]["conditions"])
    assert all(
        "SOLE_PROPRIETOR" not in constraint.get("value", [])
        if isinstance(constraint.get("value"), list)
        else True
        for constraint in kb["eligibility"]["constraints"]
    )


def test_term_rate_differences_are_not_misrepresented_as_preferential_conditions():
    products = {product.product_id: product for product in load_normalized_product_catalog()}
    term_tiered = products["INST-KR-000005-1-CDC5C03A063"].normalized.condition_snapshot
    assert term_tiered["preferential_rate"]["status"] == "NONE"
    assert term_tiered["preferential_rate"]["conditions"] == []


def test_display_only_conditions_are_distinguished_from_partially_linked_conditions():
    products = {product.product_id: product for product in load_normalized_product_catalog()}
    displayed = products["INST-KR-000010-1-C168DF72250"].normalized.condition_snapshot
    assert displayed["preferential_rate"]["status"] == "DISCLOSED_ONLY"
