from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eligibility.application_service import ApplicationService
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_pre_subscription_user,
)
from eligibility.schema.enums import ContributionFrequency, ContributionMode, RankingObjective, TermUnit
from eligibility.schema.search import ContributionPlan, ProductSearchIntent
from eligibility.search.contribution import ContributionFeasibilityEvaluator, ContributionPlanner

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, make_product

OUT = ROOT / "examples" / "v0.4.3"


def _intent(maximum: str, *, top_k: int = 1, objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST):
    return ProductSearchIntent(
        search_intent_id=f"INTENT-V043-ADV-{maximum}-{objective.value}",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=objective,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal(maximum),
            maximum_affordable_periodic_amount=Decimal(maximum),
            frequency=ContributionFrequency.MONTHLY,
            selected_term_value=26,
            selected_term_unit=TermUnit.WEEK,
        ),
        requested_top_k=top_k,
        source_utterances=[f"월 최대 {maximum}원"],
    )


def _service(*products):
    return ApplicationService(
        list(products) or [kakao_26_week_product()],
        user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user(intent=True)},
    )


def _session(service: ApplicationService, intent: ProductSearchIntent):
    return service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )


def _business_snapshot(service: ApplicationService, session_id: str):
    runtime = service._runtime(session_id)
    return deepcopy(
        {
            "session": runtime.session,
            "intent": runtime.intent,
            "active_question": runtime.active_question,
            "choices": runtime.product_contribution_choices,
            "evaluations": runtime.evaluations,
            "ranking": runtime.ranking,
            "recommendation": runtime.recommendation,
            "declined": runtime.declined_ranking_input_ids,
        }
    )


