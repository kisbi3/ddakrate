from __future__ import annotations

import pytest

from eligibility.catalog.preferential_presentation import (
    PreferentialPresentationError,
    source_text_sha256,
    validate_presentation_candidate,
)


def candidate(source: str) -> dict:
    return {
        "disclosure_id": "DISC-1",
        "category": "카드 우대",
        "display_role": "ACTION",
        "summary_text": "신한은행 결제계좌로 카드를 3개월 이상 이용한 경우",
        "detail_texts": ["실적 인정기간은 신규월부터 만기 전전월 말까지예요."],
        "reward": {"mode": "FIXED", "value": "1", "unit": "PERCENTAGE_POINT"},
        "relation": {"operator": "EXCLUSIVE", "group_id": "CARD-1", "group_label": "카드 우대", "cap": {"value": "3", "unit": "PERCENTAGE_POINT"}},
        "ai_review_status": "AI_STRUCTURED",
        "confidence": 0.93,
        "source_text_sha256": source_text_sha256(source),
    }


def test_valid_presentation_preserves_safe_group_semantics() -> None:
    source = "카드 결제 실적 3개월 이상인 경우 연 1.0%"
    result = validate_presentation_candidate(candidate(source), source_text=source, disclosure_id="DISC-1")
    assert result["relation"]["operator"] == "EXCLUSIVE"
    assert result["authorship"]["model_family"] == "gpt-5.6-luna"


@pytest.mark.parametrize("mutation", ["hash", "placeholder"])
def test_invalid_presentation_fails_closed(mutation: str) -> None:
    source = "카드 결제 실적 3개월 이상인 경우 연 1.0%"
    value = candidate(source)
    if mutation == "hash":
        value["source_text_sha256"] = "bad"
    elif mutation == "placeholder":
        value["summary_text"] = "조건별"
    with pytest.raises(PreferentialPresentationError):
        validate_presentation_candidate(value, source_text=source, disclosure_id="DISC-1")


def test_review_required_may_omit_an_actionable_summary() -> None:
    source = "조건별\n우대금리 최대 1.9%"
    value = candidate(source)
    value["summary_text"] = None
    value["ai_review_status"] = "REVIEW_REQUIRED"
    result = validate_presentation_candidate(value, source_text=source, disclosure_id="DISC-1")
    assert result["summary_text"] is None


def test_relation_cap_requires_a_typed_value() -> None:
    source = "카드 결제 실적 3개월 이상인 경우 연 1.0%"
    value = candidate(source)
    value["relation"]["cap"] = "3.0"
    with pytest.raises(PreferentialPresentationError):
        validate_presentation_candidate(value, source_text=source, disclosure_id="DISC-1")


def test_context_only_rows_cannot_claim_an_action_summary() -> None:
    source = "조건별\n우대금리 최대 1.9%"
    value = candidate(source)
    value["display_role"] = "CONTEXT_ONLY"
    value["ai_review_status"] = "REVIEW_REQUIRED"
    with pytest.raises(PreferentialPresentationError):
        validate_presentation_candidate(value, source_text=source, disclosure_id="DISC-1")
