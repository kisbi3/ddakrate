"""Backend-owned entity scopes for conversational write operations.

Catalog membership is not user authorization. A product may be addressed by an
exact ID, an unambiguous full name (optionally institution-qualified), a visible
rank, or a single conversational referent. Generic product-family words never
select arbitrary catalog rows. The same resolver scopes prompts and writes.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from eligibility.search.institution_names import resolve_institution_references

_GENERIC_PRODUCT_NAMES = {
    "정기예금", "정기적금", "자유적금", "자유적립식적금", "보통예금", "저축예금",
    "파킹통장", "입출금통장", "적금", "예금", "cma", "rp", "mmf", "mmw",
}
_REFERENCE_MARKERS = ("그거", "그상품", "이상품", "해당상품", "아까", "방금", "아니")
_AMBIGUOUS_BRAND_WORDS = {"우리", "하나"}


def _compact(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def grounded_targets(message: str, context: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Resolve target IDs without consulting the model's proposed operations."""
    rows: dict[str, tuple[str, str]] = {}
    for raw in context.get("PRODUCT_CATALOG_SUMMARY") or []:
        parts = str(raw).split("|", 2)
        if len(parts) == 3:
            rows[parts[0]] = (parts[1], parts[2])
    institutions = list(context.get("INSTITUTION_CATALOG_SUMMARY") or [])
    literal_ids = {token.casefold() for token in re.findall(r"[A-Za-z0-9_-]+", message)}
    resolved = resolve_institution_references(message, institutions)
    institution_ids = {
        str(item["institution_id"]) for item in resolved.resolved
        if set(item.get("matched_aliases") or []) - _AMBIGUOUS_BRAND_WORDS
    }
    institution_ids.update(str(item["institution_id"]) for item in institutions
                           if str(item.get("institution_id", "")).casefold() in literal_ids)
    text = _compact(message)
    product_ids = {pid for pid in rows if pid.casefold() in literal_ids}
    names: dict[str, set[str]] = {}
    for pid, (institution, name) in rows.items():
        if institution_ids and institution not in institution_ids:
            continue
        names.setdefault(_compact(name), set()).add(pid)
    for name, matches in names.items():
        if len(name) < 3 or name not in text:
            continue
        if (name in _GENERIC_PRODUCT_NAMES or len(name) < 4) and not institution_ids:
            continue
        if len(matches) == 1:
            product_ids.update(matches)

    active = context.get("ACTIVE_QUESTION") or {}
    # A pre-search question can affect the whole catalog; it is not a product
    # referent. Financial/ranking questions can identify a single product.
    active_ids = set(active.get("affected_product_ids") or []) if active.get("question_kind") != "PRE_SEARCH_PROFILE" else set()
    active_ids.intersection_update(rows)
    recent_ids = set(context.get("RECENT_PRODUCT_FOCUS") or []) & rows.keys()
    state = context.get("CURRENT_STATE_SNAPSHOT") or {}
    choices = state.get("product_contribution_choices") or []
    latest_choice = max(choices, key=lambda c: (str(c.get("answered_at", "")), c.get("version", 0)), default=None)
    latest_ids = {latest_choice["product_id"]} & rows.keys() if latest_choice else set()

    # Institution-only references may resolve a product only when unique in
    # the current focus, or unique in that institution's whole catalog.
    for institution in institution_ids:
        matching = {pid for pid, (inst, _) in rows.items() if inst == institution}
        focused = matching & (active_ids | recent_ids | latest_ids)
        if len(focused) == 1:
            product_ids.update(focused)
        elif len(matching) == 1:
            product_ids.update(matching)

    referenced = any(marker in text for marker in _REFERENCE_MARKERS)
    amount_answer = bool(re.fullmatch(r"[\d,\.\s]+(?:원)?", message.strip()))
    option_answer = active.get("question_kind") in {"RANKING_INPUT", "CONTRIBUTION_FEASIBILITY"} and any(
        phrase in text for phrase in ("제일큰", "가장큰", "제일작은", "가장작은", "최대금액", "최소금액")
    )
    global_only = any(phrase in text for phrase in ("기간만", "금액만", "정렬만", "전체기간", "전체금액"))
    choice_fields = {
        "시작금액": "preferred_start_amount", "시작금": "preferred_start_amount",
        "증액금액": "incremental_amount",
    }
    if not product_ids and not institution_ids and not global_only:
        for phrase, field in choice_fields.items():
            if phrase in text:
                owners = {str(c["product_id"]) for c in choices if c.get("field") == field}
                if len(owners) == 1:
                    product_ids.update(owners & rows.keys())
    if not product_ids and not institution_ids and not global_only and (referenced or amount_answer or option_answer):
        # An explicit correction commonly refers to the last product choice,
        # even after the next workflow question has been presented.
        referent = latest_ids if text.startswith("아니") and latest_ids else active_ids
        if len(referent) != 1:
            referent = latest_ids or recent_ids
        if len(referent) == 1:
            product_ids.update(referent)

    top = list(context.get("TOP_K_SUMMARY") or [])
    for match in re.finditer(r"(\d+)\s*위", message):
        matches = {str(item["product_id"]) for item in top if item.get("rank") == int(match.group(1))}
        if len(matches) == 1:
            product_ids.update(matches & rows.keys())
    # Undoing an unnamed institution exclusion is unambiguous only when there
    # is exactly one such current exclusion and the utterance requests undo.
    excluded = set((context.get("MUTABLE_SEARCH_STATE") or {}).get("excluded_institution_ids") or [])
    if not institution_ids and len(excluded) == 1 and any(word in text for word in ("제외취소", "뺀건취소", "뺀거취소")):
        institution_ids.update(excluded)
    return product_ids, institution_ids
