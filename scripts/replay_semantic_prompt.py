"""Replay captured model outputs through today's validator without API calls."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from experiment_semantic_prompt import sem, save, clause_diagnostics


def counts(compilation):
    result = Counter()
    def visit(node):
        if not isinstance(node, dict):
            return
        result[str(node.get("op"))] += 1
        for child in node.get("children") or []:
            visit(child)
    for row in compilation.get("clauses", []):
        visit(row.get("expression"))
    return dict(result)


def reconstruct(payload):
    packets = []
    for product in payload["products"]:
        clauses = tuple(sem.SourceClause(
            clause_id=row["clause_id"], canonical_id=row["source_metadata"]["rule_id"],
            source_hash=row["source_hash"], text=row["source_text"], row=row["source_metadata"],
            official_only=row["requires_official_confirmation"], reward_pp=None,
            existing_runtime_rule_id=row.get("existing_runtime_rule_id"),
        ) for row in product["raw_clauses"])
        packets.append(sem.ProductPacket(product["product_id"], "offline-replay", clauses, product))
    return packets


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    summary = []
    for run in sorted(args.source.iterdir()):
        if not (run / "raw_text.json").exists():
            continue
        raw = json.loads((run / "raw_text.json").read_text())
        packets = reconstruct(json.loads((run / "input.json").read_text()))
        accepted = sem.accept_compilation(raw, packets).model_dump(mode="json")
        before = json.loads((run / "accepted.json").read_text())
        dest = args.output / run.name
        dest.mkdir()
        save(dest / "accepted.json", accepted)
        allowed = {c.clause_id: c for packet in packets for c in packet.clauses}
        save(dest / "diagnostics.json", [clause_diagnostics(row, allowed[row["clause_id"]])
             for row in raw["clauses"] if row.get("clause_id") in allowed])
        summary.append({"run": run.name, "before": counts(before), "after": counts(accepted),
                        "changed": before != accepted,
                        "before_clause_count": len(before["clauses"]),
                        "after_clause_count": len(accepted["clauses"])})
    save(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
