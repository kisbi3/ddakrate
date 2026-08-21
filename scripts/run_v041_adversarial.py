from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eligibility.application import GeneratedQuestion, QuestionGenerator
from eligibility.application_service import ApplicationService
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_pre_subscription_user,
)
from eligibility.fixtures.shinhan_youth_first import (
    golden_context,
    shinhan_youth_first_product,
)
from eligibility.fixtures.user_001 import USER_ID as SHINHAN_USER_ID, user_001
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.application_input import Capability, HardConstraint
from eligibility.schema.enums import (
    CapabilityState,
    HardConstraintValue,
    RankingComparability,
    RankingObjective,
)
from eligibility.schema.product import ProductFeature
from eligibility.schema.search import MissingRankingInput, ProductSearchIntent
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.intent import IntentParser
from eligibility.search.questions import RankingAwareQuestionPlanner
from eligibility.search.ranking import RankingService
from eligibility.search.recommendation import GroundedResultExplainer, RecommendationService
from eligibility.search.retrieval import CandidateRetriever

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_intent, make_product

OUT = ROOT / "examples" / "v0.4.1"


def _salary_request():
    return FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
    ).missing_facts[0]


def _with_features(product, features):
    return product.model_copy(
        update={
            "metadata": product.metadata.model_copy(
                update={"features": features}, deep=True
            )
        },
        deep=True,
    )


