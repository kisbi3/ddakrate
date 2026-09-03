from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from eligibility.application_service import ApplicationService
from eligibility.catalog.normalized_loader import load_normalized_product_catalog
from eligibility.eligibility_text_review import eligibility_text
from eligibility.schema.eligibility_text_review import EligibilityTextReviewBatch
from eligibility.schema.enums import (
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
)
from eligibility.schema.search import ProductSearchIntent
from eligibility.schema.user_fact import UserFact, UserFactStore


@pytest.fixture(scope="module")
def sh_product():
    return next(
        product
        for product in load_normalized_product_catalog()
        if product.name == "Sh어촌청년을 응원海"
    )


def _intent(user_id: str = "TEXT-REVIEW-USER") -> ProductSearchIntent:
    return ProductSearchIntent(
        search_intent_id=f"INTENT-{user_id}",
        user_id=user_id,
        product_types=["INSTALLMENT_SAVINGS"],
        requested_top_k=5,
    )


class MissingShEligibilityReviewer:
    def __init__(self) -> None:
        self.requests = []

    def review(self, request):
        self.requests.append(request)
        product = request.products[0]
        source = product.source_refs[0]
        answer = next(
            (
                row
                for row in request.user_fact_snapshot.facts
                if str(row["fact_type"]).startswith(
                    f"ELIGIBILITY_TEXT::{product.product_id}::"
                )
            ),
            None,
        )
        if answer is not None and answer["value"] is False:
            return EligibilityTextReviewBatch.model_validate(
                {
                    "review_id": request.review_id,
                    "assistant_message": "필수 가입대상 증빙이 불가능해 추천에서 제외했어요.",
                    "product_reviews": [
                        {
                            "product_id": product.product_id,
                            "product_version": product.product_version,
                            "eligibility_text_fingerprint": product.eligibility_text_fingerprint,
                            "status": "UNSATISFIABLE",
                            "reason_code": "TEXT_EXPLICIT_REQUIRED_CONDITION_CONFLICT",
                            "evidence_bindings": [
                                {
                                    "source_ref_id": source.source_ref_id,
                                    "source_text": "아래 항목에 해당하는 경우",
                                    "claim_role": "MANDATORY_ELIGIBILITY",
                                    "applied_user_fact_ids": [answer["fact_id"]],
                                }
                            ],
                            "missing_facts": [],
                        }
                    ],
                }
            )
        return EligibilityTextReviewBatch.model_validate(
            {
                "review_id": request.review_id,
                "assistant_message": "이 상품은 가입대상 증빙 확인이 더 필요해요.",
                "product_reviews": [
                    {
                        "product_id": product.product_id,
                        "product_version": product.product_version,
                        "eligibility_text_fingerprint": (
                            product.eligibility_text_fingerprint
                        ),
                        "status": "UNKNOWN",
                        "reason_code": "TEXT_REQUIRED_USER_FACT_MISSING",
                        "evidence_bindings": [
                            {
                                "source_ref_id": source.source_ref_id,
                                "source_text": "가입일 현재 60세 미만 실명의 개인",
                                "claim_role": "MANDATORY_ELIGIBILITY",
                                "applied_user_fact_ids": [],
                            },
                            {
                                "source_ref_id": source.source_ref_id,
                                "source_text": "어업인, 귀어인, 어업종사자",
                                "claim_role": "MANDATORY_ELIGIBILITY",
                                "applied_user_fact_ids": [],
                            },
                            {
                                "source_ref_id": source.source_ref_id,
                                "source_text": "수산계 고등학교* 또는 대학교** 재학생",
                                "claim_role": "MANDATORY_ELIGIBILITY",
                                "applied_user_fact_ids": [],
                            },
                        ],
                        "missing_facts": [
                            {
                                "fact_type": (
                                    f"ELIGIBILITY_TEXT::{product.product_id}::"
                                    "QUALIFYING_STATUS_AND_PROOF"
                                ),
                                # Regression fixture for a real model output:
                                # ASK_USER questions must normalize this invalid
                                # authoritative semantic type at the boundary.
                                "semantic_type": "OBSERVED_FACT",
                                "answer_mode": "BINARY_OR_EXPLANATION",
                                "question": (
                                    "어업 관련 자격과 본인 명의 증빙이 있거나, "
                                    "수산계 학교 재학 증빙이 가능한가요?"
                                ),
                                "grounding_evidence_indexes": [1, 2],
                            }
                        ],
                    }
                ],
            }
        )


