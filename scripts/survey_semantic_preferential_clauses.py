#!/usr/bin/env python3
"""Classify unread preferential clauses for 1st-sector (BANK) products.

This is a keyword + catalog-structure survey, not a runtime LLM pass and not
a catalog rewrite. Packet membership follows the experimental compiler in
``codex/semantic-preferential-questions``: unadapted canonical rules and
``SOURCE_CLAUSE_GATE`` boolean placeholders. Typed decomposed rules are
read-only context and are not counted as unread clauses.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/survey_semantic_preferential_clauses.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eligibility.catalog.normalized_loader import load_normalized_product_catalog
from eligibility.schema.enums import ComparisonOperator
from eligibility.schema.product import ProductDefinition
from eligibility.schema.rule import FactComparisonRule


BANK_SECTOR = "BANK"
OUTPUT_JSON = ROOT / "docs/handoff/SEMANTIC_BANK_CLAUSE_SURVEY_20260906.json"

CHILDREN_RE = re.compile(
    r"자녀|다자녀|미성년 자녀|출산|출생아|아이\s*\d|소아|영아|유아"
)
MARRIAGE_RE = re.compile(r"혼인|결혼기념|결혼일|혼인신고|배우자 명의")
PREGNANCY_RE = re.compile(r"임신")
MARKETING_RE = re.compile(r"마케팅|광고성|수신\s*동의|상품\s*안내|정보성\s*동의")
CARD_RE = re.compile(r"카드")
SALARY_RE = re.compile(r"급여이체|급여계좌|급여\s*수령|급여실적|급여 입금")
FIRST_TX_RE = re.compile(
    r"첫거래|첫\s*거래|신규고객|신규 고객|당행.*없|기존고객이 아닌|"
    r"최근\s*\d+\s*개월.*없|거래실적이 없는|비고객"
)
HOLDING_RE = re.compile(r"예적금.*보유|청약.*보유|상품보유|기존.*가입")
AUTO_TRANSFER_RE = re.compile(r"자동이체")
APP_RE = re.compile(r"앱\s*로그인|앱\s*이용|모바일앱|오픈뱅킹|마이데이터|MyData")
MEMBERSHIP_RE = re.compile(r"회원\s*가입|멤버십|제휴")
CHANNEL_RE = re.compile(r"인터넷.?뱅킹|모바일.?뱅킹에서 가입|비대면 가입")
HOME_LOAN_RE = re.compile(r"주택담보대출|전세자금대출")
CROSS_TX_RE = re.compile(r"교차거래")


@dataclass(frozen=True)
class ClauseRow:
    product_id: str
    product_name: str
    institution_id: str
    institution_name: str
    product_family: str
    rule_id: str
    title: str
    text: str
    fact_key: str
    source_semantics: str
    intake: str
    official_only: bool
    empty_text: bool
    same_source_tier: bool
    reward_kind: str
    reward_value: str
    classification: str
    text_preview: str


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def clause_text(row: dict[str, Any]) -> str:
    text = row.get("source_clause_text") or row.get("condition_text") or ""
    return text.strip() if isinstance(text, str) else ""


def first_fact_key(row: dict[str, Any]) -> str:
    for node in walk(row.get("condition") or {}):
        key = node.get("fact_key")
        if key:
            return str(key)
    return ""


def source_semantics(row: dict[str, Any]) -> str:
    for node in walk(row.get("condition") or {}):
        value = node.get("source_semantics")
        if value:
            return str(value)
    return ""


def is_official_only(row: dict[str, Any], policy: dict[str, Any]) -> bool:
    rid = row.get("rule_id")
    fact_keys = {node.get("fact_key") for node in walk(row.get("condition") or {}) if node.get("fact_key")}
    metadata = {
        "rule": row,
        "global_application": policy.get("global_application") or {},
        "fact_definitions": [
            item
            for item in policy.get("fact_definitions") or []
            if item.get("fact_key") in fact_keys
        ],
        "fulfillment_details": [
            item
            for item in policy.get("fulfillment_details") or []
            if rid in (item.get("applies_to_rule_ids") or [])
        ],
    }
    nodes = list(walk(metadata))
    self_report = any(node.get("self_report_eligible") is True for node in nodes)
    return (not self_report) or any(
        node.get("self_report_eligible") is False
        or (
            node.get("authority")
            and node["authority"]
            not in {
                "USER_DECLARED",
                "CUSTOMER_DECLARATION",
                "CUSTOMER_DECLARATION_OR_BANK_RECORD",
            }
        )
        for node in nodes
    ) or bool(metadata["fulfillment_details"])


def is_packet_clause(product: ProductDefinition, row: dict[str, Any]) -> tuple[bool, str]:
    """Mirror experimental ``product_packet`` intake selection."""

    rid = row.get("rule_id")
    if not rid:
        return False, "no_rule_id"
    runtime_by_id = {
        item.canonical_rule_id or item.rule.rule_id: item
        for item in product.preferential_rules
    }
    existing = runtime_by_id.get(str(rid))
    raw_predicate = (row.get("condition") or {}).get("predicate") or {}
    opaque_gate = (
        existing is not None
        and isinstance(existing.rule, FactComparisonRule)
        and existing.rule.operator == ComparisonOperator.EQ
        and existing.rule.expected is True
        and existing.rule.subject_selector is None
        and existing.rule.value_path is None
        and raw_predicate.get("source_semantics") == "SOURCE_CLAUSE_GATE"
    )
    if existing is not None and not opaque_gate:
        return False, "typed_runtime"
    if opaque_gate:
        return True, "source_clause_gate"
    return True, "unadapted"


def classify_text(
    *,
    title: str,
    text: str,
    fact_key: str,
    empty: bool,
    same_source_tier: bool,
) -> str:
    blob = f"{title}\n{text}\n{fact_key}".upper()
    compact = f"{title} {text} {fact_key}"
    if empty:
        return "UNRESOLVED_EMPTY"
    if same_source_tier:
        return "SAME_SOURCE_TIER"
    if PREGNANCY_RE.search(compact):
        return "PREGNANCY_SLOT"
    if CHILDREN_RE.search(compact):
        return "CHILDREN_SLOT"
    if MARRIAGE_RE.search(compact):
        return "MARRIAGE_SLOT"
    if FIRST_TX_RE.search(compact) or "PRODUCT_HOLDING" in blob or "NEW_CUSTOMER" in blob:
        return "PRESEARCH_HOLDING_HISTORY"
    if MARKETING_RE.search(compact) or "MARKETING" in blob or "CONSENT" in blob:
        return "EXISTING_MARKETING_QUESTION"
    if SALARY_RE.search(compact) or "SALARY" in blob or "INCOME_CREDIT" in blob:
        return "EXISTING_TYPED_SALARY"
    if CARD_RE.search(compact) or "CARD_" in blob:
        return "EXISTING_TYPED_CARD"
    if CHANNEL_RE.search(compact) or "SUBSCRIPTION_CHANNEL" in blob:
        return "EXISTING_CHANNEL_QUESTION"
    if HOME_LOAN_RE.search(compact) or CROSS_TX_RE.search(compact):
        return "EXISTING_SPECIAL_FAMILY"
    if AUTO_TRANSFER_RE.search(compact) or "AUTO_TRANSFER" in blob:
        return "EXISTING_AUTO_TRANSFER"
    if APP_RE.search(compact) or MEMBERSHIP_RE.search(compact):
        return "EXISTING_APP_OR_MEMBERSHIP"
    if HOLDING_RE.search(compact):
        return "PRESEARCH_HOLDING_HISTORY"
    return "UNRESOLVED"


def same_source_tier_ids(rows: list[dict[str, Any]]) -> set[str]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        text = clause_text(row)
        if not text:
            continue
        grouped[text].append(row)
    flagged: set[str] = set()
    for group in grouped.values():
        if len(group) < 2:
            continue
        rewards = {
            json.dumps(item.get("reward") or {}, ensure_ascii=False, sort_keys=True)
            for item in group
        }
        if len(rewards) < 2:
            continue
        for item in group:
            rid = item.get("rule_id")
            if rid:
                flagged.add(str(rid))
    return flagged


def sample_rows(rows: Iterable[ClauseRow], limit: int = 8) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append(
            {
                "product_id": row.product_id,
                "product_name": row.product_name,
                "institution_name": row.institution_name,
                "rule_id": row.rule_id,
                "title": row.title,
                "fact_key": row.fact_key,
                "intake": row.intake,
                "official_only": row.official_only,
                "text_preview": row.text_preview,
            }
        )
        if len(out) >= limit:
            break
    return out


def main() -> int:
    products = load_normalized_product_catalog(verify_hashes=False)
    bank_products = [
        product
        for product in products
        if product.metadata is not None
        and product.metadata.institution_sector == BANK_SECTOR
    ]
    clauses: list[ClauseRow] = []
    packet_products = 0
    empty_fail_products = 0
    intake_counts: Counter[str] = Counter()
    bank_family_counts: Counter[str] = Counter()

    for product in bank_products:
        bank_family_counts[product.product_type] += 1
        if product.normalized is None:
            continue
        policy = product.normalized.return_policy.get("preferential_policy") or {}
        rows = [row for row in (policy.get("rules") or []) if isinstance(row, dict)]
        tier_ids = same_source_tier_ids(rows)
        product_packet_rows: list[ClauseRow] = []
        for row in rows:
            included, intake = is_packet_clause(product, row)
            if not included:
                continue
            text = clause_text(row)
            empty = not text
            rid = str(row.get("rule_id") or "")
            title = str(row.get("title") or "")
            fact_key = first_fact_key(row)
            reward = row.get("reward") or {}
            classification = classify_text(
                title=title,
                text=text,
                fact_key=fact_key,
                empty=empty,
                same_source_tier=rid in tier_ids,
            )
            product_packet_rows.append(
                ClauseRow(
                    product_id=product.product_id,
                    product_name=product.name,
                    institution_id=product.institution_id,
                    institution_name=product.metadata.institution_name or "",
                    product_family=product.product_type,
                    rule_id=rid,
                    title=title,
                    text=text,
                    fact_key=fact_key,
                    source_semantics=source_semantics(row),
                    intake=intake,
                    official_only=is_official_only(row, policy),
                    empty_text=empty,
                    same_source_tier=rid in tier_ids,
                    reward_kind=str(reward.get("kind") or ""),
                    reward_value=str(reward.get("value") or ""),
                    classification=classification,
                    text_preview=re.sub(r"\s+", " ", text)[:180],
                )
            )
        if not product_packet_rows:
            continue
        packet_products += 1
        if any(item.empty_text for item in product_packet_rows):
            empty_fail_products += 1
        for item in product_packet_rows:
            intake_counts[item.intake] += 1
            clauses.append(item)

    class_counts = Counter(item.classification for item in clauses)
    official_counts = Counter(
        "official_only" if item.official_only else "self_report_eligible"
        for item in clauses
    )
    family_counts = Counter(item.product_family for item in clauses)
    by_class: dict[str, list[ClauseRow]] = defaultdict(list)
    for item in clauses:
        by_class[item.classification].append(item)

    mappable = sum(
        class_counts[key]
        for key in class_counts
        if key
        not in {
            "UNRESOLVED",
            "UNRESOLVED_EMPTY",
        }
    )
    payload = {
        "catalog_product_count": len(products),
        "bank_product_count": len(bank_products),
        "bank_products_by_family": dict(bank_family_counts),
        "packet_product_count": packet_products,
        "packet_clause_count": len(clauses),
        "empty_text_products_that_would_fail_whole_packet": empty_fail_products,
        "intake": dict(intake_counts),
        "authority": dict(official_counts),
        "clauses_by_product_family": dict(family_counts),
        "classification_counts": dict(class_counts),
        "mappable_clause_count": mappable,
        "unresolved_clause_count": class_counts["UNRESOLVED"] + class_counts["UNRESOLVED_EMPTY"],
        "new_slot_required": False,
        "new_slot_reason": (
            "기존 사전 질문 9·10번, 카드/급여/마케팅/자동이체 등 typed 묶기, "
            "그리고 자녀·혼인·임신 칸으로 1금융권 unread 절의 상당수를 설명할 수 있다. "
            "나머지는 이번 단계에서 UNRESOLVED로 두고 새 칸을 만들지 않는다."
        ),
        "samples": {key: sample_rows(value) for key, value in sorted(by_class.items())},
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "samples"}, ensure_ascii=False, indent=2))
    print(f"\nWrote {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
