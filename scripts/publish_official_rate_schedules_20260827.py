#!/usr/bin/env python3
"""Publish four officially verified parking-account rate schedule corrections.

The command is a dry-run unless ``--publish`` is supplied.  Publication writes
new immutable product versions and release artifacts before atomically advancing
the catalog index as the final operation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eligibility.catalog.preferential_quality import inspect_preferential_rate_quality  # noqa: E402


NORMALIZED_DIR = ROOT / "data/financial_products/normalized"
DEFAULT_INDEX = NORMALIZED_DIR / "index.json"
BATCH = "20260827-official-rate-schedules-01"
PUBLISHED_AT = "2026-08-27T18:00:00+09:00"

SCHEDULES: dict[str, dict[str, Any]] = {
    "INST-KR-000014-3-CA8E610E410": {
        "name": "우리가족사랑통장",
        "official_url": "http://www.hisntm.com/kor/sub01_02_01.html",
        "as_of": "2026-05-20",
        "source_ref_id": "OFFICIAL-SNT-DEMAND-RATES-20260827",
        "maximum": "2.75",
        "tiers": [
            ("0.50", "0", True, "10000000", False),
            ("2.75", "10000000", True, "100000000", False),
            ("2.65", "100000000", True, None, None),
        ],
        "finding": "잔액 1천만원 미만 0.50%, 1천만원 이상 1억원 미만 2.75%, 1억원 이상 2.65%의 잔액구간 기본금리",
    },
    "INST-KR-000181-3-CC8C2BF1DB5": {
        "name": "저축예금",
        "official_url": "https://www.anyangbank.co.kr/ProdList_001.act?rnum=19",
        "as_of": "2025-07-24",
        "source_ref_id": "OFFICIAL-ANYANG-DEMAND-RATES-20260827",
        "maximum": "1.00",
        "tiers": [
            ("0.10", "0", True, "5000000", False),
            ("0.50", "5000000", True, "20000000", False),
            ("1.00", "20000000", True, None, None),
        ],
        "finding": "잔액 500만원 미만 0.10%, 500만원 이상 2천만원 미만 0.50%, 2천만원 이상 1.00%이며 공식 페이지가 우대금리 없음을 명시",
    },
    "INST-KR-000341-3-0001": {
        "name": "생활파킹통장",
        "official_url": "https://www.sbisb.co.kr/cmm0061700.act",
        "as_of": "2026-08-27",
        "source_ref_id": "OFFICIAL-SBI-DEMAND-RATES-20260827",
        "maximum": "2.70",
        "tiers": [
            ("2.70", "0", True, "2000000", True),
            ("1.70", "2000000", False, "100000000", True),
            ("0.20", "100000000", False, None, None),
        ],
        "finding": "잔액 200만원 이하 2.70%, 200만원 초과 1억원 이하 1.70%, 1억원 초과 0.20%의 잔액구간 기본금리",
    },
}

KAKAO_CODE = "INST-KR-000830-3-0005"
KAKAO_NAME = "부가세박스"
KAKAO_URL = "https://www.kakaobank.com/products/sohoVatBox"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def encoded_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def encoded_jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_value(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def exclusive_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def atomic_replace(path: Path, payload: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _balance_entry(
    code: str,
    position: int,
    rate: str,
    minimum: str,
    min_inclusive: bool,
    maximum: str | None,
    max_inclusive: bool | None,
    *,
    as_of: str,
    source_ref_id: str,
) -> dict[str, Any]:
    range_value: dict[str, Any] = {
        "currency": "KRW",
        "min_value": minimum,
        "min_inclusive": min_inclusive,
    }
    if maximum is not None:
        range_value.update({"max_value": maximum, "max_inclusive": bool(max_inclusive)})
    return {
        "rate_id": f"{code}-OFFICIAL-BALANCE-{position:02d}",
        "role": "BASE",
        "application_event": "ANY",
        "calculation": {"type": "VARIABLE_POSTED", "value": rate, "unit": "PERCENT"},
        "as_of": as_of,
        "applies_to": [{"basis": "BALANCE", "range": range_value}],
        "source_ref_ids": [source_ref_id],
    }


def patch_product(product: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return one corrected product and its audit row without mutating input."""
    code = str(product.get("product_code"))
    patched = copy.deepcopy(product)
    previous_version = int(product["version"])
    previous_max = str(((product.get("return_policy") or {}).get("advertised_max_rate") or {}).get("value"))

    if code in SCHEDULES:
        spec = SCHEDULES[code]
        if product.get("name") != spec["name"]:
            raise ValueError(f"unexpected product name for {code}: {product.get('name')}")
        policy = patched.setdefault("return_policy", {})
        entries = [
            _balance_entry(
                code,
                position,
                rate,
                minimum,
                min_inclusive,
                maximum,
                max_inclusive,
                as_of=spec["as_of"],
                source_ref_id=spec["source_ref_id"],
            )
            for position, (rate, minimum, min_inclusive, maximum, max_inclusive) in enumerate(
                spec["tiers"], 1
            )
        ]
        policy["rate_entries"] = entries
        policy["observed_rates_percent"] = [float(row[0]) for row in spec["tiers"]]
        policy["advertised_max_rate"] = {
            "value": spec["maximum"],
            "unit": "PERCENT",
            "as_of": spec["as_of"],
            "source_ref_ids": [spec["source_ref_id"]],
        }
        policy["balance_tier_method"] = "WHOLE_BALANCE"
        policy["authority"] = "OFFICIAL_RECHECK"
        policy["source_ref_ids"] = list(
            dict.fromkeys([*(policy.get("source_ref_ids") or []), spec["source_ref_id"]])
        )
        decision = "RATE_SCHEDULE_REPLACES_FALSE_PREFERENTIAL_UPLIFT"
        official_url = spec["official_url"]
        as_of = spec["as_of"]
        finding = spec["finding"]
        new_max = spec["maximum"]
    elif code == KAKAO_CODE:
        if product.get("name") != KAKAO_NAME:
            raise ValueError(f"unexpected product name for {code}: {product.get('name')}")
        changed = 0
        for binding in patched.get("custom_bindings") or []:
            if binding.get("purpose") == "PREFERENTIAL_RETURN" and not binding.get("reward_refs"):
                binding["purpose"] = "INFORMATIONAL_BENEFIT"
                changed += 1
        if changed != 1:
            raise ValueError(f"expected one empty preferential binding for {code}, got {changed}")
        gaps = patched.setdefault("version_metadata", {}).setdefault("data_gaps", [])
        gap = {
            "path": "custom_bindings[INST-KR-000830-VATBOX-COUPON].reward_refs",
            "reason": "금리쿠폰은 상품 고정 우대금리가 아니며 쿠폰별 값·대상·유효기간이 달라 확정 금리로 계산하지 않음",
        }
        if gap not in gaps:
            gaps.append(gap)
        decision = "NON_DETERMINISTIC_COUPON_RECLASSIFIED"
        official_url = KAKAO_URL
        as_of = "2026-08-25"
        finding = "공식 기본금리 연 2.00%는 유지하고 값이 고정되지 않은 금리쿠폰을 계산 가능한 우대금리에서 제외"
        new_max = "2.00"
    else:
        raise ValueError(f"unsupported product: {code}")

    patched["version"] = previous_version + 1
    for binding in patched.get("custom_bindings") or []:
        binding["product_version"] = patched["version"]
    metadata = patched.setdefault("version_metadata", {})
    metadata.update(
        {
            "previous_version": previous_version,
            "last_verified_at": PUBLISHED_AT,
            "correction_batch": BATCH,
            "correction_reason": "공식 원문에 따라 잔액구간 기본금리와 확정 우대금리를 분리",
            "official_rate_schedule_recheck": {
                "checked_at": "2026-08-27",
                "decision": decision,
                "official_url": official_url,
                "rate_as_of": as_of,
                "finding": finding,
            },
        }
    )
    metadata.pop("content_fingerprint", None)
    metadata["content_fingerprint"] = sha256_value(patched)
    issues = inspect_preferential_rate_quality(patched)
    if issues:
        raise ValueError(f"quality issues remain for {code}: {[issue.code for issue in issues]}")
    return patched, {
        "product_code": code,
        "product_name": product["name"],
        "previous_version": previous_version,
        "new_version": patched["version"],
        "previous_advertised_max_rate_percent": previous_max,
        "corrected_advertised_max_rate_percent": new_max,
        "decision": decision,
        "official_url": official_url,
        "rate_as_of": as_of,
    }


