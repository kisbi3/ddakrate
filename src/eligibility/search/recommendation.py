from __future__ import annotations

import json
from decimal import Decimal
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from eligibility.audit import AuditEventType, AuditSession, canonical_hash
from eligibility.llm import LLMGateway, LLMPurpose
from eligibility.llm.system_prompts import RESULT_EXPLANATION_SYSTEM_PROMPT
from eligibility.llm.grounding import (
    CanonicalClaim,
    CanonicalQuestionPayload,
    ClaimBinding,
    ClaimType,
    extract_condition_terms,
    extract_numeric_claims,
    extract_semantic_claims,
    validate_grounded_text,
)
from eligibility.schema.enums import (
    EligibilityBadge,
    EvaluationStatus,
    RankingComparability,
    RankingObjective,
    UserConditionStatus,
    VerificationLevel,
)
from eligibility.schema.condition_requirement import UserConditionState
from eligibility.schema.product import ProductDefinition
from eligibility.schema.search import (
    CandidateEvaluation,
    PersonalizedRecommendationReason,
    ProductRecommendationDetail,
    ProductSearchIntent,
    RecommendationReasonEntry,
    ProductRecommendationResult,
    RankingResult,
    RateBreakdownItem,
    RateCapAdjustment,
    RateContribution,
)
from eligibility.search.contribution import resolve_term, terms_equivalent
from eligibility.search.product_facts import project_product_search_facts
from eligibility.search.ranking import _institution_name, _published_rate_reference
from eligibility.search.retrieval import CandidateRetriever


def _rate_snapshot_as_of(entry: dict) -> str | None:
    value = entry.get("as_of") or (entry.get("calculation") or {}).get(
        "snapshot_as_of"
    )
    return str(value) if value else None


def _canonical_preferential_conditions(return_policy: dict) -> list[dict]:
    policy = return_policy.get("preferential_policy") or {}
    conditions: list[dict] = []
    for rule in policy.get("rules") or []:
        conditions.append(
            {
                "kind": "CANONICAL",
                "rule_id": rule.get("rule_id"),
                "title": rule.get("title"),
                "condition": rule.get("condition") or {},
                "reward": rule.get("reward") or {},
                "application": rule.get("application") or {},
                "display": rule.get("display") or {},
                "structuring_status": rule.get("structuring_status"),
                "executable": rule.get("executable", policy.get("executable", True)),
                "source_ref_ids": rule.get("source_ref_ids") or [],
            }
        )
    return conditions


class GeneratedExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    explanation: str
    claim_bindings: list[ClaimBinding] = Field(default_factory=list)


