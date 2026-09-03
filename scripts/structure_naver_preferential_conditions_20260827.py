#!/usr/bin/env python3
"""Build reviewable preferential-condition candidates from accepted Naver text.

This command is deliberately non-publishing.  Its default mode only prints a
JSON report.  ``--candidate-dir`` writes detached, patched product copies and
review metadata below the requested directory; it never changes normalized
products, manifests, hashes, or ``normalized/index.json``.

The parser promotes only an explicitly stated annual percentage attached to a
condition.  Advertised maxima, base-rate tables and inferred differences
between base/max rates are never turned into rewards.  Mutually exclusive,
choice, tiered and capped clauses remain candidates but are marked
``REVIEW_REQUIRED`` so they cannot silently become additive runtime rewards.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "data/financial_products/normalized/index.json"

RATE_RE = re.compile(r"(?:연\s*)?(\d+(?:\.\d+)?)\s*%")
NUMBER_LINE_RE = re.compile(r"^\s*(?:\d{1,2}[.)]?|[가-힣][.)])\s*$")
CIRCLED_RE = re.compile(r"^\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])\s*(.+)$")
END_RE = re.compile(r"^(?:유형\s|금리유형|중도해지|만기후|가입대상|가입기간)")
SUMMARY_RE = re.compile(r"(?:최고|최대)\s*(?:연\s*)?\d+(?:\.\d+)?\s*%.*(?:우대|적용)")
BASE_RE = re.compile(r"(?:기본\s*(?:금리|이율)|온라인가입시\s*이율|기간별\s*금리)")
AMBIGUOUS_RE = re.compile(
    r"중복\s*(?:적용\s*)?불가|택\s*[1일]|둘\s*중|중\s*하나|각각.*최고|"
    r"최고\s*(?:연\s*)?\d|최대\s*(?:연\s*)?\d|구간별|차등"
)


@dataclass
class Candidate:
    label: str
    condition_text: str
    value: str | None
    condition_type: str
    confidence: str
    review_reasons: list[str] = field(default_factory=list)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def decimal_text(value: str) -> str:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - guarded by regex
        raise ValueError(value) from exc
    return format(result.normalize(), "f")


def compact_lines(raw: str) -> list[str]:
    return [re.sub(r"\s+", " ", row).strip() for row in raw.splitlines() if row.strip()]


def preferential_scope(raw: str) -> list[str]:
    lines = compact_lines(raw)
    starts = [
        index
        for index, line in enumerate(lines)
        if line == "조건별" or ("우대조건" in line and ("충족" in line or "적용" in line))
    ]
    if not starts:
        starts = [index for index, line in enumerate(lines) if "우대" in line and RATE_RE.search(line)]
    if not starts:
        return []
    scoped = lines[starts[0] :]
    for index, line in enumerate(scoped[1:], 1):
        if END_RE.search(line):
            return scoped[:index]
    return scoped


def blocks(lines: list[str]) -> list[list[str]]:
    """Split numbered main conditions while retaining their explanatory text."""
    result: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if NUMBER_LINE_RE.match(line):
            if current:
                result.append(current)
                current = []
            continue
        # A new numbered condition is sometimes rendered as ``1 제목``.
        inline = re.match(r"^(\d{1,2})[.)]\s+(.+)$", line)
        if inline:
            if current:
                result.append(current)
            current = [inline.group(2)]
            continue
        current.append(line)
    if current:
        result.append(current)
    return result


def classify(text: str) -> str:
    mapping = [
        ("FIRST_TRANSACTION", r"첫\s*(?:거래|적금|예금)|최초\s*(?:거래|가입)|직전.*보유하지"),
        ("MARKETING_CONSENT", r"마케팅.*동의|상품서비스.*동의"),
        ("SALARY_LINKAGE", r"급여(?:이체|수령)|월급"),
        ("CARD_SPEND", r"카드.*(?:결제금액|이용금액|만원 이상)"),
        ("CARD_USAGE", r"카드.*(?:결제|이용|실적|신규)"),
        ("NON_FACE_TO_FACE_SIGNUP", r"비대면|모바일.*가입|온라인.*가입"),
        ("UTILITY_PAYMENT_LINKAGE", r"공과금|관리비.*자동"),
        ("PENSION_LINKAGE", r"연금.*(?:수령|이체)"),
        ("LIVING_EXPENSE_LINKAGE", r"생활비"),
        ("DEMAND_DEPOSIT_ACCOUNT", r"입출금(?:통장|계좌)|결제계좌"),
        ("BALANCE_THRESHOLD", r"잔액.*(?:유지|이상)|평잔"),
        ("AGE", r"만\s*\d+\s*세|연령"),
        ("BANK_APP_USAGE", r"앱|플랫폼|서비스.*가입|로그인|운동|걸음"),
        ("DOCUMENT_VERIFICATION", r"증빙|서류|확인서"),
    ]
    for condition_type, pattern in mapping:
        if re.search(pattern, text, re.IGNORECASE):
            return condition_type
    # The loader intentionally supports unknown canonical types with a generic
    # question.  OTHER preserves semantics better than assigning a false type.
    return "OTHER"


def label_from(text: str) -> str:
    first = text.split("\n", 1)[0]
    before = re.split(r"\s*:\s*|\s+-\s+", first, maxsplit=1)[0].strip()
    before = re.sub(r"^[①-⑳]\s*", "", before)
    if 1 < len(before) <= 80 and not RATE_RE.fullmatch(before):
        return before
    return re.sub(r"\s+", " ", first)[:80]


def candidate_from_text(text: str, *, inherited_ambiguity: bool = False) -> Candidate | None:
    rates = RATE_RE.findall(text)
    if len(rates) != 1:
        return None
    first_line = text.split("\n", 1)[0]
    if BASE_RE.search(first_line):
        return None
    if (
        (SUMMARY_RE.search(first_line) or "다음의 우대조건 충족 시 최고" in text)
        and not re.match(r"^[①-⑳]", first_line)
    ):
        return None
    reasons: list[str] = []
    if inherited_ambiguity or AMBIGUOUS_RE.search(text):
        reasons.append("exclusive_capped_or_choice_semantics")
    condition_type = classify(text)
    if condition_type == "OTHER":
        reasons.append("unmapped_condition_type")
    return Candidate(
        label=label_from(text),
        condition_text=text,
        value=decimal_text(rates[0]),
        condition_type=condition_type,
        confidence="REVIEW_REQUIRED" if reasons else "AUTO_READY",
        review_reasons=sorted(set(reasons)),
    )


def parse(raw: str) -> list[Candidate]:
    scope = preferential_scope(raw)
    if not scope:
        return []
    parsed: list[Candidate] = []
    for block in blocks(scope):
        if not block:
            continue
        circled = [index for index, row in enumerate(block) if CIRCLED_RE.match(row)]
        inherited_ambiguity = bool(AMBIGUOUS_RE.search("\n".join(block)))
        if circled:
            # A parent usually states only a cap; atomic rewards live in the
            # circled alternatives.  Do not emit the parent and double count.
            for pos, start in enumerate(circled):
                end = circled[pos + 1] if pos + 1 < len(circled) else len(block)
                segment = "\n".join(block[start:end])
                item = candidate_from_text(segment, inherited_ambiguity=inherited_ambiguity)
                if item:
                    parsed.append(item)
            continue
        joined = "\n".join(block)
        item = candidate_from_text(joined)
        if item:
            parsed.append(item)
            continue
        # Unnumbered comparison pages often place one complete condition on
        # each line.  Only accept lines with exactly one explicit rate.
        for row in block:
            item = candidate_from_text(row)
            if item:
                parsed.append(item)
    # Avoid duplicated Naver fragments becoming duplicated rewards.
    unique: dict[tuple[str, str], Candidate] = {}
    for item in parsed:
        unique[(item.condition_text, item.value)] = item
    return list(unique.values())


def raw_rate_text(product: dict[str, Any]) -> str | None:
    policy = product.get("return_policy") or {}
    structured = policy.get("naver_structured_fill") or {}
    for value in (structured.get("rate_text"), structured.get("raw_text"), policy.get("naver_raw_text")):
        if isinstance(value, str) and value.strip():
            return value
    return None


def has_structured_preferential(product: dict[str, Any]) -> bool:
    return bool(product.get("standard_conditions") or product.get("custom_bindings") or any(
        entry.get("role") == "PREFERENTIAL"
        for entry in (product.get("return_policy") or {}).get("rate_entries", [])
    ))


def rate_value(product: dict[str, Any], role: str) -> Decimal | None:
    values: list[Decimal] = []
    for entry in (product.get("return_policy") or {}).get("rate_entries", []):
        if entry.get("role") != role:
            continue
        calculation = entry.get("calculation") or {}
        if calculation.get("unit") != "PERCENT":
            continue
        try:
            values.append(Decimal(str(calculation.get("value"))))
        except (InvalidOperation, ValueError):
            pass
    return max(values) if values else None


def is_target(product: dict[str, Any], raw: str | None) -> bool:
    """Match the agreed 510-product service-data audit population."""
    if not raw or not re.search(r"우대|조건별", raw):
        return False
    if product.get("standard_conditions") or product.get("custom_bindings"):
        return False
    if any(
        entry.get("role") == "PREFERENTIAL"
        for entry in (product.get("return_policy") or {}).get("rate_entries", [])
    ):
        return False
    base = rate_value(product, "BASE")
    maximum = rate_value(product, "ADVERTISED_MAXIMUM")
    return base is not None and maximum is not None and maximum > base


def identifier(product_code: str, ordinal: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10].upper()
    return f"{product_code}-NVR-PREF-{ordinal:02d}-{digest}"


def detached_patch(product: dict[str, Any], candidates: list[Candidate]) -> dict[str, Any]:
    """Return a review copy. REVIEW_REQUIRED rows are provenance-only metadata."""
    result = copy.deepcopy(product)
    refs = list(result.get("source_ref_ids") or (result.get("return_policy") or {}).get("source_ref_ids") or [])
    auto = [item for item in candidates if item.confidence == "AUTO_READY"]
    entries = result.setdefault("return_policy", {}).setdefault("rate_entries", [])
    disclosures = result["return_policy"].setdefault("preferential_condition_disclosures", [])
    conditions = result.setdefault("standard_conditions", [])
    for ordinal, item in enumerate(candidates, 1):
        disclosure_id = identifier(result["product_code"], ordinal, item.condition_text)
        relationship_hints = []
        if "exclusive_capped_or_choice_semantics" in item.review_reasons:
            relationship_hints.append("EXCLUSIVE_OR_CAPPED_GROUP")
        disclosures.append({
            "disclosure_id": f"DISC-{disclosure_id}",
            "title": item.label,
            "condition_text": item.condition_text,
            "extracted_reward_candidate": (
                {"value": item.value, "unit": "PERCENTAGE_POINT"}
                if item.value is not None
                else None
            ),
            "relationship_hints": relationship_hints,
            "verification_status": (
                "ACCEPTED_NAVER_TEXT_AUTO_PARSED"
                if item.confidence == "AUTO_READY"
                else "PENDING_OFFICIAL_VERIFICATION"
            ),
            "evaluator_eligible": item.confidence == "AUTO_READY",
            "source_ref_ids": refs,
            "normalization_authority": "USER_ACCEPTED_NAVER_FIELDS",
        })
    for ordinal, item in enumerate(auto, 1):
        assert item.value is not None
        rate_id = identifier(result["product_code"], ordinal, item.condition_text)
        entries.append({
            "rate_id": rate_id,
            "role": "PREFERENTIAL",
            "application_event": "ANY",
            "calculation": {"type": "FIXED", "value": item.value, "unit": "PERCENTAGE_POINT"},
            "source_ref_ids": refs,
            "official_label": item.label,
            "condition_text": item.condition_text,
            "normalization_authority": "USER_ACCEPTED_NAVER_FIELDS",
        })
        conditions.append({
            "condition_id": f"COND-{rate_id}",
            "condition_type": item.condition_type,
            "title": item.label,
            "schema_version": 1,
            "purpose": "PREFERENTIAL_RETURN",
            "criteria": {
                "source_text": item.condition_text,
                "interpretation": "USER_CONFIRMATION_REQUIRED",
                "normalization_authority": "USER_ACCEPTED_NAVER_FIELDS",
            },
            "reward_refs": [rate_id],
            "source_ref_ids": refs,
        })
    metadata = result.setdefault("version_metadata", {})
    metadata["naver_preferential_structure_candidate"] = {
        "status": "REVIEW_REQUIRED" if any(x.confidence == "REVIEW_REQUIRED" for x in candidates) else "AUTO_READY",
        "parser_version": "20260827.1",
        "non_publishing": True,
        "auto_promoted_count": len(auto),
        "review_candidates": [item.__dict__ for item in candidates if item.confidence == "REVIEW_REQUIRED"],
    }
    pending = [item for item in candidates if item.confidence == "REVIEW_REQUIRED"]
    if pending:
        gaps = metadata.setdefault("data_gaps", [])
        gap_path = "standard_conditions/custom_bindings"
        if not any(row.get("path") == gap_path for row in gaps):
            gaps.append({
                "path": gap_path,
                "reason": "네이버 우대조건 원문은 있으나 배타·한도·구간 의미의 공식 검증 전이므로 보상 계산에서 제외",
            })
    return result


def indexed_products(index_path: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for row in read_json(index_path).get("products", []):
        path = ROOT / row["path"]
        yield path, read_json(path)


def build_report(index_path: Path, candidate_dir: Path | None, name_filter: str | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    scanned = raw_available = skipped_existing = 0
    for path, product in indexed_products(index_path):
        if name_filter and name_filter not in product.get("name", ""):
            continue
        scanned += 1
        raw = raw_rate_text(product)
        if not raw:
            continue
        raw_available += 1
        if not is_target(product, raw):
            if has_structured_preferential(product):
                skipped_existing += 1
            continue
        candidates = parse(raw)
        atomic_candidate_count = len(candidates)
        if not candidates:
            scoped = preferential_scope(raw)
            candidates = [Candidate(
                label="우대조건 원문",
                condition_text="\n".join(scoped) if scoped else raw,
                value=None,
                condition_type="OTHER",
                confidence="REVIEW_REQUIRED",
                review_reasons=["no_atomic_reward_extracted"],
            )]
        for item in candidates:
            reason_counts.update(item.review_reasons)
        row = {
            "product_code": product["product_code"],
            "product_name": product["name"],
            "source_path": str(path.relative_to(ROOT)),
            "candidate_count": len(candidates),
            "atomic_candidate_count": atomic_candidate_count,
            "auto_ready_count": sum(x.confidence == "AUTO_READY" for x in candidates),
            "review_required_count": sum(x.confidence == "REVIEW_REQUIRED" for x in candidates),
            "candidates": [item.__dict__ for item in candidates],
        }
        rows.append(row)
        if candidate_dir is not None:
            out = candidate_dir / product["product_family"].lower() / product["institution_id"]
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{product['product_code']}-v{int(product['version']):03d}.candidate.json").write_text(
                json.dumps(detached_patch(product, candidates), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return {
        "mode": "DETACHED_CANDIDATES" if candidate_dir else "DRY_RUN",
        "publishes_catalog": False,
        "index_path": str(index_path),
        "scanned_active_products": scanned,
        "products_with_naver_rate_text": raw_available,
        "skipped_already_structured": skipped_existing,
        "target_products": len(rows),
        "products_with_parsed_candidates": sum(bool(row["atomic_candidate_count"]) for row in rows),
        "auto_ready_conditions": sum(row["auto_ready_count"] for row in rows),
        "review_required_conditions": sum(row["review_required_count"] for row in rows),
        "review_reason_counts": dict(reason_counts),
        "products": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--candidate-dir", type=Path, help="write detached review copies; never publishes")
    parser.add_argument("--report", type=Path, help="write the JSON report (stdout otherwise)")
    parser.add_argument("--name", help="only inspect products whose Korean name contains this text")
    parser.add_argument("--summary-only", action="store_true", help="omit per-product rows from output")
    args = parser.parse_args()
    report = build_report(args.index.resolve(), args.candidate_dir, args.name)
    if args.summary_only:
        report.pop("products", None)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
