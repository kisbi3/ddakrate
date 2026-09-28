#!/usr/bin/env python3
"""Audit the published catalog with the preferential-rate quality gate."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eligibility.catalog.preferential_quality import (  # noqa: E402
    inspect_preferential_rate_quality,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print findings without returning a failing process status.",
    )
    args = parser.parse_args()

    index = json.loads(
        (ROOT / "data/financial_products/normalized/index.json").read_text(
            encoding="utf-8"
        )
    )
    issue_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    findings: list[dict[str, object]] = []
    for row in index["products"]:
        product = json.loads((ROOT / row["path"]).read_text(encoding="utf-8"))
        issues = inspect_preferential_rate_quality(product)
        if not issues:
            continue
        family_counts[row["product_family"]] += 1
        issue_counts.update(issue.code for issue in issues)
        findings.append(
            {
                "product_code": product["product_code"],
                "product_name": product["name"],
                "product_family": row["product_family"],
                "issues": [issue.__dict__ for issue in issues],
            }
        )

    print(
        json.dumps(
            {
                "catalog_product_count": len(index["products"]),
                "failing_product_count": len(findings),
                "issue_counts": dict(sorted(issue_counts.items())),
                "failing_products_by_family": dict(sorted(family_counts.items())),
                "findings": findings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if args.report_only or not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
