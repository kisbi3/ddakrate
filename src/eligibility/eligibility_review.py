from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eligibility.audit import AuditEventType, canonical_hash
from eligibility.schema.eligibility_text_review import (
    EligibilityTextReviewBatchInput,
    ProductEligibilityTextReview,
)
from eligibility.schema.search import CandidateEvaluation
from eligibility.eligibility_text_review import (
    _apply_validated_review_to_candidate,
    build_product_packet,
    build_user_fact_snapshot,
    eligibility_text,
    fact_snapshot_hash,
    frontier,
    review_cache_key,
    validate_batch,
)

if TYPE_CHECKING:
    from eligibility.application_service import _SearchRuntime


class EligibilityReviewMixin:
    """Top-ranked eligibility_text review workflow for :class:`ApplicationService`."""

    def _review_top_ranked_eligibility_text(
        self,
        runtime: _SearchRuntime,
    ) -> None:
        reviewer = self.eligibility_text_reviewer
        runtime.eligibility_text_review_assistant_message = None
        runtime.eligibility_text_review_rounds = 0
        if reviewer is None:
            runtime.eligibility_text_review_state = "DISABLED"
            runtime.eligibility_text_review_pending_count = 0
            return
        assert runtime.ranking is not None
        for product_id in sorted(
            set(runtime.eligibility_text_review_active_keys)
            - set(runtime.candidate_products)
        ):
            previous_key = runtime.eligibility_text_review_active_keys.pop(product_id)
            runtime.audit.emit(
                "ELIGIBILITY_TEXT_REVIEW",
                AuditEventType.ELIGIBILITY_TEXT_REVIEW_INVALIDATED,
                entity_refs={"product_id": product_id},
                payload={
                    "previous_cache_key": previous_key,
                    "reason": "PRODUCT_LEFT_CANDIDATE_SET",
                },
            )

        # Global/profile questions own the early workflow. Reviewing product
        # text before those facts exist would repeatedly call the model with a
        # deliberately incomplete profile and could surface a lower-priority
        # product question ahead of the agreed global invariants.
        if self.pre_search_enabled and self.pre_search_question_planner.select_next(
            runtime.pre_search_profile,
            runtime.intent,
            runtime.candidate_products.values(),
        ) is not None:
            runtime.eligibility_text_review_state = "DEFERRED"
            runtime.eligibility_text_review_pending_count = len(
                self._eligibility_text_frontier(runtime)
            )
            return

        snapshot = build_user_fact_snapshot(
            runtime.fact_store,
            runtime.pre_search_profile,
        )
        snapshot_hash = fact_snapshot_hash(snapshot)
        fact_ids = {
            str(row["fact_id"])
            for row in snapshot.facts
            if row.get("fact_id")
        }
        applied: dict[str, ProductEligibilityTextReview] = {}
        previous_frontier = list(runtime.eligibility_text_review_frontier_ids)

        for round_no in range(1, self.eligibility_text_review_max_rounds + 1):
            ranked_frontier = self._eligibility_text_frontier(runtime)
            frontier_ids = [item.product_id for item in ranked_frontier]
            runtime.eligibility_text_review_frontier_ids = frontier_ids
            if frontier_ids != previous_frontier:
                runtime.audit.emit(
                    "ELIGIBILITY_TEXT_REVIEW",
                    AuditEventType.ELIGIBILITY_REVIEW_FRONTIER_CHANGED,
                    output_data={"product_ids": frontier_ids},
                    payload={
                        "previous_product_ids": previous_frontier,
                        "product_ids": frontier_ids,
                        "round": round_no,
                    },
                )
                previous_frontier = frontier_ids

            packets = []
            packet_keys: dict[str, str] = {}
            for ranked_item in ranked_frontier:
                product_id = ranked_item.product_id
                product = runtime.candidate_products[product_id]
                structured = runtime.structured_evaluations[product_id]
                packet = build_product_packet(product, ranked_item.rank, structured)
                cache_key = review_cache_key(
                    runtime.session.search_session_id,
                    product_id,
                    packet.product_version,
                    packet.eligibility_text_fingerprint,
                    snapshot_hash,
                    as_of=runtime.as_of.isoformat(),
                    subscription_date=runtime.subscription_date.isoformat(),
                )
                prior_key = runtime.eligibility_text_review_active_keys.get(product_id)
                if prior_key is not None and prior_key != cache_key:
                    runtime.audit.emit(
                        "ELIGIBILITY_TEXT_REVIEW",
                        AuditEventType.ELIGIBILITY_TEXT_REVIEW_INVALIDATED,
                        entity_refs={"product_id": product_id},
                        payload={"previous_cache_key": prior_key, "cache_key": cache_key},
                    )
                    runtime.eligibility_text_review_active_keys.pop(product_id, None)
                packet_keys[product_id] = cache_key
                cached = runtime.eligibility_text_review_cache.get(cache_key)
                if cached is not None:
                    applied[product_id] = cached
                    runtime.eligibility_text_review_active_keys[product_id] = cache_key
                else:
                    packets.append(packet)

            if not packets:
                self._apply_eligibility_text_reviews(runtime, applied)
                self._rank_once(runtime)
                stable_ids = [
                    item.product_id for item in self._eligibility_text_frontier(runtime)
                ]
                if stable_ids == frontier_ids:
                    runtime.eligibility_text_review_state = "COMPLETE"
                    runtime.eligibility_text_review_pending_count = 0
                    return
                continue

            runtime.eligibility_text_review_rounds = round_no
            review_id = (
                "ELIGIBILITY-REVIEW-"
                + canonical_hash(
                    {
                        "session": runtime.session.search_session_id,
                        "round": round_no,
                        "products": [item.product_id for item in packets],
                        "fact_snapshot": snapshot_hash,
                    }
                )[:16]
            )
            request = EligibilityTextReviewBatchInput(
                review_id=review_id,
                as_of=runtime.as_of,
                subscription_date=runtime.subscription_date,
                global_policy_invariants={
                    "soldier_tomorrow_savings": (
                        "장병내일준비 상품군은 공통 복무 자격 false/모름이면 가입 불가"
                    ),
                    "youth_policy_account_holding": (
                        "청년미래적금·청년도약계좌 중복가입 여부는 전역 사실"
                    ),
                },
                user_fact_snapshot=snapshot,
                products=packets,
            )
            runtime.audit.emit(
                "ELIGIBILITY_TEXT_REVIEW",
                AuditEventType.ELIGIBILITY_TEXT_REVIEW_REQUESTED,
                input_data=request,
                payload={
                    "review_id": review_id,
                    "round": round_no,
                    "product_ids": [item.product_id for item in packets],
                    "fact_snapshot_hash": snapshot_hash,
                },
            )
            try:
                batch = validate_batch(reviewer.review(request), request)
            except Exception as exc:
                runtime.eligibility_text_review_state = "FAILED"
                runtime.eligibility_text_review_pending_count = len(packets)
                runtime.eligibility_text_review_assistant_message = None
                runtime.audit.emit(
                    "ELIGIBILITY_TEXT_REVIEW",
                    AuditEventType.ELIGIBILITY_TEXT_REVIEW_REJECTED,
                    payload={
                        "review_id": review_id,
                        "round": round_no,
                        "error": f"{type(exc).__name__}: {exc}",
                        "assistant_message_exposed": False,
                    },
                )
                # Cached, currently valid reviews remain usable; the failed
                # batch itself is applied nowhere and contributes no message.
                self._apply_eligibility_text_reviews(runtime, applied)
                self._rank_once(runtime)
                return

            staged = dict(applied)
            for review in batch.product_reviews:
                staged[review.product_id] = review
            # All conversions happen before cache/runtime mutation. This keeps
            # application atomic even if a future schema conversion raises.
            try:
                staged_candidates = self._candidates_with_eligibility_text_reviews(
                    runtime,
                    staged,
                )
            except Exception as exc:
                runtime.eligibility_text_review_state = "FAILED"
                runtime.eligibility_text_review_pending_count = len(packets)
                runtime.eligibility_text_review_assistant_message = None
                runtime.audit.emit(
                    "ELIGIBILITY_TEXT_REVIEW",
                    AuditEventType.ELIGIBILITY_TEXT_REVIEW_REJECTED,
                    payload={
                        "review_id": review_id,
                        "round": round_no,
                        "error": f"{type(exc).__name__}: {exc}",
                        "assistant_message_exposed": False,
                    },
                )
                self._apply_eligibility_text_reviews(runtime, applied)
                self._rank_once(runtime)
                return
            for review in batch.product_reviews:
                cache_key = packet_keys[review.product_id]
                runtime.eligibility_text_review_cache[cache_key] = review
                runtime.eligibility_text_review_active_keys[review.product_id] = cache_key
            applied = staged
            runtime.evaluations = staged_candidates
            runtime.eligibility_text_review_assistant_message = batch.assistant_message
            runtime.audit.emit(
                "ELIGIBILITY_TEXT_REVIEW",
                AuditEventType.ELIGIBILITY_TEXT_REVIEW_COMPLETED,
                output_data=batch,
                payload={"review_id": review_id, "round": round_no},
            )
            runtime.audit.emit(
                "ELIGIBILITY_TEXT_REVIEW",
                AuditEventType.ELIGIBILITY_TEXT_REVIEW_APPLIED,
                output_data={
                    review.product_id: review.status.value
                    for review in batch.product_reviews
                },
                payload={
                    "review_id": review_id,
                    "round": round_no,
                    "assistant_message_approved": True,
                },
            )
            self._rank_once(runtime)
            next_frontier_ids = [
                item.product_id for item in self._eligibility_text_frontier(runtime)
            ]
            if all(product_id in applied for product_id in next_frontier_ids):
                runtime.eligibility_text_review_frontier_ids = next_frontier_ids
                runtime.eligibility_text_review_state = "COMPLETE"
                runtime.eligibility_text_review_pending_count = 0
                return

        remaining = self._eligibility_text_frontier(runtime)
        reviewed_ids = set(applied)
        runtime.eligibility_text_review_state = "PROVISIONAL"
        runtime.eligibility_text_review_assistant_message = None
        runtime.eligibility_text_review_pending_count = sum(
            item.product_id not in reviewed_ids for item in remaining
        )

    def _eligibility_text_frontier(
        self,
        runtime: _SearchRuntime,
    ) -> list[Any]:
        assert runtime.ranking is not None
        all_ranked = self.ranking_service.list_items_with_shared_ranks(
            runtime.ranking,
            runtime.evaluations,
            runtime.candidate_products,
        )
        return [
            item
            for item in frontier(all_ranked, limit=3)
            if eligibility_text(runtime.candidate_products[item.product_id])
        ]

    @staticmethod
    def _candidates_with_eligibility_text_reviews(
        runtime: _SearchRuntime,
        reviews: dict[str, ProductEligibilityTextReview],
    ) -> dict[str, CandidateEvaluation]:
        candidates = dict(runtime.structured_evaluations)
        for product_id, review in reviews.items():
            candidate = candidates.get(product_id)
            if candidate is None:
                continue
            candidates[product_id] = _apply_validated_review_to_candidate(
                candidate,
                review,
            )
        return candidates

    def _apply_eligibility_text_reviews(
        self,
        runtime: _SearchRuntime,
        reviews: dict[str, ProductEligibilityTextReview],
    ) -> None:
        runtime.evaluations = self._candidates_with_eligibility_text_reviews(
            runtime,
            reviews,
        )
