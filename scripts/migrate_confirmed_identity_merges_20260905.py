#!/usr/bin/env python3
"""Allow-list migration for the 11 user-confirmed duplicate product pairs."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BATCH = "20260905-confirmed-identity-merge-01"
NOW = "2026-09-05T00:00:00+09:00"
MERGE_EVIDENCE_ID = "EVD-IDENTITY-MERGE-20260905-CONFIRMED"
PAIRS = (
    ("INST-KR-000830-1-0001", "INST-KR-000830-1-C98752E9523"),
    ("INST-KR-000830-1-0002", "INST-KR-000830-1-CA7BF0C5E7B"),
    ("INST-KR-000830-1-0003", "INST-KR-000830-1-CD19BB23CD9"),
    ("INST-KR-000830-1-0004", "INST-KR-000830-1-CAAB9B11CC0"),
    ("INST-KR-000830-1-0005", "INST-KR-000830-1-C2AFF66ADCD"),
    ("INST-KR-000830-2-0001", "INST-KR-000830-2-C0A3C28D4F5"),
    ("INST-KR-000830-3-0004", "INST-KR-000830-3-C90225C0EC4"),
    ("INST-KR-000830-3-0005", "INST-KR-000830-3-C67C2196311"),
    ("INST-KR-000099-4-0001", "INST-KR-000099-4-C0D71CA0183"),
    ("INST-KR-000099-4-0002", "INST-KR-000099-4-C7C7C5DB97E"),
    ("INST-KR-000647-4-0001", "INST-KR-000647-4-C9A274A26D1"),
)
DISPLAY_NAMES = {
    "INST-KR-000830-1-0001": "카카오뱅크 자유적금",
    "INST-KR-000830-1-0002": "카카오뱅크 26주적금",
    "INST-KR-000830-1-0003": "카카오뱅크 한달적금",
    "INST-KR-000830-1-0004": "카카오뱅크 우리아이적금",
    "INST-KR-000830-1-0005": "카카오뱅크 청년미래적금",
    "INST-KR-000830-2-0001": "카카오뱅크 정기예금",
    "INST-KR-000830-3-0004": "카카오뱅크 세이프박스",
    "INST-KR-000830-3-0005": "카카오뱅크 부가세박스",
    "INST-KR-000099-4-0001": "NH CMA (RP형)",
    "INST-KR-000099-4-0002": "NH CMA (발행어음형)",
    "INST-KR-000647-4-0001": "CAPE 오르다 CMA (RP형)",
}
RUNTIME_ID_KEYS = frozenset({
    "product_code", "rate_id", "rule_id", "relation_id", "event_id", "event_ref",
    "counter_event_ref", "fulfillment_id", "fulfillment_ref", "fact_key",
    "member_rule_ids", "members", "applies_to_rule_ids", "action_id",
})


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def latest(code: str) -> tuple[Path, dict[str, Any]]:
    paths = sorted((ROOT / "data/financial_products/normalized/products").glob(f"**/{code}/v*.json"))
    if not paths:
        raise SystemExit(f"missing product: {code}")
    return paths[-1], read(paths[-1])


def score(value: Any) -> int:
    text = str(value or "").upper()
    if "OFFICIAL" in text or "ISSUER" in text:
        return 4
    if any(x in text for x in ("USER", "HUMAN", "CONFIRM")):
        return 3
    if "NAVER" in text:
        return 2
    return 1


def source_score(meta: dict[str, Any]) -> int:
    return max(score(meta.get("source_priority")), score(meta.get("data_authority")))


def present(value: Any) -> bool:
    return value not in (None, "", [], {})


def fill(primary: Any, secondary: Any) -> Any:
    if isinstance(primary, dict) and isinstance(secondary, dict):
        result = copy.deepcopy(primary)
        for key, value in secondary.items():
            if key not in result or not present(result[key]):
                result[key] = copy.deepcopy(value)
            elif isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = fill(result[key], value)
        return result
    return copy.deepcopy(secondary) if not present(primary) and present(secondary) else copy.deepcopy(primary)


def remap_runtime_ids(value: Any, alias: str, canonical: str, *, owned: bool = False) -> Any:
    """Remap only executable identifier fields; leave provenance text/refs intact."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            child_owned = owned or key in RUNTIME_ID_KEYS
            result[key] = remap_runtime_ids(item, alias, canonical, owned=child_owned)
        return result
    if isinstance(value, list):
        return [remap_runtime_ids(item, alias, canonical, owned=owned) for item in value]
    if owned and isinstance(value, str):
        return value.replace(alias, canonical)
    return value