def test_sh_raw_text_is_reviewed_and_missing_qualification_becomes_question(
    sh_product,
) -> None:
    reviewer = MissingShEligibilityReviewer()
    service = ApplicationService([sh_product], eligibility_text_reviewer=reviewer)
    session = service.create_search_session(user_id="TEXT-REVIEW-USER", intent=_intent())
    runtime = service._sessions[session.search_session_id]

    assert "60세 미만" in eligibility_text(sh_product)
    assert len(reviewer.requests) == 1
    assert runtime.evaluations[sh_product.product_id].eligibility_status == EvaluationStatus.UNKNOWN
    assert runtime.eligibility_text_review_state == "COMPLETE"

    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.request is not None
    assert question.question == (
        "어업 관련 자격과 본인 명의 증빙이 있거나, "
        "수산계 학교 재학 증빙이 가능한가요?"
    )
    assert question.request.fact_type.startswith(
        f"ELIGIBILITY_TEXT::{sh_product.product_id}::"
    )
    assert question.request.expected_semantic_type == FactSemanticType.SELF_REPORTED_FACT

    recommendation = service.get_top_recommendations(session.search_session_id)
    assert recommendation.recommendation_status == "PROVISIONAL"
    assert recommendation.eligibility_text_review_state == "COMPLETE"
    item = recommendation.top_products[0]
    assert item.eligibility_text_review_status == EvaluationStatus.UNKNOWN
    assert item.eligibility_badge.value == "VERIFICATION_REQUIRED"
    context_rows = service._conversation_context(runtime)[
        "TOP_RANKED_ELIGIBILITY_EVIDENCE"
    ]
    assert context_rows[0]["product_id"] == sh_product.product_id
    assert "어업인" in context_rows[0]["eligibility_text"]
    assert context_rows[0]["eligibility_text_review_status"] == "UNKNOWN"

    service.submit_user_answer(
        session.search_session_id,
        answer=False,
        question_id=question.question_id,
    )
    assert len(reviewer.requests) == 2
    assert sh_product.product_id not in runtime.ranking.ordered_product_ids
    assert (
        runtime.active_question is None
        or sh_product.product_id not in runtime.active_question.affected_product_ids
    )
    assert any(
        event.event_type.value == "ELIGIBILITY_TEXT_REVIEW_INVALIDATED"
        for event in service.get_evaluation_trace(session.search_session_id)
    )

    service.revise_user_answer(
        session.search_session_id,
        request_reference=service._request_reference(question.request),
        new_value=True,
    )
    assert len(reviewer.requests) == 3
    assert sh_product.product_id in runtime.ranking.ordered_product_ids
    assert runtime.evaluations[sh_product.product_id].eligibility_status == EvaluationStatus.UNKNOWN


class AgeConflictReviewer:
    def review(self, request):
        product = request.products[0]
        age_fact = next(row for row in request.user_fact_snapshot.facts if row["fact_type"] == "AGE_YEARS")
        return EligibilityTextReviewBatch.model_validate(
            {
                "review_id": request.review_id,
                "assistant_message": "가입일 현재 연령 조건과 맞지 않아요.",
                "product_reviews": [
                    {
                        "product_id": product.product_id,
                        "product_version": product.product_version,
                        "eligibility_text_fingerprint": product.eligibility_text_fingerprint,
                        "status": "UNSATISFIABLE",
                        "reason_code": "TEXT_EXPLICIT_REQUIRED_CONDITION_CONFLICT",
                        "evidence_bindings": [
                            {
                                "source_ref_id": product.source_refs[0].source_ref_id,
                                "source_text": "가입일 현재 60세 미만",
                                "claim_role": "MANDATORY_ELIGIBILITY",
                                "applied_user_fact_ids": [age_fact["fact_id"]],
                            }
                        ],
                        "missing_facts": [],
                    }
                ],
            }
        )


