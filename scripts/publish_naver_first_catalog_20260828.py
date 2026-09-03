#!/usr/bin/env python3
"""Publish a clean, Naver-first runtime catalog without reusing v1 structures."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
REBUILD = ROOT / "data/financial_products/rebuild/20260828-naver-first-01"
INPUTS = REBUILD / "structuring_inputs.jsonl"
NORMALIZED = ROOT / "data/financial_products/normalized"
BATCH = "20260828-naver-first-01"


def dump(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_inputs() -> Iterable[dict[str, Any]]:
    for line in INPUTS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def clean_name(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return " ".join(line for line in lines if line not in {"네이버 페이", "네이버페이"})


def block(text: str, label: str, following: tuple[str, ...]) -> str:
    match = re.search(rf"(?:^|\n){re.escape(label)}\s*\n([\s\S]*?)(?=\n(?:{'|'.join(map(re.escape, following))})\s*\n|$)", text)
    return match.group(1).strip() if match else ""


def terms(text: str, *, unit: str = "MONTH") -> list[int]:
    result: set[int] = set()
    labels = "일" if unit == "DAY" else "개월|년"
    for value, label in re.findall(rf"(\d{{1,3}})\s*({labels})", text):
        normalized = int(value) * (12 if label == "년" else 1)
        if 1 <= normalized <= 600:
            result.add(normalized)
    for sequence, label in re.findall(rf"(\d{{1,3}}(?:\s*/\s*\d{{1,3}})+)\s*({labels})", text):
        multiplier = 12 if label == "년" else 1
        result.update(int(value) * multiplier for value in re.findall(r"\d+", sequence))
    return sorted(result)


def term_policy_from_text(text: str, evidence_id: str) -> tuple[dict[str, Any], list[int], list[dict[str, Any]]]:
    """Return the actual contract shape, not just the endpoints of a range."""

    day_terms = terms(text, unit="DAY")
    month_terms = terms(text)
    # A "31일" product is a day-contract.  "1개월 ... 일 단위" remains a
    # month contract whose maturity date can be selected by calendar day.
    day_contract = bool(day_terms) and not bool(month_terms)
    unit = "DAY" if day_contract else "MONTH"
    available_terms = day_terms if day_contract else month_terms
    range_match = re.search(
        rf"(\d{{1,3}})\s*({'일' if day_contract else '개월|년'})\s*(?:~|∼|부터|이상)\s*(\d{{1,3}})\s*({'일' if day_contract else '개월|년'})",
        text,
    )
    options: list[dict[str, Any]] = []
    if day_terms and month_terms:
        # One Naver item can really contain two contracts (e.g. 100-day
        # daily savings and 6~60 month monthly savings). Keep both options
        # explicit rather than silently dropping the daily one.
        options = [
            {"label": "일일 적립 옵션", "term_unit": "DAY", "available_values": day_terms, "contribution_frequency": "DAILY"},
            {"label": "월 적립 옵션", "term_unit": "MONTH", "available_values": month_terms, "contribution_frequency": "MONTHLY"},
        ]
    if range_match:
        start, start_unit, end, end_unit = range_match.groups()
        minimum = int(start) * (12 if start_unit == "년" else 1)
        maximum = int(end) * (12 if end_unit == "년" else 1)
        return ({
            "kind": "RANGE",
            "unit": unit,
            "min_value": min(minimum, maximum),
            "max_value": max(minimum, maximum),
            "source_ref_ids": [evidence_id],
        }, available_terms, options)
    if len(available_terms) == 1:
        return ({
            "kind": "FIXED",
            "unit": unit,
            "fixed_value": available_terms[0],
            "source_ref_ids": [evidence_id],
        }, available_terms, options)
    if available_terms:
        return ({
            "kind": "DISCRETE",
            "unit": unit,
            "allowed_values": available_terms,
            "source_ref_ids": [evidence_id],
        }, available_terms, options)
    return ({"kind": "OPEN_ENDED", "unit": unit, "source_ref_ids": [evidence_id]}, available_terms, options)


def rate_reference_term(raw_page: str, rate_info: str, advertised_maximum: str | None) -> tuple[int, str] | None:
    """Find the displayed term that the Naver headline rate refers to."""

    headline = re.search(
        r"이율\s*기본\s*(?:연\s*)?\d{1,2}(?:\.\d{1,3})?%\s*\((\d{1,3})\s*(일|개월|년)",
        raw_page,
    )
    if headline:
        value, unit = headline.groups()
        return int(value) * (12 if unit == "년" else 1), "DAY" if unit == "일" else "MONTH"
    if not advertised_maximum:
        return None
    tier = re.search(
        rf"(?m)^(\d{{1,3}})\s*(일|개월|년)(?:\s*이상)?[^\n]*\n\s*연\s*{re.escape(advertised_maximum)}\s*%",
        rate_info,
    )
    if tier:
        value, unit = tier.groups()
        return int(value) * (12 if unit == "년" else 1), "DAY" if unit == "일" else "MONTH"
    return None


def contribution_frequency(text: str) -> tuple[str | None, list[str]]:
    """Extract explicit cadence; calendar-day term precision is not cadence."""
    daily = bool(re.search(r"매일|1일\s*1회|일일(?:적금|부금)|(?:^|\n)일\s*\d", text))
    weekly = bool(re.search(r"매주", text))
    if daily and weekly:
        return "IRREGULAR", ["DAILY", "WEEKLY"]
    if daily:
        return "DAILY", ["DAILY"]
    if weekly:
        return "WEEKLY", ["WEEKLY"]
    return None, []


def amount_values(text: str) -> list[str]:
    multipliers = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "십만": 100_000, "만": 10_000, "천": 1_000, "백": 100, "": 1}
    values: list[str] = []
    for number, unit in re.findall(r"(\d[\d,]*)\s*(억|천만|백만|십만|만|천|백)?\s*원", text):
        values.append(str(int(number.replace(',', '')) * multipliers[unit or ""]))
    return values


def period_amount_rule(text: str, frequency: str) -> dict[str, Any] | None:
    """Extract a per-payment range without mixing an initial deposit in."""
    markers = {
        "DAILY": r"매일|1일\s*1회|1회\s*납입|일일|일\s*(?:최소|최저|\d)",
        "WEEKLY": r"매주|주\s*(?:최소|최저|\d)",
        "MONTHLY": r"매월|월\s*(?:최소|최저|\d)",
    }
    lines = text_lines(text)
    variant_label = {
        "DAILY": r"(?:일일(?:적금|부금)|일부금)\s*:\s*([^\n]+)",
        "MONTHLY": r"월\s*(?:적금|부금)\s*:\s*([^\n]+)",
    }.get(frequency)
    explicitly_scoped = re.findall(variant_label, text) if variant_label else []
    selected = explicitly_scoped or [line for line in lines if re.search(markers[frequency], line)]
    # Daily-only products often state the amount without repeating "매일".
    values = amount_values("\n".join(selected or lines))
    if not values:
        return None
    rule: dict[str, Any] = {"scope": "PER_PERIOD", "min_value": values[0], "currency": "KRW"}
    if len(values) > 1:
        rule["max_value"] = values[1]
    elif re.search(r"(?:매일|매주|매월|일\s*)\s*[\d,]+\s*원", "\n".join(selected or lines)):
        rule["max_value"] = values[0]
    return rule


def rate(text: str, labels: tuple[str, ...]) -> str | None:
    pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{pattern})[\s\S]{{0,30}}?(\d{{1,2}}(?:\.\d{{1,3}})?)\s*%", text)
    return match.group(1) if match else None


def _period_bound(text: str) -> tuple[int, str, bool, int | None, bool | None] | None:
    """Parse one displayed deposit-rate period into month bounds.

    Naver emits both ``6개월 이상 12개월 미만`` and lower-bound-only rows
    such as ``6개월(180일) 이상``.  The latter is completed by the next row
    in :func:`period_rate_entries`, so adjacent tiers cannot overlap in the
    runtime selector.
    """

    normalized = re.sub(r"\s+", "", text)
    span = re.fullmatch(
        r"(\d{1,3})(개월|년)\s*(?:~|∼|-)\s*(\d{1,3})\2", normalized
    )
    if span:
        start, unit, end = span.groups()
        multiplier = 12 if unit == "년" else 1
        return (
            int(start) * multiplier,
            "MONTH",
            True,
            int(end) * multiplier,
            True,
        )
    lower_only = re.fullmatch(
        r"(\d{1,3})(개월|년)(?:\(\d{1,4}일\))?(이상|부터)", normalized
    )
    if lower_only:
        value, unit, _ = lower_only.groups()
        return int(value) * (12 if unit == "년" else 1), "MONTH", True, None, None
    pattern = re.compile(
        r"(?P<start>\d{1,3})(?P<start_unit>개월|년)(?:\(\d{1,4}일\))?"
        r"(?:(?P<start_qual>이상|부터)\s*(?:~|-|∼)?\s*"
        r"(?P<end>\d{1,3})(?P<end_unit>개월|년)(?:\(\d{1,4}일\))?"
        r"(?P<end_qual>미만|이하))?"
    )
    match = pattern.search(normalized)
    if not match:
        return None

    def months(value: str, unit: str) -> int:
        return int(value) * (12 if unit == "년" else 1)

    start = months(match.group("start"), match.group("start_unit"))
    if match.group("end") is None:
        # A bare ``12개월`` is an exact displayed maturity, while a row
        # ending in ``이상`` is an open lower bound.
        if match.group("start_qual") in {"이상", "부터"}:
            return start, "MONTH", True, None, None
        return start, "MONTH", True, start, True
    end = months(match.group("end"), match.group("end_unit"))
    return (
        start,
        "MONTH",
        True,
        end,
        match.group("end_qual") != "미만",
    )


def period_rate_entries(
    rate_info: str, evidence_id: str, product_code: str
) -> list[dict[str, Any]]:
    """Extract the complete Naver deposit rate table as scoped BASE entries.

    The headline ``기본금리`` is a representative (usually 12-month) value;
    it must not be reused for every selectable maturity.  This parser keeps
    the first displayed column (the default maturity-payment rate when two
    columns are present) and scopes each value to ``ELAPSED_TERM``.
    """

    section_match = re.search(
        r"기간별\s*금리\s*([\s\S]*?)(?=\n(?:조건별|유형)\s*\n|$)",
        rate_info,
    )
    if not section_match:
        return []
    section = section_match.group(1)
    rows: list[tuple[str, list[str]]] = []
    pending_term = ""
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        values = re.findall(r"(?:연\s*)?(\d{1,2}(?:\.\d{1,3})?)\s*%", line)
        if values:
            term_part = re.sub(
                r"(?:연\s*)?\d{1,2}(?:\.\d{1,3})?\s*%", "", line
            ).strip()
            # A row may carry a label before its numeric rate (for example
            # ``약정이율 : 3.95%``).  Keep the pending period unless the text
            # before the percentage itself parses as a period.
            term_text = (
                term_part
                if _period_bound(term_part) is not None
                else pending_term
            )
            if term_text:
                rows.append((term_text, values))
            pending_term = ""
            continue
        # Header lines (기간/기간 금리/만기지급식...) do not parse as a
        # period and are intentionally ignored.
        if _period_bound(line) is not None:
            pending_term = line

    parsed: list[tuple[int, str, bool, int | None, bool | None, str]] = []
    for term_text, values in rows:
        bound = _period_bound(term_text)
        if bound is None:
            continue
        start, unit, min_inclusive, end, max_inclusive = bound
        parsed.append((start, unit, min_inclusive, end, max_inclusive, values[0]))
    if not parsed:
        return []

    entries: list[dict[str, Any]] = []
    for index, (start, unit, min_inclusive, end, max_inclusive, value) in enumerate(parsed, start=1):
        # Lower-bound rows are naturally closed by the next tier.  This is
        # essential for ``select_base_rate``: otherwise 6 months would match
        # every preceding ``N개월 이상`` entry.
        if end is None and index < len(parsed):
            end = parsed[index][0]
            max_inclusive = False
        scope: dict[str, Any] = {
            "basis": "ELAPSED_TERM",
            "range": {
                "unit": unit,
                "min_value": start,
                "min_inclusive": min_inclusive,
            },
        }
        if end is not None:
            scope["range"].update(
                {"max_value": end, "max_inclusive": bool(max_inclusive)}
            )
        entries.append(
            {
                "rate_id": f"{product_code}-BASE-TERM-{index:02d}",
                "role": "BASE",
                "application_event": "ANY",
                "calculation": {
                    "type": "FIXED",
                    "value": value,
                    "unit": "PERCENT",
                },
                "applies_to": [scope],
                "source_ref_ids": [evidence_id],
            }
        )
    return entries


def amount(text: str) -> str | None:
    match = re.search(r"(?:입금최소금액|최소(?:가입|예치|입금)금액)[^\d]{0,20}([\d,]+)\s*원", text)
    return match.group(1).replace(",", "") if match else None


def text_lines(value: str) -> list[str]:
    """Keep source wording, but make it useful as discrete UI/LLM sentences."""

    return [line.strip(" •\t") for line in value.splitlines() if line.strip()]


def structure_generic_eligibility(text: str) -> tuple[dict[str, Any], bool]:
    """Structure only unambiguous individual/customer wording.

    A generic ``실명의 개인`` target is sufficient to evaluate the common
    customer-type and real-name facts.  Product-specific phrases (for
    example fishermen, students, or benefit recipients) deliberately remain
    a data gap and are never guessed from prose.
    """

    policy: dict[str, Any] = {}
    if re.search(r"제한\s*없음|누구나|제한없이", text):
        policy["mode"] = "UNRESTRICTED"
        return policy, True

    customer_types: list[str] = []
    if re.search(r"개인사업자", text):
        customer_types.append("SOLE_PROPRIETOR")
    if re.search(r"(?:실명의\s*)?개인", text):
        customer_types.insert(0, "INDIVIDUAL")
    if customer_types:
        policy["mode"] = "RESTRICTED"
        policy["allowed_customer_types"] = list(dict.fromkeys(customer_types))
    if re.search(r"실명", text):
        policy["real_name_required"] = True
    age = re.search(r"만\s*(\d{1,3})\s*세\s*(이상|초과|이하|미만)", text)
    if age:
        value, qualifier = int(age.group(1)), age.group(2)
        policy.setdefault("mode", "RESTRICTED")
        policy.setdefault("age_range", {})
        if qualifier in {"이상", "초과"}:
            policy["age_range"]["min_age"] = value + (1 if qualifier == "초과" else 0)
        else:
            policy["age_range"]["max_age"] = value - (1 if qualifier == "미만" else 0)
    if re.search(r"내국인|대한민국\s*국적", text):
        policy["nationality_scope"] = "KOREAN_ONLY"
    return policy, bool(policy)


def preferential_conditions(rate_info: str, evidence_id: str, product_code: str) -> list[dict[str, Any]]:
    """Structure every Naver rate-condition block without importing v1 rules.

    These are disclosure records: their text and numeric reward are source
    faithful, while executable eligibility is added only after a later rule
    review.  Numbered blocks are the format emitted by Naver's rate panel.
    """

    section = rate_info.split("조건별", 1)[1] if "조건별" in rate_info else ""
    section = section.split("유형 ", 1)[0]
    if not section.strip():
        return []
    starts = list(re.finditer(r"(?m)^(\d{1,2})\s*$", section))
    chunks: list[str] = []
    if starts:
        preamble = section[:starts[0].start()].strip()
        if preamble:
            chunks.append(preamble)
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(section)
            body = section[match.end():end].strip()
            if body:
                chunks.append(body)
    else:
        chunks = [section.strip()]
    records: list[dict[str, Any]] = []
    for index, body in enumerate(chunks, start=1):
        lines = text_lines(body)
        if not lines:
            continue
        title = lines[0][:160]
        values = re.findall(r"(?:연\s*)?(\d{1,2}(?:\.\d{1,3})?)\s*%p?", body)
        reward = values[-1] if values else None
        # A coupon may alter an individual customer's rate, but it is not a
        # product-level preferential-rate rule until Naver supplies its value.
        # Do not present it as the condition that produces the published max.
        if not reward:
            continue
        records.append({
            "disclosure_id": f"DISC-NFI-{product_code}-{index:02d}",
            "title": title,
            "condition_text": body,
            "structured_steps": lines,
            "extracted_reward_candidate": (
                {"value": reward, "unit": "PERCENTAGE_POINT"} if reward else None
            ),
            "verification_status": "NAVER_SOURCE_STRUCTURED",
            "evaluator_eligible": False,
            "source_ref_ids": [evidence_id],
            "normalization_authority": "NAVER_RAW_CRAWL",
            "presentation": {
                "schema_version": 1,
                "category": title,
                "summary_text": lines[0],
                "detail_texts": lines[1:],
                "display_role": "ACTION",
                "reward": (
                    {"mode": "UP_TO", "value": reward, "unit": "PERCENTAGE_POINT"}
                    if reward else {"mode": "UNSPECIFIED"}
                ),
                "ai_review_status": "SOURCE_STRUCTURED",
                "confidence": 0.9,
            },
        })
    return records


def source_text(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
    if item["source_priority"] == "NAVER_PRIMARY":
        raw = item["naver_raw_record"]
        info = str(raw.get("product_info_text") or "")
        rate_info = str(raw.get("rate_info_text") or "")
        raw_page = str(raw.get("raw_page_text") or "")
        return (
            clean_name(str(raw.get("product_name") or item["product_name"])),
            info,
            rate_info,
            raw_page,
            str(raw.get("official_home_url") or raw.get("naver_detail_url") or ""),
        )
    evidence = item.get("internal_original_text_evidence") or []
    text = "\n\n".join(str(row.get("text") or "") for row in evidence)
    return item["product_name"], text, text, text, ""


def product_from_input(item: dict[str, Any], institution_names: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    name, info, rate_info, raw_page, url = source_text(item)
    target = block(
        info,
        "대상",
        ("적립방법", "우대조건", "이자지급", "유의", "금리 안내", "가입방법", "기간", "금액"),
    )
    term_text = block(info, "기간", ("금액", "가입방법", "대상", "유의")) or info
    amount_text = block(info, "금액", ("가입방법", "대상", "적립방법", "우대조건", "이자지급", "유의"))
    saving_method = block(info, "적립방법", ("우대조건", "이자지급", "유의"))
    base = rate(rate_info, ("기본금리", "기본 금리", "기본"))
    headline_maximum = re.search(
        r"이율\s*최고\s*(?:연\s*)?(\d{1,2}(?:\.\d{1,3})?)\s*%",
        raw_page,
    )
    maximum = (
        headline_maximum.group(1)
        if headline_maximum
        else rate(raw_page, ("최고금리", "최고 금리"))
        or rate(rate_info, ("최고금리", "최고 금리"))
    )
    if item["source_priority"] != "NAVER_PRIMARY":
        # Fallback evidence contains prose about mid-/post-maturity rates.
        # A bare percentage in that prose is not a published base or maximum
        # rate, so it must never participate in ranking.
        base = None
        maximum = None
    source_id = f"SRC-NFI-{item['canonical_product_code']}"
    evidence_id = f"EVD-NFI-{item['canonical_product_code']}"
    term_policy, available_terms, term_options = term_policy_from_text(term_text, evidence_id)
    rate_term = rate_reference_term(raw_page, rate_info, maximum)
    if rate_term is not None and rate_term[1] == term_policy["unit"]:
        # Cards represent the term attached to the advertised rate, while the
        # range above remains the actual set of selectable contract terms.
        term_policy["representative_value"] = rate_term[0]
    family = item["product_family"]
    funding = {"INSTALLMENT_SAVINGS": "PERIODIC", "TIME_DEPOSIT": "LUMP_SUM", "PARKING_ACCOUNT": "ON_DEMAND", "CMA": "ON_DEMAND"}[family]
    cash_flow: dict[str, Any] = {"funding_type": funding, "currency": "KRW"}
    cadence, cadence_options = contribution_frequency("\n".join((term_text, amount_text, saving_method)))
    if term_options:
        cadence = "IRREGULAR"
        cadence_options = sorted(set(cadence_options) | {"DAILY", "MONTHLY"})
    if family == "INSTALLMENT_SAVINGS" and cadence:
        cash_flow["contribution_frequency"] = cadence
        if cadence_options:
            cash_flow["contribution_frequency_options"] = cadence_options
    contribution_options = list(term_options)
    minimum = amount(info)
    if family == "INSTALLMENT_SAVINGS" and cadence:
        if len(cadence_options) == 1:
            rule = period_amount_rule(amount_text, cadence_options[0])
            if rule:
                cash_flow["amount_rules"] = [rule]
        else:
            for frequency in cadence_options:
                rule = period_amount_rule(amount_text, frequency)
                option = next(
                    (row for row in contribution_options if row.get("contribution_frequency") == frequency),
                    None,
                )
                if option is None:
                    option = {"label": f"{frequency} 납입 옵션", "contribution_frequency": frequency}
                    contribution_options.append(option)
                if rule:
                    option["amount_rules"] = [rule]
    if contribution_options:
        cash_flow["contribution_options"] = contribution_options
    elif minimum:
        key = "periodic_amount_min" if family == "INSTALLMENT_SAVINGS" else "initial_amount_min"
        cash_flow["amount_rules"] = [{"scope": "PER_PERIOD" if key.startswith("periodic") else "INITIAL_DEPOSIT", "min_value": minimum, "currency": "KRW"}]
    for policy in (cash_flow,):
        policy["source_ref_ids"] = [evidence_id]
    eligibility: dict[str, Any] = {
        "raw_text": target or info,
        "eligibility_text": target or info,
        "display_text": target or info,
        "source_ref_ids": [evidence_id],
    }
    generic_eligibility, eligibility_is_structured = structure_generic_eligibility(target or info)
    eligibility.update(generic_eligibility)
    rate_entries: list[dict[str, Any]] = []
    scoped_entries = (
        period_rate_entries(rate_info, evidence_id, item["canonical_product_code"])
        if family == "TIME_DEPOSIT"
        else []
    )
    scoped_base_values = [
        str((entry.get("calculation") or {}).get("value"))
        for entry in scoped_entries
        if entry.get("role") == "BASE"
        and (entry.get("calculation") or {}).get("value") is not None
    ]
    if scoped_base_values:
        highest_scoped_base = max(scoped_base_values, key=lambda value: Decimal(value))
        if maximum is None or Decimal(highest_scoped_base) > Decimal(maximum):
            maximum = highest_scoped_base
    if scoped_entries:
        rate_entries.extend(scoped_entries)
    elif base:
        rate_entries.append({"rate_id": f"{item['canonical_product_code']}-BASE", "role": "BASE", "application_event": "ANY", "calculation": {"type": "FIXED", "value": base, "unit": "PERCENT"}, "source_ref_ids": [evidence_id]})
    if maximum:
        rate_entries.append({"rate_id": f"{item['canonical_product_code']}-MAX", "role": "ADVERTISED_MAXIMUM", "application_event": "ANY", "calculation": {"type": "DISPLAYED_MAXIMUM", "value": maximum, "unit": "PERCENT"}, "source_ref_ids": [evidence_id]})
    disclosures = preferential_conditions(rate_info, evidence_id, item["canonical_product_code"])
    return_policy: dict[str, Any] = {
        "return_kind": "PERFORMANCE_LINKED" if family == "CMA" and "실적배당" in raw_page else "POSTED_RATE",
        "rate_entries": rate_entries,
        "preferential_condition_disclosures": disclosures,
        "source_ref_ids": [evidence_id],
    }
    if maximum:
        return_policy["advertised_max_rate"] = {"value": maximum, "unit": "PERCENT", "source_ref_ids": [evidence_id]}
    gaps = []
    # A generic individual/individual-business target is fully executable by
    # the loader.  Keep a gap only when the text contains product-specific
    # requirements that still need a question/rule tree.
    if not eligibility_is_structured:
        gaps.append({"path": "eligibility_policy", "reason": "원문 가입대상을 새 조건 트리로 구조화하기 전에는 개인화 판정을 보류"})
    if base and maximum and float(maximum) > float(base):
        gaps.append({"path": "return_policy.preferential_conditions", "reason": "원문 우대조건을 새 조건 트리로 구조화하기 전에는 최고금리 계산에서 제외"})
    if not base:
        gaps.append({"path": "return_policy.base_rate", "reason": "네이버 원문에서 기본금리를 확정 추출하지 못함"})
    product = {
        "product_code": item["canonical_product_code"], "version": 1, "institution_id": item["institution_id"], "name": name or item["product_name"], "product_family": family,
        "sale_policy": {"status": "ON_SALE", "source_ref_ids": [evidence_id]}, "eligibility_policy": eligibility,
        "term_policy": term_policy, "cash_flow_policy": cash_flow, "return_policy": return_policy,
        "standard_conditions": [], "custom_bindings": [], "source_ref_ids": [evidence_id],
        "version_metadata": {"captured_at": datetime.now(timezone.utc).isoformat(), "data_authority": "NAVER_RAW_CRAWL" if item["source_priority"] == "NAVER_PRIMARY" else "INTERNAL_ORIGINAL_TEXT", "publication_gate": "PUBLISHED_WITH_EXPLICIT_DATA_GAPS" if gaps else "PUBLISHED", "data_gaps": gaps, "source_priority": item["source_priority"], "source_reason": item["source_reason"]},
    }
    listing = {
        "product_code": item["canonical_product_code"],
        "provider": "NAVER_PAY" if item["source_priority"] == "NAVER_PRIMARY" else "INTERNAL_ORIGINAL_TEXT",
        "snapshot_at": "2026-08-28",
        "base_rate": base,
        "advertised_max_rate": maximum,
        "official_url": url or None,
    }
    condition_snapshot = {
        "product_code": item["canonical_product_code"],
        "eligibility": {
            "status": "STRUCTURED",
            "display_text": target or "가입 대상 원문이 제공되지 않았습니다.",
            "source_ref_ids": [evidence_id],
        },
        "preferential_rate": {
            "status": "STRUCTURED" if disclosures else "NO_CONDITIONS_DISCLOSED",
            "conditions": [
                {
                    "title": row["title"],
                    "condition_text": row["condition_text"],
                    "reward_value": (row.get("extracted_reward_candidate") or {}).get("value"),
                    "reward_unit": (row.get("extracted_reward_candidate") or {}).get("unit"),
                    "source_ref_ids": [evidence_id],
                }
                for row in disclosures
            ],
        },
        "product_facts": {
            "term_text": term_text,
            "available_term_values": available_terms,
            "term_unit": term_policy["unit"],
            "advertised_rate_term": (
                {"value": rate_term[0], "unit": rate_term[1]} if rate_term else None
            ),
            "term_options": term_options,
            "contribution_frequency": cadence,
            "contribution_frequency_options": cadence_options,
            "contribution_options": contribution_options,
            "amount_text": amount_text,
            "subscription_method": block(info, "가입방법", ("대상", "적립방법", "우대조건", "이자지급", "유의")),
            "saving_method": saving_method,
            "interest_payment": block(info, "이자지급", ("유의",)),
        },
    }
    source = {"source_id": source_id, "title": f"Naver-first rebuild source: {product['name']}", "url": url or None, "document_type": "NAVER_CRAWL" if item["source_priority"] == "NAVER_PRIMARY" else "INTERNAL_ORIGINAL_TEXT", "version_date": "2026-08-28"}
    evidence = {"evidence_ref_id": evidence_id, "source_id": source_id, "source_text": "\n".join(part for part in (info, rate_info, raw_page) if part), "locator": item["source_locator"], "supports": ["rebuild_input"]}
    return product, source, evidence, listing, condition_snapshot


def main() -> None:
    institutions = json.loads((ROOT / "data/institutions/normalized/snapshots/institutions_20260826T000000+0900.json").read_text())["institutions"]
    institution_names = {row["institution_id"]: row["official_name_ko"] for row in institutions}
    products_dir = NORMALIZED / "products"
    sources: list[dict[str, Any]] = []; evidence: list[dict[str, Any]] = []; listings: list[dict[str, Any]] = []; condition_snapshots: list[dict[str, Any]] = []; index_rows: list[dict[str, Any]] = []
    for item in read_inputs():
        product, source, evidence_row, listing, condition_snapshot = product_from_input(item, institution_names)
        path = products_dir / product["product_family"].lower() / product["institution_id"] / product["product_code"] / "v001.json"
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(dump(product))
        index_rows.append({"product_code": product["product_code"], "version": 1, "product_family": product["product_family"], "institution_id": product["institution_id"], "sale_status": "ON_SALE", "path": rel(path), "sha256": sha(path)})
        sources.append(source); evidence.append(evidence_row); listings.append(listing); condition_snapshots.append(condition_snapshot)
    source_path = NORMALIZED / "source_documents.json"; evidence_path = NORMALIZED / "evidence_refs.json"; custom_path = NORMALIZED / "definitions/custom/20260828-naver-first-01.json"; listing_path = NORMALIZED / "listing_snapshots/naver_first_20260828.json"; condition_path = NORMALIZED / "condition_snapshots/naver_first_20260828.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    custom_path.parent.mkdir(parents=True, exist_ok=True)
    listing_path.parent.mkdir(parents=True, exist_ok=True)
    condition_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(dump({"source_documents": sources}))
    evidence_path.write_bytes(dump({"evidence_refs": evidence}))
    custom_path.write_bytes(dump({"institution_custom_definitions": []}))
    listing_path.write_bytes(dump({"records": listings}))
    condition_path.write_bytes(dump({"records": condition_snapshots}))
    index = {"index_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "publication_status": "PUBLISHED", "source_staging_batch": BATCH, "manifest_batch": BATCH, "product_count": len(index_rows), "products": index_rows}
    index_path = NORMALIZED / "index.json"; index_path.write_bytes(dump(index))
    files = [index_path, source_path, evidence_path, custom_path, listing_path, condition_path, *[ROOT / row["path"] for row in index_rows]]
    manifest = {"manifest_version": 1, "publication_status": "PUBLISHED", "published_at": datetime.now(timezone.utc).isoformat(), "correction_batch": BATCH, "source_document_registry": rel(source_path), "evidence_ref_registry": rel(evidence_path), "custom_definition_file": rel(custom_path), "listing_snapshot_file": rel(listing_path), "condition_snapshot_file": rel(condition_path), "institution_ids": sorted({row["institution_id"] for row in index_rows}), "counts": {"total_products": len(index_rows), "custom_definitions": 0, "by_sale_status": {"ON_SALE": len(index_rows)}}, "files": [{"path": rel(path), "sha256": sha(path), "size_bytes": path.stat().st_size} for path in files]}
    manifest_path = NORMALIZED / f"manifests/{BATCH}.json"; manifest_path.parent.mkdir(parents=True, exist_ok=True); manifest_path.write_bytes(dump(manifest))
    print(json.dumps({"published_products": len(index_rows), "manifest": rel(manifest_path)}, ensure_ascii=False))


if __name__ == "__main__": main()
