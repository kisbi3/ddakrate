#!/usr/bin/env python3
"""Audit every published product for recommendation-critical structure.

This is deliberately a structural audit, not an assertion that Naver or an
official source is factually correct.  Each index row produces exactly one
ledger row so that factual source review can resume without losing coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data/financial_products/normalized/index.json"
OUTPUT = ROOT / "audit_output/structure_completeness"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def refs_in(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_ref_ids" and isinstance(child, list):
                found.update(str(item) for item in child)
            else:
                found.update(refs_in(child))
    elif isinstance(value, list):
        for child in value:
            found.update(refs_in(child))
    return found


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def repeated_paragraph(text_value: str) -> bool:
    paragraphs = [item.strip() for item in text_value.split("\n\n") if item.strip()]
    return len(paragraphs) >= 4 and len(set(paragraphs)) <= len(paragraphs) / 2


def has_term(policy: dict[str, Any]) -> bool:
    kind = policy.get("kind")
    if kind == "OPEN_ENDED":
        return True
    if kind == "FIXED":
        return policy.get("fixed_value", policy.get("value")) is not None
    if kind == "DISCRETE":
        return bool(policy.get("allowed_values"))
    if kind == "RANGE":
        return policy.get("min_value") is not None and policy.get("max_value") is not None
    return False


def rate_value(entry: dict[str, Any]) -> Decimal | None:
    try:
        return Decimal(str((entry.get("calculation") or {}).get("value")))
    except (InvalidOperation, TypeError):
        return None


def audit_product(
    index_row: dict[str, Any],
    product: dict[str, Any],
    evidence_ids: set[str],
    source_ids: set[str],
    evidence_to_source: dict[str, str],
    actual_hash: str,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []

    def issue(code: str, path: str, severity: str, detail: str) -> None:
        issues.append({"code": code, "path": path, "severity": severity, "detail": detail})

    for key in ("product_code", "institution_id", "name", "product_family"):
        if not product.get(key):
            issue("IDENTITY_FIELD_MISSING", key, "BLOCKER", f"{key} 값이 없습니다.")
    for key in ("product_code", "institution_id", "product_family"):
        if product.get(key) != index_row.get(key):
            issue("INDEX_IDENTITY_MISMATCH", key, "BLOCKER", "index와 상품 JSON 값이 다릅니다.")
    if product.get("version") != index_row.get("version"):
        issue("INDEX_VERSION_MISMATCH", "version", "BLOCKER", "index와 상품 버전이 다릅니다.")
    if actual_hash != index_row.get("sha256"):
        issue("INDEX_HASH_MISMATCH", "sha256", "BLOCKER", "index SHA-256과 파일이 다릅니다.")

    eligibility = product.get("eligibility_policy") or {}
    eligibility_raw = text(eligibility.get("raw_text") or eligibility.get("eligibility_text"))
    eligibility_rules = eligibility.get("contract_rules") or eligibility.get("rules") or []
    if not eligibility_raw:
        issue("ELIGIBILITY_SOURCE_TEXT_MISSING", "eligibility_policy.raw_text", "HIGH", "가입대상 원문이 없습니다.")
    if eligibility_raw and not eligibility_rules:
        issue("ELIGIBILITY_NOT_MACHINE_STRUCTURED", "eligibility_policy.contract_rules", "HIGH", "가입대상 원문은 있지만 판정 규칙이 없습니다.")
    if repeated_paragraph(eligibility_raw):
        issue("ELIGIBILITY_TEXT_DUPLICATED", "eligibility_policy.raw_text", "HIGH", "동일 문단이 반복 병합되었습니다.")

    term_policy = product.get("term_policy") or {}
    if not has_term(term_policy):
        issue("TERM_NOT_STRUCTURED", "term_policy", "HIGH", "가입기간을 계산 가능한 형태로 확정하지 못했습니다.")

    cash_flow = product.get("cash_flow_policy") or {}
    if not cash_flow.get("funding_type"):
        issue("FUNDING_TYPE_MISSING", "cash_flow_policy.funding_type", "HIGH", "납입 방식을 알 수 없습니다.")
    family = product.get("product_family")
    if family == "INSTALLMENT_SAVINGS":
        amount_keys = {"minimum_amount", "maximum_amount", "periodic_minimum", "periodic_maximum", "amount_options"}
        if not amount_keys.intersection(cash_flow):
            issue("CONTRIBUTION_LIMIT_NOT_STRUCTURED", "cash_flow_policy", "HIGH", "월/회차 납입 한도가 구조화되지 않았습니다.")
        if not (cash_flow.get("frequency") or cash_flow.get("frequency_options")):
            issue("CONTRIBUTION_FREQUENCY_NOT_STRUCTURED", "cash_flow_policy", "MEDIUM", "납입 주기가 구조화되지 않았습니다.")

    return_policy = product.get("return_policy") or {}
    rate_entries = return_policy.get("rate_entries") or []
    return_kind = return_policy.get("return_kind")
    if return_kind != "PERFORMANCE_LINKED" and not rate_entries:
        issue("RATE_ENTRIES_MISSING", "return_policy.rate_entries", "BLOCKER", "추천·계산에 사용할 금리표가 없습니다.")
    roles = {entry.get("role") for entry in rate_entries if isinstance(entry, dict)}
    if return_kind != "PERFORMANCE_LINKED" and "BASE" not in roles:
        issue("BASE_RATE_MISSING", "return_policy.rate_entries", "BLOCKER", "기본금리 항목이 없습니다.")
    advertised = return_policy.get("advertised_max_rate")
    if advertised is None and "ADVERTISED_MAXIMUM" not in roles and return_kind != "PERFORMANCE_LINKED":
        issue("ADVERTISED_MAX_RATE_MISSING", "return_policy.advertised_max_rate", "HIGH", "최고금리를 확정하지 못했습니다.")

    disclosures = return_policy.get("preferential_condition_disclosures") or []
    machine_conditions = product.get("standard_conditions") or []
    custom_bindings = product.get("custom_bindings") or []
    if disclosures and not (machine_conditions or custom_bindings):
        issue("PREFERENTIAL_CONDITIONS_NOT_EXECUTABLE", "return_policy.preferential_condition_disclosures", "HIGH", "우대조건은 표시용 텍스트뿐이며 금리 계산 규칙이 없습니다.")
    base_values = [value for entry in rate_entries if entry.get("role") == "BASE" if (value := rate_value(entry)) is not None]
    max_values = [value for entry in rate_entries if entry.get("role") == "ADVERTISED_MAXIMUM" if (value := rate_value(entry)) is not None]
    unexplained_uplift = bool(base_values and max_values and max(max_values) > max(base_values))
    if unexplained_uplift and not disclosures and not (machine_conditions or custom_bindings):
        issue("MAX_RATE_UPLIFT_WITHOUT_CONDITIONS", "return_policy", "HIGH", "기본금리보다 높은 최고금리의 달성 조건이 없습니다.")

    gaps = (product.get("version_metadata") or {}).get("data_gaps") or []
    if gaps:
        issue("DECLARED_DATA_GAPS", "version_metadata.data_gaps", "MEDIUM", f"명시된 미확정 필드가 {len(gaps)}개입니다.")

    refs = refs_in(product)
    if not refs:
        issue("PROVENANCE_MISSING", "source_ref_ids", "BLOCKER", "근거 참조가 없습니다.")
    missing_evidence = sorted(refs - evidence_ids)
    if missing_evidence:
        issue("EVIDENCE_REFERENCE_BROKEN", "source_ref_ids", "BLOCKER", f"미등록 근거: {', '.join(missing_evidence[:5])}")
    missing_sources = sorted({evidence_to_source[item] for item in refs & evidence_ids if evidence_to_source.get(item) not in source_ids})
    if missing_sources:
        issue("SOURCE_REFERENCE_BROKEN", "source_ref_ids", "BLOCKER", f"미등록 출처: {', '.join(missing_sources[:5])}")

    severity = Counter(item["severity"] for item in issues)
    if severity["BLOCKER"] or severity["HIGH"]:
        status = "INSUFFICIENT"
    elif issues:
        status = "PARTIAL"
    else:
        status = "STRUCTURALLY_SUFFICIENT"
    return {
        "product_code": product.get("product_code"),
        "institution_id": product.get("institution_id"),
        "name": product.get("name"),
        "product_family": product.get("product_family"),
        "path": index_row.get("path"),
        "status": status,
        "issue_count": len(issues),
        "issues": issues,
        "declared_data_gaps": gaps,
        "note": "구조 완결성 판정이며 공식 원문 사실 검증 완료를 뜻하지 않습니다.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.mkdir(parents=True, exist_ok=True)

    index = read_json(INDEX)
    evidence_rows = read_json(ROOT / "data/financial_products/normalized/evidence_refs.json").get("evidence_refs", [])
    source_rows = read_json(ROOT / "data/financial_products/normalized/source_documents.json").get("source_documents", [])
    evidence_to_source = {row["evidence_ref_id"]: row.get("source_id", "") for row in evidence_rows}
    evidence_ids = set(evidence_to_source)
    source_ids = {row["source_id"] for row in source_rows}

    ledger: list[dict[str, Any]] = []
    for row in index.get("products", []):
        path = ROOT / row["path"]
        if not path.exists():
            ledger.append({
                "product_code": row.get("product_code"), "institution_id": row.get("institution_id"),
                "name": None, "product_family": row.get("product_family"), "path": row.get("path"),
                "status": "INSUFFICIENT", "issue_count": 1,
                "issues": [{"code": "PRODUCT_FILE_MISSING", "path": row.get("path"), "severity": "BLOCKER", "detail": "상품 파일이 없습니다."}],
                "declared_data_gaps": [], "note": "구조 완결성 판정이며 공식 원문 사실 검증 완료를 뜻하지 않습니다.",
            })
            continue
        raw = path.read_bytes()
        actual_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
        product = json.loads(raw)
        ledger.append(audit_product(row, product, evidence_ids, source_ids, evidence_to_source, actual_hash))

    ledger_path = output / "review_ledger.jsonl"
    ledger_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ledger), encoding="utf-8")
    statuses = Counter(row["status"] for row in ledger)
    issue_codes = Counter(issue["code"] for row in ledger for issue in row["issues"])
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    for row in ledger:
        by_family[row["product_family"]][row["status"]] += 1
    summary = {
        "audit_kind": "STRUCTURE_COMPLETENESS",
        "factual_verification_completed": False,
        "index_product_count": index.get("product_count"),
        "reviewed_product_count": len(ledger),
        "unique_product_count": len({row["product_code"] for row in ledger}),
        "status_counts": dict(statuses),
        "by_family": {family: dict(counts) for family, counts in sorted(by_family.items())},
        "issue_counts": dict(issue_codes.most_common()),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 금융상품 구조 완결성 전수 점검", "",
        f"- index 상품: {summary['index_product_count']:,}개",
        f"- 장부 기록: {summary['reviewed_product_count']:,}개 (고유 {summary['unique_product_count']:,}개)",
        f"- 구조 충분: {statuses['STRUCTURALLY_SUFFICIENT']:,}개",
        f"- 부분 구조화: {statuses['PARTIAL']:,}개",
        f"- 구조 불충분: {statuses['INSUFFICIENT']:,}개", "",
        "> 이 결과는 각 JSON의 계산·필터링 가능성을 검사한 것입니다. 공식 원문과의 사실 대조 완료를 의미하지 않습니다.", "",
        "## 상품군별", "", "| 상품군 | 충분 | 부분 | 불충분 |", "|---|---:|---:|---:|",
    ]
    for family, counts in sorted(by_family.items()):
        lines.append(f"| {family} | {counts['STRUCTURALLY_SUFFICIENT']:,} | {counts['PARTIAL']:,} | {counts['INSUFFICIENT']:,} |")
    lines.extend(["", "## 주요 결함", "", "| 결함 코드 | 상품 수 |", "|---|---:|"])
    for code, count in issue_codes.most_common():
        lines.append(f"| {code} | {count:,} |")
    (output / "FINAL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(ledger) == index.get("product_count") == summary["unique_product_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
