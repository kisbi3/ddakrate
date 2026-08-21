from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eligibility.adapters import MCPToolAdapter, RestApplicationAdapter
from eligibility.application_service import ApplicationService
from eligibility.audit import AuditEventType
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_future_intent,
    kakao_pre_subscription_user,
)
from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
from eligibility.schema.application_input import Capability, NumericPreference
from eligibility.schema.enums import (
    CapabilityState,
    ContributionFrequency,
    FactRecordStatus,
    FactSemanticType,
    FactSourceType,
    NumericPreferenceDirection,
    PreferenceStrictness,
    RankingObjective,
    TermUnit,
)
from eligibility.schema.search import ContributionPlan, IntentPatch, ProductSearchIntent
from eligibility.search.intent import IntentParser

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, USER_ID, base_store, make_intent, make_product

OUT = ROOT / "examples" / "v0.4.2"


def _clone_kakao(product_id: str, name: str):
    original = kakao_26_week_product()
    metadata = original.metadata.model_copy(
        update={"product_id": product_id, "product_name": name}, deep=True
    )
    return original.model_copy(
        update={"product_id": product_id, "name": name, "metadata": metadata},
        deep=True,
    )


def _global_incremental_intent(*, objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST, top_k=2):
    return ProductSearchIntent(
        search_intent_id=f"INTENT-V042-{objective.value}",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=objective,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            maximum_affordable_periodic_amount=Decimal("1000000"),
            frequency=ContributionFrequency.MONTHLY,
            selected_term_value=26,
            selected_term_unit=TermUnit.WEEK,
            currency="KRW",
        ),
        requested_top_k=top_k,
        source_utterances=["월 30만원 정도 생각하고 있어"],
    )


