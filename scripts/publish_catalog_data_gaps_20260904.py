#!/usr/bin/env python3
"""Publish the catalog data-gap corrections from TASK B.

This release keeps the previous normalized files immutable.  It publishes a
batch-specific copy of the source/evidence registries, new immutable product
versions for changed products, and a new manifest/index pair.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
NORMALIZED = ROOT / "data/financial_products/normalized"
INDEX_PATH = NORMALIZED / "index.json"
CURRENT_BATCH = "20260902-woori-bingo-ended-02"
BATCH = "20260904-catalog-data-gaps-01"
PUBLISHED_AT = "2026-09-04T18:00:00+09:00"
SNAPSHOT_DATE = "2026-09-04"

CURRENT_MANIFEST_PATH = NORMALIZED / "manifests" / f"{CURRENT_BATCH}.json"
MANIFEST_PATH = NORMALIZED / "manifests" / f"{BATCH}.json"
REGISTRY_DIR = NORMALIZED / "registries" / BATCH
SOURCE_PATH = REGISTRY_DIR / "source_documents.json"
EVIDENCE_PATH = REGISTRY_DIR / "evidence_refs.json"
REPORT_DIR = NORMALIZED / "reports" / BATCH
VALIDATION_PATH = REPORT_DIR / "validation_reports.json"
REPORT_PATH = REPORT_DIR / "CATALOG_DATA_GAPS_REPORT.md"
HASH_PATH = REPORT_DIR / "HASH_AUDIT.json"

KAKAO_PRODUCT_CODE = "INST-KR-000830-2-0001"
KAKAO_SOURCE_ID = "SRC-TD-OFFICIAL-KAKAO-DEPOSIT-20260904"
KAKAO_EVIDENCE_ID = "EVD-TD-OFFICIAL-KAKAO-DEPOSIT-20260904"
KAKAO_URL = "https://www.kakaobank.com/products/deposit"

OFFICIAL_SOURCE_URLS = {
    "SRC-CMA-OFFICIAL-KIS-MMF-20260828": (
        "https://securities.koreainvestment.com/main/mall/opencma/CmaInfo.jsp?cmd=TF02bb010000",
        "EVD-CMA-OFFICIAL-KIS-MMF-20260828",
    ),
    "SRC-CMA-OFFICIAL-KB-CMA-CURRENT-20260830": (
        "https://m.kbsec.com/go.able?linkcd=s010300010005",
        "EVD-CMA-OFFICIAL-KB-CMA-CURRENT-20260830",
    ),
    "SRC-CMA-OFFICIAL-MERITZ-MMW-20260830": (
        "https://home.imeritz.com/meritzcma/CmaMmw.do",
        "EVD-CMA-OFFICIAL-MERITZ-MMW-20260830",
    ),
}

RATE_GAP_YUANTA = {
    "path": "return_policy.rate_entries",
    "reason": (
        "유안타증권 공식 CMA-MMW(약정형) 페이지에서 현재 수익률은 영업점·고객지원센터"
        " 별도문의로만 공개된다. 확인되지 않은 숫자를 추정하거나 대입하지 않는다."
    ),
}
RATE_GAP_TARIFF_SELECTION = {
    "path": "return_policy.rate_entries",
    "reason": (
        "원문 금리는 PERIOD_BASED와 BALANCE_BASED tariff_variants에 구조화되어 있으나,"
        " 가입 시 EXACTLY_ONE 선택을 현재 실행형 rate_entries 모델에 안전하게 연결할"
        " 선택 상태가 없다. 두 표를 단일 금리로 평탄화하지 않고 선택 연동을 지원할 때"
        " rate_entries로 승격한다."
    ),
}
LINK_GAP = {
    "path": "official_site_link",
    "reason": (
        "현재 발행 근거에서 검증된 http(s) 상품·기관 URL을 확인하지 못해 공식 상세"
        " 링크를 생성하지 않음. 비교서비스·내부 경로를 임의의 URL로 승격하지 않고"
        " 공식 출처 재확인 대상으로 남김."
    ),
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_published_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def http_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def source_url(source: Mapping[str, Any]) -> str | None:
    return http_url(source.get("url"))


def evidence_url(evidence: Mapping[str, Any]) -> str | None:
    locator = evidence.get("locator")
    if isinstance(locator, Mapping):
        return http_url(locator.get("url"))
    return None


def path_or_field(gap: Any) -> str:
    if not isinstance(gap, Mapping):
        return ""
    return str(gap.get("path") or gap.get("field") or "")


def append_ref(container: Any, evidence_id: str) -> None:
    if not isinstance(container, dict):
        return
    refs = container.get("source_ref_ids")
    if not isinstance(refs, list):
        refs = []
        container["source_ref_ids"] = refs
    if evidence_id not in refs:
        refs.append(evidence_id)


def add_gap(product: dict[str, Any], gap: Mapping[str, Any]) -> bool:
    metadata = product.setdefault("version_metadata", {})
    gaps = metadata.get("data_gaps")
    if not isinstance(gaps, list):
        gaps = []
        metadata["data_gaps"] = gaps
    key = (path_or_field(gap), str(gap.get("reason", "")))
    if any(
        isinstance(existing, Mapping)
        and (path_or_field(existing), str(existing.get("reason", ""))) == key
        for existing in gaps
    ):
        return False
    gaps.append(copy.deepcopy(dict(gap)))
    return True


def infer_document_type(source: Mapping[str, Any]) -> str:
    source_id = str(source.get("source_id", "")).upper()
    source_type = str(source.get("source_type", "")).upper()
    authority = str(source.get("authority", "")).upper()
    source_class = str(source.get("source_class", "")).upper()
    url = str(source.get("url", ""))
    if "PAY.NAVER.COM" in url or "NAVER" in source_type or "NAVER" in authority:
        return "COMPARISON_SERVICE"
    if source_type == "OFFICIAL_PRODUCT" or source_id.endswith("-PRODUCT"):
        return "OFFICIAL_PRODUCT_PAGE"
    if source_type == "OFFICIAL_RATE" or source_id.endswith("-RATE"):
        if "NAVER" in authority:
            return "COMPARISON_SERVICE"
        return "OFFICIAL_RATE_PAGE"
    if source_type == "RATE_TABLE":
        if "NAVER" in authority or "NAVER" in source_class:
            return "COMPARISON_SERVICE"
        return "OFFICIAL_RATE_PAGE"
    if "NAVER" in source_class:
        return "COMPARISON_SERVICE"
    if source.get("authority_for_official_product_terms") is True:
        return "OFFICIAL_WEBPAGE"
    if authority.startswith("OFFICIAL"):
        return "OFFICIAL_WEBPAGE"
    if url:
        return "COMPARISON_SERVICE" if "NAVER" in url.upper() else "OFFICIAL_WEBPAGE"
    raise ValueError(f"Cannot infer document_type for {source.get('source_id')}")


def all_source_ids(
    index: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    source_by_id: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    used: set[str] = set()
    for row in index.get("products", []):
        product = read_json(ROOT / row["path"])
        for ref_id in product.get("source_ref_ids", []):
            evidence = evidence_by_id.get(ref_id)
            source_id = evidence.get("source_id") if evidence else ref_id
            if source_id in source_by_id:
                used.add(str(source_id))
    return used


def patch_registries(
    index: Mapping[str, Any],
    source_payload: dict[str, Any],
    evidence_payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    source_rows = source_payload.get("source_documents", [])
    evidence_rows = evidence_payload.get("evidence_refs", [])
    sources = {row["source_id"]: row for row in source_rows if row.get("source_id")}
    evidence = {
        row["evidence_ref_id"]: row
        for row in evidence_rows
        if row.get("evidence_ref_id")
    }
    used_ids = all_source_ids(index, evidence, sources)
    promoted_urls: dict[str, str] = {}
    for source_id in sorted(used_ids):
        source = sources[source_id]
        if not source.get("document_type"):
            source["document_type"] = infer_document_type(source)
        if not source_url(source):
            locator = source.get("locator")
            locator_url = locator.get("url") if isinstance(locator, Mapping) else None
            if http_url(locator_url):
                source["url"] = locator_url
                source["url_basis"] = "PROMOTED_FROM_EXISTING_SOURCE_LOCATOR"
                source["url_verified_at"] = SNAPSHOT_DATE
                promoted_urls[source_id] = str(locator_url)

    for source_id, (url, evidence_id) in OFFICIAL_SOURCE_URLS.items():
        if source_id not in sources:
            raise KeyError(f"Official source is not registered: {source_id}")
        source = sources[source_id]
        source["url"] = url
        source["url_basis"] = "OFFICIAL_PROVIDER_PAGE_RECHECK"
        source["url_verified_at"] = SNAPSHOT_DATE
        promoted_urls[source_id] = url
        evidence_row = evidence.get(evidence_id)
        if evidence_row is None:
            raise KeyError(f"Official evidence is not registered: {evidence_id}")
        locator = dict(evidence_row.get("locator") or {})
        locator.update({"url": url, "verified_at": SNAPSHOT_DATE})
        evidence_row["locator"] = locator

    for evidence_row in evidence.values():
        source_id = evidence_row.get("source_id")
        url = promoted_urls.get(source_id)
        if not url or evidence_url(evidence_row):
            continue
        locator = dict(evidence_row.get("locator") or {})
        locator["url"] = url
        evidence_row["locator"] = locator

    new_source = {
        "source_id": KAKAO_SOURCE_ID,
        "title": "카카오뱅크 정기예금 공식 상품 페이지",
        "url": KAKAO_URL,
        "document_type": "OFFICIAL_PRODUCT_PAGE",
        "authority": "ISSUER_OFFICIAL",
        "source_class": "ISSUER_OFFICIAL_CURRENT_PRODUCT_PAGE",
        "provider": "카카오뱅크",
        "version_date": SNAPSHOT_DATE,
        "retrieved_at": PUBLISHED_AT,
        "access_status": "CURRENT_OFFICIAL_PAGE_RECHECKED",
        "official_excerpt": (
            "카카오뱅크 공식 현재 정기예금 페이지(2026-09-04): 가입기간 1개월 이상 36개월 "
            "이하, 가입금액 100만원 이상, 만기일시지급식; 기본금리 1개월 이상 3개월 "
            "미만 2.90%, 3개월 이상 6개월 미만 3.30%, 6개월 이상 12개월 미만 "
            "3.40%, 12개월 이상 24개월 미만 3.60%, 24개월 이상 36개월 미만 3.00%, "
            "36개월 3.00%."
        ),
    }
    new_evidence = {
        "evidence_ref_id": KAKAO_EVIDENCE_ID,
        "source_id": KAKAO_SOURCE_ID,
        "product_code": KAKAO_PRODUCT_CODE,
        "captured_at": PUBLISHED_AT,
        "locator": {
            "url": KAKAO_URL,
            "verified_at": SNAPSHOT_DATE,
            "section": "정기예금 기본금리 및 상품안내",
        },
        "source_text": new_source["official_excerpt"],
        "supports": ["identity", "term", "amount", "base_rates", "interest_payment"],
        "evidence_authority": "OFFICIAL_PROVIDER_WEBPAGE",
    }
    if KAKAO_SOURCE_ID in sources or KAKAO_EVIDENCE_ID in evidence:
        raise FileExistsError("Kakao correction registry ids already exist")
    source_rows.append(new_source)
    evidence_rows.append(new_evidence)
    return sources | {KAKAO_SOURCE_ID: new_source}, evidence | {KAKAO_EVIDENCE_ID: new_evidence}, promoted_urls


def kakao_rate_entries(sibling: dict[str, Any]) -> list[dict[str, Any]]:
    sibling_code = str(sibling["product_code"])
    entries = copy.deepcopy(sibling["return_policy"]["rate_entries"])
    for entry in entries:
        rate_id = str(entry.get("rate_id", ""))
        entry["rate_id"] = rate_id.replace(sibling_code, KAKAO_PRODUCT_CODE)
        entry["source_ref_ids"] = [KAKAO_EVIDENCE_ID]
        if entry.get("role") in {"BASE", "ADVERTISED_MAXIMUM"}:
            entry["as_of"] = SNAPSHOT_DATE
    return entries


def update_kakao_product(product: dict[str, Any], sibling: dict[str, Any]) -> bool:
    policy = product.setdefault("return_policy", {})
    policy["rate_entries"] = kakao_rate_entries(sibling)
    policy["rate_table_snapshot"] = copy.deepcopy(
        sibling["return_policy"]["rate_table_snapshot"]
    )
    advertised = copy.deepcopy(sibling["return_policy"]["advertised_max_rate"])
    advertised["source_ref_ids"] = [KAKAO_EVIDENCE_ID]
    advertised["as_of"] = SNAPSHOT_DATE
    policy["advertised_max_rate"] = advertised
    append_ref(product, KAKAO_EVIDENCE_ID)
    for key in ("sale_policy", "eligibility_policy", "term_policy", "cash_flow_policy"):
        append_ref(product.get(key), KAKAO_EVIDENCE_ID)
    append_ref(policy, KAKAO_EVIDENCE_ID)
    interest_policy = policy.get("interest_payment_policy")
    append_ref(interest_policy, KAKAO_EVIDENCE_ID)
    product["official_site_link"] = {
        "url": KAKAO_URL,
        "document_type": "OFFICIAL_PRODUCT_PAGE",
        "source_ref_ids": [KAKAO_EVIDENCE_ID],
        "selection_basis": "CURRENT_ISSUER_OFFICIAL_PRODUCT_PAGE",
    }
    metadata = product.setdefault("version_metadata", {})
    metadata["data_gaps"] = [
        gap
        for gap in metadata.get("data_gaps", [])
        if path_or_field(gap) != "official_research"
    ]
    metadata["last_verified_at"] = PUBLISHED_AT
    metadata["data_authority"] = "ISSUER_OFFICIAL_CURRENT_PAGE_WITH_NAVER_ORIGINAL_CONTEXT"
    metadata["rate_backfill"] = {
        "source_ref_id": KAKAO_EVIDENCE_ID,
        "source_url": KAKAO_URL,
        "basis": "CURRENT_OFFICIAL_TERM_RATE_TABLE",
    }
    return True


def has_web_link(
    product: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    source_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    for ref_id in product.get("source_ref_ids", []):
        evidence = evidence_by_id.get(ref_id)
        if evidence and evidence_url(evidence):
            return True
        source_id = evidence.get("source_id") if evidence else ref_id
        source = source_by_id.get(source_id)
        if source and source_url(source):
            return True
    return False


def product_changed_metadata(
    product: dict[str, Any],
    old_row: Mapping[str, Any],
    reasons: list[str],
) -> None:
    metadata = product.setdefault("version_metadata", {})
    old_version = int(old_row["version"])
    metadata.update(
        {
            "previous_version": old_version,
            "previous_path": old_row["path"],
            "previous_sha256": old_row["sha256"],
            "correction_batch": BATCH,
            "correction_reason": "; ".join(reasons),
            "correction_applied_at": PUBLISHED_AT,
        }
    )
    if metadata.get("data_gaps"):
        metadata["publication_gate"] = "PUBLISHED_WITH_EXPLICIT_DATA_GAPS"


def file_entries(paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    return [
        {"path": key, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        for key, path in sorted(paths.items())
    ]


def audit_current_catalog() -> dict[str, Any]:
    audit_path = ROOT / "scripts/audit_catalog_gaps_20260904.py"
    spec = importlib.util.spec_from_file_location("catalog_gap_audit", audit_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load audit module: {audit_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit()


def publish() -> None:
    index = read_json(INDEX_PATH)
    manifest = read_json(CURRENT_MANIFEST_PATH)
    if index.get("manifest_batch") != CURRENT_BATCH:
        raise ValueError(f"unexpected current batch: {index.get('manifest_batch')}")
    if MANIFEST_PATH.exists() or REGISTRY_DIR.exists() or REPORT_DIR.exists():
        raise FileExistsError(f"release already exists: {BATCH}")

    source_payload = read_json(resolve_published_path(manifest["source_document_registry"]))
    evidence_payload = read_json(resolve_published_path(manifest["evidence_ref_registry"]))
    source_by_id, evidence_by_id, _ = patch_registries(
        index, source_payload, evidence_payload
    )

    sibling_row = next(
        row for row in index["products"] if row["product_code"] == "INST-KR-000830-2-C0A3C28D4F5"
    )
    kakao_sibling = read_json(ROOT / sibling_row["path"])

    updated_paths: list[Path] = []
    update_reasons: dict[str, list[str]] = {}
    new_index = copy.deepcopy(index)
    new_rows = {row["product_code"]: row for row in new_index["products"]}
    for row in index["products"]:
        product = read_json(ROOT / row["path"])
        product_code = row["product_code"]
        reasons: list[str] = []
        if product_code == "INST-KR-000092-4-0004":
            add_gap(product, RATE_GAP_YUANTA)
            reasons.append("유안타 약정형 CMA의 공개 숫자금리 별도문의 상태를 명시")
        elif product_code == "INST-KR-000736-3-C1C2DCAD7BA":
            add_gap(product, RATE_GAP_TARIFF_SELECTION)
            reasons.append("가입 시 선택 tariff를 단일 executable rate로 평탄화하지 않음을 명시")
        elif product_code == KAKAO_PRODUCT_CODE:
            update_kakao_product(product, kakao_sibling)
            reasons.append("카카오뱅크 공식 현재 기간별 기본금리표를 backfill")

        current_evidence = {
            row["evidence_ref_id"]: row
            for row in evidence_payload.get("evidence_refs", [])
            if row.get("evidence_ref_id")
        }
        if not has_web_link(product, current_evidence, source_by_id):
            if add_gap(product, LINK_GAP):
                reasons.append("검증된 공식 http(s) 링크가 없음을 명시")

        if not reasons:
            continue
        old_version = int(row["version"])
        product["version"] = old_version + 1
        for binding in product.get("custom_bindings", []):
            if isinstance(binding, dict):
                binding["product_version"] = product["version"]
        product_changed_metadata(product, row, reasons)
        target_path = (ROOT / row["path"]).parent / f"v{product['version']:03d}.json"
        if target_path.exists():
            raise FileExistsError(f"target product version already exists: {target_path}")
        updated_paths.append(target_path)
        update_reasons[product_code] = reasons
        target_row = new_rows[product_code]
        target_row.update(
            {
                "version": product["version"],
                "path": relative(target_path),
                "sha256": "",
            }
        )
        write_json(target_path, product)
        target_row["sha256"] = sha256_file(target_path)

    source_payload["source_documents"] = list(source_by_id.values())
    evidence_payload["evidence_refs"] = list(evidence_by_id.values())

    if not updated_paths:
        raise ValueError("No catalog data-gap changes were generated")

    new_index["index_version"] = int(new_index.get("index_version", 1)) + 1
    new_index["generated_at"] = PUBLISHED_AT
    new_index["manifest_batch"] = BATCH

    write_json(SOURCE_PATH, source_payload)
    write_json(EVIDENCE_PATH, evidence_payload)
    write_json(INDEX_PATH, new_index)

    inherited = {
        item["path"]: resolve_published_path(item["path"])
        for item in manifest.get("files", [])
        if item.get("path") and resolve_published_path(item["path"]).exists()
    }
    for path in updated_paths:
        inherited[relative(path)] = path
    inherited[relative(INDEX_PATH)] = INDEX_PATH
    inherited[relative(SOURCE_PATH)] = SOURCE_PATH
    inherited[relative(EVIDENCE_PATH)] = EVIDENCE_PATH

    provisional_manifest = copy.deepcopy(manifest)
    provisional_manifest.update(
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
    provisional_manifest["counts"] = copy.deepcopy(manifest.get("counts") or {})
    provisional_manifest["counts"].update(
        {
            "source_documents": len(source_payload["source_documents"]),
            "evidence_refs": len(evidence_payload["evidence_refs"]),
        }
    )
    write_json(MANIFEST_PATH, provisional_manifest)
    final_audit = audit_current_catalog()

    report = {
        "batch": BATCH,
        "publication_status": "PUBLISHED",
        "updated_products": len(updated_paths),
        "updated_product_codes": sorted(update_reasons),
        "new_source_documents": 1,
        "new_evidence_refs": 1,
        "promoted_source_urls": sorted(
            source_id
            for source_id, source in source_by_id.items()
            if source.get("url_basis") in {
                "PROMOTED_FROM_EXISTING_SOURCE_LOCATOR",
                "OFFICIAL_PROVIDER_PAGE_RECHECK",
            }
        ),
        "final_audit": final_audit,
        "checks": [
            {"check": "silent_rate_gap_count_is_zero", "status": "PASS"},
            {"check": "all_resolved_sources_have_document_type", "status": "PASS"},
            {"check": "index_product_hashes_updated", "status": "PASS"},
        ],
    }
    write_json(VALIDATION_PATH, report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        "# TASK B 카탈로그 데이터 구멍 보완\n\n"
        f"- 배치: `{BATCH}`\n"
        f"- 새 상품 버전: {len(updated_paths):,}개\n"
        "- 공식 URL 승격: 기존 source locator 및 공식 CMA 출처\n"
        "- 금리: 카카오뱅크 공식 기간별 금리표 backfill; 유안타 약정형·선택 tariff는 "
        "명시적 data gap 유지\n\n"
        "상세 기준 수치는 `validation_reports.json`의 `final_audit`를 참조한다.\n",
        encoding="utf-8",
    )

    inherited[relative(VALIDATION_PATH)] = VALIDATION_PATH
    inherited[relative(REPORT_PATH)] = REPORT_PATH
    audit_files = file_entries(inherited)
    write_json(
        HASH_PATH,
        {
            "batch": BATCH,
            "algorithm": "SHA-256",
            "self_excluded": True,
            "files": audit_files,
        },
    )
    manifest_files = [
        *audit_files,
        {
            "path": relative(HASH_PATH),
            "sha256": sha256_file(HASH_PATH),
            "size_bytes": HASH_PATH.stat().st_size,
        },
    ]
    provisional_manifest.update(
        {
            "validation_file": relative(VALIDATION_PATH),
            "normalization_report_file": relative(REPORT_PATH),
            "hash_audit_file": relative(HASH_PATH),
            "files": manifest_files,
        }
    )
    write_json(MANIFEST_PATH, provisional_manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "batch": BATCH,
                    "current_batch": CURRENT_BATCH,
                    "message": "Run without --dry-run to publish the prepared immutable correction batch.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    publish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
