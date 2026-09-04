#!/usr/bin/env python3
"""Publish the user-confirmed official terms for NH ``정기예금``."""
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
CURRENT_BATCH = "20260904-nh-deposit-identity-01"
BATCH = "20260904-nh-deposit-official-terms-01"
PUBLISHED_AT = "2026-09-04T21:00:00+09:00"
SNAPSHOT_DATE = "2026-09-04"
CODE = "INST-KR-000739-2-C4C876638DA"
PDF_SHA256 = "sha256:00fd5752d6be9549a8b49959074b280f0f96af0c4b254af62ebe583637fe4d58"
DETAIL_URL = "https://smartmarket.nonghyup.com/servlet/BFDCW1021R.view"
PDF_SOURCE = "SRC-USER-20260904-NH-TERM-DEPOSIT-PDF"
PDF_EVIDENCE = "EVD-USER-20260904-NH-TERM-DEPOSIT-PDF"
PDF_TERMS_EVIDENCE = "EVD-USER-20260904-NH-TERM-DEPOSIT-PDF-TERMS"
PDF_AUTORENEWAL_EVIDENCE = "EVD-USER-20260904-NH-TERM-DEPOSIT-PDF-AUTORENEWAL-DECISION"
DETAIL_SOURCE = "SRC-OFFICIAL-20260904-NH-TERM-DEPOSIT-DETAIL"
DETAIL_EVIDENCE = "EVD-OFFICIAL-20260904-NH-TERM-DEPOSIT-DETAIL"
MANIFEST_PATH = NORMALIZED / "manifests" / f"{BATCH}.json"
REGISTRY_DIR = NORMALIZED / "registries" / BATCH
SOURCE_PATH = REGISTRY_DIR / "source_documents.json"
EVIDENCE_PATH = REGISTRY_DIR / "evidence_refs.json"
REPORT_DIR = NORMALIZED / "reports" / BATCH
VALIDATION_PATH = REPORT_DIR / "validation_reports.json"
REPORT_PATH = REPORT_DIR / "NH_DEPOSIT_OFFICIAL_TERMS_REPORT.md"
HASH_PATH = REPORT_DIR / "HASH_AUDIT.json"

def read(path: Path) -> Any: return json.loads(path.read_text(encoding="utf-8"))
def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
def rel(path: Path) -> str: return str(path.relative_to(ROOT))
def digest(path: Path) -> str: return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
def append_ref(value: Any, ref: str) -> None:
    if isinstance(value, dict):
        refs = value.setdefault("source_ref_ids", [])
        if isinstance(refs, list) and ref not in refs: refs.append(ref)

def source_rows() -> tuple[dict[str, Any], dict[str, Any]]:
    common = {"product_code": CODE, "captured_at": PUBLISHED_AT, "version_date": "2025-07-31"}
    source = {**common, "source_id": PDF_SOURCE, "title": "NH농협은행 정기예금 상품설명서 PDF", "url": DETAIL_URL,
              "document_type": "OFFICIAL_PRODUCT_DOCUMENT", "authority": "ISSUER_OFFICIAL",
              "source_system": "USER_PROVIDED_ATTACHMENT", "excerpt": "정기예금 상품설명서. 심의필 2025-4224, 유효기간 2025-09-01~2027-06-30, 작성기준일 2025-07-31.",
              "locator": {"attachment_name": "상품설명서.pdf", "sha256": PDF_SHA256, "page": 1}}
    evidence = {"evidence_ref_id": PDF_EVIDENCE, "source_id": PDF_SOURCE, "product_code": CODE,
                "captured_at": PUBLISHED_AT, "locator": source["locator"], "source_text": source["excerpt"],
                "source_text": "정기예금 상품설명서. 심의필 2025-4224, 유효기간 2025-09-01~2027-06-30, 작성기준일 2025-07-31. 만기후 3개월 이내 기본이율 50%, 6개월 이내 20%, 6개월 초과 10%.",
                "supports": ["identity", "after_maturity"],
                "document_metadata": {"review_number": "2025-4224", "valid_from": "2025-09-01", "valid_to": "2027-06-30", "as_of": "2025-07-31"}}
    terms = {"evidence_ref_id": PDF_TERMS_EVIDENCE, "source_id": PDF_SOURCE, "product_code": CODE,
             "captured_at": PUBLISHED_AT, "locator": {**source["locator"], "page": 2},
             "source_text": "정기예금 중도해지·분할해지 조건은 상품설명서 2페이지.",
             "supports": ["early_termination", "partial_withdrawal"],
             "document_metadata": {"review_number": "2025-4224"}}
    auto = {"evidence_ref_id": PDF_AUTORENEWAL_EVIDENCE, "source_id": PDF_SOURCE, "product_code": CODE,
            "captured_at": PUBLISHED_AT, "locator": {**source["locator"], "page_range": "1-5", "reviewed_pages": [1, 2, 3, 4, 5]},
            "source_text": "설명서 전체에 자동재예치 조항 없음; 사용자가 부재 시 불가로 확정.",
            "supports": ["no_automatic_renewal"], "document_metadata": {"negative_fact": True, "review_number": "2025-4224"}}
    detail_source = {"source_id": DETAIL_SOURCE, "product_code": CODE, "title": "NH농협은행 정기예금 공식 상세 페이지", "url": DETAIL_URL,
                     "document_type": "OFFICIAL_PRODUCT_PAGE", "authority": "ISSUER_OFFICIAL", "captured_at": PUBLISHED_AT,
                     "locator": {"url": DETAIL_URL}}
    detail_evidence = {"evidence_ref_id": DETAIL_EVIDENCE, "source_id": DETAIL_SOURCE, "product_code": CODE,
                       "captured_at": PUBLISHED_AT, "locator": {"url": DETAIL_URL}, "source_text": "NH농협은행 정기예금 공식 상세 URL", "supports": ["official_detail_url"]}
    return source, evidence, terms, auto, detail_source, detail_evidence