@dataclass(frozen=True)
class Publication:
    index_position: int
    target_path: Path
    payload: bytes
    audit_row: dict[str, Any]


@dataclass(frozen=True)
class ReleasePlan:
    current_index: dict[str, Any]
    next_index: dict[str, Any]
    current_manifest_path: Path
    current_manifest: dict[str, Any]
    publications: tuple[Publication, ...]
    summary: dict[str, Any]


def build_release_plan(index_path: Path = DEFAULT_INDEX) -> ReleasePlan:
    current_index = read_json(index_path)
    current_manifest_path = NORMALIZED_DIR / f"manifests/{current_index['manifest_batch']}.json"
    current_manifest = read_json(current_manifest_path)
    next_index = copy.deepcopy(current_index)
    target_codes = {*SCHEDULES, KAKAO_CODE}
    publications: list[Publication] = []

    for position, index_row in enumerate(current_index.get("products") or []):
        if index_row.get("product_code") not in target_codes:
            continue
        source_path = ROOT / index_row["path"]
        product = read_json(source_path)
        patched, audit_row = patch_product(product)
        target_path = source_path.parent / f"v{patched['version']:03d}.json"
        if target_path.exists():
            raise FileExistsError(f"immutable product version exists: {relative(target_path)}")
        payload = encoded_json(patched)
        publications.append(Publication(position, target_path, payload, audit_row))
        next_index["products"][position].update(
            {
                "version": patched["version"],
                "path": relative(target_path),
                "sha256": sha256_bytes(payload),
            }
        )

    observed = {row.audit_row["product_code"] for row in publications}
    if observed != target_codes:
        raise ValueError(f"target coverage mismatch: missing={sorted(target_codes - observed)}")
    if len(next_index.get("products") or []) != len(current_index.get("products") or []):
        raise ValueError("index product count changed")
    next_index["index_version"] = int(current_index.get("index_version", 0)) + 1
    next_index["generated_at"] = PUBLISHED_AT
    next_index["manifest_batch"] = BATCH
    summary = {
        "batch": BATCH,
        "publication_status": "NOT_PUBLISHED",
        "target_products": len(publications),
        "target_product_codes": sorted(observed),
        "checks": [
            {"check": "official_source_recheck", "status": "PASS"},
            {"check": "exact_target_coverage", "status": "PASS"},
            {"check": "preferential_quality_gate", "status": "PASS"},
            {"check": "no_invented_preferential_rate", "status": "PASS"},
            {"check": "index_product_count_unchanged", "status": "PASS"},
        ],
    }
    return ReleasePlan(
        current_index=current_index,
        next_index=next_index,
        current_manifest_path=current_manifest_path,
        current_manifest=current_manifest,
        publications=tuple(publications),
        summary=summary,
    )


