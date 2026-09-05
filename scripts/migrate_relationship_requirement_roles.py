#!/usr/bin/env python3
"""Annotate normalized relationship rows with their catalog role.

This is a one-time data migration.  Neither this migration nor the runtime
loader infers business meaning from Korean display text.  The notice IDs below
are the reviewed catalog rows; every other row remains a subscription
requirement so the migration fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ELIGIBILITY = "SUBSCRIPTION_ELIGIBILITY"
NOTICE = "OPERATING_NOTICE"

REVIEWED_OPERATING_NOTICE_IDS = frozenset(
    {
        "INST-KR-000010-3-CD5185C8011-REL-272C7FF8D959",
        "INST-KR-000018-3-0001-REL-8EB14924F439",
        "INST-KR-000055-3-0001-REL-B03D8733C132",
        "INST-KR-000055-3-C4424004A31-REL-E623548B7A32",
        "INST-KR-000083-3-0001-REL-31C4F8C9E72E",
        "INST-KR-000083-3-C540381BA58-REL-31C4F8C9E72E",
        "INST-KR-000204-3-C8A26F1B79F-REL-E73655E2F860",
        "INST-KR-000219-3-C352EB55ABC-REL-EC79757E3FDD",
        "INST-KR-000252-3-C2ACE51837D-REL-C6902DC809E4",
        "INST-KR-000252-3-C55460DB506-REL-FFF7030CF556",
        "INST-KR-000298-3-0001-REL-7554AB0ED904",
        "INST-KR-000298-3-C0E52A7AA3A-REL-7554AB0ED904",
        "INST-KR-000298-3-C19CBBFE988-REL-44D1543981FA",
        "INST-KR-000298-3-C5043A32519-REL-A1780029D4D0",
        "INST-KR-000298-3-C7209609F45-REL-44D1543981FA",
        "INST-KR-000298-3-CB2A995A542-REL-7554AB0ED904",
        "INST-KR-000516-3-CAA37940AC3-REL-1E84B0777B80",
        "INST-KR-000739-3-C6341693961-REL-E3968579E38A",
        "INST-KR-000739-3-CF383D5AE9E-REL-EA9A76492223",
        "INST-KR-000743-3-CCFC604D0E3-REL-4FE4701177D0",
        "INST-KR-000757-3-CA784DA5A62-REL-740F32C37B77",
        "INST-KR-000796-3-0002-REL-39C4E99C4D38",
        "INST-KR-000830-3-C90225C0EC4-REL-DB19CB227A77",
        "INST-KR-000856-3-C5A71420F52-REL-E609041BF8DD",
        "INST-KR-000856-3-CA93F7AD6EC-REL-BB3AC93F3252",
        "INST-KR-001106-3-0002-REL-A99B23912D57",
    }
)


def classify(row: dict[str, Any]) -> str:
    requirement_id = row.get("requirement_id")
    if not isinstance(requirement_id, str) or not requirement_id:
        raise ValueError("relationship requirement row has no stable requirement_id")
    return NOTICE if requirement_id in REVIEWED_OPERATING_NOTICE_IDS else ELIGIBILITY


def migrate(root: Path, *, check: bool = False) -> tuple[int, int]:
    changed = 0
    rows = 0
    for path in sorted((root / "products").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        policy = payload.get("eligibility_policy") or {}
        requirements = policy.get("relationship_requirements")
        if not isinstance(requirements, list):
            continue
        dirty = False
        for row in requirements:
            if not isinstance(row, dict):
                continue
            role = classify(row)
            rows += 1
            if row.get("requirement_role") != role:
                row["requirement_role"] = role
                dirty = True
        if dirty:
            changed += 1
            if not check:
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
    return changed, rows


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_integrity_metadata(root: Path) -> None:
    """Refresh published index/manifest inventories after product edits."""

    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for row in index.get("products", []):
        product_path = Path(row["path"])
        if product_path.exists():
            row["sha256"] = sha256(product_path)
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    manifest_path = root / "manifests" / f"{index['manifest_batch']}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest.get("files", []):
        path = Path(row["path"])
        if path.exists():
            row["sha256"] = sha256(path)
            row["size_bytes"] = path.stat().st_size
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    hash_path = Path(manifest["hash_audit_file"])
    hash_audit = json.loads(hash_path.read_text(encoding="utf-8"))
    for row in hash_audit.get("files", []):
        path = Path(row["path"])
        if path.exists():
            row["sha256"] = sha256(path)
            row["size_bytes"] = path.stat().st_size
    hash_path.write_text(
        json.dumps(hash_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("data/financial_products/normalized"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed, rows = migrate(args.root, check=args.check)
    if not args.check:
        refresh_integrity_metadata(args.root)
    action = "would change" if args.check else "changed"
    print(f"{action} {changed} files; annotated {rows} relationship rows")


if __name__ == "__main__":
    main()
