from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from eligibility.application import QuestionGenerator, UserAnswerMapper
from eligibility.audit import AuditEventType, AuditSession, canonical_hash
from eligibility.schema.enums import (
    EvaluationStatus,
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
from eligibility.search.ranking import RankingService


class RankingAwareQuestionPlanner:
    """Select ASK_USER facts by Top-K impact, never by an arbitrary question budget."""

    def __init__(
        self,
        question_generator: QuestionGenerator | None = None,
        ranking_service: RankingService | None = None,
    ) -> None:
        self.question_generator = question_generator or QuestionGenerator()
        self.ranking_service = ranking_service or RankingService()

    def score_candidates(
        self,
        evaluations: dict[str, CandidateEvaluation],
        intent: ProductSearchIntent,
        *,
        answered_question_ids: set[str] | None = None,
        audit: AuditSession | None = None,
    ) -> list[QuestionCandidate]:
        # ``answered_question_ids`` is retained as audit/history compatibility,
        # but current-state need is authoritative in v0.4.5.  If an answer still
        # resolves the need, the evaluator will not emit the missing item.  If a
        # later mutable-state change makes it unresolved again, the question must
        # be eligible again even when the same historical question id exists.
        answered_question_ids = answered_question_ids or set()
        frontier = self._frontier(evaluations, intent)
        ranking_inputs: list[QuestionCandidate] = []
        interest_metric_required = intent.ranking_objective in {
            RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
            RankingObjective.BALANCED,
        }
        for product_id, candidate in evaluations.items():
            if candidate.eligibility_status == EvaluationStatus.UNSATISFIABLE:
                continue
            if product_id not in frontier:
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
        for product_id in frontier:
            candidate = evaluations[product_id]
            for request in candidate.product_evaluation.missing_facts:
                if request.resolution_strategy != ResolutionStrategy.ASK_USER:
                    continue
                key = (
                    request.fact_type,
                    request.expected_semantic_type.value
                    if request.expected_semantic_type is not None
                    else None,
                )
                grouped[key].append((product_id, request))

        scored: list[QuestionCandidate] = []
        for (fact_type, _), entries in grouped.items():
            product_ids = sorted({product_id for product_id, _ in entries})
            requests = [request for _, request in entries]
            primary = self._primary_request(requests)
            question_id = f"QUESTION-{canonical_hash({'fact_type': fact_type, 'semantic': primary.expected_semantic_type.value if primary.expected_semantic_type else None})[:16]}"

            ranking_impact = sum(
                self._candidate_uncertainty(evaluations[product_id], intent)
                for product_id in product_ids
            )
            eligibility_impact = sum(
                1
                for product_id in product_ids
                if evaluations[product_id].eligibility_status == EvaluationStatus.UNKNOWN
                and self._request_in_eligibility(
                    evaluations[product_id], fact_type
                )
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
            if ranking_impact <= 0 and eligibility_impact == 0:
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
                fact_type=fact_type,
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
                reward_or_benefit_impact=reward_impact,
                branch_short_circuit_value=branch_value,
                score=score,
            )
            scored.append(candidate)
            if audit is not None:
                audit.emit(
                    "QUESTION_PLANNER",
                    AuditEventType.QUESTION_CANDIDATE_SCORED,
                    entity_refs={"question_id": question_id, "fact_type": fact_type},
                    input_data={"affected_product_ids": product_ids},
                    output_data=candidate,
                    payload={
                        "ranking_impact": str(ranking_impact),
                        "candidate_coverage": coverage,
                        "eligibility_impact": eligibility_impact,
                        "reward_impact": str(reward_impact),
                        "score": str(score),
                    },
                )

        return sorted(
            [*ranking_inputs, *scored],
            key=lambda item: (
                (
                    0
                    if item.question_kind == "CONTRIBUTION_FEASIBILITY"
                    else (1 if item.question_kind == "RANKING_INPUT" else 2)
                ),
                -item.score,
                -item.eligibility_impact,
                -item.candidate_coverage,
                item.fact_type,
            ),
        )

    def select_next(
        self,
        evaluations: dict[str, CandidateEvaluation],
        intent: ProductSearchIntent,
        *,
        answered_question_ids: set[str] | None = None,
        audit: AuditSession | None = None,
    ) -> PlannedQuestion | None:
        candidates = self.score_candidates(
            evaluations,
            intent,
            answered_question_ids=answered_question_ids,
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
            rendered_question = self.question_generator.generate(selected.request)
        question = PlannedQuestion(
            question_id=selected.question_id,
            question_kind=selected.question_kind,
            request=selected.request,
            ranking_input=selected.ranking_input,
            feasibility_clarification=selected.feasibility_clarification,
            question=rendered_question,
            affected_product_ids=selected.affected_product_ids,
            score=selected.score,
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
        frontier = {item.product_id for item in optimistic[: intent.requested_top_k]}

        final_order = sorted(
            candidates,
            key=lambda item: self.ranking_service._final_sort_key(
                item, intent.ranking_objective
            ),
        )
        current_top = final_order[: intent.requested_top_k]
        frontier.update(item.product_id for item in current_top)
        if len(final_order) > intent.requested_top_k and current_top:
            kth = self.ranking_service.realizable_metric(
                current_top[-1], intent.ranking_objective
            )
            frontier.update(
                item.product_id
                for item in final_order[intent.requested_top_k :]
                if self.ranking_service.optimistic_metric(
                    item, intent.ranking_objective
                )
                >= kth
            )
        return frontier

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
        return max(Decimal("0"), upper - realizable)

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
                item.requested_by_rule_id,
            ),
        )[0]

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