def merge(canonical: dict[str, Any], alias: dict[str, Any], old_path: Path, alias_path: Path) -> dict[str, Any]:
    cm, am = canonical.get("version_metadata") or {}, alias.get("version_metadata") or {}
    result = fill(alias, canonical) if source_score(am) > source_score(cm) else fill(canonical, alias)
    result.update({key: canonical[key] for key in ("product_code", "institution_id", "product_family")})
    result["name"] = DISPLAY_NAMES[canonical["product_code"]]
    result["version"] = max(int(canonical["version"]), int(alias["version"])) + 1
    result = remap_runtime_ids(result, alias["product_code"], canonical["product_code"])
    result["source_ref_ids"] = list(dict.fromkeys((canonical.get("source_ref_ids") or []) + (alias.get("source_ref_ids") or []) + [MERGE_EVIDENCE_ID]))
    metadata = copy.deepcopy(result.get("version_metadata") or {})
    metadata["source_ref_ids"] = list(dict.fromkeys((cm.get("source_ref_ids") or []) + (am.get("source_ref_ids") or []) + [MERGE_EVIDENCE_ID]))
    metadata.update({
        "correction_batch": BATCH, "correction_applied_at": NOW,
        "correction_reason": "사용자 확인을 받은 동일 상업상품의 기관명 표기 차이를 병합",
        "previous_version": int(canonical["version"]), "previous_path": str(old_path.relative_to(ROOT)),
        "previous_sha256": digest(old_path),
        "identity_audit": {"reviewed": True, "audit_revision": BATCH, "status": "PREFERRED_CANONICAL_FOR_DUPLICATE_SOURCE", "equivalence_group_id": f"CONFIRMED-IDENTITY-{canonical['product_code']}", "preferred_product_code": canonical["product_code"], "related_input_product_codes": [alias["product_code"]], "relation": "USER_CONFIRMED_SAME_COMMERCIAL_PRODUCT"},
        "identity_merge": {"confirmed_by": "USER", "alias_product_code": alias["product_code"], "alias_version": int(alias["version"]), "canonical_source_score": source_score(cm), "alias_source_score": source_score(am), "source_precedence": "ISSUER_OFFICIAL > USER_CONFIRMED > NAVER > INTERNAL", "merged_fields_policy": "winner_values_preserved_missing_values_filled"},
    })
    result["version_metadata"] = metadata
    return result


def merge_snapshot(path: Path, kind: str) -> Path:
    payload = read(path); records = payload.get("records") or []; by_code = {row.get("product_code"): row for row in records}; aliases = {alias for _, alias in PAIRS}
    for canonical_code, alias_code in PAIRS:
        canonical, alias = by_code.get(canonical_code), by_code.get(alias_code)
        if canonical is None and alias is None: continue
        if canonical is None: canonical = {"product_code": canonical_code}; records.append(canonical)
        if alias is not None:
            merged = fill(alias, canonical) if score(alias.get("provider")) > score(canonical.get("provider")) else fill(canonical, alias)
            merged["product_code"] = canonical_code; canonical.clear(); canonical.update(merged)
    payload["records"] = sorted((row for row in records if row.get("product_code") not in aliases), key=lambda row: row.get("product_code", ""))
    if kind == "listing_snapshots":
        payload["based_on_snapshot_id"] = payload.get("snapshot_id")
        payload["snapshot_id"] = BATCH
        payload["captured_at"] = NOW
    elif kind == "condition_snapshots":
        payload["based_on_generated_at"] = payload.get("generated_at")
        payload["correction_batch"] = BATCH
        payload["generated_at"] = NOW
    payload["record_count"] = len(payload["records"])
    out = ROOT / "data/financial_products/normalized" / kind / f"{BATCH}.json"; write(out, payload); return out