def patch_product(product: dict[str, Any], old: Mapping[str, Any]) -> None:
    append_ref(product, PDF_EVIDENCE); append_ref(product, PDF_TERMS_EVIDENCE); append_ref(product, PDF_AUTORENEWAL_EVIDENCE)
    liq = product.setdefault("liquidity_policy", {})
    terms_ref = [PDF_TERMS_EVIDENCE]
    liq.update({"automatic_renewal_allowed": False, "automatic_renewal_derivation": "USER_CONFIRMED_ABSENCE_FROM_OFFICIAL_PRODUCT_DOCUMENT",
        "automatic_renewal_audit": {"decision": "DISALLOWED", "basis": "문서 전체에 자동재예치 기능이 제공되지 않음; 사용자 지시에 따른 부재 사실 확정", "source_ref_ids": [PDF_AUTORENEWAL_EVIDENCE]},
        "early_termination_policy": {"source_ref_ids": terms_ref, "rounding": "THIRD_DECIMAL_TRUNCATED", "tiers": [
          {"elapsed_term": {"unit": "MONTH", "max_value": 3, "max_inclusive": False}, "rate": {"type": "FIXED", "value": "0.10", "unit": "PERCENT"}},
          *[{"elapsed_term": {"unit": "MONTH", "min_value": lo, "min_inclusive": True, "max_value": hi, "max_inclusive": False}, "rate": {"formula": f"EARLY_BASE_RATE*{pct}%*ELAPSED_MONTHS/CONTRACT_MONTHS", "minimum_percent": "0.10"}} for lo, hi, pct in ((3,6,50),(6,9,60),(9,11,70))],
          {"elapsed_term": {"unit": "MONTH", "min_value": 11, "min_inclusive": True}, "rate": {"formula": "EARLY_BASE_RATE*80%*ELAPSED_MONTHS/CONTRACT_MONTHS", "minimum_percent": "0.10"}}]},
        "after_maturity_policy": {"source_ref_ids": terms_ref, "tiers": [{"elapsed_term": {"unit": "MONTH", "max_value": 3, "max_inclusive": True}, "rate": {"formula": "BASIC_RATE*50%"}}, {"elapsed_term": {"unit": "MONTH", "min_value": 3, "min_inclusive": False, "max_value": 6, "max_inclusive": True}, "rate": {"formula": "BASIC_RATE*20%"}}, {"elapsed_term": {"unit": "MONTH", "min_value": 6, "min_inclusive": False}, "rate": {"formula": "BASIC_RATE*10%"}}]},
        "partial_withdrawal": {"allowed": True, "max_events_including_maturity": 3, "remaining_balance_min_krw": "1000000", "rate_on_withdrawal": "EARLY_TERMINATION_RATE", "source_ref_ids": terms_ref}, "source_ref_ids": terms_ref})
    product["official_site_link"] = {"url": DETAIL_URL, "document_type": "OFFICIAL_PRODUCT_PAGE", "source_ref_ids": [DETAIL_EVIDENCE], "provenance": "USER_PROVIDED_OFFICIAL_DETAIL_URL"}
    product["data_gaps"] = []
    meta = product.setdefault("version_metadata", {})
    meta.update({"data_gaps": [], "publication_gate": "PUBLISHED_WITH_OFFICIAL_TERMS", "correction_batch": BATCH,
                 "correction_reason": "user-confirmed official NH terms and detail URL", "correction_applied_at": PUBLISHED_AT,
                 "official_terms_document": {"attachment_name": "상품설명서.pdf", "sha256": PDF_SHA256, "review_number": "2025-4224", "effective_period": "2025-09-01/2027-06-30", "as_of": "2025-07-31", "page_locators": {"identity_after_maturity": 1, "early_termination_partial_withdrawal": 2}, "source_ref_ids": [PDF_EVIDENCE, PDF_TERMS_EVIDENCE]},
                 "previous_version": old["version"], "previous_path": old["path"], "previous_sha256": old["sha256"]})
    product["version"] = int(old["version"]) + 1

