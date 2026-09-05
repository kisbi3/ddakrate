from __future__ import annotations

from bisect import bisect_left, bisect_right
from decimal import Decimal
import re
import unicodedata

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
_MAX_DISPLAY_PRODUCTS = 100


def _published_rate_reference(
    product: ProductDefinition,
) -> tuple[str | None, str | None, str | None]:
    """Return a display-only official rate/yield without making it rankable.

    Condition-dependent posted yields may be exposed as display-only reference
    values. Performance-linked CMA observations stay in the audit catalog but
    are deliberately hidden from user-facing rate fields.
    """

    normalized = product.normalized
    if normalized is None:
        return None, None, None
    policy = normalized.return_policy
    return_kind = policy.get("return_kind")
    # Performance-linked CMA observations are retained in the normalized
    # catalog for audit, but must not be surfaced as a rate to users.
    if product.product_type == "CMA" and return_kind == "PERFORMANCE_LINKED":
        return None, None, None
    numeric_entries: list[tuple[Decimal, str | None]] = []
    for entry in policy.get("rate_entries", []):
        calculation = entry.get("calculation") or {}
        if calculation.get("unit") != "PERCENT":
            continue
        raw_value = calculation.get("value")
        if raw_value is None:
            continue
        try:
            numeric_entries.append((Decimal(str(raw_value)), entry.get("as_of")))
        except Exception:
            continue

    advertised = policy.get("advertised_max_rate") or {}
    advertised_value = advertised.get("value")
    if advertised_value is not None:
        try:
            numeric_entries.append(
                (Decimal(str(advertised_value)), advertised.get("as_of"))
            )
        except Exception:
            pass
    if not numeric_entries:
        return None, None, None

    values = sorted({value for value, _ in numeric_entries})

    def display(value: Decimal) -> str:
        return format(value.normalize(), "f")

    summary = (
        f"{display(values[0])}%"
        if len(values) == 1
        else f"{display(values[0])}~{display(values[-1])}%"
    )
    as_of_values = sorted({as_of for _, as_of in numeric_entries if as_of})
    label = {
        "POSTED_YIELD": "공시수익률",
        "PERFORMANCE_LINKED": "최근 공시수익률",
    }.get(return_kind, "공식 금리")
    return label, summary, (as_of_values[-1] if as_of_values else None)


