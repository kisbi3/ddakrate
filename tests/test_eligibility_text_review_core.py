from eligibility.eligibility_text_review import (
    build_product_packet, combine_status, eligibility_text_fingerprint,
    fact_snapshot_hash, frontier, validate_batch,
)
from eligibility.schema.enums import EvaluationStatus
import pytest


def test_frontier_includes_ties_and_combines_conservatively():
    assert [x["product_id"] for x in frontier([
        {"product_id": "a", "rank": 1}, {"product_id": "b", "rank": 3},
        {"product_id": "c", "rank": 3}, {"product_id": "d", "rank": 4},
    ])] == ["a", "b", "c"]
    assert combine_status(EvaluationStatus.SATISFIED, EvaluationStatus.UNKNOWN)[0] == EvaluationStatus.UNKNOWN


def test_packet_and_grounding_validation():
    p = build_product_packet({"product_id": "a", "name": "A", "version": 2,
                              "eligibility_text": "age under 60", "source_refs": [{"source_ref_id": "s1"}]}, 1)
    assert p.eligibility_text_fingerprint == eligibility_text_fingerprint("age under 60")
    batch = {"review_id": "r", "assistant_message": "확인이 필요합니다.", "product_reviews": [{
        "product_id": "a", "product_version": 2, "eligibility_text_fingerprint": p.eligibility_text_fingerprint,
        "status": "UNKNOWN", "reason_code": "TEXT_REQUIRED_USER_FACT_MISSING",
        "evidence_bindings": [{"source_ref_id": "s1", "source_text": "age under 60", "claim_role": "MANDATORY_ELIGIBILITY"}],
        "missing_facts": [{"fact_type": "ELIGIBILITY_TEXT::a::AGE", "semantic_type": "SELF_REPORTED_FACT", "answer_mode": "BINARY_OR_EXPLANATION", "question": "60세 미만인가요?", "grounding_evidence_indexes": [0]}],
    }]}
    validate_batch(batch, [p])
    assert fact_snapshot_hash([{"id": "x"}]) == fact_snapshot_hash([{"id": "x"}])


def test_status_reason_mismatch_is_rejected_atomically():
    packet = build_product_packet(
        {"product_id": "a", "name": "A", "version": 1,
         "eligibility_text": "개인만 가입", "source_refs": [{"source_ref_id": "s1"}]},
        1,
    )
    bad = {
        "review_id": "r",
        "assistant_message": "검수했습니다.",
        "product_reviews": [{
            "product_id": "a", "product_version": 1,
            "eligibility_text_fingerprint": packet.eligibility_text_fingerprint,
            "status": "SATISFIED",
            "reason_code": "TEXT_REQUIRED_USER_FACT_MISSING",
            "evidence_bindings": [{"source_ref_id": "s1", "source_text": "개인만 가입", "claim_role": "MANDATORY_ELIGIBILITY", "applied_user_fact_ids": []}],
            "missing_facts": [],
        }],
    }
    with pytest.raises(ValueError, match="reason_code"):
        validate_batch(bad, [packet])


def test_routine_identification_document_is_deferred_to_final_signup_checklist():
    packet = build_product_packet(
        {
            "product_id": "id-check",
            "name": "신분증 확인 상품",
            "version": 1,
            "eligibility_text": "주민등록증 또는 운전면허증을 소지한 개인",
            "source_refs": [{"source_ref_id": "s1"}],
        },
        1,
    )
    batch = validate_batch(
        {
            "review_id": "r",
            "assistant_message": "가입대상을 검수했어요.",
            "product_reviews": [
                {
                    "product_id": "id-check",
                    "product_version": 1,
                    "eligibility_text_fingerprint": packet.eligibility_text_fingerprint,
                    "status": "UNKNOWN",
                    "reason_code": "TEXT_REQUIRED_USER_FACT_MISSING",
                    "evidence_bindings": [
                        {
                            "source_ref_id": "s1",
                            "source_text": "주민등록증 또는 운전면허증을 소지한 개인",
                            "claim_role": "MANDATORY_ELIGIBILITY",
                            "applied_user_fact_ids": [],
                        }
                    ],
                    "missing_facts": [
                        {
                            "fact_type": "ELIGIBILITY_TEXT::id-check::IDENTIFICATION_DOCUMENT_POSSESSION",
                            "semantic_type": "SELF_REPORTED_FACT",
                            "answer_mode": "BINARY_OR_EXPLANATION",
                            "question": "주민등록증 또는 운전면허증이 있나요?",
                            "grounding_evidence_indexes": [0],
                        }
                    ],
                }
            ],
        },
        [packet],
    )

    review = batch.product_reviews[0]
    assert review.status == EvaluationStatus.ACHIEVABLE
    assert review.missing_facts == []
