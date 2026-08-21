from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

from eligibility.audit import AuditEventType, AuditSession, canonical_hash
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.schema.enums import (
    EvaluationStatus,
    PreferenceValue,
    RankingComparability,
)
from eligibility.schema.product import ContractTerm, ProductDefinition
from eligibility.schema.search import (
    CandidateEvaluation,
    ContributionFeasibilityClarification,
    ProductContributionChoice,
    ProductSearchIntent,
)
from eligibility.schema.user_fact import UserFactStore
from eligibility.search.contribution import (
    ContributionPlanner,
    ContributionFeasibilityEvaluator,
    apply_product_contribution_choices,
    build_evaluation_context,
    resolve_term,
    terms_equivalent,
)
from eligibility.search.retrieval import CandidateRetriever


class MultiProductEvaluator:
    """Orchestrate N calls to the deterministic per-product core."""

    def __init__(
        self,
        engine: FinancialEligibilityEngine | None = None,
        contribution_planner: ContributionPlanner | None = None,
        contribution_feasibility: ContributionFeasibilityEvaluator | None = None,
    ) -> None:
        self.engine = engine or FinancialEligibilityEngine()
        self.contribution_planner = contribution_planner or ContributionPlanner()
        self.contribution_feasibility = (
            contribution_feasibility or ContributionFeasibilityEvaluator()
        )

    def evaluate(
        self,
        products: Iterable[ProductDefinition],
        fact_store: UserFactStore,
        intent: ProductSearchIntent,
        *,
        as_of: date,
        subscription_date: date,
        audit: AuditSession | None = None,
        product_contribution_choices: dict[str, list[ProductContributionChoice]] | None = None,
    ) -> dict[str, CandidateEvaluation]:
        results: dict[str, CandidateEvaluation] = {}
        choice_map = product_contribution_choices or {}
        for product in products:
            effective_plan = apply_product_contribution_choices(
                product,
                intent.contribution_plan,
                choice_map.get(product.product_id, []),
            )
            planned = self.contribution_planner.build(
                product, effective_plan, subscription_date=subscription_date
            )
            filtered_inputs = []
            option_feasibilities = []
            feasibility_clarification = None
            for missing_input in planned.missing_ranking_inputs:
                filtered, feasibility_rows, clarification = (
                    self.contribution_feasibility.analyze_ranking_input(
                        product,
                        intent.contribution_plan,
                        missing_input,
                        planner=self.contribution_planner,
                        subscription_date=subscription_date,
                    )
                )
                option_feasibilities.extend(feasibility_rows)
                if filtered is not None:
                    filtered_inputs.append(filtered)
                if clarification is not None:
                    feasibility_clarification = clarification

            # A previously committed product-specific choice can become
            # unaffordable after a global affordability patch.  v0.4.4 does not
            # silently discard that product: it derives feasible replacement
            # options from the product policy with the stale choice removed and
            # offers choice revision alongside adjust/exclude.
            if planned.core_plan is not None:
                affordability = self.contribution_feasibility.check_projection(
                    planned.projection, intent.contribution_plan
                )
                if not affordability.feasible:
                    assert affordability.affordability_limit is not None
                    assert affordability.affordability_frequency is not None
                    active_choices = list(choice_map.get(product.product_id, []))
                    stale_choice = None
                    replacement_options: list = []
                    for current_choice in active_choices:
                        other_choices = [
                            item
                            for item in active_choices
                            if (item.product_id, item.field)
                            != (current_choice.product_id, current_choice.field)
                        ]
                        without_current = apply_product_contribution_choices(
                            product, intent.contribution_plan, other_choices
                        )
                        replanned = self.contribution_planner.build(
                            product, without_current, subscription_date=subscription_date
                        )
                        replacement_input = next(
                            (
                                item
                                for item in replanned.missing_ranking_inputs
                                if item.required_field == current_choice.field
                            ),
                            None,
                        )
                        if replacement_input is None:
                            continue
                        filtered_replacement, replacement_rows, _ = (
                            self.contribution_feasibility.analyze_ranking_input(
                                product,
                                intent.contribution_plan,
                                replacement_input,
                                planner=self.contribution_planner,
                                subscription_date=subscription_date,
                            )
                        )
                        option_feasibilities.extend(replacement_rows)
                        if filtered_replacement is not None and filtered_replacement.allowed_options:
                            stale_choice = current_choice
                            replacement_options = list(filtered_replacement.allowed_options)
                            break

                    identity = {
                        "product_id": product.product_id,
                        "limit": str(affordability.affordability_limit),
                        "frequency": affordability.affordability_frequency.value,
                        "max_bucket": str(affordability.max_bucket_amount),
                        "stale_choice_id": (stale_choice.choice_id if stale_choice is not None else None),
                        "replacement_options": [str(item) for item in replacement_options],
                    }
                    frequency_label = {
                        "DAILY": "일",
                        "WEEKLY": "주",
                        "MONTHLY": "월",
                        "FLEXIBLE": "회차",
                    }[affordability.affordability_frequency.value]
                    if stale_choice is not None and replacement_options:
                        def render_choice(value) -> str:
                            try:
                                return f"{int(Decimal(str(value))):,}원"
                            except Exception:
                                return str(value)

                        rendered = ", ".join(render_choice(item) for item in replacement_options)
                        question_text = (
                            f"기존에 고른 {render_choice(stale_choice.value)} 선택은 새 {frequency_label} "
                            f"최대 {int(affordability.affordability_limit):,}원 조건을 넘습니다. "
                            f"현재 가능한 선택은 {rendered}입니다. 이 중 하나로 바꿀까요, "
                            "한도를 다시 조정할까요, 아니면 이 상품을 제외할까요?"
                        )
                        allowed_resolutions = [
                            "CHANGE_PRODUCT_CONTRIBUTION_CHOICE",
                            "ADJUST_GLOBAL_AFFORDABILITY",
                            "EXCLUDE_PRODUCT",
                        ]
                    else:
                        question_text = (
                            f"현재 설정한 {frequency_label} 최대 {int(affordability.affordability_limit):,}원으로는 "
                            f"{product.name}의 선택한 납입 스케줄을 유지하기 어렵습니다. "
                            f"한도를 조정할까요, 아니면 이 상품은 제외할까요?"
                        )
                        allowed_resolutions = [
                            "ADJUST_GLOBAL_AFFORDABILITY",
                            "EXCLUDE_PRODUCT",
                        ]
                    feasibility_clarification = ContributionFeasibilityClarification(
                        clarification_id=f"CONTRIBUTION-FEASIBILITY-{canonical_hash(identity)[:16]}",
                        product_id=product.product_id,
                        current_affordability_amount=affordability.affordability_limit,
                        current_affordability_frequency=affordability.affordability_frequency,
                        field=(stale_choice.field if stale_choice is not None else None),
                        current_choice_id=(stale_choice.choice_id if stale_choice is not None else None),
                        current_choice_value=(stale_choice.value if stale_choice is not None else None),
                        feasible_options=replacement_options,
                        minimum_required_affordability=affordability.max_bucket_amount,
                        allowed_resolutions=allowed_resolutions,
                        reason_code=(
                            "STALE_PRODUCT_CONTRIBUTION_CHOICE"
                            if stale_choice is not None
                            else "ACTIVE_PRODUCT_CONTRIBUTION_INFEASIBLE"
                        ),
                        question=question_text,
                    )
            context = build_evaluation_context(
                product,
                effective_plan,
                as_of=as_of,
                subscription_date=subscription_date,
            )
            evaluation = self.engine.evaluate_product(
                product,
                fact_store,
                context,
                contribution_plan=planned.core_plan,
                audit=audit,
            )
            interests = evaluation.interest_estimates
            confirmed = interests.get("confirmed")
            realizable = interests.get("realizable")
            upper = interests.get("user_specific_conditional_upper")
            unresolved = [
                item.missing_fact_id or f"{item.fact_type}:{item.requested_by_rule_id}"
                for item in evaluation.missing_facts
                if item.impact is not None
                or evaluation.eligibility_status == EvaluationStatus.UNKNOWN
            ]
            unresolved.extend(item.input_id for item in filtered_inputs)
            if feasibility_clarification is not None:
                unresolved.append(feasibility_clarification.clarification_id)
            if realizable is not None and feasibility_clarification is None:
                ranking_comparability = RankingComparability.COMPARABLE
            elif filtered_inputs or feasibility_clarification is not None:
                ranking_comparability = RankingComparability.MISSING_CONTRIBUTION_INPUT
            else:
                ranking_comparability = RankingComparability.NOT_COMPARABLE
            candidate = CandidateEvaluation(
                product_id=product.product_id,
                product_evaluation=evaluation,
                confirmed_rate=evaluation.rates.confirmed_rate,
                realizable_rate=evaluation.rates.realizable_rate,
                user_specific_conditional_upper_rate=(
                    evaluation.rates.user_specific_conditional_upper_rate
                ),
                confirmed_after_tax_interest=(confirmed.after_tax_interest if confirmed else None),
                realizable_after_tax_interest=(realizable.after_tax_interest if realizable else None),
                conditional_upper_after_tax_interest=(upper.after_tax_interest if upper else None),
                confirmed_pre_tax_interest=(confirmed.pre_tax_interest if confirmed else None),
                conditional_upper_pre_tax_interest=(upper.pre_tax_interest if upper else None),
                estimated_total_principal=(
                    realizable.total_principal
                    if realizable is not None
                    else planned.projection.estimated_total_principal
                ),
                realizable_pre_tax_interest=(realizable.pre_tax_interest if realizable else None),
                preference_score=self._preference_score(product, intent),
                action_burden_score=self._action_burden(product, evaluation),
                material_unknown_count=len(set(unresolved)),
                unresolved_material_fact_ids=sorted(set(unresolved)),
                contribution_projection=planned.projection,
                ranking_comparability=ranking_comparability,
                missing_ranking_inputs=list(filtered_inputs),
                contribution_option_feasibilities=option_feasibilities,
                contribution_feasibility_clarification=feasibility_clarification,
            )
            results[product.product_id] = candidate
            if audit is not None:
                for feasibility in option_feasibilities:
                    audit.emit(
                        "CONTRIBUTION_FEASIBILITY",
                        AuditEventType.CONTRIBUTION_OPTION_FEASIBILITY_EVALUATED,
                        entity_refs={
                            "product_id": product.product_id,
                            "field": feasibility.field,
                        },
                        output_data=feasibility,
                        payload={
                            "option": str(feasibility.option_value),
                            "status": feasibility.status,
                            "reason_code": feasibility.reason_code,
                            "violation_bucket": feasibility.violation_bucket,
                        },
                    )
                    if feasibility.status == "INFEASIBLE":
                        audit.emit(
                            "CONTRIBUTION_FEASIBILITY",
                            AuditEventType.CONTRIBUTION_OPTION_PRUNED,
                            entity_refs={
                                "product_id": product.product_id,
                                "field": feasibility.field,
                            },
                            output_data=feasibility,
                            payload={"option": str(feasibility.option_value)},
                        )
                if feasibility_clarification is not None:
                    audit.emit(
                        "CONTRIBUTION_FEASIBILITY",
                        AuditEventType.CONTRIBUTION_FEASIBILITY_CLARIFICATION_CREATED,
                        entity_refs={
                            "product_id": product.product_id,
                            "clarification_id": feasibility_clarification.clarification_id,
                        },
                        output_data=feasibility_clarification,
                        payload={
                            "minimum_required_affordability": (
                                str(feasibility_clarification.minimum_required_affordability)
                                if feasibility_clarification.minimum_required_affordability is not None
                                else None
                            )
                        },
                    )
                audit.emit(
                    "CANDIDATE_SEARCH_SERVICE",
                    AuditEventType.CANDIDATE_EVALUATED,
                    entity_refs={
                        "product_id": product.product_id,
                        "evaluation_id": evaluation.evaluation_id,
                    },
                    input_data={"intent_id": intent.search_intent_id},
                    output_data=candidate,
                    payload={
                        "confirmed_rate": str(candidate.confirmed_rate),
                        "realizable_rate": str(candidate.realizable_rate),
                        "conditional_upper_rate": str(candidate.user_specific_conditional_upper_rate),
                        "realizable_after_tax_interest": (
                            str(candidate.realizable_after_tax_interest)
                            if candidate.realizable_after_tax_interest is not None
                            else None
                        ),
                        "ranking_comparability": candidate.ranking_comparability.value,
                        "missing_ranking_input_ids": [
                            item.input_id for item in candidate.missing_ranking_inputs
                        ],
                        "contribution_feasibility_clarification_id": (
                            candidate.contribution_feasibility_clarification.clarification_id
                            if candidate.contribution_feasibility_clarification is not None
                            else None
                        ),
                    },
                )
                audit.emit(
                    "CANDIDATE_SEARCH_SERVICE",
                    AuditEventType.OPTIMISTIC_BOUND_CALCULATED,
                    entity_refs={"product_id": product.product_id},
                    output_data={
                        "conditional_upper_rate": candidate.user_specific_conditional_upper_rate,
                        "conditional_upper_after_tax_interest": candidate.conditional_upper_after_tax_interest,
                    },
                    payload={
                        "upper_is_search_only": True,
                        "unknown_favorable_branch_in_final_ranking": False,
                    },
                )
        return results

    def validate_product_contribution_choice(
        self,
        product: ProductDefinition,
        global_plan,
        existing_choices: list[ProductContributionChoice],
        tentative_choice: ProductContributionChoice,
        *,
        subscription_date: date,
    ) -> None:
        """Validate one product-scoped answer without mutating session state."""

        effective_plan = apply_product_contribution_choices(
            product,
            global_plan,
            [*existing_choices, tentative_choice],
        )
        planned = self.contribution_planner.build(
            product, effective_plan, subscription_date=subscription_date
        )
        if any(
            item.required_field == tentative_choice.field
            for item in planned.missing_ranking_inputs
        ):
            raise ValueError("INVALID_OR_INFEASIBLE_OPTION: answer did not resolve the requested field")
        if planned.core_plan is None:
            if planned.missing_ranking_inputs:
                # The answer is valid, but another field is still needed before a
                # full cashflow can be built.  Commit only this resolved field.
                return
            raise ValueError(
                "INVALID_OR_INFEASIBLE_OPTION: product contribution policy rejected the answer"
            )
        affordability = self.contribution_feasibility.check_projection(
            planned.projection, global_plan
        )
        if not affordability.feasible:
            raise ValueError(
                "INVALID_OR_INFEASIBLE_OPTION: selected cashflow exceeds global affordability "
                f"({affordability.violation_bucket or affordability.reason_code})"
            )

    @staticmethod
    def _preference_score(product: ProductDefinition, intent: ProductSearchIntent) -> int:
        score = 0
        if (
            intent.contribution_plan is not None
            and intent.contribution_plan.selected_term_value is not None
            and intent.contribution_plan.selected_term_unit is not None
        ):
            requested = ContractTerm(
                value=intent.contribution_plan.selected_term_value,
                unit=intent.contribution_plan.selected_term_unit,
            )
            actual = resolve_term(product, None)
            score += 1 if terms_equivalent(actual, requested) else -1
        for preference in intent.preferences:
            if preference.preference == PreferenceValue.NEUTRAL:
                continue
            present = CandidateRetriever._feature_present(product, preference.field)
            if present is None:
                # Unknown typed feature metadata is neutral; do not infer from
                # product/rule wording.
                continue
            if preference.preference == PreferenceValue.PREFER_PRESENT:
                score += 1 if present else -1
            else:
                score += 1 if not present else -1
        return score

    @staticmethod
    def _action_burden(product: ProductDefinition, evaluation) -> int:
        result_by_id = {
            result.rule_id: result for result in evaluation.preferential_rule_results
        }
        burden = 0
        for preferential in product.preferential_rules:
            result = result_by_id.get(preferential.rule.rule_id)
            if result is None or result.status != EvaluationStatus.ACHIEVABLE:
                continue
            candidates: list[int] = []
            for node in MultiProductEvaluator._walk(preferential.rule):
                action = getattr(node, "action", None)
                if action is not None and action.burden_score is not None:
                    candidates.append(action.burden_score)
                future = getattr(node, "future_achievement", None)
                if future is not None:
                    if future.action is not None and future.action.burden_score is not None:
                        candidates.append(future.action.burden_score)
                    for path in future.action_paths:
                        path_scores = [
                            item.burden_score
                            for item in [*path.one_time_actions, *path.recurring_actions]
                            if item.burden_score is not None
                        ]
                        if path_scores:
                            candidates.append(sum(path_scores))
            if candidates:
                burden += min(candidates)
        return burden

    @staticmethod
    def _walk(rule):
        yield rule
        for child in getattr(rule, "children", None) or []:
            yield from MultiProductEvaluator._walk(child)
        child = getattr(rule, "child", None)
        if child is not None:
            yield from MultiProductEvaluator._walk(child)