def entries(paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    return [{"path": k, "sha256": digest(v), "size_bytes": v.stat().st_size} for k, v in sorted(paths.items())]

def publish() -> None:
    index = read(INDEX_PATH); current = read(NORMALIZED / "manifests" / f"{CURRENT_BATCH}.json")
    if index.get("manifest_batch") != CURRENT_BATCH: raise ValueError("unexpected active batch")
    if MANIFEST_PATH.exists() or REGISTRY_DIR.exists() or REPORT_DIR.exists(): raise FileExistsError(BATCH)
    evidence_payload, source_payload = load_active_registries(index)
    sources = source_payload["source_documents"]; evidence = evidence_payload["evidence_refs"]
    pdf_source, pdf_evidence, terms_evidence, auto_evidence, detail_source, detail_evidence = source_rows()
    sources.extend([pdf_source, detail_source]); evidence.extend([pdf_evidence, terms_evidence, auto_evidence, detail_evidence])
    row = next(x for x in index["products"] if x["product_code"] == CODE); product = read(ROOT / row["path"])
    if product["name"] != "정기예금" or int(row["version"]) != 5: raise ValueError("unexpected active product")
    patch_product(product, row); target = (ROOT / row["path"]).parent / "v006.json"; write(target, product)
    new_index = copy.deepcopy(index); new_row = next(x for x in new_index["products"] if x["product_code"] == CODE)
    new_row.update({"version": 6, "path": rel(target), "sha256": digest(target)}); new_index.update({"index_version": int(index["index_version"])+1, "generated_at": PUBLISHED_AT, "manifest_batch": BATCH})
    write(SOURCE_PATH, source_payload); write(EVIDENCE_PATH, evidence_payload); write(INDEX_PATH, new_index)
    inherited = {x["path"]: ROOT / x["path"] for x in current.get("files", []) if x.get("path") and (ROOT/x["path"]).exists()}
    inherited.update({rel(target): target, rel(INDEX_PATH): INDEX_PATH, rel(SOURCE_PATH): SOURCE_PATH, rel(EVIDENCE_PATH): EVIDENCE_PATH})
    manifest = copy.deepcopy(current); manifest.update({"manifest_version": int(current.get("manifest_version",1))+1, "published_at": PUBLISHED_AT, "previous_manifest": rel(NORMALIZED/"manifests"/f"{CURRENT_BATCH}.json"), "correction_batch": BATCH, "source_document_registry": rel(SOURCE_PATH), "evidence_ref_registry": rel(EVIDENCE_PATH), "files": entries(inherited)})
    manifest.setdefault("counts", {}).update({"source_documents": len(sources), "evidence_refs": len(evidence)}); write(MANIFEST_PATH, manifest)
    final = audit(); assert final["counts"]["unresolved_source_ref_products"] == 0
    REPORT_DIR.mkdir(parents=True, exist_ok=True); write(VALIDATION_PATH, {"batch": BATCH, "updated_product_codes": [CODE], "pdf_sha256": PDF_SHA256, "detail_url": DETAIL_URL, "final_audit": final})
    REPORT_PATH.write_text(f"# NH 정기예금 공식 약관 보완\n\n- 배치: `{BATCH}`\n- v006: 공식 설명서 기반 정책 보완, 기존 현재 금리 2.40 유지\n", encoding="utf-8")
    inherited.update({rel(VALIDATION_PATH): VALIDATION_PATH, rel(REPORT_PATH): REPORT_PATH}); write(HASH_PATH, {"batch": BATCH, "algorithm": "SHA-256", "self_excluded": True, "files": entries(inherited)})
    manifest["hash_audit_file"] = rel(HASH_PATH); manifest["validation_file"] = rel(VALIDATION_PATH); manifest["normalization_report_file"] = rel(REPORT_PATH); write(MANIFEST_PATH, manifest)

if __name__ == "__main__": publish()
