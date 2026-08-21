from __future__ import annotations

from decimal import Decimal

from eligibility.audit import AuditEventType, AuditSession, canonical_hash
from eligibility.schema.enums import (
    EligibilityBadge,
    EvaluationStatus,
    RankingComparability,
    RankingObjective,
    VerificationBadge,
    VerificationLevel,
)
from eligibility.schema.product import ProductDefinition
from eligibility.schema.search import (
    CandidateEvaluation,
    RankInterval,
    RankingResult,
    RecommendationListItem,
    TopKStabilityResult,
)


_NEG_INF = Decimal("-Infinity")


class RankingService:
    """Transparent lexicographic ranking; optimistic bounds never enter final order."""

    def rank(
        self,
        evaluations: dict[str, CandidateEvaluation],
        products: dict[str, ProductDefinition],
        *,
        objective: RankingObjective,
        top_k: int,
        audit: AuditSession | None = None,
    ) -> tuple[RankingResult, dict[str, CandidateEvaluation]]:
        eligible = [
            candidate
            for candidate in evaluations.values()
            if candidate.eligibility_status != EvaluationStatus.UNSATISFIABLE
        ]
        known = [
            item
            for item in eligible
            if item.eligibility_status in {
                EvaluationStatus.SATISFIED,
                EvaluationStatus.ACHIEVABLE,
            }
        ]
        unknown = [
            item for item in eligible if item.eligibility_status == EvaluationStatus.UNKNOWN
        ]
        # When enough non-UNKNOWN products exist, unresolved eligibility is
        # conservatively placed behind them. Otherwise financial value remains
        # the primary ordering and UNKNOWN candidates can fill the available Top K.
        if len(known) >= top_k:
            ordered = [
                *sorted(known, key=lambda item: self._final_sort_key(item, objective)),
                *sorted(unknown, key=lambda item: self._final_sort_key(item, objective)),
            ]
        else:
            ordered = sorted(
                eligible,
                key=lambda item: self._final_sort_key(item, objective),
            )
        ranked = self._with_rank_intervals(ordered, objective)
        ordered = [ranked[item.product_id] for item in ordered]
        top = ordered[:top_k]
        stability = self.check_stability(ordered, top_k=top_k, objective=objective)
        items = [
            self._list_item(rank, candidate, products[candidate.product_id])
            for rank, candidate in enumerate(top, start=1)
        ]
        identity = {
            "objective": objective.value,
            "ordered": [item.product_id for item in ordered],
            "top_k": top_k,
            "evaluations": [item.product_evaluation.evaluation_id for item in ordered],
        }
        ranking_run_id = f"RANK-{canonical_hash(identity)[:16]}"
        result = RankingResult(
            ranking_run_id=ranking_run_id,
            ranking_objective=objective,
            items=items,
            ordered_product_ids=[item.product_id for item in ordered],
            stability=stability,
        )
        if audit is not None:
            audit.bind_search_context(ranking_run_id=ranking_run_id)
            audit.emit(
                "RANKING_SERVICE",
                AuditEventType.RANKING_CALCULATED,
                output_data=result,
                payload={
                    "objective": objective.value,
                    "uses_realizable_only": True,
                    "ordered_product_ids": result.ordered_product_ids,
                },
            )
            audit.emit(
                "RANKING_SERVICE",
                AuditEventType.TOP_K_STABILITY_CHECKED,
                output_data=stability,
                payload=stability.model_dump(mode="json"),
            )
        return result, ranked

    def check_stability(
        self,
        ordered: list[CandidateEvaluation],
        *,
        top_k: int,
        objective: RankingObjective,
        material_internal_question_remaining: bool = False,
    ) -> TopKStabilityResult:
        if objective in {
            RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
            RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST,
            RankingObjective.BALANCED,
        } and any(
            item.ranking_comparability != RankingComparability.COMPARABLE
            for item in ordered
        ):
            # Even when every available product is displayed, their financial
            # ordering is not stable until all direct comparison metrics are KRW.
            return TopKStabilityResult(
                stable=False,
                stable_membership=(len(ordered) <= top_k),
                material_internal_question_remaining=material_internal_question_remaining,
                reason_code="MISSING_COMPARABLE_INTEREST_INPUT",
            )
        if len(ordered) <= top_k:
            return TopKStabilityResult(
                stable=not material_internal_question_remaining,
                stable_membership=True,
                material_internal_question_remaining=material_internal_question_remaining,
                reason_code=(
                    "ALL_AVAILABLE_CANDIDATES_SELECTED"
                    if not material_internal_question_remaining
                    else "MATERIAL_QUESTION_REMAINS"
                ),
            )
        selected = ordered[:top_k]
        nonselected = ordered[top_k:]
        kth = self.realizable_metric(selected[-1], objective)
        maximum_upper = max(self.optimistic_metric(item, objective) for item in nonselected)
        stable = kth > maximum_upper
        return TopKStabilityResult(
            stable=stable and not material_internal_question_remaining,
            stable_membership=stable,
            current_kth_realizable_score=kth,
            maximum_nonselected_optimistic_score=maximum_upper,
            material_internal_question_remaining=material_internal_question_remaining,
            reason_code=(
                "MATERIAL_QUESTION_REMAINS"
                if stable and material_internal_question_remaining
                else (
                    "KTH_REALIZABLE_EXCEEDS_ALL_NONSELECTED_UPPERS"
                    if stable
                    else "NONSELECTED_CANDIDATE_CAN_ENTER_TOP_K"
                )
            ),
        )

    def _with_rank_intervals(
        self,
        ordered: list[CandidateEvaluation],
        objective: RankingObjective,
    ) -> dict[str, CandidateEvaluation]:
        result: dict[str, CandidateEvaluation] = {}
        for candidate in ordered:
            if (
                objective
                in {
                    RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
                    RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST,
                    RankingObjective.BALANCED,
                }
                and candidate.ranking_comparability != RankingComparability.COMPARABLE
            ):
                result[candidate.product_id] = candidate.model_copy(
                    update={
                        "rank_interval": RankInterval(
                            best_possible_rank=1,
                            worst_possible_rank=max(1, len(ordered)),
                        )
                    },
                    deep=True,
                )
                continue
            own_realizable = self.realizable_metric(candidate, objective)
            own_upper = self.optimistic_metric(candidate, objective)
            best = 1 + sum(
                1
                for other in ordered
                if other.product_id != candidate.product_id
                and self.realizable_metric(other, objective) > own_upper
            )
            worst = 1 + sum(
                1
                for other in ordered
                if other.product_id != candidate.product_id
                and self.optimistic_metric(other, objective) >= own_realizable
            )
            result[candidate.product_id] = candidate.model_copy(
                update={
                    "rank_interval": RankInterval(
                        best_possible_rank=max(1, best),
                        worst_possible_rank=max(best, worst),
                    )
                },
                deep=True,
            )
        return result

    def _final_sort_key(
        self,
        candidate: CandidateEvaluation,
        objective: RankingObjective,
    ) -> tuple:
        eligibility_priority = {
            EvaluationStatus.SATISFIED: 0,
            EvaluationStatus.ACHIEVABLE: 1,
            EvaluationStatus.UNKNOWN: 2,
            EvaluationStatus.UNSATISFIABLE: 3,
        }[candidate.eligibility_status]

        if objective == RankingObjective.MAX_REALIZABLE_RATE:
            primary = candidate.realizable_rate
            secondary = (
                candidate.realizable_after_tax_interest
                if candidate.realizable_after_tax_interest is not None
                else Decimal("0")
            )
        elif objective == RankingObjective.MIN_ACTION_BURDEN:
            primary = Decimal(-candidate.action_burden_score)
            secondary = candidate.realizable_rate
        elif objective == RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST:
            if (
                candidate.ranking_comparability != RankingComparability.COMPARABLE
                or candidate.realizable_pre_tax_interest is None
            ):
                return (
                    1,
                    eligibility_priority,
                    candidate.material_unknown_count,
                    candidate.product_id,
                )
            primary = candidate.realizable_pre_tax_interest
            secondary = candidate.realizable_rate
        else:
            if (
                candidate.ranking_comparability != RankingComparability.COMPARABLE
                or candidate.realizable_after_tax_interest is None
            ):
                # Missing KRW interest is not replaced by a percentage.  These
                # candidates remain in the session but are explicitly unordered
                # by the interest objective until RankingInput is resolved.
                return (
                    1,
                    eligibility_priority,
                    candidate.material_unknown_count,
                    candidate.product_id,
                )
            primary = candidate.realizable_after_tax_interest
            secondary = candidate.realizable_rate

        # Financial objective is primary. Product eligibility uncertainty is a
        # conservative tie-break and is also handled at the cohort level in rank().
        return (
            0,
            -primary,
            -secondary,
            -candidate.preference_score,
            candidate.action_burden_score,
            candidate.material_unknown_count,
            eligibility_priority,
            candidate.product_id,
        )

    @staticmethod
    def realizable_metric(
        candidate: CandidateEvaluation,
        objective: RankingObjective,
    ) -> Decimal:
        if objective == RankingObjective.MAX_REALIZABLE_RATE:
            return candidate.realizable_rate
        if objective == RankingObjective.MIN_ACTION_BURDEN:
            return Decimal(-candidate.action_burden_score)
        if objective == RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST:
            return candidate.realizable_pre_tax_interest or Decimal("0")
        if candidate.realizable_after_tax_interest is None:
            return Decimal("0")
        return candidate.realizable_after_tax_interest

    @staticmethod
    def optimistic_metric(
        candidate: CandidateEvaluation,
        objective: RankingObjective,
    ) -> Decimal:
        if objective == RankingObjective.MAX_REALIZABLE_RATE:
            return candidate.user_specific_conditional_upper_rate
        if objective == RankingObjective.MIN_ACTION_BURDEN:
            return Decimal(-candidate.action_burden_score)
        if objective == RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST:
            return candidate.conditional_upper_pre_tax_interest or Decimal("0")
        if candidate.conditional_upper_after_tax_interest is None:
            return Decimal("0")
        return candidate.conditional_upper_after_tax_interest

    @staticmethod
    def _list_item(
        rank: int,
        candidate: CandidateEvaluation,
        product: ProductDefinition,
    ) -> RecommendationListItem:
        metadata = product.metadata
        institution_name = _institution_name(product.institution_id)
        eligibility_badge = {
            EvaluationStatus.SATISFIED: EligibilityBadge.ELIGIBLE,
            EvaluationStatus.ACHIEVABLE: EligibilityBadge.PLAN_REQUIRED,
            EvaluationStatus.UNKNOWN: EligibilityBadge.VERIFICATION_REQUIRED,
            EvaluationStatus.UNSATISFIABLE: EligibilityBadge.INELIGIBLE,
        }[candidate.eligibility_status]
        levels = set(candidate.product_evaluation.evidence_levels)
        if candidate.material_unknown_count:
            verification_badge = VerificationBadge.ADDITIONAL_VERIFICATION_REQUIRED
        elif VerificationLevel.SELF_REPORTED in levels:
            verification_badge = VerificationBadge.USER_RESPONSE_INCLUDED
        elif VerificationLevel.USER_INTENT in levels:
            verification_badge = VerificationBadge.PLAN_BASED
        else:
            verification_badge = VerificationBadge.FINANCIAL_DATA_VERIFIED
        return RecommendationListItem(
            rank=rank,
            product_id=product.product_id,
            institution_name=institution_name,
            product_name=product.name,
            product_type=product.product_type,
            realizable_rate=candidate.realizable_rate,
            advertised_max_rate=product.advertised_max_rate,
            term_summary=candidate.contribution_projection.term_summary,
            contribution_summary=candidate.contribution_projection.contribution_summary,
            maximum_deposit_summary=candidate.contribution_projection.maximum_deposit_summary,
            planned_contribution_summary=candidate.contribution_projection.planned_contribution_summary,
            estimated_total_principal=candidate.estimated_total_principal,
            estimated_pre_tax_interest=candidate.realizable_pre_tax_interest,
            estimated_after_tax_interest=candidate.realizable_after_tax_interest,
            ranking_comparability=candidate.ranking_comparability,
            missing_ranking_input_count=len(candidate.missing_ranking_inputs),
            eligibility_badge=eligibility_badge,
            verification_badge=verification_badge,
            material_unknown_count=candidate.material_unknown_count,
        )


def _institution_name(institution_id: str) -> str:
    return {
        "SHINHAN_BANK": "신한은행",
        "KAKAO_BANK": "카카오뱅크",
        "KAKAOBANK": "카카오뱅크",
        "IBK_BANK": "IBK기업은행",
        "HANA_BANK": "하나은행",
        "KB_BANK": "KB국민은행",
        "KBANK": "케이뱅크",
        "TOSS_BANK": "토스뱅크",
        "WOORI_BANK": "우리은행",
        "BNK_BUSAN": "BNK부산은행",
        "BNK_KYONGNAM": "BNK경남은행",
    }.get(institution_id, institution_id)