class GroundedResultExplainer:
    """LLM wording over typed DTO claims; deterministic fallback on any drift."""

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway

    def explain(self, detail: ProductRecommendationDetail) -> str:
        fallback = self.deterministic_fallback(detail)
        if self.gateway is None:
            return fallback
        payload = self._payload(detail, fallback)
        prompt = "RECOMMENDATION_DETAIL_AND_CLAIMS:\n" + json.dumps(
                {
                    "detail": detail.model_dump(mode="json"),
                    "canonical_claims": payload.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        try:
            response = self.gateway.generate_structured(
                LLMPurpose.RESULT_EXPLANATION,
                prompt,
                GeneratedExplanation,
                system_prompt=RESULT_EXPLANATION_SYSTEM_PROMPT,
                metadata={"detail_hash": canonical_hash(detail)},
            )
        except Exception:
            return fallback
        if not validate_grounded_text(
            response.data.explanation,
            payload,
            bindings=response.data.claim_bindings,
            require_bindings=True,
        ):
            return fallback
        return response.data.explanation

    @staticmethod
    def deterministic_fallback(detail: ProductRecommendationDetail) -> str:
        sentences = [f"{detail.product_name}은 현재 추천 {detail.rank}위입니다."]
        if detail.realizable_rate is None:
            if detail.return_kind == "PERFORMANCE_LINKED":
                sentences.append(
                    "실적배당 상품이므로 확정금리나 확정수익으로 계산하지 않았습니다."
                )
            else:
                sentences.append(
                    "현재 공식 데이터만으로 적용 금리를 확정할 수 없어 금리 계산을 보류했습니다."
                )
        elif detail.estimated_pre_tax_interest is not None:
            sentences.append(
                "계획대로 우대조건을 달성할 경우 예상금리는 "
                f"{detail.realizable_rate}%이며, 예상 세전이자는 "
                f"{int(detail.estimated_pre_tax_interest):,}원입니다."
            )
        else:
            sentences.append(
                "계획대로 우대조건을 달성할 경우 예상금리는 "
                f"{detail.realizable_rate}%입니다."
            )

        def join_items(items: list[str], limit: int) -> str:
            return "; ".join(
                item.strip().rstrip(".。") for item in items[:limit] if item.strip()
            )

        if detail.recommendation_reason.positives:
            sentences.append(
                "주요 추천 근거: "
                + join_items(detail.recommendation_reason.positives, 3)
                + "."
            )
        if detail.recommendation_reason.limitations:
            sentences.append(
                "확인된 제한: "
                + join_items(detail.recommendation_reason.limitations, 2)
                + "."
            )
        if detail.recommendation_reason.actions:
            sentences.append(
                "필요한 행동: "
                + join_items(detail.recommendation_reason.actions, 2)
                + "."
            )
        if detail.recommendation_reason.unknowns:
            sentences.append(
                "아직 확인 전인 항목: "
                + join_items(detail.recommendation_reason.unknowns, 2)
                + "."
            )
        return " ".join(sentences)

    @staticmethod
    def _payload(
        detail: ProductRecommendationDetail,
        fallback: str,
    ) -> CanonicalQuestionPayload:
        claims: list[CanonicalClaim] = []
        claims.extend(extract_numeric_claims(fallback, source_ref="fallback"))
        claims.extend(extract_numeric_claims(detail.term_summary, source_ref="term_summary"))
        claims.extend(extract_semantic_claims(fallback, source_ref="fallback"))

        def flatten(items):
            for item in items:
                yield item
                yield from flatten(item.children)

        flat_breakdown = list(flatten(detail.rate_breakdown))
        for item in flat_breakdown:
            if item.nominal_reward_pp is not None:
                claims.append(
                    CanonicalClaim(
                        claim_id=f"CLAIM-{canonical_hash({'rule': item.rule_id, 'reward': str(item.nominal_reward_pp)})[:16]}",
                        claim_type=ClaimType.RATE,
                        value=str(item.nominal_reward_pp.normalize()),
                        unit="PERCENTAGE_POINT",
                        source_ref=item.rule_id,
                        label=f"+{item.nominal_reward_pp}%p",
                    )
                )
            status_concept = {
                EvaluationStatus.SATISFIED: "STATUS_SATISFIED",
                EvaluationStatus.ACHIEVABLE: "STATUS_ACHIEVABLE",
                EvaluationStatus.UNSATISFIABLE: "STATUS_UNSATISFIABLE",
                EvaluationStatus.UNKNOWN: "STATUS_UNKNOWN",
            }[item.status]
            claims.append(
                CanonicalClaim(
                    claim_id=f"CLAIM-{canonical_hash({'rule': item.rule_id, 'status': status_concept})[:16]}",
                    claim_type=ClaimType.STATUS,
                    value=status_concept,
                    source_ref=item.rule_id,
                    label=item.status.value,
                )
            )
        reason_entries = [
            *detail.recommendation_reason.positive_entries,
            *detail.recommendation_reason.limitation_entries,
            *detail.recommendation_reason.action_entries,
            *detail.recommendation_reason.unknown_entries,
        ]
        for entry in reason_entries:
            claims.append(
                CanonicalClaim(
                    claim_id=f"CLAIM-{canonical_hash({'reason': entry.code, 'product': detail.product_id})[:16]}",
                    claim_type=ClaimType.RECOMMENDATION_REASON,
                    value=entry.code,
                    source_ref="recommendation_reason",
                    label=entry.text,
                )
            )
        allowed_text = " ".join(
            [
                detail.product_name,
                *[item.rule_label for item in flat_breakdown],
                *[item.action_summary or "" for item in flat_breakdown],
                *detail.recommendation_reason.positives,
                *detail.recommendation_reason.limitations,
                *detail.recommendation_reason.actions,
                *detail.recommendation_reason.unknowns,
                fallback,
            ]
        )
        claims.extend(extract_semantic_claims(allowed_text, source_ref="detail_semantics"))
        unique = {
            (item.claim_type.value, item.value, item.unit): item for item in claims
        }
        return CanonicalQuestionPayload(
            fact_type=detail.product_id,
            rule_id="RECOMMENDATION_DETAIL",
            question_type="RESULT_EXPLANATION",
            claims=list(unique.values()),
            required_terms=[],
            allowed_condition_terms=sorted(extract_condition_terms(allowed_text)),
            deterministic_fallback=fallback,
        )


class RecommendationService:
    def __init__(self, explainer: GroundedResultExplainer | None = None) -> None:
        self.explainer = explainer or GroundedResultExplainer()

    def build_result(
        self,
        search_session_id: str,
        ranking: RankingResult,
        *,
        audit: AuditSession | None = None,
    ) -> ProductRecommendationResult:
        recommendation_id = f"REC-{canonical_hash({'session': search_session_id, 'ranking': ranking.ranking_run_id})[:16]}"
        result = ProductRecommendationResult(
            recommendation_id=recommendation_id,
            search_session_id=search_session_id,
            ranking_objective=ranking.ranking_objective,
            generated_at=ranking.generated_at,
            top_products=ranking.items,
            ranked_products=ranking.display_items or ranking.items,
            unresolved_global_assumptions=[],
            ranking_explanation_refs=[ranking.ranking_run_id],
        )
        if audit is not None:
            audit.bind_search_context(recommendation_id=recommendation_id)
            audit.emit(
                "RECOMMENDATION_SERVICE",
                AuditEventType.RECOMMENDATION_CREATED,
                output_data=result,
                payload={
                    "top_product_ids": [item.product_id for item in result.top_products],
                    "top_k": len(result.top_products),
                },
            )
        return result

    def build_detail(
        self,
        *,
        search_session_id: str,
        recommendation_id: str,
        product: ProductDefinition,
        candidate: CandidateEvaluation,
        rank: int,
        intent: ProductSearchIntent | None = None,
        ranking: RankingResult | None = None,
        condition_states: Mapping[str, UserConditionState] | None = None,
        audit: AuditSession | None = None,
        include_explanation: bool = True,
    ) -> ProductRecommendationDetail:
        condition_states = condition_states or {}
        result_by_id = {
            result.rule_id: result
            for result in candidate.product_evaluation.preferential_rule_results
        }
        presentation_by_rate_id: dict[str, dict] = {}
        if product.normalized is not None:
            return_policy = product.normalized.return_policy
            disclosures_by_text = {
                str(row.get("condition_text") or ""): row
                for row in return_policy.get("preferential_condition_disclosures", [])
                if row.get("presentation") and row.get("condition_text")
            }
            for entry in return_policy.get("rate_entries", []):
                disclosure = disclosures_by_text.get(str(entry.get("condition_text") or ""))
                if disclosure is not None and entry.get("rate_id"):
                    presentation_by_rate_id[str(entry["rate_id"])] = disclosure
        breakdown: list[RateBreakdownItem] = []
        for preferential in product.preferential_rules:
            rule = preferential.rule
            result = result_by_id[rule.rule_id]
            variable_ids = self._condition_variable_ids(rule)
            condition_state = next(
                (
                    condition_states[variable_id]
                    for variable_id in variable_ids
                    if variable_id in condition_states
                ),
                None,
            )
            disclosure = next(
                (
                    row
                    for rate_id, row in presentation_by_rate_id.items()
                    if rule.rule_id.endswith(f":{rate_id}")
                ),
                None,
            )
            breakdown.append(
                RateBreakdownItem(
                    rule_id=rule.rule_id,
                    rule_label=rule.name,
                    nominal_reward_pp=preferential.reward.value,
                    status=result.status,
                    verification_level=result.verification_level,
                    evidence_basis=self._evidence_basis(result),
                    action_summary=self._action_summary(result),
                    reason_code=result.reason_code,
                    source_reference=rule.source,
                    disclosure_id=(disclosure or {}).get("disclosure_id"),
                    presentation=(disclosure or {}).get("presentation"),
                    disclosure_verification_status=(disclosure or {}).get("verification_status"),
                    user_condition_status=(
                        condition_state.status if condition_state is not None else None
                    ),
                    display_status=self._condition_display_status(
                        result,
                        condition_state,
                    ),
                    children=self._meaningful_breakdown_children(rule, result),
                )
            )

        contributions = [
            RateContribution(
                rule_id=item.rule_id,
                nominal_reward_pp=item.reward_pp,
                status=item.status,
                evidence_bucket=item.evidence_bucket,
                included_in_confirmed=item.included_in_confirmed,
                included_in_realizable=item.included_in_realizable,
                included_in_conditional_upper=(
                    item.included_in_user_specific_conditional_upper
                ),
            )
            for item in candidate.product_evaluation.rates.applied_rewards
        ]
        pre_cap = sum(
            item.reward_pp
            for item in candidate.product_evaluation.rates.applied_rewards
            if item.included_in_realizable
        )
        evaluated_base_rate = candidate.product_evaluation.rates.evidence_breakdown.base_rate
        post_cap = (
            candidate.realizable_rate - evaluated_base_rate
            if candidate.realizable_rate is not None and evaluated_base_rate is not None
            else None
        )
        cap_reduction = (
            max(Decimal("0"), pre_cap - post_cap) if post_cap is not None else None
        )
        cap_adjustment = RateCapAdjustment(
            cap_pp=candidate.product_evaluation.rates.preferential_cap,
            pre_cap_total_pp=pre_cap,
            cap_reduction_pp=cap_reduction,
            post_cap_total_pp=post_cap,
        )
        reason = self._reason(
            product,
            candidate,
            rank=rank,
            intent=intent,
            ranking=ranking,
        )
        confirmed_or_achievable_products_ahead = 0
        if ranking is not None:
            for ranked_item in ranking.display_items:
                if ranked_item.product_id == product.product_id:
                    break
                if ranked_item.eligibility_badge in {
                    EligibilityBadge.ELIGIBLE,
                    EligibilityBadge.PLAN_REQUIRED,
                }:
                    confirmed_or_achievable_products_ahead += 1
        projection = candidate.contribution_projection
        normalized = product.normalized
        performance_linked_cma = (
            product.product_type == "CMA"
            and normalized is not None
            and normalized.return_policy.get("return_kind") == "PERFORMANCE_LINKED"
        )
        rate_entries = (
            []
            if performance_linked_cma
            else normalized.return_policy.get("rate_entries", [])
            if normalized is not None
            else []
        )
        rate_as_of_values = sorted(
            {
                value
                for item in rate_entries
                if (value := _rate_snapshot_as_of(item)) is not None
            }
        )
        published_label, published_summary, published_as_of = (
            _published_rate_reference(product)
        )
        preferential_conditions: list[dict] = []
        if normalized is not None:
            preferential_conditions.extend(
                normalized.return_policy.get("preferential_condition_disclosures", [])
            )
            preferential_conditions.extend(
                _canonical_preferential_conditions(normalized.return_policy)
            )

            def condition_source(identifier: str | None) -> dict | None:
                if not identifier:
                    return None
                for preferential in product.preferential_rules:
                    if identifier not in preferential.rule.rule_id:
                        continue
                    source = preferential.rule.source
                    if source is not None:
                        return source.model_dump(mode="json")
                return None

            preferential_conditions.extend(
                {
                    "kind": "STANDARD",
                    **item,
                    "official_source": condition_source(item.get("condition_id")),
                }
                for item in normalized.standard_conditions
                if item.get("purpose") == "PREFERENTIAL_RETURN"
            )
            custom_by_key = {
                (item.get("custom_code"), item.get("version")): item
                for item in normalized.custom_definitions
            }
            for binding in normalized.custom_bindings:
                if binding.get("purpose") != "PREFERENTIAL_RETURN":
                    continue
                definition = custom_by_key.get(
                    (binding.get("custom_code"), binding.get("custom_version")), {}
                )
                preferential_conditions.append(
                    {
                        "kind": "CUSTOM",
                        **binding,
                        "title": definition.get("title"),
                        "evaluation_mode": definition.get("evaluation_mode"),
                        "content_blocks": definition.get("content_blocks", []),
                        "official_source": condition_source(
                            binding.get("custom_code")
                        ),
                    }
                )
        detail = ProductRecommendationDetail(
            semantic_interpretations=candidate.semantic_interpretations,
            recommendation_id=recommendation_id,
            search_session_id=search_session_id,
            product_id=product.product_id,
            product_name=product.name,
            institution_name=(
                normalized.institution_name
                if normalized is not None
                else _institution_name(product.institution_id)
            ),
            product_type=product.product_type,
            ranking_objective=(
                ranking.ranking_objective
                if ranking is not None
                else RankingObjective.MAX_REALIZABLE_RATE
            ),
            eligibility_status=candidate.eligibility_status,
            material_unknown_count=candidate.material_unknown_count,
            confirmed_or_achievable_products_ahead=(
                confirmed_or_achievable_products_ahead
            ),
            product_subtype=(normalized.product_subtype if normalized is not None else None),
            sale_status=(normalized.sale_status if normalized is not None else None),
            return_kind=(
                normalized.return_policy.get("return_kind")
                if normalized is not None
                else "INTEREST"
            ),
            published_rate_label=published_label,
            published_rate_summary=published_summary,
            published_rate_as_of=published_as_of,
            calculation_method=(
                normalized.return_policy.get("calculation_method")
                if normalized is not None
                else None
            ),
            rate_calculation_status=(
                candidate.product_evaluation.rates.calculation_status
            ),
            rate_calculation_reason=(
                candidate.product_evaluation.rates.calculation_reason
            ),
            rate_as_of=(
                None
                if performance_linked_cma
                else rate_as_of_values[-1] if rate_as_of_values else None
            ),
            target_customer_summary=(
                product.metadata.target_customer_summary
                if product.metadata is not None
                else None
            ),
            protection_status=(
                normalized.protection_policy.get("coverage_status")
                if normalized is not None
                else None
            ),
            search_facts=project_product_search_facts(product),
            rate_evaluation=candidate.rate_evaluation,
            term_policy=(normalized.term_policy if normalized is not None else {}),
            cash_flow_policy=(normalized.cash_flow_policy if normalized is not None else {}),
            fee_policy=(normalized.fee_policy if normalized is not None else {}),
            tax_policy=(normalized.tax_policy if normalized is not None else {}),
            liquidity_policy=(normalized.liquidity_policy if normalized is not None else {}),
            rate_entries=rate_entries,
            preferential_conditions=preferential_conditions,
            data_gaps=(normalized.data_gaps if normalized is not None else []),
            official_sources=(normalized.official_sources if normalized is not None else []),
            rank=rank,
            realizable_rate=candidate.realizable_rate,
            confirmed_rate=candidate.confirmed_rate,
            advertised_max_rate=(
                None
                if performance_linked_cma
                else candidate.product_evaluation.rates.advertised_max_rate
            ),
            additional_possible_rate_pp=(
                max(
                    Decimal("0"),
                    candidate.user_specific_conditional_upper_rate
                    - candidate.realizable_rate,
                )
                if candidate.user_specific_conditional_upper_rate is not None
                and candidate.realizable_rate is not None
                else None
            ),
            term_summary=projection.term_summary,
            contribution_summary=projection.contribution_summary,
            maximum_deposit_summary=projection.maximum_deposit_summary,
            planned_contribution_summary=projection.planned_contribution_summary,
            estimated_total_principal=candidate.estimated_total_principal,
            estimated_pre_tax_interest=candidate.realizable_pre_tax_interest,
            estimated_after_tax_interest=candidate.realizable_after_tax_interest,
            ranking_comparability=candidate.ranking_comparability,
            missing_ranking_inputs=candidate.missing_ranking_inputs,
            rate_breakdown=breakdown,
            rate_contributions=contributions,
            rate_cap_adjustment=cap_adjustment,
            recommendation_reason=reason,
        )
        if include_explanation:
            detail = detail.model_copy(
                update={"explanation": self.explainer.explain(detail)}, deep=True
            )
        if audit is not None:
            audit.emit(
                "RECOMMENDATION_SERVICE",
                AuditEventType.PRODUCT_DETAIL_OPENED,
                entity_refs={"product_id": product.product_id},
                output_data=detail,
                payload={"rank": rank},
            )
            if include_explanation:
                audit.emit(
                    "RESULT_EXPLAINER",
                    AuditEventType.EXPLANATION_GENERATED,
                    entity_refs={"product_id": product.product_id},
                    output_data={"explanation": detail.explanation},
                    payload={"grounded": True},
                )
        return detail

    @staticmethod
    def _condition_variable_ids(rule) -> list[str]:
        """Collect canonical question families without depending on rule type."""

        identifiers: list[str] = []
        missing = getattr(rule, "missing_fact", None)
        if missing is not None:
            identifier = getattr(missing, "action_id", None) or getattr(
                rule, "fact_type", None
            )
            if identifier:
                identifiers.append(str(identifier))
        for child in getattr(rule, "children", None) or []:
            identifiers.extend(RecommendationService._condition_variable_ids(child))
        child = getattr(rule, "child", None)
        if child is not None:
            identifiers.extend(RecommendationService._condition_variable_ids(child))
        return list(dict.fromkeys(identifiers))

    @staticmethod
    def _condition_display_status(
        result,
        state: UserConditionState | None,
    ) -> str:
        if state is not None:
            if state.status in {
                UserConditionStatus.WILLING_UNSPECIFIED,
                UserConditionStatus.ACKNOWLEDGED_UNKNOWN,
                UserConditionStatus.NOT_ASKED,
            }:
                return "확인 전"
            if state.status == UserConditionStatus.DECLINED:
                return "적용 안 함"
            if state.status == UserConditionStatus.VERIFIED:
                return "확인 완료"
            if state.status == UserConditionStatus.DECLARED_FEASIBLE:
                return (
                    "적용 안 함"
                    if result.status == EvaluationStatus.UNSATISFIABLE
                    else "확인 전"
                    if result.status == EvaluationStatus.UNKNOWN
                    else "적용 예상"
                )
        if result.status == EvaluationStatus.UNKNOWN:
            return "확인 전"
        if result.status == EvaluationStatus.UNSATISFIABLE:
            return "적용 안 함"
        if result.status == EvaluationStatus.ACHIEVABLE:
            return "적용 예상"
        if result.verification_level in {
            VerificationLevel.INSTITUTION_VERIFIED,
            VerificationLevel.MYDATA_VERIFIED,
            VerificationLevel.VERIFIED,
        }:
            return "확인 완료"
        return "적용 예상"

    @staticmethod
    def _evidence_basis(result) -> str:
        if result.status == EvaluationStatus.ACHIEVABLE:
            return "계획대로 달성 시 적용 가능"
        if result.status == EvaluationStatus.UNSATISFIABLE:
            return "현재 정보로 받을 수 없음"
        if result.status == EvaluationStatus.UNKNOWN:
            return "아직 확인 전"
        if VerificationLevel.SELF_REPORTED in result.evidence_levels:
            return "사용자 응답 기준 충족"
        return "금융데이터로 확인"

    @staticmethod
    def _action_summary(result) -> str | None:
        action = result.evidence.get("action")
        if isinstance(action, dict):
            return action.get("description") or action.get("action_id")
        paths = result.evidence.get("action_paths")
        if isinstance(paths, list) and paths:
            labels = [item.get("label") for item in paths if isinstance(item, dict) and item.get("label")]
            if labels:
                return " / ".join(labels)
        return None

    @classmethod
    def _meaningful_breakdown_children(cls, rule, result) -> list[RateBreakdownItem]:
        """Expose user-meaningful logical branches without dumping the full AST."""

        if getattr(rule, "type", None) != "OR" or not result.children:
            return []
        children: list[RateBreakdownItem] = []
        ast_children = getattr(rule, "children", None) or []
        for child_rule, child_result in zip(ast_children, result.children, strict=False):
            children.append(
                RateBreakdownItem(
                    rule_id=child_result.rule_id,
                    rule_label=child_result.rule_name,
                    nominal_reward_pp=None,
                    status=child_result.status,
                    verification_level=child_result.verification_level,
                    evidence_basis=cls._evidence_basis(child_result),
                    action_summary=cls._action_summary(child_result),
                    reason_code=child_result.reason_code,
                    display_status=cls._condition_display_status(
                        child_result,
                        None,
                    ),
                    source_reference=getattr(child_rule, "source", None),
                    children=cls._meaningful_breakdown_children(child_rule, child_result),
                )
            )
        return children

    @staticmethod
    def _reason(
        product: ProductDefinition,
        candidate: CandidateEvaluation,
        *,
        rank: int,
        intent: ProductSearchIntent | None,
        ranking: RankingResult | None,
    ) -> PersonalizedRecommendationReason:
        projection = candidate.contribution_projection
        positive_entries: list[RecommendationReasonEntry] = []

        if (
            intent is not None
            and intent.contribution_plan is not None
            and intent.contribution_plan.selected_term_value is not None
            and intent.contribution_plan.selected_term_unit is not None
        ):
            from eligibility.schema.product import ContractTerm

            requested = ContractTerm(
                value=intent.contribution_plan.selected_term_value,
                unit=intent.contribution_plan.selected_term_unit,
            )
            actual = resolve_term(product, intent.contribution_plan)
            if terms_equivalent(actual, requested):
                positive_entries.append(
                    RecommendationReasonEntry(
                        code="TERM_MATCH",
                        text=f"희망 기간 {projection.term_summary}과 일치",
                        evidence={
                            "requested_value": requested.value,
                            "requested_unit": requested.unit.value,
                            "resolved_value": actual.value,
                            "resolved_unit": actual.unit.value,
                        },
                    )
                )

        if (
            intent is not None
            and intent.contribution_plan is not None
            and intent.contribution_plan.desired_periodic_amount is not None
            and projection.planned_periodic_amount == intent.contribution_plan.desired_periodic_amount
            and candidate.ranking_comparability == RankingComparability.COMPARABLE
        ):
            positive_entries.append(
                RecommendationReasonEntry(
                    code="CONTRIBUTION_PLAN_FULLY_SUPPORTED",
                    text="사용자의 실제 납입계획을 상품 스케줄에 그대로 반영 가능",
                    evidence={
                        "desired_periodic_amount": str(
                            intent.contribution_plan.desired_periodic_amount
                        ),
                        "planned_periodic_amount": str(projection.planned_periodic_amount),
                    },
                )
            )

        if (
            ranking is not None
            and rank == 1
            and ranking.ranking_objective
            in {
                RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
                RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST,
                RankingObjective.BALANCED,
            }
            and candidate.ranking_comparability == RankingComparability.COMPARABLE
            and candidate.realizable_pre_tax_interest is not None
            and ranking.items
            and ranking.items[0].product_id == product.product_id
        ):
            positive_entries.append(
                RecommendationReasonEntry(
                    code="HIGHEST_AFTER_TAX_INTEREST",
                    text=f"현재 비교 가능한 후보 중 예상 세전이자가 가장 높음 ({int(candidate.realizable_pre_tax_interest):,}원)",
                    evidence={
                        "estimated_pre_tax_interest": str(
                            candidate.realizable_pre_tax_interest
                        ),
                        "estimated_after_tax_interest": str(
                            candidate.realizable_after_tax_interest
                        ),
                        "ranking_run_id": ranking.ranking_run_id,
                    },
                )
            )

        if candidate.action_burden_score == 0:
            positive_entries.append(
                RecommendationReasonEntry(
                    code="LOW_ACTION_BURDEN",
                    text="현재 평가에서 추가 행동부담 점수가 0",
                    evidence={"action_burden_score": 0},
                )
            )

        explicit_new_card = CandidateRetriever._feature_present(
            product, "NEW_CARD_REQUIRED"
        )
        if explicit_new_card is False:
            positive_entries.append(
                RecommendationReasonEntry(
                    code="NO_NEW_CARD_REQUIRED",
                    text="typed metadata상 신규카드 발급이 가입 필수조건이 아님",
                    evidence={"NEW_CARD_REQUIRED": False},
                )
            )

        positives = [entry.text for entry in positive_entries]

        limitation_entries: list[RecommendationReasonEntry] = []
        action_entries: list[RecommendationReasonEntry] = []
        unknown_entries: list[RecommendationReasonEntry] = []
        for result in candidate.product_evaluation.preferential_rule_results:
            if result.status == EvaluationStatus.UNSATISFIABLE:
                limitation_entries.append(
                    RecommendationReasonEntry(
                        code="RULE_UNSATISFIABLE",
                        text=f"{result.rule_name}: 현재 받을 수 없음",
                        evidence={"rule_id": result.rule_id, "reason_code": result.reason_code},
                    )
                )
            elif result.status == EvaluationStatus.ACHIEVABLE:
                action = RecommendationService._action_summary(result)
                action_entries.append(
                    RecommendationReasonEntry(
                        code="ACTION_REQUIRED",
                        text=action or f"{result.rule_name} 조건 관리 필요",
                        evidence={"rule_id": result.rule_id, "reason_code": result.reason_code},
                    )
                )
            elif result.status == EvaluationStatus.UNKNOWN:
                unknown_entries.append(
                    RecommendationReasonEntry(
                        code="RULE_UNKNOWN",
                        text=f"{result.rule_name}: 아직 확인 전",
                        evidence={"rule_id": result.rule_id, "reason_code": result.reason_code},
                    )
                )

        if candidate.ranking_comparability != RankingComparability.COMPARABLE:
            unknown_entries.append(
                RecommendationReasonEntry(
                    code="MISSING_CONTRIBUTION_INPUT",
                    text="예상 세전이자 비교에 필요한 납입 입력이 아직 완료되지 않음",
                    evidence={
                        "ranking_comparability": candidate.ranking_comparability.value,
                        "missing_input_ids": [
                            item.input_id for item in candidate.missing_ranking_inputs
                        ],
                    },
                )
            )

        if candidate.eligibility_status == EvaluationStatus.UNKNOWN:
            unknown_entries.append(
                RecommendationReasonEntry(
                    code="ELIGIBILITY_UNCONFIRMED",
                    text=(
                        "가입대상 관련 확인이 아직 끝나지 않음"
                        + (
                            f" ({candidate.material_unknown_count}건)"
                            if candidate.material_unknown_count
                            else ""
                        )
                    ),
                    evidence={
                        "eligibility_status": candidate.eligibility_status.value,
                        "material_unknown_count": candidate.material_unknown_count,
                    },
                )
            )

        return PersonalizedRecommendationReason(
            positives=positives,
            limitations=[entry.text for entry in limitation_entries],
            actions=[entry.text for entry in action_entries],
            unknowns=[entry.text for entry in unknown_entries],
            positive_entries=positive_entries,
            limitation_entries=limitation_entries,
            action_entries=action_entries,
            unknown_entries=unknown_entries,
        )