class RankingService:
    """Rank viable products by the best user-specific outcome still possible.

    Unknown conditions remain in the provisional metric until an answer or an
    authoritative fact proves them unavailable.  The separately retained
    realizable metric is the lower bound used to decide when Top-K membership
    has become stable and is still the value shown as the user's expected rate.
    """

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
        # The selected financial objective remains primary for every viable
        # product. For the rate objective this means the user-specific possible
        # maximum, not only the rewards already confirmed at this point in the
        # conversation. Eligibility uncertainty stays visible and is a
        # tie-break; a still-possible high-rate product must remain in the
        # provisional ranking until the user or an authoritative fact rules it
        # out.
        ordered = sorted(
            eligible,
            key=lambda item: self._final_sort_key(
                item, objective, products.get(item.product_id)
            ),
        )
        ranked = self._with_rank_intervals(ordered, objective)
        ordered = [ranked[item.product_id] for item in ordered]
        display_candidates = ordered[:_MAX_DISPLAY_PRODUCTS]
        display_items = self._list_items_with_shared_ranks(
            display_candidates, products, objective
        )
        top = ordered[:top_k]
        stability = self.check_stability(ordered, top_k=top_k, objective=objective)
        items = display_items[: len(top)]
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
            display_items=display_items,
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
                    "uses_realizable_only": False,
                    "frontier_ordering_basis": "USER_POSSIBLE_MAX_WITH_REALIZABLE_LOWER_BOUND",
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
        if objective == RankingObjective.MAX_REALIZABLE_RATE and any(
            self._realizable_rate(item) is None for item in ordered
        ):
            return TopKStabilityResult(
                stable=False,
                stable_membership=(len(ordered) <= top_k),
                material_internal_question_remaining=material_internal_question_remaining,
                reason_code="MISSING_COMPARABLE_RATE_INPUT",
            )
        if objective in {
            RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
            RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST,
            RankingObjective.BALANCED,
        } and any(
            not self._interest_comparable(item)
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
        if any(item.eligibility_status == EvaluationStatus.UNKNOWN for item in ordered[:top_k]):
            # A missing eligibility fact can remove a selected product. Keep
            # the unbounded lower sentinel internal; API decimals stay finite.
            return TopKStabilityResult(
                stable=False,
                stable_membership=False,
                material_internal_question_remaining=material_internal_question_remaining,
                reason_code="PENDING_SELECTED_ELIGIBILITY",
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
        kth = self.selected_lower_bound(selected, objective)
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

    @classmethod
    def selected_lower_bound(
        cls,
        selected: list[CandidateEvaluation],
        objective: RankingObjective,
    ) -> Decimal:
        """Conservative lower bound shared by membership and question frontier.

        Possible-max order is not lower-bound order. Any selected product can
        lose its unknown bonus, including the current first-ranked product.
        """
        if any(item.eligibility_status == EvaluationStatus.UNKNOWN for item in selected):
            # A pending mandatory eligibility condition can remove the product
            # regardless of its quoted rate, so outsiders remain challengers.
            return _NEG_INF
        return min((cls.realizable_metric(item, objective) for item in selected), default=_NEG_INF)

    def _with_rank_intervals(
        self,
        ordered: list[CandidateEvaluation],
        objective: RankingObjective,
    ) -> dict[str, CandidateEvaluation]:
        """Attach exact rank intervals without rescanning the full list per item.

        The original pairwise implementation was acceptable for the initial
        50-product catalog, but became the dominant request cost after the
        normalized catalog grew to thousands of products. Sorted metric vectors
        preserve the same comparisons while reducing O(n²) work to O(n log n).
        """

        result: dict[str, CandidateEvaluation] = {}
        realizable_by_product = {
            candidate.product_id: self.realizable_metric(candidate, objective)
            for candidate in ordered
        }
        optimistic_by_product = {
            candidate.product_id: self.optimistic_metric(candidate, objective)
            for candidate in ordered
        }
        sorted_realizable = sorted(realizable_by_product.values())
        sorted_optimistic = sorted(optimistic_by_product.values())
        candidate_count = len(ordered)
        for candidate in ordered:
            if (
                objective
                in {
                    RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
                    RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST,
                    RankingObjective.BALANCED,
                }
                and not self._interest_comparable(candidate)
            ):
                result[candidate.product_id] = candidate.model_copy(
                    update={
                        "rank_interval": RankInterval(
                            best_possible_rank=1,
                            worst_possible_rank=max(1, len(ordered)),
                        )
                    }
                )
                continue
            own_realizable = realizable_by_product[candidate.product_id]
            own_upper = optimistic_by_product[candidate.product_id]

            realizable_above_upper = candidate_count - bisect_right(
                sorted_realizable, own_upper
            )
            # Product data should normally satisfy realizable <= optimistic,
            # but retain the original self-exclusion for adversarial inputs.
            if own_realizable > own_upper:
                realizable_above_upper -= 1
            best = 1 + realizable_above_upper

            optimistic_at_or_above_realizable = candidate_count - bisect_left(
                sorted_optimistic, own_realizable
            )
            if own_upper >= own_realizable:
                optimistic_at_or_above_realizable -= 1
            worst = 1 + optimistic_at_or_above_realizable
            result[candidate.product_id] = candidate.model_copy(
                update={
                    "rank_interval": RankInterval(
                        best_possible_rank=max(1, best),
                        worst_possible_rank=max(best, worst),
                    )
                }
            )
        return result

    def _final_sort_key(
        self,
        candidate: CandidateEvaluation,
        objective: RankingObjective,
        product: ProductDefinition | None = None,
    ) -> tuple:
        amount_match_priority = {
            "NOT_SPECIFIED": 0,
            "EXACT": 0,
            "WITHIN_TOLERANCE": 1,
            "OUTSIDE_TOLERANCE": 2,
            "INPUT_REQUIRED": 3,
        }.get(candidate.contribution_projection.amount_match_status, 3)
        if candidate.contribution_projection.product_choice_override:
            amount_match_priority = 0
        term_match_priority = {
            "NOT_SPECIFIED": 0,
            "EXACT": 0,
            "SELECTABLE_EXACT": 0,
            "WITHIN_BOUNDS": 0,
            "ALTERNATIVE_SHORTER": 1,
            "MISMATCH": 2,
        }.get(candidate.contribution_projection.term_match_status, 2)
        eligibility_priority = {
            EvaluationStatus.SATISFIED: 0,
            EvaluationStatus.ACHIEVABLE: 1,
            EvaluationStatus.UNKNOWN: 2,
            EvaluationStatus.UNSATISFIABLE: 3,
        }[candidate.eligibility_status]
        institution_sort_key, product_sort_key = self._product_name_sort_keys(product)
        realizable_rate = self._realizable_rate(candidate)

        if objective == RankingObjective.MAX_REALIZABLE_RATE:
            # ``MAX_REALIZABLE_RATE`` is retained as the public API enum name,
            # but the provisional recommendation order follows the best rate
            # that is still possible for this user.  The realizable lower bound
            # remains a separate display/calculation value.  This prevents an
            # unanswered or chance-based preferential condition from silently
            # removing a genuine Top-3 contender before it can be asked about.
            possible_rate = self._possible_rate(candidate)
            if possible_rate is None:
                return (
                    amount_match_priority,
                    term_match_priority,
                    1,
                    eligibility_priority,
                    candidate.material_unknown_count,
                    candidate.product_id,
                )
            primary = possible_rate
            secondary = candidate.realizable_pre_tax_interest or Decimal("0")
            resolved_tiebreak = realizable_rate or _NEG_INF
        elif objective == RankingObjective.MIN_ACTION_BURDEN:
            primary = Decimal(-candidate.action_burden_score)
            secondary = realizable_rate or _NEG_INF
            resolved_tiebreak = secondary
        elif objective == RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST:
            if (
                not self._interest_comparable(candidate)
                or candidate.realizable_pre_tax_interest is None
            ):
                return (
                    amount_match_priority,
                    term_match_priority,
                    1,
                    eligibility_priority,
                    candidate.material_unknown_count,
                    candidate.product_id,
                )
            primary = candidate.realizable_pre_tax_interest
            secondary = realizable_rate or _NEG_INF
            resolved_tiebreak = realizable_rate or _NEG_INF
        else:
            if (
                not self._interest_comparable(candidate)
                or candidate.realizable_after_tax_interest is None
            ):
                # Missing KRW interest is not replaced by a percentage.  These
                # candidates remain in the session but are explicitly unordered
                # by the interest objective until RankingInput is resolved.
                return (
                    amount_match_priority,
                    term_match_priority,
                    1,
                    eligibility_priority,
                    candidate.material_unknown_count,
                    candidate.product_id,
                )
            primary = candidate.realizable_after_tax_interest
            secondary = realizable_rate or _NEG_INF
            resolved_tiebreak = realizable_rate or _NEG_INF

        # Financial objective is primary. Product eligibility uncertainty is a
        # conservative tie-break and is also handled at the cohort level in rank().
        return (
            amount_match_priority,
            term_match_priority,
            0,
            -primary,
            -secondary,
            -resolved_tiebreak,
            -(
                realizable_rate
                if realizable_rate is not None
                else _NEG_INF
            ),
            institution_sort_key,
            product_sort_key,
            -candidate.preference_score,
            candidate.action_burden_score,
            candidate.material_unknown_count,
            eligibility_priority,
            candidate.product_id,
        )

    def _list_items_with_shared_ranks(
        self,
        ordered: list[CandidateEvaluation],
        products: dict[str, ProductDefinition],
        objective: RankingObjective,
    ) -> list[RecommendationListItem]:
        """Use competition ranking for equal pre-tax rate and interest values."""

        items: list[RecommendationListItem] = []
        previous_tie_key: tuple[Decimal, Decimal] | None = None
        current_rank = 0
        for position, candidate in enumerate(ordered, start=1):
            tie_key = self._display_tie_key(candidate, objective)
            if tie_key is None or tie_key != previous_tie_key:
                current_rank = position
            items.append(
                self._list_item(
                    current_rank,
                    candidate,
                    products[candidate.product_id],
                )
            )
            previous_tie_key = tie_key
        return items

    def list_items_with_shared_ranks(
        self,
        ranking: RankingResult,
        evaluations: dict[str, CandidateEvaluation],
        products: dict[str, ProductDefinition],
    ) -> list[RecommendationListItem]:
        """Materialize competition ranks for the full ordered set, including ties."""

        ordered = [
            evaluations[product_id]
            for product_id in ranking.ordered_product_ids
            if product_id in evaluations and product_id in products
        ]
        return self._list_items_with_shared_ranks(
            ordered, products, ranking.ranking_objective
        )

    @classmethod
    def _display_tie_key(
        cls,
        candidate: CandidateEvaluation,
        objective: RankingObjective,
    ) -> tuple[Decimal, Decimal] | None:
        if objective == RankingObjective.MAX_REALIZABLE_RATE:
            rate = cls._possible_rate(candidate)
            if rate is None:
                return None
            # Products with the same possible maximum share a competition
            # rank. Their currently expected rate remains visible separately
            # and must not make identical possible maxima look like different
            # ranked offers.
            return rate, Decimal("0")
        if objective == RankingObjective.MIN_ACTION_BURDEN:
            return Decimal(-candidate.action_burden_score), (
                cls._realizable_rate(candidate) or _NEG_INF
            )
        if not cls._interest_comparable(candidate):
            return None
        if objective == RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST:
            interest = candidate.realizable_pre_tax_interest
        else:
            interest = candidate.realizable_after_tax_interest
        if interest is None:
            return None
        return interest, cls._realizable_rate(candidate) or _NEG_INF

    @staticmethod
    def _realizable_rate(candidate: CandidateEvaluation) -> Decimal | None:
        """Read the family-neutral result, falling back for legacy DTOs."""
        result = candidate.rate_evaluation
        if result is not None:
            return result.realizable_rate
        return candidate.realizable_rate

    @staticmethod
    def _conditional_upper_rate(candidate: CandidateEvaluation) -> Decimal | None:
        result = candidate.rate_evaluation
        if result is not None:
            return result.user_specific_conditional_upper_rate
        return candidate.user_specific_conditional_upper_rate

    @classmethod
    def _possible_rate(cls, candidate: CandidateEvaluation) -> Decimal | None:
        """Return the best rate that has not been ruled out for this user.

        Structured user-specific conditions are authoritative when available.
        Some normalized products cannot yet calculate a scenario base rate
        until an amount or term is supplied. In that provisional state the
        published maximum is retained as a conservative candidate-discovery
        fallback instead of dropping the product from the Top-3 frontier.
        """

        upper = cls._conditional_upper_rate(candidate)
        if upper is not None:
            return upper
        result = candidate.rate_evaluation
        if result is not None and result.advertised_max_rate is not None:
            return result.advertised_max_rate
        return candidate.product_evaluation.rates.advertised_max_rate

    @staticmethod
    def _interest_comparable(candidate: CandidateEvaluation) -> bool:
        result = candidate.rate_evaluation
        if result is not None:
            return result.interest_comparable
        return candidate.ranking_comparability == RankingComparability.COMPARABLE

    @staticmethod
    def _product_name_sort_keys(
        product: ProductDefinition | None,
    ) -> tuple[str, str]:
        if product is None:
            return "", ""
        metadata = product.metadata
        institution_name = (
            metadata.institution_name
            if metadata is not None and metadata.institution_name
            else _institution_name(product.institution_id)
        )

        def normalize(value: str) -> str:
            value = unicodedata.normalize("NFKC", value).strip()
            value = re.sub(r"^(?:\(주\)|주식회사)\s*", "", value)
            return value.casefold()

        return normalize(institution_name), normalize(product.name)

    @classmethod
    def realizable_metric(
        cls,
        candidate: CandidateEvaluation,
        objective: RankingObjective,
    ) -> Decimal:
        if objective == RankingObjective.MAX_REALIZABLE_RATE:
            rate = cls._realizable_rate(candidate)
            return rate if rate is not None else _NEG_INF
        if objective == RankingObjective.MIN_ACTION_BURDEN:
            return Decimal(-candidate.action_burden_score)
        if objective == RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST:
            return candidate.realizable_pre_tax_interest or Decimal("0")
        if candidate.realizable_after_tax_interest is None:
            return Decimal("0")
        return candidate.realizable_after_tax_interest

    @classmethod
    def optimistic_metric(
        cls,
        candidate: CandidateEvaluation,
        objective: RankingObjective,
    ) -> Decimal:
        if objective == RankingObjective.MAX_REALIZABLE_RATE:
            upper = cls._possible_rate(candidate)
            return upper if upper is not None else _NEG_INF
        if objective == RankingObjective.MIN_ACTION_BURDEN:
            return Decimal(-candidate.action_burden_score)
        if objective == RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST:
            return candidate.conditional_upper_pre_tax_interest or Decimal("0")
        if candidate.conditional_upper_after_tax_interest is None:
            return Decimal("0")
        return candidate.conditional_upper_after_tax_interest

    @classmethod
    def _list_item(
        cls,
        rank: int,
        candidate: CandidateEvaluation,
        product: ProductDefinition,
    ) -> RecommendationListItem:
        metadata = product.metadata
        institution_name = (
            metadata.institution_name
            if metadata is not None and metadata.institution_name
            else _institution_name(product.institution_id)
        )
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
        published_label, published_summary, published_as_of = (
            _published_rate_reference(product)
        )
        performance_linked_cma = (
            product.product_type == "CMA"
            and product.normalized is not None
            and product.normalized.return_policy.get("return_kind")
            == "PERFORMANCE_LINKED"
        )
        return RecommendationListItem(
            rank=rank,
            product_id=product.product_id,
            institution_id=product.institution_id,
            institution_name=institution_name,
            institution_sector=(
                metadata.institution_sector if metadata is not None else "UNKNOWN"
            ),
            product_name=product.name,
            product_type=product.product_type,
            realizable_rate=cls._realizable_rate(candidate),
            user_specific_conditional_upper_rate=cls._conditional_upper_rate(candidate),
            base_rate=(
                None
                if performance_linked_cma
                else candidate.product_evaluation.rates.evidence_breakdown.base_rate
            ),
            advertised_max_rate=(
                None
                if performance_linked_cma
                else candidate.product_evaluation.rates.advertised_max_rate
            ),
            return_kind=(
                product.normalized.return_policy.get("return_kind")
                if product.normalized is not None
                else "INTEREST"
            ),
            published_rate_label=published_label,
            published_rate_summary=published_summary,
            published_rate_as_of=published_as_of,
            term_summary=candidate.contribution_projection.term_summary,
            contribution_summary=candidate.contribution_projection.contribution_summary,
            maximum_deposit_summary=candidate.contribution_projection.maximum_deposit_summary,
            planned_contribution_summary=candidate.contribution_projection.planned_contribution_summary,
            requested_periodic_amount=candidate.contribution_projection.requested_periodic_amount,
            planned_periodic_amount=candidate.contribution_projection.planned_periodic_amount,
            monthly_equivalent_amount=candidate.contribution_projection.monthly_equivalent_amount,
            amount_difference=candidate.contribution_projection.amount_difference,
            amount_difference_ratio=candidate.contribution_projection.amount_difference_ratio,
            amount_match_status=candidate.contribution_projection.amount_match_status,
            product_choice_override=candidate.contribution_projection.product_choice_override,
            term_match_status=candidate.contribution_projection.term_match_status,
            estimated_total_principal=candidate.estimated_total_principal,
            estimated_pre_tax_interest=candidate.realizable_pre_tax_interest,
            estimated_after_tax_interest=candidate.realizable_after_tax_interest,
            conditional_upper_pre_tax_interest=(
                candidate.conditional_upper_pre_tax_interest
            ),
            conditional_upper_after_tax_interest=(
                candidate.conditional_upper_after_tax_interest
            ),
            ranking_comparability=candidate.ranking_comparability,
            missing_ranking_input_count=len(candidate.missing_ranking_inputs),
            eligibility_badge=eligibility_badge,
            verification_badge=verification_badge,
            material_unknown_count=candidate.material_unknown_count,
            eligibility_text_review_status=candidate.eligibility_text_review_status,
            eligibility_text_review_reason_code=(
                candidate.eligibility_text_review_reason_code
            ),
            eligibility_text_review_fingerprint=(
                candidate.eligibility_text_review_fingerprint
            ),
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
