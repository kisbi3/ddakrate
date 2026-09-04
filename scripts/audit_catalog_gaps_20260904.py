#!/usr/bin/env python3
"""Audit data gaps in the published normalized product catalog.

The published product's ``source_ref_ids`` point to evidence references, not
directly to source documents.  This audit deliberately follows that two-step
relationship before checking URLs and document types.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
NORMALIZED = ROOT / "data/financial_products/normalized"
INDEX_PATH = NORMALIZED / "index.json"
EVIDENCE_PATH = NORMALIZED / "evidence_refs.json"
SOURCE_PATH = NORMALIZED / "source_documents.json"

EXPECTED_INITIAL_COUNTS = {
    "catalog_products": 4306,
    "missing_web_link": 55,
    "no_rate_entries": 67,
    "no_rate_entries_performance_observations": 11,
    "no_rate_entries_ranking_ineligible": 3,
    "no_rate_entries_declared_rate_gap": 50,
    "no_rate_entries_silent": 3,
    "missing_document_type": 28,
    "data_gap_schema_count": 14,
    "field_schema_gap_items": 108,
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_published_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_active_registries(
    index: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load registries named by the index's active published manifest.

    Correction releases keep older registries immutable and point a new
    manifest at a batch-specific copy.  Resolving through that manifest keeps
    this audit aligned with the runtime loader after such a publication while
    retaining the original-root fallback for older indexes.
    """

    batch = str(index.get("manifest_batch") or index.get("source_staging_batch") or "")
    if not batch:
        return load_json(EVIDENCE_PATH), load_json(SOURCE_PATH)
    manifest_path = NORMALIZED / "manifests" / f"{batch}.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Active normalized manifest not found: {manifest_path}")
    manifest = load_json(manifest_path)
    evidence_path = manifest.get("evidence_ref_registry")
    source_path = manifest.get("source_document_registry")
    if not isinstance(evidence_path, str) or not isinstance(source_path, str):
        raise ValueError(f"Active manifest has no registry paths: {manifest_path}")
    return load_json(resolve_published_path(evidence_path)), load_json(
        resolve_published_path(source_path)
    )


def as_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def resolve_sources(
    product: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    source_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], list[str]]:
    """Resolve product source refs through evidence_refs to source_documents."""

    evidence_rows: list[Mapping[str, Any]] = []
    source_rows: list[Mapping[str, Any]] = []
    unresolved: list[str] = []
    seen_evidence: set[str] = set()
    seen_sources: set[str] = set()

    for ref_id in as_strings(product.get("source_ref_ids")):
        evidence = evidence_by_id.get(ref_id)
        if evidence is None:
            unresolved.append(f"evidence:{ref_id}")
            continue
        evidence_id = str(evidence.get("evidence_ref_id") or ref_id)
        if evidence_id not in seen_evidence:
            seen_evidence.add(evidence_id)
            evidence_rows.append(evidence)

        source_id = evidence.get("source_id")
        if not isinstance(source_id, str):
            unresolved.append(f"source_for_evidence:{evidence_id}")
            continue
        source = source_by_id.get(source_id)
        if source is None:
            unresolved.append(f"source:{source_id}")
            continue
        if source_id not in seen_sources:
            seen_sources.add(source_id)
            source_rows.append(source)

    return evidence_rows, source_rows, unresolved


def evidence_url_values(row: Mapping[str, Any]) -> Iterable[str]:
    locator = row.get("locator")
    if isinstance(locator, Mapping):
        value = locator.get("url")
        if isinstance(value, str):
            yield value


def source_url_values(row: Mapping[str, Any]) -> Iterable[str]:
    value = row.get("url")
    if isinstance(value, str):
        yield value


def has_http_url(
    evidence_rows: Iterable[Mapping[str, Any]],
    source_rows: Iterable[Mapping[str, Any]],
) -> bool:
    return any(
        value.startswith(("http://", "https://"))
        for row in evidence_rows
        for value in evidence_url_values(row)
    ) or any(
        value.startswith(("http://", "https://"))
        for row in source_rows
        for value in source_url_values(row)
    )


def gap_path_or_field(gap: Any) -> str:
    if not isinstance(gap, Mapping):
        return ""
    # Both forms exist in the published catalog.  Keep this helper explicit so
    # callers cannot accidentally regress to g["path"] only.
    return str(gap.get("path") or gap.get("field") or "")


def is_declared_rate_gap(gap: Any) -> bool:
    """Return whether a gap explicitly concerns a rate/yield observation."""

    if not isinstance(gap, Mapping):
        return False
    searchable = " ".join(
        str(gap.get(key, ""))
        for key in ("path", "field", "reason", "message", "reason_code")
    ).lower()
    markers = (
        "rate",
        "yield",
        "interest_rate",
        "performance",
        "금리",
        "수익률",
        "실적수익",
        "실적 수익",
        "이율",
    )
    return any(marker in searchable for marker in markers)