def _shinhan_detail():
    product = shinhan_youth_first_product()
    intent = ProductSearchIntent(
        search_intent_id="INTENT-ADV-SHINHAN",
        user_id=SHINHAN_USER_ID,
        ranking_objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        contribution_plan=make_intent(top_k=1).contribution_plan,
        requested_top_k=1,
    )
    context = golden_context()
    candidate = MultiProductEvaluator().evaluate(
        [product],
        user_001(),
        intent,
        as_of=context.as_of,
        subscription_date=context.subscription_date,
    )[product.product_id]
    ranking, ranked = RankingService().rank(
        {product.product_id: candidate},
        {product.product_id: product},
        objective=intent.ranking_objective,
        top_k=1,
    )
    service = RecommendationService()
    recommendation = service.build_result("SEARCH-ADV-SHINHAN", ranking)
    return service.build_detail(
        search_session_id="SEARCH-ADV-SHINHAN",
        recommendation_id=recommendation.recommendation_id,
        product=product,
        candidate=ranked[product.product_id],
        rank=1,
        intent=intent,
        ranking=ranking,
        include_explanation=False,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {}

    # A. Interest Ranking: never compare 165 KRW to 5.0 percent.
    products = [make_product(f"UNIT-{i}", base_rate="0.01") for i in range(1, 7)]
    intent = make_intent(top_k=6)
    base = MultiProductEvaluator().evaluate(
        products,
        base_store(),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    evaluations = {}
    for product in products[:5]:
        evaluations[product.product_id] = base[product.product_id].model_copy(
            update={
                "realizable_rate": Decimal("0.01"),
                "realizable_after_tax_interest": Decimal("165"),
                "conditional_upper_after_tax_interest": Decimal("165"),
                "ranking_comparability": RankingComparability.COMPARABLE,
            },
            deep=True,
        )
    missing = MissingRankingInput(
        input_id="MRI-UNIT-6",
        product_id="UNIT-6",
        required_field="preferred_start_amount",
        allowed_options=["1000", "2000"],
        reason="cashflow unresolved",
        question="시작금액을 선택해 주세요.",
    )
    evaluations["UNIT-6"] = base["UNIT-6"].model_copy(
        update={
            "realizable_rate": Decimal("5.0"),
            "realizable_after_tax_interest": None,
            "conditional_upper_after_tax_interest": None,
            "ranking_comparability": RankingComparability.MISSING_CONTRIBUTION_INPUT,
            "missing_ranking_inputs": [missing],
        },
        deep=True,
    )
    ranking_service = RankingService()
    ranking, ranked = ranking_service.rank(
        evaluations,
        {p.product_id: p for p in products},
        objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        top_k=6,
    )
    assert ranking_service.realizable_metric(
        ranked["UNIT-6"], RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST
    ) == Decimal("0")
    assert ranking_service.optimistic_metric(
        ranked["UNIT-6"], RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST
    ) == Decimal("0")
    assert ranking.items[-1].product_id == "UNIT-6"
    assert ranking.items[-1].estimated_after_tax_interest is None
    assert ranking.items[-1].ranking_comparability == RankingComparability.MISSING_CONTRIBUTION_INPUT
    assert ranking.stability.stable is False
    results["A_interest_ranking_units"] = {
        "comparable_candidates": [
            {"product_id": item.product_id, "after_tax_interest_krw": "165"}
            for item in ranking.items[:5]
        ],
        "unresolved_candidate": {
            "product_id": ranking.items[-1].product_id,
            "realizable_rate_percent": "5.0",
            "after_tax_interest_krw": None,
            "ranking_comparability": ranking.items[-1].ranking_comparability.value,
        },
        "percent_substituted_as_krw": False,
        "stable": ranking.stability.stable,
    }

    # B. Kakao: monthly wish alone -> start amount question -> 26-week cashflow -> interest.
    kakao_service = ApplicationService(
        [kakao_26_week_product()],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
    )
    kakao_intent = ProductSearchIntent(
        search_intent_id="INTENT-ADV-KAKAO",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
        contribution_plan=make_intent(
            user_id=KAKAO_USER_ID,
            desired_amount="300000",
            maximum_amount="1000000",
            selected_term_value=26,
            selected_term_unit="WEEK",
            frequency="MONTHLY",
            top_k=1,
        ).contribution_plan,
        requested_top_k=1,
        source_utterances=["월 30만원 정도 생각하고 있어"],
    )
    session = kakao_service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=kakao_intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = kakao_service.get_next_question(session.search_session_id)
    assert question is not None and question.question_kind == "RANKING_INPUT"
    assert question.ranking_input is not None
    assert question.ranking_input.required_field == "preferred_start_amount"
    assert question.ranking_input.allowed_options == ["1000", "2000", "3000", "5000", "10000"]
    before = kakao_service.evaluate_candidates(session.search_session_id)[
        kakao_26_week_product().product_id
    ]
    assert before.realizable_after_tax_interest is None
    kakao_service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer="10000",
    )
    after = kakao_service.evaluate_candidates(session.search_session_id)[
        kakao_26_week_product().product_id
    ]
    kakao_recommendation = kakao_service.get_top_recommendations(session.search_session_id)
    assert after.ranking_comparability == RankingComparability.COMPARABLE
    assert after.contribution_projection.contribution_count == 26
    assert after.contribution_projection.initial_amount == Decimal("10000")
    assert after.contribution_projection.increment_amount == Decimal("10000")
    assert after.contribution_projection.estimated_total_principal == Decimal("3510000")
    assert after.realizable_after_tax_interest is not None and after.realizable_after_tax_interest > 0
    results["B_kakao_contribution_completion"] = {
        "question_kind": question.question_kind,
        "required_field": question.ranking_input.required_field,
        "allowed_options": question.ranking_input.allowed_options,
        "answer": "10000",
        "cashflow_count": after.contribution_projection.contribution_count,
        "initial_amount_krw": str(after.contribution_projection.initial_amount),
        "increment_amount_krw": str(after.contribution_projection.increment_amount),
        "principal_krw": str(after.contribution_projection.estimated_total_principal),
        "after_tax_interest_krw": str(after.realizable_after_tax_interest),
        "rank_1": kakao_recommendation.top_products[0].product_id,
    }

    # C. Follow-up intent is patch/merge; unrelated capabilities survive.
    current_intent = make_intent(
        capabilities=[
            Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN),
            Capability(capability_id="BRANCH_VISIT", state=CapabilityState.CAN),
            Capability(capability_id="NEW_CARD_ISSUANCE", state=CapabilityState.UNKNOWN),
        ]
    )
    patched = IntentParser().update(current_intent, "카드 새로 만드는 건 싫어.")
    cap_map = {item.capability_id: item.state.value for item in patched.capabilities}
    assert cap_map == {
        "CHANGE_SALARY_ACCOUNT": "CAN",
        "BRANCH_VISIT": "CAN",
        "NEW_CARD_ISSUANCE": "CANNOT",
    }
    results["C_intent_patch"] = cap_map

    # D. Typed hard filter: a preferential card rule is not a mandatory new-card requirement.
    card_product = make_product(
        "ADV-EXISTING-CARD",
        reward_pp="0.5",
        rule_name="신한카드 신규카드 카드 결제 우대",
    )
    card_product = _with_features(
        card_product,
        [
            ProductFeature(
                feature_id="NEW_CARD_REQUIRED",
                present=False,
                required_for_subscription=False,
            )
        ],
    )
    exclusion_intent = make_intent(
        hard_constraints=[
            HardConstraint(
                field="NEW_CARD_REQUIRED",
                constraint=HardConstraintValue.EXCLUDE,
            )
        ]
    )
    retained, decisions = CandidateRetriever().retrieve(
        [card_product], exclusion_intent, as_of=AS_OF
    )
    assert [p.product_id for p in retained] == [card_product.product_id]
    assert decisions[0].retained is True
    results["D_typed_hard_filter"] = {
        "retained": True,
        "product_id": card_product.product_id,
        "typed_feature_present": False,
        "keyword_rule_name_ignored_for_hard_filter": True,
    }

    # E. Nonnumeric LLM hallucination is rejected by canonical semantics.
    request = _salary_request()
    hallucinated_question = (
        "신규 입출금통장을 개설하고 월급봉투 인정조건을 6개월 달성해 "
        "+1.0%p 우대를 목표로 관리할까요?"
    )
    generated = GeneratedQuestion(
        question=hallucinated_question,
        fact_type=request.fact_type,
        rule_id=request.requested_by_rule_id,
        action_id=request.action_id,
        reward_id=request.reward_id,
    )
    safe_question = QuestionGenerator(
        LLMGateway(MockLLMAdapter({LLMPurpose.QUESTION_GENERATION: generated}))
    ).generate(request)
    assert safe_question == request.question
    assert "입출금통장" not in safe_question

    detail_for_explanation = _shinhan_detail()
    explainer = GroundedResultExplainer()
    fallback = explainer.deterministic_fallback(detail_for_explanation)
    payload = explainer._payload(detail_for_explanation, fallback)
    bindings = [
        {
            "claim_id": claim.claim_id,
            "claim_type": claim.claim_type.value,
            "value": claim.value,
            "unit": claim.unit,
        }
        for claim in payload.claims
    ]
    hallucinated_explanation = (
        f"{detail_for_explanation.product_name}은 현재 추천 {detail_for_explanation.rank}위입니다. "
        "신규 입출금통장을 개설하면 첫거래 조건을 해결할 수 있습니다. "
        f"예상금리는 {detail_for_explanation.realizable_rate}%입니다."
    )
    rejecting_explainer = GroundedResultExplainer(
        LLMGateway(
            MockLLMAdapter(
                {
                    LLMPurpose.RESULT_EXPLANATION: {
                        "explanation": hallucinated_explanation,
                        "claim_bindings": bindings,
                    }
                }
            )
        )
    )
    safe_explanation = rejecting_explainer.explain(detail_for_explanation)
    assert safe_explanation == fallback
    assert "입출금통장" not in safe_explanation
    results["E_nonnumeric_llm_grounding"] = {
        "question_hallucination_rejected": True,
        "explanation_hallucination_rejected": True,
        "fallback_used": True,
    }

    # F. User-facing detail preserves meaningful OR branch distinction.
    first_or_event = next(
        item for item in detail_for_explanation.rate_breakdown
        if item.rule_id == "RATE_FIRST_OR_EVENT"
    )
    branch_status = {item.rule_id: item.status.value for item in first_or_event.children}
    branch_reasons = {item.rule_id: item.reason_code for item in first_or_event.children}
    assert branch_status["RATE_FIRST_TRANSACTION_BRANCH"] == "UNSATISFIABLE"
    assert branch_status["RATE_EVENT_BRANCH"] == "UNKNOWN"
    results["F_nested_detail"] = {
        "parent_status": first_or_event.status.value,
        "branches": [
            {
                "rule_id": child.rule_id,
                "label": child.rule_label,
                "status": child.status.value,
                "reason_code": child.reason_code,
            }
            for child in first_or_event.children
        ],
        "branch_status": branch_status,
        "branch_reasons": branch_reasons,
    }

    # G. 6+ candidates: stable Top-5 membership is not enough while a material
    # internal user-answerable question remains.
    stability_products = [
        make_product(
            "STAB-1",
            base_rate="6.0",
            reward_pp="1.0",
            bonus_fact_type="MATERIAL_FACT",
        ),
        make_product("STAB-2", base_rate="5.0"),
        make_product("STAB-3", base_rate="4.0"),
        make_product("STAB-4", base_rate="3.0"),
        make_product("STAB-5", base_rate="2.0"),
        make_product("STAB-6", base_rate="0.5"),
    ]
    stability_intent = make_intent(top_k=5)
    stability_evals = MultiProductEvaluator().evaluate(
        stability_products,
        base_store(),
        stability_intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    first_ranking, stability_ranked = ranking_service.rank(
        stability_evals,
        {p.product_id: p for p in stability_products},
        objective=stability_intent.ranking_objective,
        top_k=5,
    )
    planner = RankingAwareQuestionPlanner(ranking_service=ranking_service)
    material_question = planner.select_next(stability_ranked, stability_intent)
    ordered = [
        stability_ranked[product_id] for product_id in first_ranking.ordered_product_ids
    ]
    final_stability = ranking_service.check_stability(
        ordered,
        top_k=5,
        objective=stability_intent.ranking_objective,
        material_internal_question_remaining=material_question is not None,
    )
    assert first_ranking.stability.stable_membership is True
    assert material_question is not None and material_question.request is not None
    assert material_question.request.fact_type == "MATERIAL_FACT"
    assert final_stability.stable_membership is True
    assert final_stability.stable is False
    results["G_top5_stability"] = {
        "ordered_product_ids": first_ranking.ordered_product_ids,
        "top5_membership_stable_before_materiality": first_ranking.stability.stable_membership,
        "material_question_fact_type": material_question.request.fact_type,
        "material_question_product_ids": material_question.affected_product_ids,
        "final_stable_membership": final_stability.stable_membership,
        "final_stable": final_stability.stable,
        "reason_code": final_stability.reason_code,
    }

    output = OUT / "adversarial-results-v0.4.1.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nWROTE {output}")


if __name__ == "__main__":
    main()
