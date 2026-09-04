from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from eligibility.catalog.normalized_loader import (
    _adapt_product,
    _load_institutions,
    select_base_rate,
)
from eligibility.schema.enums import TermUnit
from eligibility.schema.product import ContractTerm


ROOT = Path(__file__).resolve().parents[1]

# data/20260829-cma-01/ is a build shard that .gitignore excludes. Its 71 product
# files under products/cma/ are byte-for-byte identical to the ones already published
# under data/financial_products/normalized/products/cma/ (verified: 71 identical, 0
# different), so the product reads below go to the published catalog and only the four
# build ledgers that have no published equivalent are committed alongside the shard.
SHARD = ROOT / "data/20260829-cma-01"
PUBLISHED_CMA = ROOT / "data/financial_products/normalized/products/cma"


def _jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (SHARD / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _product(product_code: str) -> dict:
    path = next(PUBLISHED_CMA.glob(f"*/{product_code}/v002.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def _rate(
    product_code: str,
    value: int,
    unit: TermUnit = TermUnit.DAY,
    *,
    customer_scope: str | None = None,
    as_of: str = "2026-08-30",
) -> Decimal | None:
    product = _product(product_code)
    return select_base_rate(
        product["return_policy"],
        ContractTerm(value=value, unit=unit),
        balance=Decimal("1000000"),
        customer_scope=customer_scope,
        as_of=as_of,
    )


def test_every_cma_input_has_one_complete_individual_review() -> None:
    rows = _jsonl("product_review_ledger.jsonl")
    assert len(rows) == 71
    assert [row["input_line"] for row in rows] == list(range(1, 72))
    assert len({row["product_code"] for row in rows}) == 71
    assert {row["review_status"] for row in rows} == {"COMPLETE"}
    assert all(row.get("checkpoints", {}).get("runtime_boundary_test") for row in rows)


def test_shard_index_hashes_and_paths_are_current() -> None:
    rows = _jsonl("index_rows.jsonl")
    assert len(rows) == 71
    for row in rows:
        # row["path"] is the published destination; row["shard_path"] is the staging
        # copy the batch was assembled in. Both carry the same sha256, so assert against
        # the artifact that actually ships.
        path = ROOT / row["path"]
        assert path.exists()
        assert row["sha256"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        product = json.loads(path.read_text(encoding="utf-8"))
        assert (product["product_code"], product["version"], product["institution_id"]) == (
            row["product_code"],
            row["version"],
            row["institution_id"],
        )


def test_all_product_evidence_references_resolve() -> None:
    sources = {
        row["source_id"]
        for row in json.loads(
            (SHARD / "source_documents.fragment.json").read_text(encoding="utf-8")
        )["source_documents"]
    }
    evidence_rows = json.loads(
        (SHARD / "evidence_refs.fragment.json").read_text(encoding="utf-8")
    )["evidence_refs"]
    evidence = {row["evidence_ref_id"] for row in evidence_rows}
    assert all(row["source_id"] in sources for row in evidence_rows)

    def refs(value: object) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"source_ref_ids", "evidence_ref_ids"} and isinstance(item, list):
                    found.update(str(ref) for ref in item)
                else:
                    found.update(refs(item))
        elif isinstance(value, list):
            for item in value:
                found.update(refs(item))
        return found

    for path in PUBLISHED_CMA.glob("*/*/v002.json"):
        product = json.loads(path.read_text(encoding="utf-8"))
        assert refs(product) <= evidence | sources


def test_customer_scope_and_scheduled_rates_are_executable() -> None:
    assert _rate("INST-KR-000053-4-0002", 1, customer_scope="INDIVIDUAL") == Decimal("2.60")
    assert _rate("INST-KR-000053-4-0002", 1, customer_scope="CORPORATION") == Decimal("2.50")
    assert _rate("INST-KR-000053-4-0002", 1) is None
    assert _rate(
        "INST-KR-000060-4-CCFC5EEBE0A",
        1,
        customer_scope="INDIVIDUAL",
        as_of="2026-08-30",
    ) == Decimal("2.40")
    assert _rate(
        "INST-KR-000060-4-CCFC5EEBE0A",
        1,
        customer_scope="INDIVIDUAL",
        as_of="2026-08-31",
    ) == Decimal("2.60")


def test_elapsed_day_boundaries_and_reinvestment_reset() -> None:
    champion_expected = {
        15: "2.75",
        16: "2.78",
        60: "2.78",
        61: "2.81",
        90: "2.81",
        91: "2.84",
        150: "2.87",
        151: "2.90",
        180: "2.90",
        181: "2.75",
        360: "2.90",
        361: "2.75",
    }
    for day, expected in champion_expected.items():
        assert _rate("INST-KR-000148-4-0002", day) == Decimal(expected)

    note_expected = {
        1: "2.70",
        7: "2.70",
        8: "2.70",
        89: "2.70",
        90: "3.10",
        179: "3.10",
        180: "3.40",
        269: "3.40",
        270: "3.50",
        364: "3.50",
        365: "3.60",
    }
    for day, expected in note_expected.items():
        assert _rate("INST-KR-000793-4-0002", day) == Decimal(expected)
    assert _rate("INST-KR-000793-4-0002", 12, TermUnit.MONTH) == Decimal("3.60")
    assert _rate("INST-KR-000793-4-0002", 1, TermUnit.YEAR) == Decimal("3.60")


def test_performance_linked_products_never_get_fixed_base_rates() -> None:
    product = _product("INST-KR-000034-4-0003")
    assert product["return_policy"]["return_kind"] == "PERFORMANCE_LINKED"
    for day in (1, 7, 30, 90, 365):
        assert _rate("INST-KR-000034-4-0003", day, customer_scope="INDIVIDUAL") is None


def test_nested_canonical_preferential_rule_is_compiled_for_runtime() -> None:
    product = _product("INST-KR-000793-4-0002")
    sources = {
        row["source_id"]: row
        for row in json.loads(
            (SHARD / "source_documents.fragment.json").read_text(encoding="utf-8")
        )["source_documents"]
    }
    evidence = {
        row["evidence_ref_id"]: row
        for row in json.loads(
            (SHARD / "evidence_refs.fragment.json").read_text(encoding="utf-8")
        )["evidence_refs"]
    }
    institution = _load_institutions(ROOT)[product["institution_id"]]
    adapted = _adapt_product(product, institution, {}, sources, evidence)

    assert len(adapted.preferential_rules) == 1
    assert adapted.preferential_rules[0].reward.value == Decimal("0.10")
