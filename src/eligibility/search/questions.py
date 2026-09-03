from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import re

from eligibility.application import QuestionGenerator, UserAnswerMapper
from eligibility.audit import AuditEventType, AuditSession, canonical_hash
from eligibility.schema.enums import (
    EvaluationStatus,
    FactSemanticType,
    PreferenceValue,
    RankingInputStatus,
    RankingObjective,
    ResolutionStrategy,
)
from eligibility.schema.search import (
    CandidateEvaluation,
    PlannedQuestion,
    ProductSearchIntent,
    QuestionCandidate,
)
from eligibility.schema.condition_requirement import QuestionSpec
from eligibility.question_policy import (
    is_future_action_fact,
    is_card_benefit_request,
    is_first_transaction_history_fact,
    is_institution_product_holding_history_fact,
    is_information_only_fact,
    is_official_random_promotion_result_fact,
    is_routine_onboarding_fact,
    is_salary_benefit_request,
    normalize_user_question_request,
)
from eligibility.search.ranking import RankingService


class RankingAwareQuestionPlanner:
    """Select ASK_USER facts by Top-K impact, never by an arbitrary question budget."""

    # Kept as a compatibility constant for clients that exposed this setting.
    # Frontier membership itself is never truncated: a product outside this
    # number may still challenge third place and must remain questionable.
    MAX_USER_QUESTION_FRONTIER = None
    USER_VERIFICATION_TOP_K = 3

    PRE_SEARCH_PROFILE_FACTS = {
        "SALARY_ACCOUNT_CHANGE_POSSIBLE",
    }
    PRE_SEARCH_PROFILE_ORDER = {
        "SALARY_ACCOUNT_CHANGE_POSSIBLE": 0,
    }

    def __init__(
        self,
        question_generator: QuestionGenerator | None = None,
        ranking_service: RankingService | None = None,
        exploration_depth_multiplier: int = 1,
    ) -> None:
        if exploration_depth_multiplier < 1:
            raise ValueError("exploration_depth_multiplier must be at least 1")
        self.question_generator = question_generator or QuestionGenerator()
        self.ranking_service = ranking_service or RankingService()
        self.exploration_depth_multiplier = exploration_depth_multiplier

    def score_candidates(
        self,
        evaluations: dict[str, CandidateEvaluation],
        intent: ProductSearchIntent,
        *,
        answered_question_ids: set[str] | None = None,
        suppressed_question_ids: set[str] | None = None,
        suppressed_rule_ids: set[str] | None = None,
        acknowledged_question_families: set[str] | None = None,
        audit: AuditSession | None = None,
    ) -> list[QuestionCandidate]:
        # ``answered_question_ids`` is retained as audit/history compatibility,
        # but current-state need is authoritative in v0.4.5.  If an answer still
        # resolves the need, the evaluator will not emit the missing item.  If a
        # later mutable-state change makes it unresolved again, the question must
        # be eligible again even when the same historical question id exists.
        answered_question_ids = answered_question_ids or set()
        suppressed_question_ids = suppressed_question_ids or set()
        suppressed_rule_ids = suppressed_rule_ids or set()
        acknowledged_question_families = acknowledged_question_families or set()
        current_top = self._frontier(evaluations, intent)
        visible_top3 = self._visible_top3(evaluations, intent)
        product_rate_order = self._product_rate_order(evaluations, intent)
        ranking_inputs: list[QuestionCandidate] = []
        interest_metric_required = intent.ranking_objective in {
            RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
            RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST,
            RankingObjective.BALANCED,
        }
        for product_id, candidate in evaluations.items():
            if candidate.eligibility_status == EvaluationStatus.UNSATISFIABLE:
                continue
            if product_id not in current_top:
                continue
            clarification = candidate.contribution_feasibility_clarification
            if clarification is not None:
                question_id = f"QUESTION-{canonical_hash({'contribution_feasibility': clarification.clarification_id})[:16]}"
                feasibility_candidate = QuestionCandidate(
                    question_id=question_id,
                    fact_type="CONTRIBUTION_FEASIBILITY",
                    question_kind="CONTRIBUTION_FEASIBILITY",
                    request=None,
                    ranking_input=None,
                    feasibility_clarification=clarification,
                    affected_product_ids=[product_id],
                    affected_request_ids=[clarification.clarification_id],
                    ranking_impact=Decimal("0"),
                    candidate_coverage=1,
                    eligibility_impact=0,
                    reward_or_benefit_impact=Decimal("0"),
                    branch_short_circuit_value=0,
                    score=Decimal("1100000000000"),
                )
                ranking_inputs.append(feasibility_candidate)
                if audit is not None:
                    audit.emit(
                        "QUESTION_PLANNER",
                        AuditEventType.QUESTION_CANDIDATE_SCORED,
                        entity_refs={
                            "question_id": question_id,
                            "product_id": product_id,
                            "clarification_id": clarification.clarification_id,
                        },
                        output_data=feasibility_candidate,
                        payload={
                            "question_kind": "CONTRIBUTION_FEASIBILITY",
                            "minimum_required_affordability": (
                                str(clarification.minimum_required_affordability)
                                if clarification.minimum_required_affordability is not None
                                else None
                            ),
                        },
                    )
                # No ranking-input option is valid while the current global
                # affordability makes every schedule infeasible.
                continue
            for ranking_input in candidate.missing_ranking_inputs:
                if ranking_input.status == RankingInputStatus.DECLINED:
                    continue
                # MissingRankingInput currently represents cashflow inputs whose
                # affected metric is after-tax interest.  A rate-only objective
                # must not ask for them merely to decorate the detail view.
                if (
                    ranking_input.affected_metric == "ESTIMATED_AFTER_TAX_INTEREST"
                    and not interest_metric_required
                ):
                    continue
                question_id = f"QUESTION-{canonical_hash({'ranking_input': ranking_input.input_id})[:16]}"
                ranking_candidate = QuestionCandidate(
                    question_id=question_id,
                    fact_type=f"RANKING_INPUT:{ranking_input.required_field}",
                    question_kind="RANKING_INPUT",
                    request=None,
                    ranking_input=ranking_input,
                    affected_product_ids=[product_id],
                    affected_request_ids=[ranking_input.input_id],
                    # Ranking-input questions are prerequisites for a valid KRW
                    # comparison, not merely another percentage/reward question.
                    ranking_impact=Decimal("0"),
                    candidate_coverage=1,
                    eligibility_impact=0,
                    reward_or_benefit_impact=Decimal("0"),
                    branch_short_circuit_value=0,
                    score=Decimal("1000000000000"),
                )
                ranking_inputs.append(ranking_candidate)
                if audit is not None:
                    audit.emit(
                        "QUESTION_PLANNER",
                        AuditEventType.QUESTION_CANDIDATE_SCORED,
                        entity_refs={
                            "question_id": question_id,
                            "product_id": product_id,
                            "ranking_input_id": ranking_input.input_id,
                        },
                        input_data={"affected_product_ids": [product_id]},
                        output_data=ranking_candidate,
                        payload={
                            "question_kind": "RANKING_INPUT",
                            "required_field": ranking_input.required_field,
                            "affected_metric": ranking_input.affected_metric,
                        },
                    )
        grouped: dict[tuple[str, str | None], list[tuple[str, object]]] = defaultdict(list)
        declined_benefit_fields = {
            item.field
            for item in intent.preferences
            if item.preference == PreferenceValue.PREFER_ABSENT
        }
        # Ask only within the Top-3 verification frontier. Every optimistic
        # challenger that can reach the third-place cutoff remains eligible;
        # the frontier is intentionally not truncated by an arbitrary count.
        for product_id in current_top:
            candidate = evaluations[product_id]
            for request in candidate.product_evaluation.missing_facts:
                rate_benefit = (
                    request.impact is not None
                    and request.impact.rate_pp is not None
                )
                if (
                    rate_benefit
                    and "CARD_BENEFIT" in declined_benefit_fields
                    and is_card_benefit_request(request)
                ):
                    continue
                if (
                    rate_benefit
                    and "SALARY_BENEFIT" in declined_benefit_fields
                    and is_salary_benefit_request(request)
                ):
                    continue
                if (
                    rate_benefit
                    and "FIRST_TRANSACTION_BENEFIT" in declined_benefit_fields
                    and is_first_transaction_history_fact(request)
                ):
                    continue
                family_id = self.question_family_id(request)
                if family_id in acknowledged_question_families:
                    continue
                if is_routine_onboarding_fact(request.fact_type, request.question):
                    # Ordinary ID preparation belongs in the final sign-up
                    # checklist; it does not help compare financial products.
                    continue
                if is_information_only_fact(request.fact_type) and not (
                    request.impact is not None
                    and request.impact.rate_pp is not None
                    and request.impact.rate_pp > 0
                ):
                    continue
                if is_official_random_promotion_result_fact(request.fact_type):
                    # An official draw result cannot be answered or promised
                    # by the customer.  Chance-based products are handled by
                    # the separate include/exclude preference question. Other
                    # prerequisites in the same rule (for example marketing
                    # consent) remain valid user questions and must not be
                    # suppressed with the draw outcome.
                    continue
                if is_institution_product_holding_history_fact(request.fact_type):
                    # Past bank-product history is collected once in the final
                    # deterministic pre-search question, not as opaque
                    # product-specific ledger jargon.
                    continue
                if self._is_duplicate_eligibility_text_request(candidate, request):
                    continue
                if (
                    is_future_action_fact(request.fact_type)
                    and not request.question
                    and not any(
                        term.strip()
                        and term.strip()
                        not in {"우대조건", "가입조건", "공식 가입대상"}
                        for term in request.grounding_terms
                    )
                ):
                    # Institution/MyData outcome fields such as a future card or
                    # marketing performance are not automatically answerable by
                    # the customer.  Without an explicit action or grounded
                    # wording, manufacturing a generic yes/no question both
                    # overstates certainty and creates apparent duplicates.
                    continue
                if request.resolution_strategy not in {
                    ResolutionStrategy.ASK_USER,
                    ResolutionStrategy.QUERY_INSTITUTION,
                    ResolutionStrategy.QUERY_MYDATA,
                }:
                    continue
                if (
                    request.resolution_strategy != ResolutionStrategy.ASK_USER
                    and (
                        product_id not in current_top
                        or not self._is_boolean_confirmation(
                            candidate, request.requested_by_rule_id
                        )
                    )
                ):
                    continue
                request = normalize_user_question_request(request)
                if is_future_action_fact(request.fact_type) and not request.question:
                    # Unknown future-action families stay visible as data
                    # uncertainty; they are never turned into an opaque generic
                    # customer question.
                    continue
                key = (
                    self.question_family_id(request),
                    request.expected_semantic_type.value
                    if request.expected_semantic_type is not None
                    else None,
                )
                grouped[key].append((product_id, request))

        scored: list[QuestionCandidate] = []
        for (family_id, _), entries in grouped.items():
            product_ids = sorted({product_id for product_id, _ in entries})
            requests = [request for _, request in entries]
            primary = self._primary_request(requests)
            question_id = f"QUESTION-{canonical_hash({'family_id': family_id, 'semantic': primary.expected_semantic_type.value if primary.expected_semantic_type else None})[:16]}"

            ranking_impact = sum(
                self._candidate_uncertainty(evaluations[product_id], intent)
                for product_id in product_ids
            )
            eligibility_entries = [
                (product_id, request)
                for product_id, request in entries
                if evaluations[product_id].eligibility_status == EvaluationStatus.UNKNOWN
                and self._request_in_eligibility(
                    evaluations[product_id], request.fact_type
                )
            ]
            eligibility_impact = len(eligibility_entries)
            top3_eligibility_impact = sum(
                1
                for product_id, _ in eligibility_entries
                if product_id in visible_top3
            )
            reward_impact = sum(
                (
                    request.impact.rate_pp
                    if request.impact is not None and request.impact.rate_pp is not None
                    else Decimal("0")
                )
                for request in requests
            )
            coverage = len(product_ids)
            # A stated reward impact is not enough by itself: a cap or a
            # satisfied sibling branch may make the answer unable to change
            # eligibility, realizable/upper value, Top-K membership, or order.
            # A question can stop changing membership once a product has made
            # the current Top K, but it still has to be presented before that
            # list is considered fully reviewed.  This closes every ASK_USER
            # condition for the visible products instead of silently ending as
            # soon as their numerical order happens to be stable.
            if (
                ranking_impact <= 0
                and eligibility_impact == 0
                and not current_top.intersection(product_ids)
            ):
                continue
            branch_value = sum(
                1
                for product_id, request in entries
                if self._can_short_circuit_branch(
                    evaluations[product_id], request.requested_by_rule_id
                )
            )
            # Transparent deterministic score. The ranking delta is kept in its
            # native metric; fixed components only break ties and prioritize
            # eligibility/shared facts.
            score = (
                ranking_impact
                + Decimal(eligibility_impact * 10000)
                + reward_impact * Decimal("1000")
                + Decimal(coverage * 100)
                + Decimal(branch_value * 10)
            )
            candidate = QuestionCandidate(
                question_id=question_id,
                fact_type=primary.fact_type,
                family_id=family_id,
                question_kind="FINANCIAL_FACT",
                request=primary,
                ranking_input=None,
                affected_product_ids=product_ids,
                affected_request_ids=sorted(
                    {
                        UserAnswerMapper.request_reference(request)
                        for request in requests
                    }
                ),
                ranking_impact=ranking_impact,
                candidate_coverage=coverage,
                eligibility_impact=eligibility_impact,
                top3_eligibility_impact=top3_eligibility_impact,
                reward_or_benefit_impact=reward_impact,
                branch_short_circuit_value=branch_value,
                score=score,
            )
            scored.append(candidate)
            if audit is not None:
                audit.emit(
                    "QUESTION_PLANNER",
                    AuditEventType.QUESTION_CANDIDATE_SCORED,
                    entity_refs={"question_id": question_id, "fact_type": family_id},
                    input_data={"affected_product_ids": product_ids},
                    output_data=candidate,
                    payload={
                        "ranking_impact": str(ranking_impact),
                        "candidate_coverage": coverage,
                        "eligibility_impact": eligibility_impact,
                        "top3_eligibility_impact": top3_eligibility_impact,
                        "reward_impact": str(reward_impact),
                        "score": str(score),
                    },
                )

        return sorted(
            [
                item
                for item in [*ranking_inputs, *scored]
                if item.question_id not in suppressed_question_ids
            ],
            key=lambda item: (
                (
                    0
                    if item.fact_type in self.PRE_SEARCH_PROFILE_FACTS
                    else 1
                    if item.question_kind == "CONTRIBUTION_FEASIBILITY"
                    else (2 if item.question_kind == "RANKING_INPUT" else 3)
                ),
                self.PRE_SEARCH_PROFILE_ORDER.get(item.fact_type, 99),
                -item.top3_eligibility_impact,
                -item.candidate_coverage,
                -item.ranking_impact,
                -item.reward_or_benefit_impact,
                min(
                    (
                        product_rate_order.get(product_id, len(product_rate_order))
                        for product_id in item.affected_product_ids
                    ),
                    default=len(product_rate_order),
                ),
                item.fact_type,
            ),
        )

    def frontier_product_ids(
        self,
        evaluations: dict[str, CandidateEvaluation],
        intent: ProductSearchIntent,
    ) -> list[str]:
        """Expose every product that can currently enter the visible Top 3."""

        return sorted(self._frontier(evaluations, intent))

    def select_next(
        self,
        evaluations: dict[str, CandidateEvaluation],
        intent: ProductSearchIntent,
        *,
        answered_question_ids: set[str] | None = None,
        suppressed_question_ids: set[str] | None = None,
        suppressed_rule_ids: set[str] | None = None,
        acknowledged_question_families: set[str] | None = None,
        audit: AuditSession | None = None,
    ) -> PlannedQuestion | None:
        candidates = self.score_candidates(
            evaluations,
            intent,
            answered_question_ids=answered_question_ids,
            suppressed_question_ids=suppressed_question_ids,
            suppressed_rule_ids=suppressed_rule_ids,
            acknowledged_question_families=acknowledged_question_families,
            audit=audit,
        )
        if not candidates:
            return None
        selected = candidates[0]
        if selected.question_kind == "CONTRIBUTION_FEASIBILITY":
            assert selected.feasibility_clarification is not None
            rendered_question = selected.feasibility_clarification.question
        elif selected.question_kind == "RANKING_INPUT":
            assert selected.ranking_input is not None
            rendered_question = selected.ranking_input.question
        else:
            assert selected.request is not None
            request = selected.request
            if "TAMNANEUNJEON_PLATFORM_MEMBERSHIP" in request.fact_type:
                # This is an onboarding condition, not a requirement that the
                # user happened to have registered before starting the search.
                request = request.model_copy(
                    update={
                        "expected_semantic_type": FactSemanticType.FUTURE_INTENT,
                        "question": (
                            "탐나는전 플랫폼에 이미 회원가입되어 있거나, "
                            "지금 회원가입을 진행할 수 있나요?"
                        ),
                    },
                    deep=True,
                )
            if request.resolution_strategy in {
                ResolutionStrategy.QUERY_INSTITUTION,
                ResolutionStrategy.QUERY_MYDATA,
            }:
                request = request.model_copy(
                    update={
                        "expected_semantic_type": FactSemanticType.SELF_REPORTED_FACT,
                    },
                    deep=True,
                )
            if "TAMNANEUNJEON_PLATFORM_MEMBERSHIP" in request.fact_type:
                rendered_question = request.question or ""
            elif request.fact_type.startswith("ELIGIBILITY_TEXT::"):
                # This wording is part of the already-grounded review batch.
                # Running a second wording model would add latency and could
                # detach the question from its validated evidence bindings.
                if not request.question:
                    raise ValueError("Grounded eligibility-text question is missing")
                rendered_question = request.question
            else:
                rendered_question = self.question_generator.generate(request)
        question = PlannedQuestion(
            question_id=selected.question_id,
            question_kind=selected.question_kind,
            request=(
                request
                if selected.question_kind == "FINANCIAL_FACT"
                else selected.request
            ),
            ranking_input=selected.ranking_input,
            feasibility_clarification=selected.feasibility_clarification,
            question=rendered_question,
            affected_product_ids=selected.affected_product_ids,
            score=selected.score,
            answer_mode=(
                (
                    "OPTIONS"
                    if self._is_quantitative_family(
                        selected.family_id or selected.fact_type
                    )
                    else "BINARY"
                )
                if selected.question_kind == "FINANCIAL_FACT"
                else "OPTIONS"
            ),
            question_spec=(
                QuestionSpec(
                    question_id=selected.question_id,
                    family_id=(selected.family_id or selected.fact_type),
                    variable_id=(selected.family_id or selected.fact_type),
                    value_schema={
                        "type": (
                            "money"
                            if self._is_quantitative_family(
                                selected.family_id or selected.fact_type
                            )
                            else "boolean"
                        ),
                        "currency": (
                            "KRW"
                            if self._is_quantitative_family(
                                selected.family_id or selected.fact_type
                            )
                            else None
                        ),
                        "period": (
                            "MONTH"
                            if self._is_quantitative_family(
                                selected.family_id or selected.fact_type
                            )
                            else None
                        ),
                        "semantic_type": (
                            request.expected_semantic_type.value
                            if request.expected_semantic_type is not None
                            else None
                        ),
                    },
                    scope={
                        "condition_scope": (
                            "ELIGIBILITY"
                            if selected.eligibility_impact > 0
                            else "RATE_BENEFIT"
                        ),
                        "answer_scope": "QUESTION_FAMILY",
                    },
                    bound_requirement_ids=list(selected.affected_request_ids),
                    affected_product_ids=list(selected.affected_product_ids),
                    options=(
                        [0, 100000, 300000, 500000, 1000000]
                        if self._is_quantitative_family(
                            selected.family_id or selected.fact_type
                        )
                        else [False, True]
                    ),
                    prompt_template_id=(
                        f"family:{selected.family_id or selected.fact_type}:v1"
                    ),
                )
                if selected.question_kind == "FINANCIAL_FACT"
                else None
            ),
        )
        if audit is not None:
            audit.bind_search_context(question_id=question.question_id)
            audit.emit(
                "QUESTION_PLANNER",
                AuditEventType.QUESTION_SELECTED,
                entity_refs={
                    "question_id": question.question_id,
                    "fact_type": (
                        question.request.fact_type
                        if question.request is not None
                        else (
                            f"RANKING_INPUT:{question.ranking_input.required_field}"
                            if question.ranking_input is not None
                            else "CONTRIBUTION_FEASIBILITY"
                        )
                    ),
                },
                output_data=question,
                payload={
                    "affected_product_ids": question.affected_product_ids,
                    "score": str(question.score),
                    "fixed_question_budget": None,
                    "question_kind": question.question_kind,
                },
            )
        return question

    @staticmethod
    def _is_quantitative_family(family_id: str) -> bool:
        key = family_id.upper()
        return any(
            marker in key
            for marker in (
                "CARD_MONTHLY_SPEND",
                "CARD_SPEND_AMOUNT",
                "MONTHLY_SPEND_LIMIT",
            )
        )

    @staticmethod
    def question_family_id(request) -> str:
        """Return the stable user-variable key used for repeat suppression.

        ``action_id`` is preferred when supplied by the normalized catalog; the
        exact grounded question scopes legacy rows that do not have an action
        identifier. A bare fact type is not sufficient: the catalog may reuse
        ``CUSTOMER_SALARY_TRANSFER_MONTH_COUNT`` for different institutions,
        thresholds, and even a military-pay condition. Grouping those rows
        would present one product's wording and propagate its answer to all of
        them. Identical questions still share one answer.
        """

        normalized_request = normalize_user_question_request(request)
        if (
            RankingAwareQuestionPlanner._is_quantitative_family(
                str(request.action_id or request.fact_type)
            )
            or RankingAwareQuestionPlanner._is_quantitative_family(request.fact_type)
            or (
                normalized_request.question
                == "카드 이용실적은 한 달에 최대 얼마까지 가능하신가요?"
                and is_card_benefit_request(normalized_request)
            )
        ):
            return "ACTION-CARD_MONTHLY_SPEND_LIMIT"
        if request.action_id:
            return str(request.action_id)
        question = " ".join((request.question or "").split())
        if question:
            scope = canonical_hash(
                {
                    "fact_type": request.fact_type,
                    "question": question,
                    "semantic": (
                        request.expected_semantic_type.value
                        if request.expected_semantic_type is not None
                        else None
                    ),
                }
            )[:16]
            return f"{request.fact_type}::{scope}"
        # With no action ID or grounded wording there is no proof that another
        # product's answer is semantically interchangeable. Keep it rule-local.
        return f"{request.fact_type}::{request.requested_by_rule_id}"

    @staticmethod
    def _is_duplicate_eligibility_text_request(
        candidate: CandidateEvaluation,
        request,
    ) -> bool:
        """Do not ask an LLM text-review restatement of a typed rule.

        The source-text reviewer can legitimately rediscover phrases such as
        "1인 1계좌".  The normalized account-limit rule already owns that
        predicate and has the only unambiguous wording, so a second fact would
        ask the same thing twice under a different identifier.
        """

        if not request.fact_type.startswith("ELIGIBILITY_TEXT::"):
            return False
        if not request.fact_type.endswith("::EXISTING_ACCOUNT_STATUS"):
            return False
        return any(
            item.fact_type.startswith("EXISTING_PRODUCT_ACCOUNT_LIMIT_REACHED::")
            for item in candidate.product_evaluation.missing_facts
        )

    def _frontier(
        self,
        evaluations: dict[str, CandidateEvaluation],
        intent: ProductSearchIntent,
    ) -> set[str]:
        candidates = [
            item
            for item in evaluations.values()
            if item.eligibility_status != EvaluationStatus.UNSATISFIABLE
        ]
        if not candidates:
            return set()
        optimistic = sorted(
            candidates,
            key=lambda item: (
                -self.ranking_service.optimistic_metric(item, intent.ranking_objective),
                item.product_id,
            ),
        )
        final_order = sorted(
            candidates,
            key=lambda item: self.ranking_service._final_sort_key(
                item, intent.ranking_objective
            ),
        )
        verification_top_k = min(len(final_order), self.USER_VERIFICATION_TOP_K)
        # Verify the visible Top 3 first, then add *every* outside challenger
        # whose optimistic upper can reach the third-place cutoff.  Truncating
        # this set (even at ten products) can silently miss a product that later
        # belongs in Top 3.
        selected = list(final_order[:verification_top_k])
        selected_ids = {item.product_id for item in selected}
        kth = (
            self.ranking_service.realizable_metric(
                selected[-1], intent.ranking_objective
            )
            if selected
            else Decimal("-Infinity")
        )
        for candidate in optimistic:
            if (
                candidate.product_id not in selected_ids
                and self.ranking_service.optimistic_metric(
                    candidate, intent.ranking_objective
                ) >= kth
            ):
                selected.append(candidate)
                selected_ids.add(candidate.product_id)
        # Explicit diagnostic exploration may inspect the next bounded slice,
        # but normal recommendation questioning remains frontier-only.  This
        # does not affect the full challenger calculation above.
        if self.exploration_depth_multiplier > 1:
            extra_limit = verification_top_k * self.exploration_depth_multiplier
            for candidate in optimistic:
                if len(selected) >= extra_limit:
                    break
                if candidate.product_id not in selected_ids:
                    selected.append(candidate)
                    selected_ids.add(candidate.product_id)
        return selected_ids

    def _visible_top3(
        self,
        evaluations: dict[str, CandidateEvaluation],
        intent: ProductSearchIntent,
    ) -> set[str]:
        candidates = [
            item
            for item in evaluations.values()
            if item.eligibility_status != EvaluationStatus.UNSATISFIABLE
        ]
        ordered = sorted(
            candidates,
            key=lambda item: self.ranking_service._final_sort_key(
                item, intent.ranking_objective
            ),
        )
        return {
            item.product_id
            for item in ordered[: self.USER_VERIFICATION_TOP_K]
        }

    def _candidate_uncertainty(
        self,
        candidate: CandidateEvaluation,
        intent: ProductSearchIntent,
    ) -> Decimal:
        upper = self.ranking_service.optimistic_metric(
            candidate, intent.ranking_objective
        )
        realizable = self.ranking_service.realizable_metric(
            candidate, intent.ranking_objective
        )
        # A missing comparable rate is represented by ``-Infinity``.  Products
        # such as performance-linked CMA can legitimately have neither an
        # optimistic nor a realizable fixed rate; subtracting the two sentinels
        # raises decimal.InvalidOperation even though the uncertainty delta is
        # simply not numerically comparable.
        if not upper.is_finite():
            return Decimal("0")
        if not realizable.is_finite():
            return max(Decimal("0"), upper)
        return max(Decimal("0"), upper - realizable)

    def _product_rate_order(
        self,
        evaluations: dict[str, CandidateEvaluation],
        intent: ProductSearchIntent,
    ) -> dict[str, int]:
        """Order verification by the best user-specific outcome still possible."""

        ordered = sorted(
            (
                candidate
                for candidate in evaluations.values()
                if candidate.eligibility_status != EvaluationStatus.UNSATISFIABLE
            ),
            key=lambda item: (
                -self.ranking_service.optimistic_metric(
                    item, intent.ranking_objective
                ),
                item.product_id,
            ),
        )
        return {
            candidate.product_id: index
            for index, candidate in enumerate(ordered)
        }

    @staticmethod
    def _primary_request(requests):
        return sorted(
            requests,
            key=lambda item: (
                -(
                    item.impact.rate_pp
                    if item.impact is not None and item.impact.rate_pp is not None
                    else Decimal("0")
                ),
                -RankingAwareQuestionPlanner._target_magnitude(item),
                item.requested_by_rule_id,
            ),
        )[0]

    @staticmethod
    def _target_magnitude(request) -> int:
        """Prefer one representative high-water-mark for repeated action rules."""

        text = " ".join([request.question or "", *request.grounding_terms])
        targets = [
            int(match.group(1))
            for match in re.finditer(
                r"(\d+)\s*(?:일|회|개월|주|번|번째)",
                text,
            )
        ]
        return max(targets, default=0)

    @staticmethod
    def _request_in_eligibility(
        candidate: CandidateEvaluation,
        fact_type: str,
    ) -> bool:
        stack = [candidate.product_evaluation.eligibility]
        while stack:
            result = stack.pop()
            if any(item.fact_type == fact_type for item in result.missing_facts):
                return True
            stack.extend(result.children)
        return False

    @staticmethod
    def _is_boolean_confirmation(
        candidate: CandidateEvaluation,
        requested_by_rule_id: str,
    ) -> bool:
        evaluation = candidate.product_evaluation
        stack = [
            evaluation.eligibility,
            *evaluation.preferential_rule_results,
            *evaluation.global_guard_results,
        ]
        while stack:
            result = stack.pop()
            if result.rule_id == requested_by_rule_id:
                return (
                    result.rule_name.strip() != "공식 가입대상 충족"
                    and isinstance(result.evidence.get("expected"), bool)
                )
            stack.extend(result.children)
        return False

    @staticmethod
    def _can_short_circuit_branch(
        candidate: CandidateEvaluation,
        requested_by_rule_id: str,
    ) -> bool:
        # The deterministic core already suppresses missing facts from OR
        # branches once another branch is satisfied. Remaining OR requests are
        # therefore material; this flag captures a likely one-answer closure.
        for result in candidate.product_evaluation.preferential_rule_results:
            if result.rule_id == requested_by_rule_id and result.rule_type == "OR":
                return True
            if any(
                child.rule_id == requested_by_rule_id
                for child in result.children
            ) and result.rule_type == "OR":
                return True
        return False
