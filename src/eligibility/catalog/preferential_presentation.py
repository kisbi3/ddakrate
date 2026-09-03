"""Validation helpers for user-facing preferential-condition presentations."""

from __future__ import annotations

import hashlib
import re
from typing import Any


REWARD_MODES = {
    "FIXED",
    "UP_TO",
    "RANGE",
    "TIERED",
    "VARIABLE",
    "PROBABILISTIC",
    "UNKNOWN",
}
RELATION_OPERATORS = {"AND", "OR", "EXCLUSIVE", "CAPPED", "TIERED", "NONE", "UNKNOWN"}
REVIEW_STATUSES = {"AI_STRUCTURED", "REVIEW_REQUIRED"}
DISPLAY_ROLES = {"ACTION", "CONTEXT_ONLY"}
PLACEHOLDER_SUMMARIES = {
    "조건별",
    "우대조건",
    "우대조건 원문",
    "다음의 조건을 충족하는 경우",
    "다음 조건을 충족하는 경우",
}


class PreferentialPresentationError(ValueError):
    """Raised when an AI-authored presentation is unsafe to publish."""


def source_text_sha256(source_text: str) -> str:
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreferentialPresentationError(f"{field} must be non-empty text")
    return value.strip()


def validate_presentation_candidate(
    candidate: dict[str, Any],
    *,
    source_text: str,
    disclosure_id: str,
) -> dict[str, Any]:
    """Validate and normalize one detached AI presentation candidate.

    The source text remains authoritative.  This validator deliberately checks
    structure and provenance rather than deciding whether a financial claim is
    true; uncertain semantics must stay REVIEW_REQUIRED and evaluator-ineligible.
    """

    if candidate.get("disclosure_id") != disclosure_id:
        raise PreferentialPresentationError(f"disclosure_id mismatch: {disclosure_id}")
    expected_hash = source_text_sha256(source_text)
    observed_hash = str(candidate.get("source_text_sha256") or "").removeprefix("sha256:")
    if observed_hash != expected_hash:
        raise PreferentialPresentationError(f"source hash mismatch: {disclosure_id}")

    category = _required_text(candidate.get("category"), "category")
    if re.fullmatch(r"(?:조건별|우대조건)(?:\s+원문)?[.:]?", category):
        category = "우대조건"
    review_status = candidate.get("ai_review_status")
    if review_status not in REVIEW_STATUSES:
        raise PreferentialPresentationError(f"invalid ai_review_status: {disclosure_id}")
    display_role = candidate.get("display_role")
    if display_role not in DISPLAY_ROLES:
        raise PreferentialPresentationError(f"invalid display_role: {disclosure_id}")
    if display_role == "CONTEXT_ONLY" and (
        review_status != "REVIEW_REQUIRED" or candidate.get("summary_text") is not None
    ):
        raise PreferentialPresentationError(
            f"CONTEXT_ONLY must be summary-free REVIEW_REQUIRED: {disclosure_id}"
        )
    raw_summary = candidate.get("summary_text")
    if raw_summary is None and review_status == "REVIEW_REQUIRED":
        summary = None
    else:
        summary = _required_text(raw_summary, "summary_text")
        if summary in PLACEHOLDER_SUMMARIES or re.fullmatch(r"(?:조건별|우대조건)(?:\s+원문)?[.:]?", summary):
            raise PreferentialPresentationError(f"placeholder summary: {disclosure_id}")
        if len(summary) > 100:
            raise PreferentialPresentationError(f"summary is too long: {disclosure_id}")
        if "\n" in summary or re.search(
            r"(?:합니다|됩니다|제공합니다|적용됩니다)\.?인 경우|\(\s*\)|[.:]\s*\.에|"
            r"이면|충촉|(?:최고|최대)\s*$|:\s*$|^우대조건을 충족하면 우대금리가 적용돼요$|"
            r"^(?:우대조건|아래(?:의)? 조건|조건)\s*충족.{0,12}(?:최고|최대)|"
            r"^최고\s*연\s*\d+(?:\.\d+)?%p?.{0,25}다음 조건",
            summary,
        ):
            raise PreferentialPresentationError(f"malformed summary: {disclosure_id}")

    details = candidate.get("detail_texts") or []
    if not isinstance(details, list) or any(not isinstance(row, str) or not row.strip() for row in details):
        raise PreferentialPresentationError(f"detail_texts must be non-empty strings: {disclosure_id}")
    if any(len(row.strip()) > 180 for row in details):
        raise PreferentialPresentationError(f"detail_texts item is too long: {disclosure_id}")

    reward = candidate.get("reward") or {}
    reward_mode = reward.get("mode")
    if reward_mode not in REWARD_MODES:
        raise PreferentialPresentationError(f"invalid reward mode: {disclosure_id}")

    relation = candidate.get("relation") or {}
    operator = relation.get("operator")
    if operator not in RELATION_OPERATORS:
        raise PreferentialPresentationError(f"invalid relation operator: {disclosure_id}")
    group_id = relation.get("group_id")
    cap = relation.get("cap")
    if cap is not None and (
        not isinstance(cap, dict)
        or cap.get("value") is None
        or cap.get("unit") not in {"PERCENT", "PERCENTAGE_POINT"}
    ):
        raise PreferentialPresentationError(f"invalid relation cap: {disclosure_id}")
    group_details = relation.get("group_detail_texts") or []
    if not isinstance(group_details, list) or any(
        not isinstance(row, str) or not row.strip() for row in group_details
    ):
        raise PreferentialPresentationError(f"invalid group_detail_texts: {disclosure_id}")
    if group_details and not group_id:
        raise PreferentialPresentationError(f"group details without group_id: {disclosure_id}")

    confidence = candidate.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise PreferentialPresentationError(f"confidence must be between 0 and 1: {disclosure_id}")
    if review_status == "AI_STRUCTURED" and summary is None:
        raise PreferentialPresentationError(f"AI_STRUCTURED requires summary: {disclosure_id}")

    return {
        "schema_version": 1,
        "category": category,
        "summary_text": summary,
        "detail_texts": [row.strip() for row in details],
        "display_role": display_role,
        "reward": reward,
        "relation": {
            "operator": operator,
            "group_id": group_id,
            "group_label": relation.get("group_label"),
            "cap": cap,
            "group_detail_texts": [row.strip() for row in group_details],
        },
        "ai_review_status": review_status,
        "confidence": confidence,
        "authorship": {
            "kind": "AI_STRUCTURED",
            "model_family": "gpt-5.6-luna",
        },
        "source_text_sha256": expected_hash,
    }