def _clone_kakao(product_id: str, name: str):
    product = kakao_26_week_product()
    return product.model_copy(
        update={
            "product_id": product_id,
            "name": name,
            "metadata": product.metadata.model_copy(
                update={"product_id": product_id, "product_name": name}, deep=True
            ),
        },
        deep=True,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {}

    # A. Cross-frequency affordability: actual Kakao dates, calendar-month buckets,
    # and deterministic option pruning under a monthly 300k global limit.
    kakao = kakao_26_week_product()
    service = _service(kakao)
    session = _session(service, _intent("300000"))
    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.question_kind == "RANKING_INPUT"
    assert question.ranking_input is not None
    assert question.ranking_input.allowed_options == ["1000", "2000", "3000"]
    candidate = service.evaluate_candidates(session.search_session_id)[kakao.product_id]
    rows = {
        str(item.option_value): {
            "status": item.status,
            "max_bucket_amount": str(item.max_bucket_amount),
            "violation_bucket": item.violation_bucket,
        }
        for item in candidate.contribution_option_feasibilities
    }
    assert rows["3000"]["status"] == "FEASIBLE"
    assert rows["5000"]["status"] == "INFEASIBLE"
    assert rows["10000"]["status"] == "INFEASIBLE"
    results["A_cross_frequency_affordability"] = {
        "global_monthly_limit": "300000",
        "allowed_options": question.ranking_input.allowed_options,
        "option_results": rows,
    }

    # B. Prove a 5-occurrence calendar month is counted and a four-week
    # approximation would produce the wrong affordability conclusion.
    fixed = make_product(
        "WEEKLY-V043-ADV",
        term_value=26,
        term_unit=TermUnit.WEEK,
        frequency=ContributionFrequency.WEEKLY,
        contribution_mode=ContributionMode.FIXED,
        periodic_max="100000",
    )
    weekly_plan = ContributionPlan(
        desired_periodic_amount=Decimal("80000"),
        maximum_affordable_periodic_amount=Decimal("350000"),
        frequency=ContributionFrequency.WEEKLY,
    )
    global_monthly = ContributionPlan(
        desired_periodic_amount=Decimal("80000"),
        maximum_affordable_periodic_amount=Decimal("350000"),
        frequency=ContributionFrequency.MONTHLY,
    )
    projection = ContributionPlanner().build(
        fixed, weekly_plan, subscription_date=SUBSCRIPTION_DATE
    ).projection
    october = [
        item for item in projection.cashflows
        if item.contribution_date is not None
        and item.contribution_date.year == 2026
        and item.contribution_date.month == 10
    ]
    check = ContributionFeasibilityEvaluator().check_projection(projection, global_monthly)
    assert len(october) == 5
    assert sum(item.amount for item in october) == Decimal("400000")
    assert Decimal("80000") * 4 < Decimal("350000") < Decimal("80000") * 5
    assert check.feasible is False and check.max_bucket_amount == Decimal("400000")
    results["B_five_occurrence_calendar_month"] = {
        "subscription_date": SUBSCRIPTION_DATE.isoformat(),
        "october_event_dates": [item.contribution_date.isoformat() for item in october],
        "october_total": "400000",
        "four_week_approximation": "320000",
        "limit": "350000",
        "actual_feasible": False,
    }

    # C. All options infeasible -> structured adjust/exclude clarification rather
    # than silent candidate removal.
    low_service = _service(kakao)
    low_session = _session(low_service, _intent("80000"))
    low_q = low_service.get_next_question(low_session.search_session_id)
    assert low_q is not None and low_q.question_kind == "CONTRIBUTION_FEASIBILITY"
    clarification = low_q.feasibility_clarification
    assert clarification is not None
    assert clarification.minimum_required_affordability == Decimal("90000")
    assert clarification.allowed_resolutions == [
        "ADJUST_GLOBAL_AFFORDABILITY", "EXCLUDE_PRODUCT"
    ]
    assert kakao.product_id in low_service.get_search_status(low_session.search_session_id).candidate_product_ids
    results["C_all_options_infeasible_clarification"] = {
        "candidate_silently_removed": False,
        "current_limit": str(clarification.current_affordability_amount),
        "minimum_required": str(clarification.minimum_required_affordability),
        "allowed_resolutions": clarification.allowed_resolutions,
    }

    # D. Increase the global affordability, recalculate, answer a feasible option,
    # then calculate interest and rerank.
    low_service.submit_user_answer(
        low_session.search_session_id,
        question_id=low_q.question_id,
        answer={
            "resolution": "ADJUST_GLOBAL_AFFORDABILITY",
            "maximum_affordable_periodic_amount": "300000",
        },
    )
    option_q = low_service.get_next_question(low_session.search_session_id)
    assert option_q is not None and option_q.ranking_input is not None
    assert option_q.ranking_input.allowed_options == ["1000", "2000", "3000"]
    low_service.submit_user_answer(
        low_session.search_session_id,
        question_id=option_q.question_id,
        answer="3000",
    )
    after = low_service.evaluate_candidates(low_session.search_session_id)[kakao.product_id]
    ranking = low_service.get_top_recommendations(low_session.search_session_id)
    assert after.realizable_after_tax_interest is not None
    assert ranking.top_products and ranking.top_products[0].product_id == kakao.product_id
    results["D_affordability_adjustment_to_rerank"] = {
        "new_global_monthly_limit": str(
            low_service.get_search_intent(low_session.search_session_id)
            .contribution_plan.maximum_affordable_periodic_amount
        ),
        "selected_start_amount": "3000",
        "total_principal": str(after.estimated_total_principal),
        "after_tax_interest": str(after.realizable_after_tax_interest),
        "rank": ranking.top_products[0].rank,
    }

    # E. Invalid/infeasible answer must be transactionally rejected.  Business
    # state is byte-for-byte model-equal, the same question remains active, and
    # the user can retry successfully.
    atomic_service = _service(kakao)
    atomic_session = _session(atomic_service, _intent("300000"))
    atomic_q = atomic_service.get_next_question(atomic_session.search_session_id)
    assert atomic_q is not None
    before = _business_snapshot(atomic_service, atomic_session.search_session_id)
    try:
        atomic_service.submit_user_answer(
            atomic_session.search_session_id,
            question_id=atomic_q.question_id,
            answer="10000",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("infeasible answer unexpectedly committed")
    after_invalid = _business_snapshot(atomic_service, atomic_session.search_session_id)
    assert after_invalid == before
    retry = atomic_service.get_next_question(atomic_session.search_session_id)
    assert retry is not None and retry.question_id == atomic_q.question_id
    atomic_service.submit_user_answer(
        atomic_session.search_session_id,
        question_id=retry.question_id,
        answer="2000",
    )
    choices = atomic_service.get_product_contribution_choices(atomic_session.search_session_id)
    assert len(choices) == 1 and Decimal(str(choices[0].value)) == Decimal("2000")
    results["E_invalid_answer_atomicity"] = {
        "invalid_answer": "10000",
        "business_state_unchanged": True,
        "same_question_retryable": True,
        "valid_retry_committed": "2000",
    }

    # F. Product-scoped state remains isolated after feasibility evaluation.
    a = _clone_kakao("V043-A", "점증식 A")
    b = _clone_kakao("V043-B", "점증식 B")
    iso = _service(a, b)
    iso_session = _session(iso, _intent("300000", top_k=2))
    q1 = iso.get_next_question(iso_session.search_session_id)
    assert q1 is not None and q1.ranking_input is not None
    iso.submit_user_answer(iso_session.search_session_id, question_id=q1.question_id, answer="3000")
    q2 = iso.get_next_question(iso_session.search_session_id)
    assert q2 is not None and q2.ranking_input is not None
    iso.submit_user_answer(iso_session.search_session_id, question_id=q2.question_id, answer="2000")
    scoped = {(c.product_id, c.field): str(c.value) for c in iso.get_product_contribution_choices(iso_session.search_session_id)}
    assert scoped[(q1.ranking_input.product_id, "preferred_start_amount")] == "3000"
    assert scoped[(q2.ranking_input.product_id, "preferred_start_amount")] == "2000"
    global_plan = iso.get_search_intent(iso_session.search_session_id).contribution_plan
    assert global_plan is not None and global_plan.maximum_affordable_periodic_amount == Decimal("300000")
    results["F_product_state_isolation"] = {
        "choices": {f"{pid}:{field}": value for (pid, field), value in sorted(scoped.items())},
        "global_monthly_limit": str(global_plan.maximum_affordable_periodic_amount),
        "global_desired_amount": str(global_plan.desired_periodic_amount),
    }

    path = OUT / "v0.4.3-adversarial.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"[PASS] v0.4.3 adversarial scenarios A-F: {path}")


if __name__ == "__main__":
    main()