def test_sh_age_65_is_unsatisfiable_and_removed(sh_product) -> None:
    user_id = "TEXT-REVIEW-AGE"
    store = UserFactStore(
        user_id=user_id,
        facts=[
            UserFact(
                fact_id="FACT-AGE-65",
                user_id=user_id,
                fact_type="AGE_YEARS",
                value=65,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                collected_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            )
        ],
    )
    service = ApplicationService(
        [sh_product],
        user_fact_stores={user_id: store},
        eligibility_text_reviewer=AgeConflictReviewer(),
    )
    session = service.create_search_session(user_id=user_id, intent=_intent(user_id))
    runtime = service._sessions[session.search_session_id]

    assert runtime.structured_evaluations[sh_product.product_id].eligibility_status == EvaluationStatus.SATISFIED
    assert sh_product.product_id not in runtime.evaluations
    assert runtime.ranking is not None
    assert sh_product.product_id not in runtime.ranking.ordered_product_ids


class InvalidGroundingReviewer:
    def review(self, request):
        product = request.products[0]
        return {
            "review_id": request.review_id,
            "assistant_message": "검수가 완료됐어요.",
            "product_reviews": [
                {
                    "product_id": product.product_id,
                    "product_version": product.product_version,
                    "eligibility_text_fingerprint": product.eligibility_text_fingerprint,
                    "status": "UNKNOWN",
                    "reason_code": "TEXT_AMBIGUOUS_OR_INSUFFICIENT_EVIDENCE",
                    "evidence_bindings": [
                        {
                            "source_ref_id": product.source_refs[0].source_ref_id,
                            "source_text": "원문에 존재하지 않는 조작된 조건",
                            "claim_role": "MANDATORY_ELIGIBILITY",
                            "applied_user_fact_ids": [],
                        }
                    ],
                    "missing_facts": [],
                }
            ],
        }


def test_bad_grounding_rolls_back_entire_batch_and_suppresses_message(sh_product) -> None:
    service = ApplicationService(
        [sh_product],
        eligibility_text_reviewer=InvalidGroundingReviewer(),
    )
    session = service.create_search_session(user_id="TEXT-REVIEW-USER", intent=_intent())
    runtime = service._sessions[session.search_session_id]

    assert runtime.eligibility_text_review_state == "FAILED"
    assert runtime.eligibility_text_review_pending_count == 1
    assert runtime.eligibility_text_review_cache == {}
    assert runtime.eligibility_text_review_assistant_message is None
    assert runtime.evaluations[sh_product.product_id].eligibility_status == EvaluationStatus.SATISFIED
    recommendation = service.get_top_recommendations(session.search_session_id)
    assert recommendation.recommendation_status == "PROVISIONAL"


def _clone_product(product, product_id: str, rate: str):
    metadata = product.metadata.model_copy(
        update={"product_id": product_id, "product_name": product_id},
        deep=True,
    )
    normalized = product.normalized.model_copy(
        update={
            "version": 1,
            "eligibility_policy": {
                "eligibility_text": "실명의 개인만 가입 가능",
                "source_ref_ids": [f"SRC-{product_id}"],
            },
            "raw_product": {
                **deepcopy(product.normalized.raw_product),
                "product_code": product_id,
                "version": 1,
                "name": product_id,
            },
        },
        deep=True,
    )
    return product.model_copy(
        update={
            "product_id": product_id,
            "name": product_id,
            "base_rate": Decimal(rate),
            "advertised_max_rate": Decimal(rate),
            "preferential_rate_cap": Decimal("0"),
            "preferential_rules": [],
            "metadata": metadata.model_copy(
                update={
                    "base_rate": Decimal(rate),
                    "advertised_max_rate": Decimal(rate),
                    "preferential_rate_cap": Decimal("0"),
                },
                deep=True,
            ),
            "normalized": normalized,
        },
        deep=True,
    )


class CascadeReviewer:
    def __init__(self) -> None:
        self.product_batches = []

    def review(self, request):
        self.product_batches.append([product.product_id for product in request.products])
        reviews = []
        for product in request.products:
            status = "UNSATISFIABLE" if product.product_id == "P-A" else "SATISFIED"
            fact_ids = (
                ["FACT-A-BLOCKED"]
                if status == "UNSATISFIABLE"
                else ["FACT-REAL-NAME"]
            )
            reviews.append(
                {
                    "product_id": product.product_id,
                    "product_version": product.product_version,
                    "eligibility_text_fingerprint": product.eligibility_text_fingerprint,
                    "status": status,
                    "reason_code": (
                        "TEXT_EXPLICIT_REQUIRED_CONDITION_CONFLICT"
                        if status == "UNSATISFIABLE"
                        else "TEXT_ALL_MANDATORY_CONDITIONS_SUPPORTED"
                    ),
                    "evidence_bindings": [
                        {
                            "source_ref_id": product.source_refs[0].source_ref_id,
                            "source_text": "실명의 개인만 가입 가능",
                            "claim_role": "MANDATORY_ELIGIBILITY",
                            "applied_user_fact_ids": fact_ids,
                        }
                    ],
                    "missing_facts": [],
                }
            )
        return EligibilityTextReviewBatch.model_validate(
            {
                "review_id": request.review_id,
                "assistant_message": "상위 상품들의 가입대상을 검수했어요.",
                "product_reviews": reviews,
            }
        )


