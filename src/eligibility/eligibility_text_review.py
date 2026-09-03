"""Top-ranked ``eligibility_text`` review primitives and LLM boundary."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from eligibility.audit import canonical_hash
from eligibility.engine.logical import combine_and
from eligibility.llm import LLMGateway, LLMPurpose
from eligibility.llm.system_prompts import ELIGIBILITY_TEXT_REVIEW_SYSTEM_PROMPT
from eligibility.question_policy import is_routine_onboarding_fact
from eligibility.schema.eligibility_text_review import (
    EligibilityEvidenceSource,
    EligibilityTextReviewBatch,
    EligibilityTextReviewBatchInput,
    EligibilityTextReviewProductInput,
    EligibilityTextStructuredEvaluation,
    EligibilityTextUserFactSnapshot,
    ProductEligibilityTextReview,
    EligibilityTextReviewReasonCode,
)
from eligibility.schema.enums import (
    EvaluationStatus,
    FactSemanticType,
    ResolutionStrategy,
)
from eligibility.schema.evaluation import MissingFactRequest, RuleEvaluation
from eligibility.schema.product import ProductDefinition
from eligibility.schema.search import CandidateEvaluation
from eligibility.schema.user_fact import UserFactStore


ELIGIBILITY_TEXT_REVIEW_POLICY_VERSION = (
    "eligibility-text-review-v1:"
    + hashlib.sha256(
        ELIGIBILITY_TEXT_REVIEW_SYSTEM_PROMPT.encode("utf-8")
    ).hexdigest()[:16]
)


class EligibilityTextReviewValidationError(ValueError):
    """A complete LLM batch failed deterministic grounding validation."""


class EligibilityTextReviewer:
    """One structured LLM call for one frontier batch."""

    def __init__(self, gateway: LLMGateway) -> None:
        self.gateway = gateway

    def review(
        self,
        request: EligibilityTextReviewBatchInput,
    ) -> EligibilityTextReviewBatch:
        response = self.gateway.generate_structured(
            LLMPurpose.ELIGIBILITY_TEXT_REVIEW,
            "ELIGIBILITY_TEXT_REVIEW_INPUT:\n"
            + json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2),
            EligibilityTextReviewBatch,
            system_prompt=ELIGIBILITY_TEXT_REVIEW_SYSTEM_PROMPT,
            metadata={
                "review_id": request.review_id,
                "product_ids": [item.product_id for item in request.products],
                "input_context_hash": canonical_hash(request),
                "policy_version": ELIGIBILITY_TEXT_REVIEW_POLICY_VERSION,
            },
        )
        return validate_batch(response.data, request)


def eligibility_text(product: ProductDefinition) -> str:
    if product.normalized is None:
        return ""
    value = product.normalized.eligibility_policy.get("eligibility_text")
    return value.strip() if isinstance(value, str) else ""


def eligibility_text_fingerprint(text: str | None) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def fact_snapshot_hash(facts: Any) -> str:
    payload = _jsonable(facts)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def review_cache_key(
    search_session_id: str,
    product_id: str,
    product_version: int,
    text_fingerprint: str,
    fact_hash: str,
    *,
    as_of: str,
    subscription_date: str,
    policy_version: str = ELIGIBILITY_TEXT_REVIEW_POLICY_VERSION,
) -> str:
    return canonical_hash(
        {
            "search_session_id": search_session_id,
            "product_id": product_id,
            "product_version": product_version,
            "eligibility_text_fingerprint": text_fingerprint,
            "user_fact_snapshot_hash": fact_hash,
            "as_of": as_of,
            "subscription_date": subscription_date,
            "policy_version": policy_version,
        }
    )


def frontier(items: Sequence[Any], limit: int = 3) -> list[Any]:
    """Return every competition-ranked item at or above the numeric boundary."""

    ranked = [(int(_get(item, "rank", 10**9)), item) for item in items]
    return [item for rank, item in ranked if rank <= limit]


def build_user_fact_snapshot(
    fact_store: UserFactStore,
    pre_search_profile: Mapping[str, Any] | None = None,
) -> EligibilityTextUserFactSnapshot:
    return EligibilityTextUserFactSnapshot(
        facts=[
            {
                "fact_id": fact.fact_id,
                "fact_type": fact.fact_type,
                "value": fact.value,
                "source_type": fact.source_type.value,
                "semantic_type": fact.semantic_type.value,
                "valid_from": fact.valid_from,
                "valid_to": fact.valid_to,
            }
            for fact in fact_store.active_facts
        ],
        pre_search_profile={
            key: (
                value.model_dump(mode="json")
                if hasattr(value, "model_dump")
                else _jsonable(value)
            )
            for key, value in (pre_search_profile or {}).items()
        },
    )


def build_product_packet(
    product: ProductDefinition | Mapping[str, Any],
    rank: int,
    structured_evaluation: CandidateEvaluation | Mapping[str, Any] | None = None,
) -> EligibilityTextReviewProductInput:
    """Build the immutable product packet sent to the review model."""

    if isinstance(product, ProductDefinition):
        text = eligibility_text(product)
        if not text:
            raise ValueError(f"{product.product_id} has no eligibility_text")
        normalized = product.normalized
        assert normalized is not None
        ref_ids = list(normalized.eligibility_policy.get("source_ref_ids") or [])
        if not ref_ids:
            ref_ids = [
                f"ELIGIBILITY_TEXT::{product.product_id}::v{normalized.version}"
            ]
        status = (
            structured_evaluation.eligibility_status
            if isinstance(structured_evaluation, CandidateEvaluation)
            else EvaluationStatus(
                _get(
                    _get(structured_evaluation, "structured_evaluation", structured_evaluation),
                    "status",
                    EvaluationStatus.UNKNOWN,
                )
            )
        )
        reason_codes = (
            [structured_evaluation.product_evaluation.eligibility.reason_code]
            if isinstance(structured_evaluation, CandidateEvaluation)
            else list(_get(structured_evaluation, "reason_codes", []) or [])
        )
        return EligibilityTextReviewProductInput(
            rank=rank,
            product_id=product.product_id,
            product_name=product.name,
            product_version=normalized.version,
            eligibility_text_fingerprint=eligibility_text_fingerprint(text),
            eligibility_text=text,
            structured_evaluation=EligibilityTextStructuredEvaluation(
                status=status,
                reason_codes=reason_codes,
            ),
            source_refs=[
                EligibilityEvidenceSource(source_ref_id=ref_id, source_text=text)
                for ref_id in ref_ids
            ],
        )

    product_id = str(_get(product, "product_id"))
    text = str(_get(product, "eligibility_text", ""))
    version = int(_get(product, "product_version", _get(product, "version", 1)))
    sources = []
    for source in _get(product, "source_refs", []) or []:
        ref_id = str(_get(source, "source_ref_id", _get(source, "id", "")))
        if ref_id:
            sources.append(
                EligibilityEvidenceSource(source_ref_id=ref_id, source_text=text)
            )
    if not sources:
        sources = [
            EligibilityEvidenceSource(
                source_ref_id=f"ELIGIBILITY_TEXT::{product_id}::v{version}",
                source_text=text,
            )
        ]
    structured = _get(product, "structured_evaluation", structured_evaluation) or {}
    return EligibilityTextReviewProductInput(
        rank=rank,
        product_id=product_id,
        product_name=str(_get(product, "product_name", _get(product, "name", ""))),
        product_version=version,
        eligibility_text_fingerprint=str(
            _get(product, "eligibility_text_fingerprint", "")
            or eligibility_text_fingerprint(text)
        ),
        eligibility_text=text,
        structured_evaluation=EligibilityTextStructuredEvaluation(
            status=EvaluationStatus(_get(structured, "status", EvaluationStatus.UNKNOWN)),
            reason_codes=list(_get(structured, "reason_codes", []) or []),
        ),
        source_refs=sources,
    )


def validate_batch(
    batch: EligibilityTextReviewBatch | Mapping[str, Any],
    request: EligibilityTextReviewBatchInput | Sequence[Mapping[str, Any]],
    fact_ids: set[str] | None = None,
) -> EligibilityTextReviewBatch:
    """Validate a complete batch before any result or message can be applied."""

    try:
        parsed = (
            batch
            if isinstance(batch, EligibilityTextReviewBatch)
            else EligibilityTextReviewBatch.model_validate(batch)
        )
    except Exception as exc:
        raise EligibilityTextReviewValidationError(str(exc)) from exc

    if isinstance(request, EligibilityTextReviewBatchInput):
        packets = request.products
        allowed_fact_ids = {
            str(row.get("fact_id"))
            for row in request.user_fact_snapshot.facts
            if row.get("fact_id")
        }
        expected_review_id = request.review_id
        fact_rows = {
            str(row.get("fact_id")): row
            for row in request.user_fact_snapshot.facts
            if row.get("fact_id")
        }
    else:
        packets = [
            item
            if isinstance(item, EligibilityTextReviewProductInput)
            else EligibilityTextReviewProductInput.model_validate(item)
            for item in request
        ]
        allowed_fact_ids = fact_ids or set()
        expected_review_id = parsed.review_id
        fact_rows = {fact_id: {} for fact_id in allowed_fact_ids}

    if parsed.review_id != expected_review_id:
        raise EligibilityTextReviewValidationError("review_id mismatch")
    allowed = {packet.product_id: packet for packet in packets}
    reviewed = {review.product_id for review in parsed.product_reviews}
    if reviewed != set(allowed):
        raise EligibilityTextReviewValidationError(
            "product_reviews must exactly match the requested product_ids"
        )

    for review in parsed.product_reviews:
        packet = allowed[review.product_id]
        allowed_reasons = {
            EvaluationStatus.SATISFIED: {
                EligibilityTextReviewReasonCode.TEXT_ALL_MANDATORY_CONDITIONS_SUPPORTED,
                EligibilityTextReviewReasonCode.TEXT_NO_EXECUTABLE_ELIGIBILITY_CLAIM,
            },
            EvaluationStatus.ACHIEVABLE: {
                EligibilityTextReviewReasonCode.TEXT_ALLOWED_FUTURE_ACTION_CAN_RESOLVE,
            },
            EvaluationStatus.UNKNOWN: {
                EligibilityTextReviewReasonCode.TEXT_REQUIRED_USER_FACT_MISSING,
                EligibilityTextReviewReasonCode.TEXT_AMBIGUOUS_OR_INSUFFICIENT_EVIDENCE,
            },
            EvaluationStatus.UNSATISFIABLE: {
                EligibilityTextReviewReasonCode.TEXT_EXPLICIT_REQUIRED_CONDITION_CONFLICT,
            },
        }
        if review.reason_code not in allowed_reasons[review.status]:
            raise EligibilityTextReviewValidationError(
                "reason_code is incompatible with review status"
            )
        if (
            review.product_version != packet.product_version
            or review.eligibility_text_fingerprint
            != packet.eligibility_text_fingerprint
        ):
            raise EligibilityTextReviewValidationError(
                "product version/fingerprint mismatch"
            )
        source_ids = {source.source_ref_id for source in packet.source_refs}
        if not review.evidence_bindings:
            raise EligibilityTextReviewValidationError(
                "every review requires grounded eligibility_text evidence"
            )
        for evidence in review.evidence_bindings:
            if evidence.source_ref_id not in source_ids:
                raise EligibilityTextReviewValidationError("unknown source_ref_id")
            if not evidence.source_text or evidence.source_text not in packet.eligibility_text:
                raise EligibilityTextReviewValidationError(
                    "source_text is not a substring of eligibility_text"
                )
            if not set(evidence.applied_user_fact_ids) <= allowed_fact_ids:
                raise EligibilityTextReviewValidationError("unknown user fact id")
        namespace = f"ELIGIBILITY_TEXT::{review.product_id}::"
        for draft in review.missing_facts:
            if not draft.fact_type.startswith(namespace):
                raise EligibilityTextReviewValidationError(
                    "missing fact_type is outside the product namespace"
                )
            if any(
                index < 0 or index >= len(review.evidence_bindings)
                for index in draft.grounding_evidence_indexes
            ):
                raise EligibilityTextReviewValidationError(
                    "missing fact grounding index is out of range"
                )
            if any(
                review.evidence_bindings[index].claim_role
                != "MANDATORY_ELIGIBILITY"
                for index in draft.grounding_evidence_indexes
            ):
                raise EligibilityTextReviewValidationError(
                    "missing facts must be grounded in mandatory eligibility evidence"
                )
        if review.status != EvaluationStatus.UNKNOWN and review.missing_facts:
            raise EligibilityTextReviewValidationError(
                "only UNKNOWN reviews may request missing facts"
            )
        if (
            review.reason_code
            == EligibilityTextReviewReasonCode.TEXT_REQUIRED_USER_FACT_MISSING
            and not review.missing_facts
        ):
            raise EligibilityTextReviewValidationError(
                "missing-user-fact reason requires at least one question"
            )
        if (
            review.status == EvaluationStatus.UNKNOWN
            and not review.missing_facts
            and not review.evidence_bindings
        ):
            raise EligibilityTextReviewValidationError(
                "UNKNOWN requires a missing fact or grounded ambiguity"
            )
        if review.status == EvaluationStatus.UNSATISFIABLE and not any(
            evidence.applied_user_fact_ids for evidence in review.evidence_bindings
        ):
            raise EligibilityTextReviewValidationError(
                "UNSATISFIABLE requires an applied user fact"
            )
        if (
            review.status == EvaluationStatus.SATISFIED
            and review.reason_code
            == EligibilityTextReviewReasonCode.TEXT_ALL_MANDATORY_CONDITIONS_SUPPORTED
            and not any(
                evidence.applied_user_fact_ids
                for evidence in review.evidence_bindings
            )
        ):
            raise EligibilityTextReviewValidationError(
                "SATISFIED mandatory conditions require applied user facts"
            )
        if (
            review.reason_code
            == EligibilityTextReviewReasonCode.TEXT_NO_EXECUTABLE_ELIGIBILITY_CLAIM
            and not any(
                evidence.claim_role == "NO_EXECUTABLE_ELIGIBILITY_CLAIM"
                for evidence in review.evidence_bindings
            )
        ):
            raise EligibilityTextReviewValidationError(
                "no-claim reason requires an explicit no-claim evidence role"
            )
        if review.status == EvaluationStatus.ACHIEVABLE:
            applied_ids = {
                fact_id
                for evidence in review.evidence_bindings
                for fact_id in evidence.applied_user_fact_ids
            }
            if not any(
                fact_rows.get(fact_id, {}).get("semantic_type") == "FUTURE_INTENT"
                for fact_id in applied_ids
            ) and not _has_routine_onboarding_evidence(review):
                raise EligibilityTextReviewValidationError(
                    "ACHIEVABLE requires an applied FUTURE_INTENT fact"
                )
    return _defer_routine_onboarding_checks(parsed)


def _defer_routine_onboarding_checks(
    batch: EligibilityTextReviewBatch,
) -> EligibilityTextReviewBatch:
    """Keep ordinary identity-document preparation out of ranking questions.

    These checks are real at the final application step, but asking whether an
    adult already has an ID creates no useful product distinction.  Preserve
    every other grounded eligibility condition from the review batch.
    """

    normalized_reviews: list[ProductEligibilityTextReview] = []
    for review in batch.product_reviews:
        remaining = [
            draft
            for draft in review.missing_facts
            if not is_routine_onboarding_fact(draft.fact_type, draft.question)
        ]
        if len(remaining) == len(review.missing_facts):
            normalized_reviews.append(review)
            continue
        update: dict[str, Any] = {"missing_facts": remaining}
        if (
            review.status == EvaluationStatus.UNKNOWN
            and review.reason_code
            == EligibilityTextReviewReasonCode.TEXT_REQUIRED_USER_FACT_MISSING
            and not remaining
        ):
            update.update(
                {
                    "status": EvaluationStatus.ACHIEVABLE,
                    "reason_code": (
                        EligibilityTextReviewReasonCode
                        .TEXT_ALLOWED_FUTURE_ACTION_CAN_RESOLVE
                    ),
                }
            )
        normalized_reviews.append(review.model_copy(update=update, deep=True))
    return batch.model_copy(
        update={"product_reviews": normalized_reviews},
        deep=True,
    )


def _has_routine_onboarding_evidence(review: ProductEligibilityTextReview) -> bool:
    return any(
        is_routine_onboarding_fact("", evidence.source_text)
        for evidence in review.evidence_bindings
    )


def combine_status(
    structured: EvaluationStatus,
    text: EvaluationStatus,
) -> tuple[EvaluationStatus, str]:
    return combine_and([structured, text])


def _apply_validated_review_to_candidate(
    candidate: CandidateEvaluation,
    review: ProductEligibilityTextReview,
) -> CandidateEvaluation:
    """Apply one review from a batch already accepted by ``validate_batch``."""

    combined_status, combined_reason = combine_status(
        candidate.eligibility_status,
        review.status,
    )
    rule_id = f"{candidate.product_id}:ELIGIBILITY_TEXT_REVIEW"
    missing_requests: list[MissingFactRequest] = []
    for draft in review.missing_facts:
        grounding_terms = [
            review.evidence_bindings[index].source_text
            for index in draft.grounding_evidence_indexes
        ]
        missing_requests.append(
            MissingFactRequest(
                fact_type=draft.fact_type,
                resolution_strategy=ResolutionStrategy.ASK_USER,
                question=draft.question,
                requested_by_rule_id=rule_id,
                # This boundary always creates ASK_USER requests.  Observed
                # facts/events require an authoritative source and cannot be
                # populated from a user's answer.  Normalize a model mistake
                # here so the answer transaction cannot fail and loop.
                expected_semantic_type=(
                    draft.semantic_type
                    if draft.semantic_type
                    in {
                        FactSemanticType.SELF_REPORTED_FACT,
                        FactSemanticType.FUTURE_INTENT,
                    }
                    else FactSemanticType.SELF_REPORTED_FACT
                ),
                grounding_terms=grounding_terms,
                missing_fact_id=(
                    "MISSING-"
                    + canonical_hash(
                        {
                            "product_id": candidate.product_id,
                            "fact_type": draft.fact_type,
                            "rule_id": rule_id,
                        }
                    )[:16]
                ),
            )
        )
    text_rule = RuleEvaluation(
        rule_id=rule_id,
        rule_name="가입조건 원문 LLM 검수",
        rule_type="ELIGIBILITY_TEXT_REVIEW",
        status=review.status,
        reason_code=review.reason_code.value,
        missing_facts=missing_requests,
        evidence={
            "product_version": review.product_version,
            "eligibility_text_fingerprint": review.eligibility_text_fingerprint,
            "evidence_bindings": [
                item.model_dump(mode="json") for item in review.evidence_bindings
            ],
        },
    )
    original = candidate.product_evaluation
    combined_rule = RuleEvaluation(
        rule_id=f"{candidate.product_id}:ELIGIBILITY_COMBINED",
        rule_name="구조화 가입조건과 원문 검수 결합",
        rule_type="AND",
        status=combined_status,
        reason_code=combined_reason,
        missing_facts=[*original.eligibility.missing_facts, *missing_requests],
        children=[original.eligibility, text_rule],
    )
    all_missing = [*original.missing_facts, *missing_requests]
    unresolved_ids = list(candidate.unresolved_material_fact_ids)
    unresolved_ids.extend(
        request.missing_fact_id
        or f"{request.fact_type}:{request.requested_by_rule_id}"
        for request in missing_requests
    )
    product_evaluation = original.model_copy(
        update={
            "evaluation_id": (
                "EVAL-"
                + canonical_hash(
                    {
                        "structured": original.evaluation_id,
                        "text_review": review.model_dump(mode="json"),
                    }
                )[:16]
            ),
            "eligibility_status": combined_status,
            "eligibility": combined_rule,
            "missing_facts": all_missing,
            "is_provisional": (
                original.is_provisional or combined_status == EvaluationStatus.UNKNOWN
            ),
        },
        deep=True,
    )
    return candidate.model_copy(
        update={
            "product_evaluation": product_evaluation,
            "material_unknown_count": (
                candidate.material_unknown_count + len(missing_requests)
            ),
            "unresolved_material_fact_ids": list(dict.fromkeys(unresolved_ids)),
            "eligibility_text_review_status": review.status,
            "eligibility_text_review_reason_code": review.reason_code.value,
            "eligibility_text_review_fingerprint": review.eligibility_text_fingerprint,
        },
        deep=True,
    )


def _get(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return sorted((_jsonable(item) for item in value), key=repr)
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    return value
