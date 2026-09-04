#!/usr/bin/env python3
"""Publish evidence bridges for legacy direct source references.

Some older normalized products stored a source-document id directly in
``source_ref_ids``.  This correction release preserves those source records
and their locators/excerpts, while adding a first-class evidence row and
pointing immutable product versions at the required evidence -> source chain.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_catalog_gaps_20260904 import (
    audit,
    load_active_registries,
    load_json,
)


NORMALIZED = ROOT / "data/financial_products/normalized"
INDEX_PATH = NORMALIZED / "index.json"
CURRENT_BATCH = "20260904-catalog-data-gaps-01"
BATCH = "20260904-catalog-source-refs-01"
PUBLISHED_AT = "2026-09-04T19:00:00+09:00"
SNAPSHOT_DATE = "2026-09-04"
CURRENT_MANIFEST_PATH = NORMALIZED / "manifests" / f"{CURRENT_BATCH}.json"
MANIFEST_PATH = NORMALIZED / "manifests" / f"{BATCH}.json"
REGISTRY_DIR = NORMALIZED / "registries" / BATCH
SOURCE_PATH = REGISTRY_DIR / "source_documents.json"
EVIDENCE_PATH = REGISTRY_DIR / "evidence_refs.json"
REPORT_DIR = NORMALIZED / "reports" / BATCH
VALIDATION_PATH = REPORT_DIR / "validation_reports.json"
REPORT_PATH = REPORT_DIR / "CATALOG_SOURCE_REF_REPAIR_REPORT.md"
HASH_PATH = REPORT_DIR / "HASH_AUDIT.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def direct_source_ids(
    index: Mapping[str, Any],
    evidence: Mapping[str, Any],
    sources: Mapping[str, Any],
) -> dict[str, set[str]]:
    """Return direct source refs grouped by product, including nested policies."""

    found: dict[str, set[str]] = {}

    def walk(value: Any, product_code: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key == "source_ref_ids" and isinstance(child, list):
                    for ref_id in child:
                        if isinstance(ref_id, str) and ref_id in sources and ref_id not in evidence:
                            found.setdefault(product_code, set()).add(ref_id)
                else:
                    walk(child, product_code)
        elif isinstance(value, list):
            for child in value:
                walk(child, product_code)

    for row in index.get("products", []):
        product = read_json(ROOT / row["path"])
        walk(product, str(row["product_code"]))
    return found


def evidence_bridge(source: Mapping[str, Any], product_code: str) -> dict[str, Any]:
    source_id = str(source["source_id"])
    return {
        "evidence_ref_id": f"EVD-SOURCE-BRIDGE-{source_id}",
        "source_id": source_id,
        "product_code": product_code,
        "captured_at": source.get("retrieved_at") or PUBLISHED_AT,
        "locator": copy.deepcopy(source.get("locator") or {}),
        "source_text": source.get("excerpt") or source.get("official_excerpt") or "",
        "supports": ["legacy_source_record", "product_terms"],
        "evidence_authority": str(source.get("authority") or source.get("source_type") or "UNKNOWN"),
        "migration": {
            "kind": "DIRECT_SOURCE_REFERENCE_BRIDGE",
            "source_ref_id": source_id,
            "preserves_locator_and_excerpt": True,
            "migrated_at": SNAPSHOT_DATE,
        },
    }


def replace_refs(value: Any, replacements: Mapping[str, str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_ref_ids" and isinstance(child, list):
                value[key] = [replacements.get(ref, ref) for ref in child]
            else:
                replace_refs(child, replacements)
    elif isinstance(value, list):
        for child in value:
            replace_refs(child, replacements)


def file_entries(paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    return [
        {"path": path, "sha256": sha256_file(file_path), "size_bytes": file_path.stat().st_size}
        for path, file_path in sorted(paths.items())
    ]


def publish() -> None:
    index = read_json(INDEX_PATH)
    manifest = read_json(CURRENT_MANIFEST_PATH)
    if index.get("manifest_batch") != CURRENT_BATCH:
        raise ValueError(f"unexpected current batch: {index.get('manifest_batch')}")
    if MANIFEST_PATH.exists() or REGISTRY_DIR.exists() or REPORT_DIR.exists():
        raise FileExistsError(f"release already exists: {BATCH}")

    evidence_payload, source_payload = load_active_registries(index)
    evidence_rows = evidence_payload.get("evidence_refs", [])
    source_rows = source_payload.get("source_documents", [])
    original_evidence_count = len(evidence_rows)
    evidence = {row["evidence_ref_id"]: row for row in evidence_rows if row.get("evidence_ref_id")}
    sources = {row["source_id"]: row for row in source_rows if row.get("source_id")}
    grouped = direct_source_ids(index, evidence, sources)
    source_to_evidence: dict[str, str] = {}
    product_bridges: dict[str, list[str]] = {}
    for product_code, source_ids in sorted(grouped.items()):
        for source_id in sorted(source_ids):
            evidence_id = f"EVD-SOURCE-BRIDGE-{source_id}"
            existing = evidence.get(evidence_id)
            if existing is None:
                row = evidence_bridge(sources[source_id], product_code)
                evidence_rows.append(row)
                evidence[evidence_id] = row
            elif existing.get("source_id") != source_id:
                raise ValueError(f"Evidence bridge points at wrong source: {evidence_id}")
            source_to_evidence[source_id] = evidence_id
            product_bridges.setdefault(product_code, []).append(evidence_id)

    if not product_bridges:
        raise ValueError("No direct source references found")

    new_index = copy.deepcopy(index)
    new_rows = {row["product_code"]: row for row in new_index["products"]}
    updated_paths: list[Path] = []
    for row in index["products"]:
        product_code = row["product_code"]
        if product_code not in product_bridges:
            continue
        product = read_json(ROOT / row["path"])
        replace_refs(product, source_to_evidence)
        metadata = product.setdefault("version_metadata", {})
        old_version = int(row["version"])
        product["version"] = old_version + 1
        metadata.update(
            {
                "previous_version": old_version,
                "previous_path": row["path"],
                "previous_sha256": row["sha256"],
                "correction_batch": BATCH,
                "correction_reason": "legacy direct source references bridged through evidence_refs",
                "correction_applied_at": PUBLISHED_AT,
            }
        )
        for binding in product.get("custom_bindings", []):
            if isinstance(binding, dict):
                binding["product_version"] = product["version"]
        target_path = (ROOT / row["path"]).parent / f"v{product['version']:03d}.json"
        if target_path.exists():
            raise FileExistsError(f"target product version already exists: {target_path}")
        write_json(target_path, product)
        updated_paths.append(target_path)
        new_rows[product_code].update(
            {"version": product["version"], "path": relative(target_path), "sha256": sha256_file(target_path)}
        )

    source_payload["source_documents"] = source_rows
    evidence_payload["evidence_refs"] = evidence_rows
    write_json(SOURCE_PATH, source_payload)
    write_json(EVIDENCE_PATH, evidence_payload)
    new_index["index_version"] = int(new_index.get("index_version", 1)) + 1
    new_index["generated_at"] = PUBLISHED_AT
    new_index["manifest_batch"] = BATCH
    write_json(INDEX_PATH, new_index)

    inherited = {
        item["path"]: ROOT / item["path"]
        for item in manifest.get("files", [])
        if item.get("path") and (ROOT / item["path"]).exists()
    }
    for path in updated_paths:
        inherited[relative(path)] = path
    inherited[relative(INDEX_PATH)] = INDEX_PATH
    inherited[relative(SOURCE_PATH)] = SOURCE_PATH
    inherited[relative(EVIDENCE_PATH)] = EVIDENCE_PATH

    new_manifest = copy.deepcopy(manifest)
    new_manifest.update(
        {
            "manifest_version": int(manifest.get("manifest_version", 1)) + 1,
            "published_at": PUBLISHED_AT,
            "previous_manifest": relative(CURRENT_MANIFEST_PATH),
            "correction_batch": BATCH,
            "source_document_registry": relative(SOURCE_PATH),
            "evidence_ref_registry": relative(EVIDENCE_PATH),
            "application_code_changed": False,
            "files": file_entries(inherited),
        }
    )
    new_manifest["counts"] = copy.deepcopy(manifest.get("counts") or {})
    new_manifest["counts"]["evidence_refs"] = len(evidence_rows)
    write_json(MANIFEST_PATH, new_manifest)

    final_audit = audit()
    if final_audit["counts"].get("unresolved_source_ref_products") != 0:
        raise ValueError(f"source refs remain unresolved: {final_audit['counts']}")
    report = {
        "batch": BATCH,
        "publication_status": "PUBLISHED",
        "updated_products": len(updated_paths),
        "updated_product_codes": sorted(product_bridges),
        "new_evidence_refs": len(evidence_rows) - original_evidence_count,
        "bridged_source_ids": sorted(source_to_evidence),
        "final_audit": final_audit,
    }
    write_json(VALIDATION_PATH, report)
    REPORT_PATH.write_text(
        "# 직접 source 참조 evidence bridge 보완\n\n"
        f"- 배치: `{BATCH}`\n- 새 상품 버전: {len(updated_paths)}개\n"
        f"- 새 evidence 참조: {len(source_to_evidence)}개\n"
        "- 기존 source의 locator/excerpt를 보존하고 evidence → source 2단계로 정규화\n",
        encoding="utf-8",
    )
    inherited[relative(VALIDATION_PATH)] = VALIDATION_PATH
    inherited[relative(REPORT_PATH)] = REPORT_PATH
    hash_files = file_entries(inherited)
    write_json(HASH_PATH, {"batch": BATCH, "algorithm": "SHA-256", "self_excluded": True, "files": hash_files})
    new_manifest["hash_audit_file"] = relative(HASH_PATH)
    new_manifest["files"] = [
        *hash_files,
        {"path": relative(HASH_PATH), "sha256": sha256_file(HASH_PATH), "size_bytes": HASH_PATH.stat().st_size},
    ]
    new_manifest["validation_file"] = relative(VALIDATION_PATH)
    new_manifest["normalization_report_file"] = relative(REPORT_PATH)
    write_json(MANIFEST_PATH, new_manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    publish()
