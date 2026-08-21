from __future__ import annotations

import json
from decimal import Decimal

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
    EvaluationStatus,
    RankingComparability,
    RankingObjective,
    VerificationLevel,
)
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
from eligibility.search.ranking import _institution_name
from eligibility.search.retrieval import CandidateRetriever


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
        if detail.estimated_pre_tax_interest is not None:
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
                "가입 시 최종 확인 항목: "
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
        audit: AuditSession | None = None,
        include_explanation: bool = True,
    ) -> ProductRecommendationDetail:
        result_by_id = {
            result.rule_id: result
            for result in candidate.product_evaluation.preferential_rule_results
        }
        breakdown: list[RateBreakdownItem] = []
        for preferential in product.preferential_rules:
            rule = preferential.rule
            result = result_by_id[rule.rule_id]
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
        post_cap = (
            candidate.realizable_rate - product.base_rate
        )
        cap_adjustment = RateCapAdjustment(
            cap_pp=product.preferential_rate_cap,
            pre_cap_total_pp=pre_cap,
            cap_reduction_pp=max(Decimal("0"), pre_cap - post_cap),
            post_cap_total_pp=post_cap,
        )
        reason = self._reason(
            product,
            candidate,
            rank=rank,
            intent=intent,
            ranking=ranking,
        )
        projection = candidate.contribution_projection
        detail = ProductRecommendationDetail(
            recommendation_id=recommendation_id,
            search_session_id=search_session_id,
            product_id=product.product_id,
            product_name=product.name,
            institution_name=_institution_name(product.institution_id),
            product_type=product.product_type,
            rank=rank,
            realizable_rate=candidate.realizable_rate,
            confirmed_rate=candidate.confirmed_rate,
            advertised_max_rate=product.advertised_max_rate,
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
    def _evidence_basis(result) -> str:
        if result.status == EvaluationStatus.ACHIEVABLE:
            return "계획대로 달성 시 적용 가능"
        if result.status == EvaluationStatus.UNSATISFIABLE:
            return "현재 정보로 받을 수 없음"
        if result.status == EvaluationStatus.UNKNOWN:
            return "가입 시 최종 확인 필요"
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
                        text=f"{result.rule_name}: 가입 시 최종 확인 필요",
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