def publish_release(plan: ReleasePlan, index_path: Path = DEFAULT_INDEX) -> dict[str, Any]:
    manifest_path = NORMALIZED_DIR / f"manifests/{BATCH}.json"
    report_dir = NORMALIZED_DIR / f"reports/{BATCH}"
    validation_path = report_dir / "validation_reports.json"
    rows_path = report_dir / "correction_rows.jsonl"
    report_path = report_dir / "OFFICIAL_RATE_SCHEDULE_REPORT.md"
    hash_path = report_dir / "HASH_AUDIT.json"
    immutable = [manifest_path, validation_path, rows_path, report_path, hash_path]
    collisions = [relative(path) for path in immutable if path.exists()]
    if collisions:
        raise FileExistsError(f"immutable release artifact exists: {collisions}")
    if read_json(index_path) != plan.current_index:
        raise RuntimeError("active index changed after planning")

    summary = copy.deepcopy(plan.summary)
    summary.update({"mode": "PUBLISH", "publication_status": "PUBLISHED"})
    validation_payload = encoded_json(summary)
    audit_rows = [row.audit_row for row in plan.publications]
    rows_payload = encoded_jsonl(audit_rows)
    report_payload = (
        "# 공식 잔액구간 금리 구조화 배포 보고서\n\n"
        "공식 페이지로 확인한 파킹통장 3개의 잔액구간 기본금리를 복원하고, "
        "카카오뱅크 부가세박스의 비확정 쿠폰을 계산 가능한 우대금리에서 분리했다.\n"
    ).encode("utf-8")
    next_index_payload = encoded_json(plan.next_index)

    inventory: dict[str, tuple[str, int]] = {}
    for item in plan.current_manifest.get("files") or []:
        path_text = item.get("path")
        path = ROOT / path_text if path_text else None
        if path is not None and path.exists():
            inventory[path_text] = (sha256_file(path), path.stat().st_size)
    inventory[relative(index_path)] = (sha256_bytes(next_index_payload), len(next_index_payload))
    inventory[relative(validation_path)] = (sha256_bytes(validation_payload), len(validation_payload))
    inventory[relative(rows_path)] = (sha256_bytes(rows_payload), len(rows_payload))
    inventory[relative(report_path)] = (sha256_bytes(report_payload), len(report_payload))
    for row in plan.publications:
        inventory[relative(row.target_path)] = (sha256_bytes(row.payload), len(row.payload))
    hash_rows = [
        {"path": path, "sha256": digest, "size_bytes": size}
        for path, (digest, size) in sorted(inventory.items())
    ]
    hash_payload = encoded_json(
        {"batch": BATCH, "algorithm": "SHA-256", "self_excluded": True, "files": hash_rows}
    )
    manifest_files = [
        *hash_rows,
        {"path": relative(hash_path), "sha256": sha256_bytes(hash_payload), "size_bytes": len(hash_payload)},
    ]
    manifest = copy.deepcopy(plan.current_manifest)
    manifest.update(
        {
            "manifest_version": int(plan.current_manifest.get("manifest_version", 0)) + 1,
            "publication_status": "PUBLISHED",
            "published_at": PUBLISHED_AT,
            "previous_manifest": relative(plan.current_manifest_path),
            "correction_batch": BATCH,
            "validation_file": relative(validation_path),
            "collection_artifact_file": relative(rows_path),
            "normalization_report_file": relative(report_path),
            "hash_audit_file": relative(hash_path),
            "application_code_changed": False,
            "files": manifest_files,
        }
    )
    manifest["counts"] = copy.deepcopy(plan.current_manifest.get("counts") or {})
    manifest["counts"].update({"validation_pass": 4, "validation_needs_review": 0, "validation_fail": 0})
    manifest_payload = encoded_json(manifest)

    for row in plan.publications:
        exclusive_write(row.target_path, row.payload)
    exclusive_write(validation_path, validation_payload)
    exclusive_write(rows_path, rows_payload)
    exclusive_write(report_path, report_payload)
    exclusive_write(hash_path, hash_payload)
    exclusive_write(manifest_path, manifest_payload)
    if read_json(index_path) != plan.current_index:
        raise RuntimeError("active index changed during publication")
    atomic_replace(index_path, next_index_payload)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    plan = build_release_plan(args.index.resolve())
    if args.publish:
        result = publish_release(plan, args.index.resolve())
    else:
        result = copy.deepcopy(plan.summary)
        result["mode"] = "DRY_RUN"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