def product_summary(
    row: Mapping[str, Any],
    product: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    source_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    evidence_rows, source_rows, unresolved = resolve_sources(
        product, evidence_by_id, source_by_id
    )
    version_metadata = product.get("version_metadata") or {}
    gaps = version_metadata.get("data_gaps") or []
    return_policy = product.get("return_policy") or {}
    rate_entries = return_policy.get("rate_entries") or []
    has_performance_observations = bool(
        return_policy.get("performance_observations")
    )
    ranking_eligible = product.get("ranking_eligible")
    if ranking_eligible is None:
        ranking_eligible = return_policy.get("ranking_eligible")
    declared_rate_gap = any(is_declared_rate_gap(gap) for gap in gaps)
    no_document_type = bool(source_rows) and all(
        not source.get("document_type") for source in source_rows
    )
    return {
        "product_code": product.get("product_code") or row.get("product_code"),
        "product_name": product.get("name"),
        "product_family": row.get("product_family") or product.get("product_family"),
        "institution_id": row.get("institution_id") or product.get("institution_id"),
        "path": row.get("path"),
        "source_ref_ids": as_strings(product.get("source_ref_ids")),
        "source_document_types": sorted(
            {str(source.get("document_type")) for source in source_rows if source.get("document_type")}
        ),
        "source_urls": sorted(
            {
                value
                for source in source_rows
                for value in source_url_values(source)
            }
            | {
                value
                for evidence in evidence_rows
                for value in evidence_url_values(evidence)
            }
        ),
        "unresolved_source_refs": unresolved,
        "has_web_link": has_http_url(evidence_rows, source_rows),
        "has_document_type": not no_document_type,
        "rate_entries_count": len(rate_entries) if isinstance(rate_entries, list) else None,
        "has_performance_observations": has_performance_observations,
        "ranking_eligible": ranking_eligible,
        "declared_rate_gap": declared_rate_gap,
        "data_gap_paths_or_fields": [gap_path_or_field(gap) for gap in gaps],
        "data_gap_schema": [
            sorted(gap.keys())
            for gap in gaps
            if isinstance(gap, Mapping)
        ],
    }


def audit() -> dict[str, Any]:
    index = load_json(INDEX_PATH)
    evidence_payload, source_payload = load_active_registries(index)
    evidence_by_id = {
        row["evidence_ref_id"]: row
        for row in evidence_payload.get("evidence_refs", [])
        if isinstance(row, Mapping) and row.get("evidence_ref_id")
    }
    source_by_id = {
        row["source_id"]: row
        for row in source_payload.get("source_documents", [])
        if isinstance(row, Mapping) and row.get("source_id")
    }

    summaries: list[dict[str, Any]] = []
    for row in index.get("products", []):
        product = load_json(ROOT / row["path"])
        summaries.append(product_summary(row, product, evidence_by_id, source_by_id))

    missing_web_link = [item for item in summaries if not item["has_web_link"]]
    no_rate_entries = [item for item in summaries if item["rate_entries_count"] == 0]
    no_rate_performance = [
        item for item in no_rate_entries if item["has_performance_observations"]
    ]
    no_rate_ranking_ineligible = [
        item for item in no_rate_entries if item["ranking_eligible"] is False
    ]
    no_rate_declared_gap = [
        item
        for item in no_rate_entries
        if item not in no_rate_performance
        and item not in no_rate_ranking_ineligible
        and item["declared_rate_gap"]
    ]
    no_rate_silent = [
        item
        for item in no_rate_entries
        if item not in no_rate_performance
        and item not in no_rate_ranking_ineligible
        and item not in no_rate_declared_gap
    ]
    missing_document_type = [item for item in summaries if not item["has_document_type"]]

    schema_counts: Counter[tuple[str, ...]] = Counter(
        tuple(schema)
        for item in summaries
        for schema in item["data_gap_schema"]
    )
    field_schema_gap_items = sum(
        count for schema, count in schema_counts.items() if "field" in schema
    )
    schema_counts_for_json = {
        ", ".join(schema): count for schema, count in sorted(schema_counts.items())
    }

    def by_institution(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        return dict(sorted(Counter(item["institution_id"] for item in items).items()))

    return {
        "counts": {
            "catalog_products": len(summaries),
            "missing_web_link": len(missing_web_link),
            "no_rate_entries": len(no_rate_entries),
            "no_rate_entries_performance_observations": len(no_rate_performance),
            "no_rate_entries_ranking_ineligible": len(no_rate_ranking_ineligible),
            "no_rate_entries_declared_rate_gap": len(no_rate_declared_gap),
            "no_rate_entries_silent": len(no_rate_silent),
            "missing_document_type": len(missing_document_type),
            "data_gap_schema_count": len(schema_counts),
            "field_schema_gap_items": field_schema_gap_items,
            "unresolved_source_ref_products": sum(
                bool(item["unresolved_source_refs"]) for item in summaries
            ),
        },
        "missing_web_link_by_institution": by_institution(missing_web_link),
        "missing_document_type_by_institution": by_institution(missing_document_type),
        "data_gap_schema_counts": schema_counts_for_json,
        "missing_web_link_products": missing_web_link,
        "no_rate_entries_performance_products": no_rate_performance,
        "no_rate_entries_ranking_ineligible_products": no_rate_ranking_ineligible,
        "no_rate_entries_declared_gap_products": no_rate_declared_gap,
        "no_rate_entries_silent_products": no_rate_silent,
        "missing_document_type_products": missing_document_type,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assert-initial",
        action="store_true",
        help="Fail unless the handoff's pre-change counts are reproduced.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print only counts and distributions.",
    )
    args = parser.parse_args()
    report = audit()
    if args.assert_initial:
        observed = report["counts"]
        mismatches = {
            key: {"expected": expected, "observed": observed.get(key)}
            for key, expected in EXPECTED_INITIAL_COUNTS.items()
            if observed.get(key) != expected
        }
        if mismatches:
            print(json.dumps({"mismatches": mismatches, **report}, ensure_ascii=False, indent=2))
            return 1

    output = {"counts": report["counts"]}
    if args.compact:
        output.update(
            {
                "missing_web_link_by_institution": report["missing_web_link_by_institution"],
                "missing_document_type_by_institution": report[
                    "missing_document_type_by_institution"
                ],
                "data_gap_schema_counts": report["data_gap_schema_counts"],
                "no_rate_entries_silent_products": report[
                    "no_rate_entries_silent_products"
                ],
            }
        )
    else:
        output = report
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
