from datetime import date

import pytest
from pydantic import ValidationError

from eligibility.llm.models import LLMPurpose
from eligibility.llm.profiles import DEFAULT_PROFILES
from eligibility.llm import LLMGateway, MockLLMAdapter
from eligibility.eligibility_text_review import EligibilityTextReviewer
from eligibility.schema.eligibility_text_review import (
    EligibilityTextReviewBatch,
    EligibilityTextReviewBatchInput,
    EligibilityTextReviewReasonCode,
    ProductEligibilityTextReview,
)
from eligibility.schema.enums import EvaluationStatus


def test_review_purpose_and_strict_output():
    assert LLMPurpose.ELIGIBILITY_TEXT_REVIEW.value == "ELIGIBILITY_TEXT_REVIEW"
    profile = DEFAULT_PROFILES[LLMPurpose.ELIGIBILITY_TEXT_REVIEW]
    assert profile.response_schema_version == "1.0.0"
    with pytest.raises(ValidationError):
        ProductEligibilityTextReview(product_id="p", product_version=1,
            eligibility_text_fingerprint="f", status="SATISFIED",
            reason_code="TEXT_ALL_MANDATORY_CONDITIONS_SUPPORTED", nope=True)


def test_satisfied_cannot_have_missing_fact():
    with pytest.raises(ValidationError):
        EligibilityTextReviewBatch(review_id="r", assistant_message="확인했습니다.",
            product_reviews=[{"product_id":"p", "product_version":1,
                "eligibility_text_fingerprint":"f", "status":EvaluationStatus.SATISFIED,
                "reason_code":EligibilityTextReviewReasonCode.TEXT_ALL_MANDATORY_CONDITIONS_SUPPORTED,
                "missing_facts":[{"fact_type":"x", "semantic_type":"SELF_REPORTED_FACT",
                    "answer_mode":"BINARY_OR_EXPLANATION", "question":"가능한가요?",
                    "grounding_evidence_indexes":[0]}]}])


def test_dedicated_gateway_purpose_returns_strict_grounded_batch():
    response = {
        "review_id": "REVIEW-1",
        "assistant_message": "연령 확인이 필요해요.",
        "product_reviews": [
            {
                "product_id": "P-1",
                "product_version": 1,
                "eligibility_text_fingerprint": "sha256:test",
                "status": "UNKNOWN",
                "reason_code": "TEXT_REQUIRED_USER_FACT_MISSING",
                "evidence_bindings": [
                    {
                        "source_ref_id": "SRC-1",
                        "source_text": "60세 미만",
                        "claim_role": "MANDATORY_ELIGIBILITY",
                        "applied_user_fact_ids": [],
                    }
                ],
                "missing_facts": [
                    {
                        "fact_type": "ELIGIBILITY_TEXT::P-1::AGE_UNDER_60",
                        "semantic_type": "SELF_REPORTED_FACT",
                        "answer_mode": "BINARY_OR_EXPLANATION",
                        "question": "가입일에 60세 미만인가요?",
                        "grounding_evidence_indexes": [0],
                    }
                ],
            }
        ],
    }
    adapter = MockLLMAdapter({LLMPurpose.ELIGIBILITY_TEXT_REVIEW: response})
    reviewer = EligibilityTextReviewer(LLMGateway(adapter))
    request = EligibilityTextReviewBatchInput.model_validate(
        {
            "review_id": "REVIEW-1",
            "as_of": "2026-08-28",
            "subscription_date": "2026-08-28",
            "global_policy_invariants": {},
            "user_fact_snapshot": {"facts": [], "pre_search_profile": {}},
            "products": [
                {
                    "rank": 1,
                    "product_id": "P-1",
                    "product_name": "상품",
                    "product_version": 1,
                    "eligibility_text_fingerprint": "sha256:test",
                    "eligibility_text": "가입일 현재 60세 미만",
                    "structured_evaluation": {
                        "status": "SATISFIED",
                        "reason_codes": [],
                    },
                    "source_refs": [
                        {"source_ref_id": "SRC-1", "source_text": "가입일 현재 60세 미만"}
                    ],
                }
            ],
        }
    )

    result = reviewer.review(request)

    assert result.product_reviews[0].status == EvaluationStatus.UNKNOWN
    assert adapter.call_history[0].purpose == LLMPurpose.ELIGIBILITY_TEXT_REVIEW


def test_input_source_text_must_match_the_reviewed_text():
    with pytest.raises(ValidationError, match="exactly match"):
        EligibilityTextReviewBatchInput.model_validate(
            {
                "review_id": "REVIEW-BAD-SOURCE",
                "as_of": "2026-08-28",
                "subscription_date": "2026-08-28",
                "global_policy_invariants": {},
                "user_fact_snapshot": {"facts": [], "pre_search_profile": {}},
                "products": [
                    {
                        "rank": 1,
                        "product_id": "P-1",
                        "product_name": "상품",
                        "product_version": 1,
                        "eligibility_text_fingerprint": "sha256:test",
                        "eligibility_text": "개인만 가입",
                        "structured_evaluation": {
                            "status": "SATISFIED",
                            "reason_codes": [],
                        },
                        "source_refs": [
                            {"source_ref_id": "SRC-1", "source_text": "다른 원문"}
                        ],
                    }
                ],
            }
        )
