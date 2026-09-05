"""Publish the reviewed Kakao term-domain correction without rewriting history.

Only the typed term domain changes. Existing source text, rate entries, aliases
and prior product/manifest files are preserved; active index hashes are renewed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = "INST-KR-000830-2-0001"
BATCH = "20260905-recommendation-term-domain-01"
STAMP = "2026-09-05T16:00:00+09:00"


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publish(root: Path = ROOT) -> None:
    index_path = root / "data/financial_products/normalized/index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("manifest_batch") == BATCH:
        return
    if index.get("manifest_batch") != "20260905-confirmed-identity-merge-01":
        raise ValueError("Unexpected publication base; review newer catalog changes before publishing")
    row = next(item for item in index["products"] if item["product_code"] == CODE)
    old_path = root / row["path"]
    if row["version"] != 5 or digest(old_path) != row["sha256"]:
        raise ValueError("Unexpected Kakao product version or content hash")
    product = json.loads(old_path.read_text(encoding="utf-8"))
    term = product["term_policy"]
    if term.get("source_text") != "1개월 ~ 36개월 (월, 일단위로 지정가능)" or (term.get("min_value"), term.get("max_value")) != (1, 36):
        raise ValueError("Source-backed continuous term range is not present")
    # Keep the original JSON untouched, including its former discrete values.
    old_relative = row["path"]
    product["version"] = 6
    term["kind"] = "RANGE"
    term.pop("allowed_values", None)
    term.pop("representative_selection_status", None)
    term.pop("representative_selection_basis", None)
    term["selection_units"] = ["MONTH", "DAY"]
    product["version_metadata"].update({
        "previous_version": 5, "supersedes_version": 5,
        "previous_path": old_relative, "previous_sha256": digest(old_path),
        "correction_batch": BATCH, "correction_applied_at": STAMP,
        "correction_reason": "보존된 공식 1~36개월 범위를 계약기간으로 복원; 금리표 구간 경계를 선택 가능한 기간 목록으로 오인한 DISCRETE 값 제거",
    })
    for binding in product.get("custom_bindings", []):
        binding["product_version"] = 6
    new_path = old_path.with_name("v006.json")
    if new_path.exists():
        raise ValueError("Correction product version already exists; never overwrite a release")
    write_json(new_path, product)
    previous_manifest = f"data/financial_products/normalized/manifests/{index['manifest_batch']}.json"
    manifest = deepcopy(json.loads((root / previous_manifest).read_text(encoding="utf-8")))
    row.update({"version": 6, "path": new_path.relative_to(root).as_posix(), "sha256": digest(new_path)})
    index.update({"index_version": index["index_version"] + 1, "manifest_batch": BATCH, "generated_at": STAMP})
    write_json(index_path, index)
    report_dir = root / f"data/financial_products/normalized/reports/{BATCH}"
    validation = report_dir / "validation_reports.json"
    write_json(validation, {
        "status": "VALID", "scope": "KAKAO_TERM_DOMAIN_CORRECTION", "product_code": CODE,
        "previous_version": 5, "published_version": 6,
        "checks": {"source_range_preserved": True, "rates_unchanged": True, "historical_product_unchanged": True},
        "source_ref_ids": term["source_ref_ids"],
    })
    report = report_dir / "TERM_DOMAIN_CORRECTION.md"
    report.write_text("# Kakao deposit term-domain correction\n\n"
        "The active v005 record already preserves the official 1–36 month range, selectable by month/day. "
        "Its stale DISCRETE rate-table breakpoints contradicted that range and rejected EXACT 12/24 month searches. "
        "v006 restores RANGE, preserves all rate/source/identity data, and leaves v005 immutable. "
        "The new manifest inherits registries and refreshes active product/index hashes.\n", encoding="utf-8")
    files = {item["path"]: dict(item) for item in manifest["files"]}
    files.pop(old_relative, None)
    for path in (new_path, index_path, validation, report):
        files[path.relative_to(root).as_posix()] = {"path": path.relative_to(root).as_posix(), "sha256": digest(path), "size_bytes": path.stat().st_size}
    manifest.update({
        "manifest_version": manifest["manifest_version"] + 1, "published_at": STAMP,
        "correction_batch": BATCH, "previous_manifest": previous_manifest,
        "application_code_changed": True,
        "validation_file": validation.relative_to(root).as_posix(),
        "normalization_report_file": report.relative_to(root).as_posix(),
        "files": [files[key] for key in sorted(files)],
    })
    manifest.pop("hash_audit_file", None)
    manifest_path = root / f"data/financial_products/normalized/manifests/{BATCH}.json"
    if manifest_path.exists():
        raise ValueError("Correction manifest already exists")
    write_json(manifest_path, manifest)


if __name__ == "__main__":
    publish()