def main() -> None:
    old_manifest_path = ROOT / "data/financial_products/normalized/manifests/20260904-nh-deposit-official-terms-01.json"; old_manifest = read(old_manifest_path)
    index_path = ROOT / "data/financial_products/normalized/index.json"; index = read(index_path); by_code = {row["product_code"]: row for row in index["products"]}
    if any(code not in by_code for pair in PAIRS for code in pair): raise SystemExit("all confirmed IDs must be active before migration")
    new_rows = {}
    for canonical_code, alias_code in PAIRS:
        canonical_path, canonical = latest(canonical_code); alias_path, alias = latest(alias_code); merged = merge(canonical, alias, canonical_path, alias_path)
        new_path = canonical_path.parent / f"v{merged['version']:03d}.json"
        if new_path.exists(): raise SystemExit(f"refusing to overwrite existing version: {new_path}")
        write(new_path, merged); row = copy.deepcopy(by_code[canonical_code]); row.update(version=merged["version"], path=str(new_path.relative_to(ROOT)), sha256=digest(new_path)); new_rows[canonical_code] = row
    aliases = {alias for _, alias in PAIRS}; index["products"] = [new_rows.get(row["product_code"], row) for row in index["products"] if row["product_code"] not in aliases]
    index.update({"product_count": len(index["products"]), "index_version": int(index.get("index_version", 0)) + 1, "manifest_batch": BATCH, "generated_at": NOW})
    index["aliases"] = [{"alias_product_code": alias, "canonical_product_code": canonical, "confirmed": True, "relation": "USER_CONFIRMED_SAME_COMMERCIAL_PRODUCT", "historical_latest_version": latest(alias)[1]["version"], "historical_latest_path": str(latest(alias)[0].relative_to(ROOT)), "resolved_at": NOW} for canonical, alias in PAIRS]; write(index_path, index)
    listing = merge_snapshot(ROOT / old_manifest["listing_snapshot_file"], "listing_snapshots"); conditions = merge_snapshot(ROOT / old_manifest["condition_snapshot_file"], "condition_snapshots")
    source_path = ROOT / old_manifest["source_document_registry"]; evidence_path = ROOT / old_manifest["evidence_ref_registry"]; source = read(source_path); evidence = read(evidence_path); source_id = "SRC-IDENTITY-MERGE-20260905-CONFIRMED"; codes = [code for pair in PAIRS for code in pair]
    source["source_documents"].append({"source_id": source_id, "title": "사용자 확인 확정 중복 11쌍 병합 기록", "document_type": "IDENTITY_MERGE_RECORD", "authority": "USER_CONFIRMED", "source_system": "CATALOG_MIGRATION", "captured_at": NOW, "product_codes": codes, "excerpt": "카카오뱅크 8쌍, NH투자증권 CMA 2쌍, 케이프투자증권 CMA 1쌍은 동일 상업상품으로 사용자 확인됨."})
    evidence["evidence_refs"].append({"evidence_ref_id": MERGE_EVIDENCE_ID, "source_id": source_id, "product_codes": codes, "captured_at": NOW, "source_text": "사용자 확인: 지정된 11쌍은 중복이며 numeric ID를 canonical로 유지하고 hash ID는 alias로 보존.", "supports": ["identity", "alias_resolution", "catalog_listing"], "status": "VERIFIED"})
    new_source = ROOT / "data/financial_products/normalized/registries" / BATCH / "source_documents.json"; new_evidence = ROOT / "data/financial_products/normalized/registries" / BATCH / "evidence_refs.json"; write(new_source, source); write(new_evidence, evidence)
    files = {item["path"]: item for item in old_manifest["files"]}
    for row in new_rows.values(): files[row["path"]] = {"path": row["path"], "sha256": row["sha256"], "size_bytes": (ROOT / row["path"]).stat().st_size}
    for path in (index_path, listing, conditions, new_source, new_evidence): files[str(path.relative_to(ROOT))] = {"path": str(path.relative_to(ROOT)), "sha256": digest(path), "size_bytes": path.stat().st_size}
    manifest = copy.deepcopy(old_manifest); manifest.update({"manifest_version": int(old_manifest.get("manifest_version", 11)) + 1, "published_at": NOW, "correction_batch": BATCH, "previous_manifest": str(old_manifest_path.relative_to(ROOT)), "source_document_registry": str(new_source.relative_to(ROOT)), "evidence_ref_registry": str(new_evidence.relative_to(ROOT)), "listing_snapshot_file": str(listing.relative_to(ROOT)), "condition_snapshot_file": str(conditions.relative_to(ROOT)), "files": sorted(files.values(), key=lambda row: row["path"]), "identity_aliases": index["aliases"]}); manifest["counts"]["total_products"] -= len(PAIRS); manifest["counts"]["by_sale_status"]["ON_SALE"] -= len(PAIRS); manifest["counts"]["source_documents"] = len(source["source_documents"]); manifest["counts"]["evidence_refs"] = len(evidence["evidence_refs"])
    write(ROOT / "data/financial_products/normalized/manifests" / f"{BATCH}.json", manifest); print(f"published {len(PAIRS)} confirmed identity merges")


if __name__ == "__main__": main()
