#!/usr/bin/env python3
"""Create clean structuring inputs from the Naver-first source ledger.

The output contains no previous normalized rules, rates, eligibility decisions,
or display disclosures.  Naver-backed products retain their original crawl row;
the small internal-only set retains only original-text evidence and provenance.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
REBUILD_DIR = ROOT / "data/financial_products/rebuild/20260828-naver-first-01"
LEDGER_PATH = REBUILD_DIR / "source_priority_ledger.jsonl"
OUTPUT_PATH = REBUILD_DIR / "structuring_inputs.jsonl"
REPORT_PATH = REBUILD_DIR / "structuring_input_report.json"

RAW_TEXT_KEYS = {
    "raw_text",
    "naver_raw_text",
    "eligibility_text",
    "rate_text",
    "term_text",
    "amount_text",
    "tax_text",
    "liquidity_text",
    "protection_text",
    "condition_text",
    "source_text",
}
_EVIDENCE_CACHE: dict[str, list[dict[str, str]]] | None = None


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _collect_text_evidence(value: Any, path: str = "") -> list[dict[str, str]]:
    """Extract verbatim evidence only; never carry an old structured field."""

    result: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in RAW_TEXT_KEYS and isinstance(child, str) and child.strip():
                result.append({"path": child_path, "text": child})
            else:
                result.extend(_collect_text_evidence(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(_collect_text_evidence(child, f"{path}[{index}]"))
    return result


def _collect_source_refs(value: Any) -> list[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"source_ref_ids", "evidence_ref_ids"} and isinstance(child, list):
                result.update(str(item) for item in child if item)
            else:
                result.update(_collect_source_refs(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_collect_source_refs(child))
    return sorted(result)


def _evidence_text_by_ref() -> dict[str, list[dict[str, str]]]:
    """Index original evidence registries, never the archived normalized data."""

    global _EVIDENCE_CACHE
    if _EVIDENCE_CACHE is not None:
        return _EVIDENCE_CACHE
    result: dict[str, list[dict[str, str]]] = {}
    for path in sorted((ROOT / "data/financial_products/raw").rglob("evidence_refs.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for row in payload.get("evidence_refs") or []:
            identifier = row.get("evidence_ref_id") or row.get("evidence_id")
            text = row.get("source_text")
            if not identifier or not isinstance(text, str) or not text.strip():
                continue
            candidate = {
                "path": str(path.relative_to(ROOT)),
                "text": text,
            }
            bucket = result.setdefault(str(identifier), [])
            if candidate not in bucket:
                bucket.append(candidate)
    _EVIDENCE_CACHE = result
    return result


def clean_input(row: dict[str, Any]) -> dict[str, Any]:
    source = row["source"]
    priority = row["source_priority"]
    base = {
        "rebuild_input_id": row["ledger_id"].replace("NPR", "NFI", 1),
        "canonical_product_code": row["canonical_product_code"],
        "product_name": row["product_name"],
        "institution_id": row["institution_id"],
        "product_family": row["product_family"],
        "source_priority": priority,
        "source_reason": row["source_reason"],
        "structuring_status": "PENDING",
        "source_system": source["source_system"],
        "source_locator": source["source_locator"],
    }
    if priority == "NAVER_PRIMARY":
        # This is the untouched crawler payload.  It is the only source from
        # which a Naver-backed product may be structured in this rebuild.
        base["source_record_id"] = source["source_record_id"]
        base["naver_raw_record"] = source["raw_record"]
    else:
        raw_record = source["raw_record"]
        source_ref_ids = _collect_source_refs(raw_record)
        registry = _evidence_text_by_ref()
        reference_evidence = [
            {"path": f"evidence_ref:{identifier}:{item['path']}", "text": item["text"]}
            for identifier in source_ref_ids
            for item in registry.get(identifier, [])
        ]
        text_evidence = _collect_text_evidence(raw_record)
        base["internal_original_text_evidence"] = [*text_evidence, *reference_evidence]
        base["source_ref_ids"] = source_ref_ids
        base["internal_source_status"] = (
            "ORIGINAL_TEXT_READY"
            if base["internal_original_text_evidence"]
            else "SOURCE_RECOLLECTION_REQUIRED"
        )
    return base


def build_inputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [clean_input(row) for row in read_jsonl(LEDGER_PATH)]
    counts = Counter(row["source_priority"] for row in rows)
    internal = [row for row in rows if row["source_priority"] == "INTERNAL_ORIGINAL_FALLBACK"]
    report = {
        "batch": "20260828-naver-first-rebuild-01",
        "publication_status": "WORK_ONLY_NOT_PUBLISHED",
        "counts": {
            "structuring_inputs": len(rows),
            "naver_primary": counts["NAVER_PRIMARY"],
            "internal_original_fallback": counts["INTERNAL_ORIGINAL_FALLBACK"],
            "internal_fallback_original_text_ready": sum(
                row["internal_source_status"] == "ORIGINAL_TEXT_READY" for row in internal
            ),
            "internal_fallback_source_recollection_required": sum(
                row["internal_source_status"] == "SOURCE_RECOLLECTION_REQUIRED" for row in internal
            ),
        },
        "invariants": {
            "all_ledger_rows_preserved": len(rows) == 4306,
            "naver_primary_uses_only_naver_raw_record": all(
                "naver_raw_record" in row and "internal_original_text_evidence" not in row
                for row in rows
                if row["source_priority"] == "NAVER_PRIMARY"
            ),
            "internal_fallback_uses_text_evidence_only": all(
                "internal_original_text_evidence" in row and "naver_raw_record" not in row
                for row in internal
            ),
            "all_internal_fallbacks_have_explicit_source_outcome": all(
                row["internal_source_status"] in {"ORIGINAL_TEXT_READY", "SOURCE_RECOLLECTION_REQUIRED"}
                for row in internal
            ),
        },
    }
    if not all(report["invariants"].values()):
        raise ValueError(f"Clean rebuild input validation failed: {report}")
    return rows, report


def main() -> None:
    rows, report = build_inputs()
    write_jsonl(OUTPUT_PATH, rows)
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
