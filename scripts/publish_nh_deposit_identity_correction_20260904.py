#!/usr/bin/env python3
"""Separate NH Bank's generic term deposit from Big Satisfaction Deposit.

User-provided screenshots of both the Naver Pay comparison list and the
issuer's product mall show ``정기예금`` and ``큰만족실세예금`` as two
simultaneously listed products.  An older publication incorrectly attached
the latter product's PDF to the former.  This correction release removes that
cross-product evidence and every policy derived solely from it.
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

from scripts.audit_catalog_gaps_20260904 import audit, load_active_registries


NORMALIZED = ROOT / "data/financial_products/normalized"
INDEX_PATH = NORMALIZED / "index.json"
CURRENT_BATCH = "20260904-catalog-source-refs-01"
BATCH = "20260904-nh-deposit-identity-01"
PUBLISHED_AT = "2026-09-04T20:00:00+09:00"
SNAPSHOT_DATE = "2026-09-04"
CURRENT_MANIFEST_PATH = NORMALIZED / "manifests" / f"{CURRENT_BATCH}.json"
MANIFEST_PATH = NORMALIZED / "manifests" / f"{BATCH}.json"
REGISTRY_DIR = NORMALIZED / "registries" / BATCH
SOURCE_PATH = REGISTRY_DIR / "source_documents.json"
EVIDENCE_PATH = REGISTRY_DIR / "evidence_refs.json"
REPORT_DIR = NORMALIZED / "reports" / BATCH
VALIDATION_PATH = REPORT_DIR / "validation_reports.json"
REPORT_PATH = REPORT_DIR / "NH_DEPOSIT_IDENTITY_CORRECTION_REPORT.md"
HASH_PATH = REPORT_DIR / "HASH_AUDIT.json"

TERM_DEPOSIT_CODE = "INST-KR-000739-2-C4C876638DA"
BIG_SATISFACTION_CODE = "INST-KR-000739-2-C87539C2B27"
WRONG_PDF_EVIDENCE_ID = "EVD-TD-ROW0512-NH-PDF"
WRONG_PDF_SOURCE_ID = "SRC-TD-ROW0512-NH-PDF"
NAVER_DETAIL_EVIDENCE_ID = "EVD-TD-ROW0512-NAVER"
NAVER_SCREENSHOT_SOURCE_ID = "SRC-USER-20260904-NAVER-NH-DEPOSIT-LIST"
NAVER_SCREENSHOT_EVIDENCE_ID = "EVD-USER-20260904-NAVER-NH-DEPOSIT-LIST"
OFFICIAL_SCREENSHOT_SOURCE_ID = "SRC-USER-20260904-NH-OFFICIAL-DEPOSIT-LIST"
OFFICIAL_SCREENSHOT_EVIDENCE_ID = "EVD-USER-20260904-NH-OFFICIAL-DEPOSIT-LIST"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def append_unique(rows: list[dict[str, Any]], row: dict[str, Any], key: str) -> None:
    row_id = row[key]
    if any(existing.get(key) == row_id for existing in rows):
        raise ValueError(f"duplicate registry id: {row_id}")
    rows.append(row)


def add_ref(values: list[str], ref_id: str) -> list[str]:
    return values if ref_id in values else [*values, ref_id]


def remove_source_ref(value: Any, ref_id: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_ref_ids" and isinstance(child, list):
                value[key] = [item for item in child if item != ref_id]
            else:
                remove_source_ref(child, ref_id)
    elif isinstance(value, list):
        for child in value:
            remove_source_ref(child, ref_id)


def user_sources() -> list[dict[str, Any]]:
    return [
        {
            "source_id": NAVER_SCREENSHOT_SOURCE_ID,
            "title": "사용자 제공 Naver Pay NH농협은행 예금 목록 화면",
            "url": None,
            "document_type": "USER_PROVIDED_COMPARISON_SCREENSHOT",
            "authority": "COMPARISON_SERVICE",
            "source_system": "CONVERSATION_ATTACHMENT",
            "version_date": SNAPSHOT_DATE,
            "captured_at": PUBLISHED_AT,
            "excerpt": (
                "NH농협은행 예금 목록에 정기예금(최고·기본 2.40%)과 "
                "큰만족실세예금(최고·기본 2.45%)이 별도 행으로 동시에 표시됨."
            ),
            "locator": {
                "attachment_name": (
                    "codex-clipboard-0af940c8-b780-48f5-a43a-c7917a0e2125.png"
                ),
                "sha256": (
                    "sha256:1cbb62b6045d15ac701d64744bd97fdad6ca75bb2e02368b452c1fb7553ae05d"
                ),
            },
        },
        {
            "source_id": OFFICIAL_SCREENSHOT_SOURCE_ID,
            "title": "사용자 제공 NH농협은행 공식 상품몰 예금 목록 화면",
            "url": "https://banking.nonghyup.com/content/html/ip/sd/ipsd0010c.html",
            "document_type": "USER_PROVIDED_OFFICIAL_SCREENSHOT",
            "authority": "ISSUER_OFFICIAL",
            "authority_for_official_product_terms": True,
            "source_system": "CONVERSATION_ATTACHMENT",
            "version_date": SNAPSHOT_DATE,
            "captured_at": PUBLISHED_AT,
            "excerpt": (
                "NH농협은행 상품몰에 정기예금(최고·최저 2.40%, 영업점가입)과 "
                "큰만족실세예금(최고·최저 2.45%, 가입하기)이 별도 상품으로 "
                "동시에 표시됨."
            ),
            "locator": {
                "attachment_name": (
                    "codex-clipboard-bf33d208-6f3a-4e1f-8dd1-17ae22646f6b.png"
                ),
                "sha256": (
                    "sha256:544b268519c9b904f14638b7517841625a61ee584a03f7cdcb9fde11ee7f610e"
                ),
            },
        },
    ]


def user_evidence() -> list[dict[str, Any]]:
    related = [TERM_DEPOSIT_CODE, BIG_SATISFACTION_CODE]
    return [
        {
            "evidence_ref_id": NAVER_SCREENSHOT_EVIDENCE_ID,
            "source_id": NAVER_SCREENSHOT_SOURCE_ID,
            "product_code": TERM_DEPOSIT_CODE,
            "related_product_codes": related,
            "source_text": (
                "Naver Pay의 NH농협은행 필터 결과에서 정기예금 2.40%와 "
                "큰만족실세예금 2.45%가 별도 상품으로 동시에 표시된다."
            ),
            "locator": user_sources()[0]["locator"],
            "supports": [
                "distinct_product_identity",
                "current_listing",
                "advertised_rate",
                "eligibility_badge",
            ],
            "verification_status": "USER_PROVIDED_CURRENT_SCREENSHOT",
        },
        {
            "evidence_ref_id": OFFICIAL_SCREENSHOT_EVIDENCE_ID,
            "source_id": OFFICIAL_SCREENSHOT_SOURCE_ID,
            "product_code": TERM_DEPOSIT_CODE,
            "related_product_codes": related,
            "source_text": (
                "NH농협은행 공식 상품몰에서 정기예금 2.40%와 큰만족실세예금 "
                "2.45%가 별도 상품으로 표시되며 정기예금은 영업점가입 상품이다."
            ),
            "locator": user_sources()[1]["locator"],
            "supports": [
                "distinct_product_identity",
                "issuer_current_listing",
                "advertised_rate",
                "subscription_channel",
            ],
            "verification_status": "USER_CONFIRMED_ISSUER_SCREENSHOT",
        },
    ]


def patch_product(product: dict[str, Any], index_row: Mapping[str, Any]) -> None:
    remove_source_ref(product, WRONG_PDF_EVIDENCE_ID)

    product["version"] = int(index_row["version"]) + 1
    product["source_ref_ids"] = [
        NAVER_DETAIL_EVIDENCE_ID,
        NAVER_SCREENSHOT_EVIDENCE_ID,
        OFFICIAL_SCREENSHOT_EVIDENCE_ID,
    ]
    product["sale_policy"]["source_ref_ids"] = add_ref(
        add_ref(
            product["sale_policy"].get("source_ref_ids", []),
            NAVER_SCREENSHOT_EVIDENCE_ID,
        ),
        OFFICIAL_SCREENSHOT_EVIDENCE_ID,
    )
    product["eligibility_policy"]["source_ref_ids"] = add_ref(
        product["eligibility_policy"].get("source_ref_ids", []),
        NAVER_SCREENSHOT_EVIDENCE_ID,
    )

    return_policy = product["return_policy"]
    return_policy["source_ref_ids"] = add_ref(
        add_ref(
            return_policy.get("source_ref_ids", []),
            NAVER_SCREENSHOT_EVIDENCE_ID,
        ),
        OFFICIAL_SCREENSHOT_EVIDENCE_ID,
    )
    for entry in return_policy.get("rate_entries", []):
        if entry.get("role") == "ADVERTISED_MAXIMUM":
            entry["source_ref_ids"] = add_ref(
                add_ref(
                    entry.get("source_ref_ids", []),
                    NAVER_SCREENSHOT_EVIDENCE_ID,
                ),
                OFFICIAL_SCREENSHOT_EVIDENCE_ID,
            )
    for key in ("advertised_max_rate", "listing_base_rate_reference"):
        row = return_policy.get(key)
        if isinstance(row, dict):
            row["source_ref_ids"] = add_ref(
                add_ref(
                    row.get("source_ref_ids", []),
                    NAVER_SCREENSHOT_EVIDENCE_ID,
                ),
                OFFICIAL_SCREENSHOT_EVIDENCE_ID,
            )

    product["cash_flow_policy"]["source_ref_ids"] = [NAVER_DETAIL_EVIDENCE_ID]
    product["liquidity_policy"] = {
        "early_termination_allowed": True,
        "early_termination_policy": {
            "source_text": (
                "만기 전 해지할 경우 계약한 이자율보다 낮은 "
                "중도해지이자율이 적용됩니다."
            ),
            "source_ref_ids": [NAVER_DETAIL_EVIDENCE_ID],
        },
        "source_ref_ids": [NAVER_DETAIL_EVIDENCE_ID],
    }
    interest_payment = product.get("interest_payment_policy") or {}
    interest_payment["default_context_id"] = "MATURITY_PAYMENT"
    interest_payment["default_context_derivation"] = "LISTING_BASE_VALUE_MATCH"
    product["interest_payment_policy"] = interest_payment

    gaps = [
        {
            "path": "liquidity_policy.automatic_renewal_allowed",
            "reason": "정기예금의 자동재예치 가능 여부를 현재 상품 상세에서 확인하지 못함",
        },
        {
            "path": "liquidity_policy.early_termination_policy.rate_schedule",
            "reason": "중도해지 시 낮은 이율 적용은 확인했으나 현재 상세 이율표는 미확인",
        },
        {
            "path": "liquidity_policy.after_maturity_policy",
            "reason": "정기예금의 현재 만기후 이율 정책을 상품 상세에서 확인하지 못함",
        },
        {
            "path": "liquidity_policy.partial_withdrawal",
            "reason": "정기예금의 분할인출 가능 여부를 현재 상품 상세에서 확인하지 못함",
        },
        {
            "path": "official_site_link",
            "reason": "공식 상품몰 현재 목록은 사용자 화면으로 확인했으나 상세 URL은 미수집",
        },
    ]
    product["data_gaps"] = copy.deepcopy(gaps)
    metadata = product.setdefault("version_metadata", {})
    metadata.update(
        {
            "captured_at": PUBLISHED_AT,
            "data_authority": "NAVER_PRIMARY_PLUS_USER_VERIFIED_NH_OFFICIAL_LISTING",
            "publication_gate": "PUBLISHED_WITH_EXPLICIT_DATA_GAPS",
            "data_gaps": gaps,
            "source_priority": "NAVER_PRIMARY_WITH_ISSUER_LISTING_CORROBORATION",
            "source_reason": (
                "USER_SCREENSHOTS_PROVE_TERM_DEPOSIT_AND_BIG_SATISFACTION_"
                "DEPOSIT_ARE_DISTINCT_PRODUCTS"
            ),
            "previous_version": int(index_row["version"]),
            "previous_path": index_row["path"],
            "previous_sha256": index_row["sha256"],
            "correction_batch": BATCH,
            "correction_reason": (
                "remove cross-product 큰만족실세예금 PDF and derived policies "
                "from 정기예금"
            ),
            "correction_applied_at": PUBLISHED_AT,
            "user_confirmation_date": SNAPSHOT_DATE,
        }
    )


def file_entries(paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": path,
            "sha256": sha256_file(file_path),
            "size_bytes": file_path.stat().st_size,
        }
        for path, file_path in sorted(paths.items())
    ]


def publish() -> None:
    index = read_json(INDEX_PATH)
    current_manifest = read_json(CURRENT_MANIFEST_PATH)
    if index.get("manifest_batch") != CURRENT_BATCH:
        raise ValueError(f"unexpected current batch: {index.get('manifest_batch')}")
    if MANIFEST_PATH.exists() or REGISTRY_DIR.exists() or REPORT_DIR.exists():
        raise FileExistsError(f"release already exists: {BATCH}")

    evidence_payload, source_payload = load_active_registries(index)
    evidence_rows = evidence_payload.get("evidence_refs", [])
    source_rows = source_payload.get("source_documents", [])

    pdf_source = next(
        (row for row in source_rows if row.get("source_id") == WRONG_PDF_SOURCE_ID),
        None,
    )
    if pdf_source is None:
        raise ValueError("mislinked PDF source not found")
    if pdf_source.get("product_code") != TERM_DEPOSIT_CODE:
        raise ValueError("unexpected PDF source ownership")
    pdf_source["product_code"] = BIG_SATISFACTION_CODE

    pdf_evidence = next(
        (
            row
            for row in evidence_rows
            if row.get("evidence_ref_id") == WRONG_PDF_EVIDENCE_ID
        ),
        None,
    )
    if pdf_evidence is None:
        raise ValueError("mislinked PDF evidence not found")
    if pdf_evidence.get("product_code") != TERM_DEPOSIT_CODE:
        raise ValueError("unexpected PDF evidence ownership")
    pdf_evidence["product_code"] = BIG_SATISFACTION_CODE

    for row in user_sources():
        append_unique(source_rows, row, "source_id")
    for row in user_evidence():
        append_unique(evidence_rows, row, "evidence_ref_id")

    rows_by_code = {row["product_code"]: row for row in index["products"]}
    index_row = rows_by_code[TERM_DEPOSIT_CODE]
    product = read_json(ROOT / index_row["path"])
    if product.get("name") != "정기예금" or int(index_row["version"]) != 4:
        raise ValueError("unexpected active NH term-deposit version")
    patch_product(product, index_row)
    target_path = (ROOT / index_row["path"]).parent / "v005.json"
    if target_path.exists():
        raise FileExistsError(f"target product version exists: {target_path}")
    write_json(target_path, product)

    write_json(SOURCE_PATH, source_payload)
    write_json(EVIDENCE_PATH, evidence_payload)

    new_index = copy.deepcopy(index)
    new_row = next(
        row for row in new_index["products"] if row["product_code"] == TERM_DEPOSIT_CODE
    )
    new_row.update(
        {
            "version": 5,
            "path": relative(target_path),
            "sha256": sha256_file(target_path),
        }
    )
    new_index["index_version"] = int(index["index_version"]) + 1
    new_index["generated_at"] = PUBLISHED_AT
    new_index["manifest_batch"] = BATCH
    write_json(INDEX_PATH, new_index)

    inherited = {
        item["path"]: ROOT / item["path"]
        for item in current_manifest.get("files", [])
        if item.get("path") and (ROOT / item["path"]).exists()
    }
    inherited[relative(target_path)] = target_path
    inherited[relative(INDEX_PATH)] = INDEX_PATH
    inherited[relative(SOURCE_PATH)] = SOURCE_PATH
    inherited[relative(EVIDENCE_PATH)] = EVIDENCE_PATH

    new_manifest = copy.deepcopy(current_manifest)
    new_manifest.update(
        {
            "manifest_version": int(current_manifest.get("manifest_version", 1)) + 1,
            "published_at": PUBLISHED_AT,
            "previous_manifest": relative(CURRENT_MANIFEST_PATH),
            "correction_batch": BATCH,
            "source_document_registry": relative(SOURCE_PATH),
            "evidence_ref_registry": relative(EVIDENCE_PATH),
            "application_code_changed": False,
            "files": file_entries(inherited),
        }
    )
    new_manifest["counts"] = copy.deepcopy(current_manifest.get("counts") or {})
    new_manifest["counts"]["source_documents"] = len(source_rows)
    new_manifest["counts"]["evidence_refs"] = len(evidence_rows)
    write_json(MANIFEST_PATH, new_manifest)

    final_audit = audit()
    if final_audit["counts"].get("unresolved_source_ref_products") != 0:
        raise ValueError(f"source refs remain unresolved: {final_audit['counts']}")

    report = {
        "batch": BATCH,
        "publication_status": "PUBLISHED",
        "updated_product_codes": [TERM_DEPOSIT_CODE],
        "distinct_product_codes": [TERM_DEPOSIT_CODE, BIG_SATISFACTION_CODE],
        "removed_cross_product_evidence_ref": WRONG_PDF_EVIDENCE_ID,
        "corrected_pdf_owner": BIG_SATISFACTION_CODE,
        "new_source_documents": 2,
        "new_evidence_refs": 2,
        "user_attachment_sha256": {
            "naver_pay": (
                "sha256:1cbb62b6045d15ac701d64744bd97fdad6ca75bb2e02368b452c1fb7553ae05d"
            ),
            "nh_official_mall": (
                "sha256:544b268519c9b904f14638b7517841625a61ee584a03f7cdcb9fde11ee7f610e"
            ),
        },
        "final_audit": final_audit,
    }
    write_json(VALIDATION_PATH, report)
    REPORT_PATH.write_text(
        "# NH농협은행 정기예금 상품 식별 교정\n\n"
        f"- 배치: `{BATCH}`\n"
        "- 사용자 제공 Naver Pay 및 NH농협은행 공식 상품몰 화면에서 "
        "`정기예금`과 `큰만족실세예금`이 별도 상품임을 확인\n"
        "- `정기예금`에서 `큰만족실세예금` PDF와 해당 PDF에서만 파생된 "
        "재예치·중도해지 상세·만기후·분할인출 정책을 제거\n"
        "- 두 상품의 현재 최고/기본금리 2.40%와 2.45%는 그대로 보존\n",
        encoding="utf-8",
    )
    inherited[relative(VALIDATION_PATH)] = VALIDATION_PATH
    inherited[relative(REPORT_PATH)] = REPORT_PATH
    hash_files = file_entries(inherited)
    write_json(
        HASH_PATH,
        {
            "batch": BATCH,
            "algorithm": "SHA-256",
            "self_excluded": True,
            "files": hash_files,
        },
    )
    new_manifest["hash_audit_file"] = relative(HASH_PATH)
    new_manifest["files"] = [
        *hash_files,
        {
            "path": relative(HASH_PATH),
            "sha256": sha256_file(HASH_PATH),
            "size_bytes": HASH_PATH.stat().st_size,
        },
    ]
    new_manifest["validation_file"] = relative(VALIDATION_PATH)
    new_manifest["normalization_report_file"] = relative(REPORT_PATH)
    write_json(MANIFEST_PATH, new_manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    publish()