def test_new_top_three_entrant_is_reviewed_in_a_second_round(sh_product) -> None:
    products = [
        _clone_product(sh_product, "P-A", "5.0"),
        _clone_product(sh_product, "P-B", "4.0"),
        _clone_product(sh_product, "P-C", "3.0"),
        _clone_product(sh_product, "P-D", "2.0"),
    ]
    user_id = "TEXT-REVIEW-CASCADE"
    store = UserFactStore(
        user_id=user_id,
        facts=[
            UserFact(
                fact_id="FACT-A-BLOCKED",
                user_id=user_id,
                fact_type="BLOCK_P_A",
                value=True,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            ),
            UserFact(
                fact_id="FACT-REAL-NAME",
                user_id=user_id,
                fact_type="REAL_NAME_SUBSCRIPTION_POSSIBLE",
                value=True,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            ),
        ],
    )
    reviewer = CascadeReviewer()
    service = ApplicationService(
        products,
        user_fact_stores={user_id: store},
        eligibility_text_reviewer=reviewer,
    )
    session = service.create_search_session(user_id=user_id, intent=_intent(user_id))
    runtime = service._sessions[session.search_session_id]

    assert reviewer.product_batches[0] == ["P-A", "P-B", "P-C"]
    assert reviewer.product_batches[1] == ["P-D"]
    assert runtime.eligibility_text_review_rounds == 2
    assert runtime.eligibility_text_review_state == "COMPLETE"
    assert runtime.ranking is not None
    assert runtime.ranking.ordered_product_ids[:3] == ["P-B", "P-C", "P-D"]


def test_round_limit_never_confirms_an_unreviewed_new_entrant(sh_product) -> None:
    products = [
        _clone_product(sh_product, "P-A", "5.0"),
        _clone_product(sh_product, "P-B", "4.0"),
        _clone_product(sh_product, "P-C", "3.0"),
        _clone_product(sh_product, "P-D", "2.0"),
    ]
    user_id = "TEXT-REVIEW-LIMIT"
    store = UserFactStore(
        user_id=user_id,
        facts=[
            UserFact(
                fact_id="FACT-A-BLOCKED",
                user_id=user_id,
                fact_type="BLOCK_P_A",
                value=True,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            ),
            UserFact(
                fact_id="FACT-REAL-NAME",
                user_id=user_id,
                fact_type="REAL_NAME_SUBSCRIPTION_POSSIBLE",
                value=True,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            ),
        ],
    )
    service = ApplicationService(
        products,
        user_fact_stores={user_id: store},
        eligibility_text_reviewer=CascadeReviewer(),
        eligibility_text_review_max_rounds=1,
    )
    session = service.create_search_session(user_id=user_id, intent=_intent(user_id))
    runtime = service._sessions[session.search_session_id]

    assert runtime.eligibility_text_review_state == "PROVISIONAL"
    assert runtime.eligibility_text_review_pending_count == 1
    assert runtime.eligibility_text_review_assistant_message is None
    recommendation = service.get_top_recommendations(session.search_session_id)
    assert recommendation.recommendation_status == "PROVISIONAL"
    assert recommendation.eligibility_text_review_pending_count == 1


def test_feature_disabled_keeps_previous_deterministic_behavior(sh_product) -> None:
    service = ApplicationService([sh_product])
    session = service.create_search_session(user_id="TEXT-REVIEW-USER", intent=_intent())
    runtime = service._sessions[session.search_session_id]

    assert runtime.eligibility_text_review_state == "DISABLED"
    assert runtime.evaluations[sh_product.product_id].eligibility_status == EvaluationStatus.SATISFIED