def _salary_product():
    return make_product(
        "SALARY-V042",
        base_rate="2.0",
        reward_pp="1.0",
        bonus_fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {}

    # A. Two incremental products keep independent product-scoped choices while
    # the global monthly preference remains unchanged.
    a = _clone_kakao("KAKAO-LIKE-A", "점증식 A")
    b = _clone_kakao("KAKAO-LIKE-B", "점증식 B")
    service = ApplicationService(
        [a, b], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_global_incremental_intent(top_k=2),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    answers: dict[str, Decimal] = {}
    for value in (Decimal("10000"), Decimal("5000")):
        question = service.get_next_question(session.search_session_id)
        assert question is not None and question.question_kind == "RANKING_INPUT"
        assert question.ranking_input is not None
        answers[question.ranking_input.product_id] = value
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer=str(value),
        )

    intent = service.get_search_intent(session.search_session_id)
    assert intent.contribution_plan is not None
    assert intent.contribution_plan.desired_periodic_amount == Decimal("300000")
    assert intent.contribution_plan.frequency == ContributionFrequency.MONTHLY
    assert intent.contribution_plan.preferred_start_amount is None

    choices = {
        (item.product_id, item.field): Decimal(str(item.value))
        for item in service.get_product_contribution_choices(session.search_session_id)
    }
    evaluations = service.evaluate_candidates(session.search_session_id)
    for product_id, expected in answers.items():
        assert choices[(product_id, "preferred_start_amount")] == expected
        assert evaluations[product_id].contribution_projection.initial_amount == expected
        assert evaluations[product_id].realizable_after_tax_interest is not None
    high = next(pid for pid, value in answers.items() if value == Decimal("10000"))
    low = next(pid for pid, value in answers.items() if value == Decimal("5000"))
    assert evaluations[high].estimated_total_principal == evaluations[low].estimated_total_principal * 2
    ranking = service.get_top_recommendations(session.search_session_id)
    assert {item.product_id for item in ranking.top_products} == {a.product_id, b.product_id}
    results["A_product_specific_contribution_isolation"] = {
        "global_desired_periodic_amount_krw": str(intent.contribution_plan.desired_periodic_amount),
        "global_frequency": intent.contribution_plan.frequency.value,
        "product_choices": {pid: str(value) for pid, value in sorted(answers.items())},
        "principal_krw": {
            pid: str(evaluations[pid].estimated_total_principal) for pid in sorted(answers)
        },
        "after_tax_interest_krw": {
            pid: str(evaluations[pid].realizable_after_tax_interest) for pid in sorted(answers)
        },
        "ranked_products": [item.product_id for item in ranking.top_products],
    }

    # B. Capability revision true -> false supersedes the prior user declaration,
    # leaves one active declaration, and triggers deterministic re-evaluation.
    salary_product = _salary_product()
    salary_service = ApplicationService(
        [salary_product], user_fact_stores={USER_ID: base_store()}
    )
    salary_session = salary_service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(
            top_k=1,
            capabilities=[
                Capability(
                    capability_id="CHANGE_SALARY_ACCOUNT",
                    state=CapabilityState.CAN,
                )
            ],
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    before = salary_service.evaluate_candidates(salary_session.search_session_id)[salary_product.product_id]
    salary_service.update_search_intent(
        salary_session.search_session_id,
        patch=IntentPatch(
            upsert_capabilities=[
                Capability(
                    capability_id="CHANGE_SALARY_ACCOUNT",
                    state=CapabilityState.CANNOT,
                )
            ]
        ),
    )
    after = salary_service.evaluate_candidates(salary_session.search_session_id)[salary_product.product_id]
    runtime = salary_service._runtime(salary_session.search_session_id)
    active = [
        fact
        for fact in runtime.fact_store.facts
        if fact.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
        and fact.source_type == FactSourceType.USER_DECLARED
        and fact.semantic_type in {FactSemanticType.FUTURE_INTENT, FactSemanticType.SELF_REPORTED_FACT}
        and fact.record_status == FactRecordStatus.ACTIVE
    ]
    assert len(active) == 1 and active[0].value is False
    assert before.realizable_rate > after.realizable_rate
    assert any(
        event.event_type == AuditEventType.USER_DECLARED_FACT_SUPERSEDED_BY_INTENT_UPDATE
        for event in salary_service.get_evaluation_trace(salary_session.search_session_id)
    )
    results["B_user_fact_supersession"] = {
        "before_realizable_rate": str(before.realizable_rate),
        "after_realizable_rate": str(after.realizable_rate),
        "active_user_declared_values": [fact.value for fact in active],
        "audit_supersession_event": True,
    }

    # C. NEW_CARD_ISSUANCE=CANNOT must not remove Shinhan merely because it has a
    # preferential card-benefit feature.
    shinhan = shinhan_youth_first_product()
    card_service = ApplicationService(
        [shinhan], user_fact_stores={"U001": base_store().model_copy(update={"user_id": "U001"}, deep=True)}
    )
    retained = card_service.candidate_retriever.retrieve(
        [shinhan],
        make_intent(
            user_id="U001",
            capabilities=[
                Capability(
                    capability_id="NEW_CARD_ISSUANCE",
                    state=CapabilityState.CANNOT,
                )
            ],
        ),
        as_of=AS_OF,
    )[0]
    assert retained and retained[0].product_id == shinhan.product_id
    feature_map = {item.feature_id: item.required_for_subscription for item in shinhan.metadata.features}
    assert feature_map["CARD_BENEFIT"] is False
    assert "NEW_CARD_REQUIRED" not in feature_map
    results["C_preferential_card_feature_not_mandatory"] = {
        "shinhan_retained": True,
        "card_benefit_present": True,
        "card_benefit_required_for_subscription": False,
        "new_card_required_feature_present": False,
    }

    # D. MAX_REALIZABLE_RATE does not request start amount solely to compute interest.
    rate_store = kakao_pre_subscription_user()
    rate_store = rate_store.with_fact(kakao_future_intent(True, target=7))
    rate_store = rate_store.with_fact(kakao_future_intent(True, target=26))
    rate_service = ApplicationService(
        [kakao_26_week_product()], user_fact_stores={KAKAO_USER_ID: rate_store}
    )
    rate_session = rate_service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_global_incremental_intent(
            objective=RankingObjective.MAX_REALIZABLE_RATE,
            top_k=1,
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    rate_question = rate_service.get_next_question(rate_session.search_session_id)
    assert rate_question is None or rate_question.question_kind != "RANKING_INPUT"
    rate_eval = rate_service.evaluate_candidates(rate_session.search_session_id)[kakao_26_week_product().product_id]
    assert rate_eval.realizable_rate is not None
    results["D_objective_aware_question_suppression"] = {
        "objective": RankingObjective.MAX_REALIZABLE_RATE.value,
        "ranking_input_question_asked": False,
        "realizable_rate": str(rate_eval.realizable_rate),
    }

    # E. Lower and upper bounds on one numeric field survive independent patching.
    range_intent = make_intent(
        numeric_preferences=[
            NumericPreference(
                field="MONTHLY_CONTRIBUTION",
                value=Decimal("200000"),
                direction=NumericPreferenceDirection.AT_LEAST,
                strictness=PreferenceStrictness.HARD,
                currency="KRW",
            ),
            NumericPreference(
                field="MONTHLY_CONTRIBUTION",
                value=Decimal("300000"),
                direction=NumericPreferenceDirection.AT_MOST,
                strictness=PreferenceStrictness.HARD,
                currency="KRW",
            ),
        ]
    )
    range_intent = IntentParser.apply_patch(
        range_intent,
        IntentPatch(
            upsert_numeric_preferences=[
                NumericPreference(
                    field="MONTHLY_CONTRIBUTION",
                    value=Decimal("220000"),
                    direction=NumericPreferenceDirection.AT_LEAST,
                    strictness=PreferenceStrictness.HARD,
                    currency="KRW",
                )
            ]
        ),
    )
    bounds = {(item.field, item.direction.value): item.value for item in range_intent.numeric_preferences}
    assert bounds[("MONTHLY_CONTRIBUTION", "AT_LEAST")] == Decimal("220000")
    assert bounds[("MONTHLY_CONTRIBUTION", "AT_MOST")] == Decimal("300000")
    results["E_numeric_range_patch"] = {
        "lower_bound_krw": str(bounds[("MONTHLY_CONTRIBUTION", "AT_LEAST")]),
        "upper_bound_krw": str(bounds[("MONTHLY_CONTRIBUTION", "AT_MOST")]),
    }

    # F. REST exposes the product-scoped ranking-input answer without mutating the
    # global plan.  This verifies the Web handoff contract, not a separate engine.
    rest_product = kakao_26_week_product()
    rest_service = ApplicationService(
        [rest_product], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    rest_session = rest_service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_global_incremental_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    rest = RestApplicationAdapter(rest_service)
    rest_q = rest.handle("GET", f"/search-sessions/{rest_session.search_session_id}/questions/next")
    assert rest_q.status_code == 200 and rest_q.body["question_kind"] == "RANKING_INPUT"
    rest_answer = rest.handle(
        "POST",
        f"/search-sessions/{rest_session.search_session_id}/answers",
        {"question_id": rest_q.body["question_id"], "answer": "10000"},
    )
    assert rest_answer.status_code == 200
    assert rest_answer.body["product_contribution_choices"][0]["product_id"] == rest_product.product_id
    rest_intent = rest_service.get_search_intent(rest_session.search_session_id)
    assert rest_intent.contribution_plan.desired_periodic_amount == Decimal("300000")
    results["F_rest_product_choice_contract"] = {
        "question_product_id": rest_q.body["ranking_input"]["product_id"],
        "recorded_choice": rest_answer.body["product_contribution_choices"][0],
        "global_plan_unchanged": True,
    }

    # G. MCP uses the same ApplicationService state; no contribution logic lives in
    # the adapter.
    mcp_product = kakao_26_week_product()
    mcp_service = ApplicationService(
        [mcp_product], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    mcp_session = mcp_service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_global_incremental_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    mcp = MCPToolAdapter(mcp_service)
    mcp_q = mcp.get_next_question(search_session_id=mcp_session.search_session_id)
    assert mcp_q["question_kind"] == "RANKING_INPUT"
    mcp_state = mcp.submit_user_fact(
        search_session_id=mcp_session.search_session_id,
        question_id=mcp_q["question_id"],
        answer="5000",
    )
    assert mcp_state["product_contribution_choices"][0]["value"] == "5000"
    results["G_mcp_thin_adapter"] = {
        "choice_product_id": mcp_state["product_contribution_choices"][0]["product_id"],
        "choice_value": mcp_state["product_contribution_choices"][0]["value"],
        "shared_application_service": True,
    }

    destination = OUT / "v0.4.2-adversarial-results.json"
    destination.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"wrote {destination}")


if __name__ == "__main__":
    main()
