#!/usr/bin/env python3
"""Build a safe, display-oriented eligibility and preferential-rate snapshot.

The canonical product documents remain the contractual source of truth.  This
snapshot makes their conditions consistently consumable without pretending that
free text is a machine-evaluable rule.  Every extracted value retains the
original text and is tagged with its evaluation safety.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "data/financial_products/normalized/index.json"
DEFAULT_OUTPUT = ROOT / "data/financial_products/normalized/condition_snapshots/eligibility_preferential_20260828.json"
KST = timezone(timedelta(hours=9))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def decimal_text(value: Any) -> str | None:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return None


def eligibility_text(policy: dict[str, Any]) -> str:
    return compact(policy.get("eligibility_text") or policy.get("raw_text"))


def eligibility_constraints(text: str) -> tuple[list[dict[str, Any]], str]:
    """Extract only literal, non-ambiguous subscription facts from text."""
    if not text:
        return [], "MISSING"
    normalized = re.sub(r"\s+", " ", text)
    if re.fullmatch(r"(?:가입)?\s*제한\s*없음", normalized, re.IGNORECASE):
        return [{"kind": "UNRESTRICTED", "operator": "EQ", "value": True, "source_text": text}], "STRUCTURED"

    result: list[dict[str, Any]] = []
    lower = normalized.lower()
    age_min = re.search(r"만\s*(\d{1,3})\s*세\s*(?:이상|부터)", normalized)
    age_max = re.search(r"만\s*(\d{1,3})\s*세\s*(?:이하|미만)", normalized)
    if age_min:
        result.append({"kind": "AGE", "operator": "GTE", "value": int(age_min.group(1)), "unit": "YEAR", "source_text": age_min.group(0)})
    if age_max:
        result.append({"kind": "AGE", "operator": "LTE" if "이하" in age_max.group(0) else "LT", "value": int(age_max.group(1)), "unit": "YEAR", "source_text": age_max.group(0)})
    if "여성" in normalized:
        result.append({"kind": "GENDER", "operator": "EQ", "value": "FEMALE", "source_text": "여성"})
    elif "남성" in normalized:
        result.append({"kind": "GENDER", "operator": "EQ", "value": "MALE", "source_text": "남성"})
    sole_proprietor_forbidden = bool(
        re.search(r"개인사업자.{0,40}(?:가입\s*)?불가|(?:가입\s*)?불가.{0,40}개인사업자", normalized)
    )
    if "개인 및 개인사업자" in normalized and not sole_proprietor_forbidden:
        result.append({"kind": "CUSTOMER_TYPE", "operator": "IN", "value": ["INDIVIDUAL", "SOLE_PROPRIETOR"], "source_text": "개인 및 개인사업자"})
    elif re.search(r"실명의\s*개인|개인\s*고객|개인에\s*한", normalized):
        result.append({"kind": "CUSTOMER_TYPE", "operator": "EQ", "value": "INDIVIDUAL", "source_text": "실명의 개인" if "실명의 개인" in normalized else "개인 고객"})
    if "군인" in lower or "장병" in lower:
        result.append({"kind": "MILITARY_SERVICE", "operator": "REQUIRES_VERIFICATION", "value": True, "source_text": "군인/장병 대상 문구"})
    return result, "STRUCTURED" if result else "TEXT_ONLY"


def channels(policy: dict[str, Any]) -> list[str]:
    values = policy.get("subscription_channels") or policy.get("allowed_channels") or []
    if isinstance(values, str):
        values = re.split(r"[,/·]", values)
    return sorted({str(value).strip() for value in values if str(value).strip()})


def preferential_conditions(product: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    policy = product.get("return_policy") or {}
    entries = {str(row.get("rate_id")): row for row in policy.get("rate_entries") or [] if row.get("rate_id")}
    rows: list[dict[str, Any]] = []
    linked: set[str] = set()
    for condition in [*(product.get("standard_conditions") or []), *(product.get("custom_bindings") or [])]:
        if condition.get("purpose") != "PREFERENTIAL_RETURN":
            continue
        for rate_id in condition.get("reward_refs") or []:
            entry = entries.get(str(rate_id), {})
            calculation = entry.get("calculation") or {}
            linked.add(str(rate_id))
            rows.append({
                "condition_id": condition.get("condition_id") or condition.get("binding_id"),
                "title": condition.get("title") or entry.get("official_label") or "우대 조건",
                "condition_type": condition.get("condition_type") or "CUSTOM",
                "criteria": condition.get("criteria") or {},
                "reward_rate_id": rate_id,
                "reward_value": decimal_text(calculation.get("value")),
                "reward_unit": calculation.get("unit"),
                "condition_text": entry.get("condition_text") or (condition.get("criteria") or {}).get("source_text"),
                "evaluation_status": "STRUCTURED_LINKED",
            })
    seen_disclosures: set[tuple[str, str | None]] = {
        (compact(str(row.get("condition_text") or "")), row.get("reward_value"))
        for row in rows
    }
    for row in policy.get("preferential_condition_disclosures") or []:
        candidate = (compact(str(row.get("condition_text") or "")), decimal_text((row.get("extracted_reward_candidate") or {}).get("value")))
        if candidate in seen_disclosures:
            continue
        seen_disclosures.add(candidate)
        rows.append({
            "condition_id": row.get("disclosure_id"),
            "title": row.get("title") or "우대 조건",
            "condition_type": "DISCLOSURE",
            "criteria": {},
            "reward_rate_id": None,
            "reward_value": decimal_text((row.get("extracted_reward_candidate") or {}).get("value")),
            "reward_unit": (row.get("extracted_reward_candidate") or {}).get("unit"),
            "condition_text": row.get("condition_text"),
            "evaluation_status": "DISPLAY_ONLY",
        })
    for rate_id, entry in entries.items():
        if entry.get("role") == "PREFERENTIAL" and rate_id not in linked:
            calculation = entry.get("calculation") or {}
            candidate = (compact(str(entry.get("condition_text") or "")), decimal_text(calculation.get("value")))
            if candidate in seen_disclosures:
                continue
            seen_disclosures.add(candidate)
            rows.append({
                "condition_id": None,
                "title": entry.get("official_label") or "우대 금리",
                "condition_type": "UNLINKED",
                "criteria": {},
                "reward_rate_id": rate_id,
                "reward_value": decimal_text(calculation.get("value")),
                "reward_unit": calculation.get("unit"),
                "condition_text": entry.get("condition_text"),
                "evaluation_status": "REVIEW_REQUIRED",
            })
    if rows:
        statuses = {str(row["evaluation_status"]) for row in rows}
        if statuses == {"STRUCTURED_LINKED"}:
            return rows, "STRUCTURED"
        if "STRUCTURED_LINKED" in statuses:
            return rows, "PARTIALLY_STRUCTURED"
        if statuses == {"DISPLAY_ONLY"}:
            return rows, "DISCLOSED_ONLY"
        if "DISPLAY_ONLY" in statuses:
            return rows, "DISCLOSED_WITH_REVIEW"
        return rows, "REVIEW_REQUIRED"

    base = [decimal_text((row.get("calculation") or {}).get("value")) for row in entries.values() if row.get("role") == "BASE"]
    maximum = decimal_text((policy.get("advertised_max_rate") or {}).get("value"))
    if maximum is None:
        maximum = next((decimal_text((row.get("calculation") or {}).get("value")) for row in entries.values() if row.get("role") == "ADVERTISED_MAXIMUM"), None)
    raw_rate_text = compact(
        policy.get("naver_raw_text")
        or (policy.get("naver_structured_fill") or {}).get("rate_text")
        or (policy.get("naver_structured_fill") or {}).get("raw_text")
    )
    has_preferential_signal = bool(
        re.search(r"우대\s*(?:금리|이율|조건)|조건별", raw_rate_text)
    )
    # A higher longest-term/tier rate is not an optional preferential reward.
    # It must be described by rate_entries/applies_to, not by a fictional
    # "조건 원문" entry.  Only retain source text here when it explicitly
    # discloses preferential conditions.
    if (
        base
        and maximum
        and has_preferential_signal
        and any(value is not None and Decimal(maximum) > Decimal(value) for value in base)
    ):
        return [{"title": "우대 조건 원문 확인 필요", "condition_type": "TEXT_ONLY", "criteria": {}, "reward_rate_id": None, "reward_value": None, "reward_unit": None, "condition_text": compact(policy.get("naver_raw_text")), "evaluation_status": "TEXT_ONLY"}], "TEXT_ONLY"
    return [], "NONE"


def record(product: dict[str, Any]) -> dict[str, Any]:
    policy = product.get("eligibility_policy") or {}
    text = eligibility_text(policy)
    constraints, status = eligibility_constraints(text)
    preferences, preference_status = preferential_conditions(product)
    return {
        "product_code": product["product_code"],
        "eligibility": {
            "status": status,
            "constraints": constraints,
            "subscription_channels": channels(policy),
            "display_text": text or None,
        },
        "preferential_rate": {
            "status": preference_status,
            "application": (product.get("return_policy") or {}).get("preferential_application") or {},
            "conditions": preferences,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    index = read_json(args.index)
    records = []
    eligibility = Counter()
    preferential = Counter()
    for row in index["products"]:
        product = read_json(ROOT / row["path"])
        item = record(product)
        records.append(item)
        eligibility[item["eligibility"]["status"]] += 1
        preferential[item["preferential_rate"]["status"]] += 1
    payload = {
        "schema_version": 1,
        "snapshot_kind": "ELIGIBILITY_AND_PREFERENTIAL_STRUCTURE",
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source_index": str(args.index.relative_to(ROOT)),
        "record_count": len(records),
        "coverage": {"eligibility": dict(sorted(eligibility.items())), "preferential_rate": dict(sorted(preferential.items()))},
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "record_count": len(records), "coverage": payload["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
