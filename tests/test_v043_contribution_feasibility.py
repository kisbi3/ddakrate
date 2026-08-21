from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from eligibility.adapters import MCPToolAdapter, RestApplicationAdapter
from eligibility.application_service import ApplicationService
from eligibility.audit import AuditEventType
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_pre_subscription_user,
)
from eligibility.schema.enums import (
    ContributionFrequency,
    ContributionMode,
    RankingObjective,
    TermUnit,
)
from eligibility.schema.search import ContributionPlan, ContributionPlanPatch, IntentPatch, ProductSearchIntent
from eligibility.search.contribution import ContributionFeasibilityEvaluator, ContributionPlanner

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, make_product


def _kakao_intent(
    maximum: str,
    *,
    desired: str | None = None,
    objective: RankingObjective = RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
    top_k: int = 1,
) -> ProductSearchIntent:
    desired = desired or maximum
    return ProductSearchIntent(
        search_intent_id=f"INTENT-V043-{maximum}-{objective.value}",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=objective,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal(desired),
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
        products or [kakao_26_week_product()],
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
    runtime = service._runtime(session_id)  # white-box regression for transaction atomicity
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


def test_weekly_schedule_uses_calendar_month_buckets():
    product = make_product(
        "WEEKLY-FIXED-CALENDAR",
        term_value=26,
        term_unit=TermUnit.WEEK,
        frequency=ContributionFrequency.WEEKLY,
        contribution_mode=ContributionMode.FIXED,
        periodic_max="100000",
    )
    product_plan = ContributionPlan(
        desired_periodic_amount=Decimal("80000"),
        maximum_affordable_periodic_amount=Decimal("350000"),
        frequency=ContributionFrequency.WEEKLY,
    )
    global_plan = ContributionPlan(
        desired_periodic_amount=Decimal("80000"),
        maximum_affordable_periodic_amount=Decimal("350000"),
        frequency=ContributionFrequency.MONTHLY,
    )
    projection = ContributionPlanner().build(
        product, product_plan, subscription_date=SUBSCRIPTION_DATE
    ).projection
    check = ContributionFeasibilityEvaluator().check_projection(projection, global_plan)

    assert projection.cashflows[0].contribution_date == SUBSCRIPTION_DATE
    assert projection.cashflows[1].contribution_date == date(2026, 8, 27)
    assert check.feasible is False
    assert check.max_bucket_amount == Decimal("400000")
    assert check.affordability_limit == Decimal("350000")


def test_five_occurrence_month_is_counted_correctly():
    product = make_product(
        "WEEKLY-FIVE-OCCURRENCES",
        term_value=26,
        term_unit=TermUnit.WEEK,
        frequency=ContributionFrequency.WEEKLY,
        contribution_mode=ContributionMode.FIXED,
        periodic_max="100000",
    )
    plan = ContributionPlan(
        desired_periodic_amount=Decimal("80000"),
        maximum_affordable_periodic_amount=Decimal("350000"),
        frequency=ContributionFrequency.WEEKLY,
    )
    projection = ContributionPlanner().build(
        product, plan, subscription_date=SUBSCRIPTION_DATE
    ).projection
    october = [
        item for item in projection.cashflows
        if item.contribution_date is not None
        and item.contribution_date.year == 2026
        and item.contribution_date.month == 10
    ]
    assert len(october) == 5
    assert sum(item.amount for item in october) == Decimal("400000")


def test_four_week_approximation_would_be_wrong():
    # Four weekly events fit 350k, but October contains five dated events.
    assert Decimal("80000") * 4 == Decimal("320000") < Decimal("350000")
    assert Decimal("80000") * 5 == Decimal("400000") > Decimal("350000")


def test_kakao_option_feasibility_prunes_to_actual_calendar_affordability():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("300000"))
    question = service.get_next_question(session.search_session_id)

    assert question is not None and question.question_kind == "RANKING_INPUT"
    assert question.ranking_input is not None
    assert question.ranking_input.allowed_options == ["1000", "2000", "3000"]
    candidate = service.evaluate_candidates(session.search_session_id)[
        kakao_26_week_product().product_id
    ]
    by_option = {
        Decimal(str(item.option_value)): item
        for item in candidate.contribution_option_feasibilities
    }
    assert by_option[Decimal("3000")].status == "FEASIBLE"
    assert by_option[Decimal("3000")].max_bucket_amount == Decimal("270000")
    assert by_option[Decimal("5000")].status == "INFEASIBLE"
    assert by_option[Decimal("5000")].max_bucket_amount == Decimal("450000")
    assert by_option[Decimal("10000")].max_bucket_amount == Decimal("900000")


def test_single_feasible_option_is_still_confirmed_by_user():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("100000"))
    question = service.get_next_question(session.search_session_id)

    assert question is not None and question.question_kind == "RANKING_INPUT"
    assert question.ranking_input is not None
    assert question.ranking_input.allowed_options == ["1000"]
    assert "진행할까요" in question.question
    assert service.get_product_contribution_choices(session.search_session_id) == ()


def test_all_options_infeasible_creates_adjust_or_exclude_clarification():
    product = kakao_26_week_product()
    service = _service(product)
    session = _session(service, _kakao_intent("80000"))
    question = service.get_next_question(session.search_session_id)

    assert product.product_id in service.get_search_status(session.search_session_id).candidate_product_ids
    assert question is not None and question.question_kind == "CONTRIBUTION_FEASIBILITY"
    clarification = question.feasibility_clarification
    assert clarification is not None
    assert clarification.current_affordability_amount == Decimal("80000")
    assert clarification.current_affordability_frequency == ContributionFrequency.MONTHLY
    assert clarification.minimum_required_affordability == Decimal("90000")
    assert clarification.allowed_resolutions == [
        "ADJUST_GLOBAL_AFFORDABILITY",
        "EXCLUDE_PRODUCT",
    ]
    events = service.get_evaluation_trace(session.search_session_id)
    assert any(
        event.event_type == AuditEventType.CONTRIBUTION_FEASIBILITY_CLARIFICATION_CREATED
        for event in events
    )


def test_affordability_increase_recalculates_options_then_interest_and_ranking():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("80000"))
    first = service.get_next_question(session.search_session_id)
    assert first is not None and first.question_kind == "CONTRIBUTION_FEASIBILITY"

    service.submit_user_answer(
        session.search_session_id,
        question_id=first.question_id,
        answer={
            "resolution": "ADJUST_GLOBAL_AFFORDABILITY",
            "maximum_affordable_periodic_amount": "300000",
        },
    )
    assert (
        service.get_search_intent(session.search_session_id)
        .contribution_plan.maximum_affordable_periodic_amount
        == Decimal("300000")
    )
    second = service.get_next_question(session.search_session_id)
    assert second is not None and second.question_kind == "RANKING_INPUT"
    assert second.ranking_input is not None
    assert second.ranking_input.allowed_options == ["1000", "2000", "3000"]

    service.submit_user_answer(
        session.search_session_id,
        question_id=second.question_id,
        answer="3000",
    )
    candidate = service.evaluate_candidates(session.search_session_id)[
        kakao_26_week_product().product_id
    ]
    assert candidate.realizable_after_tax_interest is not None
    assert candidate.ranking_comparability.value == "COMPARABLE"
    assert service.get_top_recommendations(session.search_session_id).top_products[0].product_id == kakao_26_week_product().product_id


def test_user_excluded_product_is_session_local_and_reranks_others():
    kakao = kakao_26_week_product()
    other = make_product("V043-OTHER", base_rate="1.5")
    service = _service(kakao, other)
    session = _session(service, _kakao_intent("80000", top_k=2))
    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.question_kind == "CONTRIBUTION_FEASIBILITY"

    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer="EXCLUDE_PRODUCT",
    )
    state = service.get_search_status(session.search_session_id)
    assert state.excluded_product_ids == [kakao.product_id]
    assert kakao.product_id not in state.candidate_product_ids
    assert other.product_id in state.candidate_product_ids
    recs = service.get_top_recommendations(session.search_session_id)
    assert [item.product_id for item in recs.top_products] == [other.product_id]
    assert any(
        event.event_type == AuditEventType.PRODUCT_EXCLUDED_BY_USER
        for event in service.get_evaluation_trace(session.search_session_id)
    )


@pytest.mark.parametrize("answer", ["-1000", "5000", {"bad": "type"}])
def test_invalid_answer_does_not_commit_any_business_state(answer):
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("300000"))
    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.question_kind == "RANKING_INPUT"
    before = _business_snapshot(service, session.search_session_id)

    with pytest.raises(ValueError):
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer=answer,
        )
    after = _business_snapshot(service, session.search_session_id)
    assert after == before
    assert service.get_product_contribution_choices(session.search_session_id) == ()
    assert service.get_search_status(session.search_session_id).answered_question_ids == []
    assert service.get_search_status(session.search_session_id).active_question_id == question.question_id


def test_stale_previously_allowed_option_is_revalidated_at_submit_time():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("1000000", desired="300000"))
    old_question = service.get_next_question(session.search_session_id)
    assert old_question is not None and old_question.ranking_input is not None
    assert "5000" in old_question.ranking_input.allowed_options

    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                maximum_affordable_periodic_amount=Decimal("300000")
            )
        ),
    )
    current = service.get_next_question(session.search_session_id)
    assert current is not None and current.question_id == old_question.question_id
    assert current.ranking_input is not None and "5000" not in current.ranking_input.allowed_options
    before = _business_snapshot(service, session.search_session_id)
    with pytest.raises(ValueError):
        service.submit_user_answer(
            session.search_session_id,
            question_id=old_question.question_id,
            answer="5000",
        )
    assert _business_snapshot(service, session.search_session_id) == before


def test_valid_answer_commits_only_after_validation():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("300000"))
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer="3000",
    )
    state = service.get_search_status(session.search_session_id)
    assert question.question_id in state.answered_question_ids
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("3000")


def test_max_realizable_rate_skips_interest_input_when_some_option_is_affordable():
    service = _service(kakao_26_week_product())
    session = _session(
        service,
        _kakao_intent("300000", objective=RankingObjective.MAX_REALIZABLE_RATE),
    )
    question = service.get_next_question(session.search_session_id)
    assert question is None or question.question_kind != "RANKING_INPUT"
    assert question is None or question.question_kind != "CONTRIBUTION_FEASIBILITY"


def test_feasibility_filter_is_product_scoped_and_global_plan_remains_global():
    a = kakao_26_week_product().model_copy(
        update={
            "product_id": "V043-KAKAO-A",
            "name": "V043 A",
            "metadata": kakao_26_week_product().metadata.model_copy(
                update={"product_id": "V043-KAKAO-A", "product_name": "V043 A"}, deep=True
            ),
        },
        deep=True,
    )
    b = kakao_26_week_product().model_copy(
        update={
            "product_id": "V043-KAKAO-B",
            "name": "V043 B",
            "metadata": kakao_26_week_product().metadata.model_copy(
                update={"product_id": "V043-KAKAO-B", "product_name": "V043 B"}, deep=True
            ),
        },
        deep=True,
    )
    service = _service(a, b)
    session = _session(service, _kakao_intent("300000", top_k=2))
    q1 = service.get_next_question(session.search_session_id)
    assert q1 is not None and q1.ranking_input is not None
    service.submit_user_answer(session.search_session_id, question_id=q1.question_id, answer="3000")
    q2 = service.get_next_question(session.search_session_id)
    assert q2 is not None and q2.ranking_input is not None
    service.submit_user_answer(session.search_session_id, question_id=q2.question_id, answer="2000")

    choices = {(c.product_id, c.field): c.value for c in service.get_product_contribution_choices(session.search_session_id)}
    assert choices[(q1.ranking_input.product_id, "preferred_start_amount")] == Decimal("3000")
    assert choices[(q2.ranking_input.product_id, "preferred_start_amount")] == Decimal("2000")
    global_plan = service.get_search_intent(session.search_session_id).contribution_plan
    assert global_plan is not None
    assert global_plan.desired_periodic_amount == Decimal("300000")
    assert global_plan.maximum_affordable_periodic_amount == Decimal("300000")
    assert global_plan.frequency == ContributionFrequency.MONTHLY


def test_rest_and_mcp_can_drive_feasibility_flow_without_business_logic_duplication():
    product = kakao_26_week_product()
    service = _service(product)
    session = _session(service, _kakao_intent("80000"))
    rest = RestApplicationAdapter(service)
    q = rest.handle("GET", f"/search-sessions/{session.search_session_id}/questions/next")
    assert q.status_code == 200 and q.body["question_kind"] == "CONTRIBUTION_FEASIBILITY"
    adjusted = rest.handle(
        "POST",
        f"/search-sessions/{session.search_session_id}/answers",
        {
            "question_id": q.body["question_id"],
            "answer": {
                "resolution": "ADJUST_GLOBAL_AFFORDABILITY",
                "maximum_affordable_periodic_amount": "300000",
            },
        },
    )
    assert adjusted.status_code == 200

    mcp = MCPToolAdapter(service)
    next_q = mcp.get_next_question(search_session_id=session.search_session_id)
    assert next_q["question_kind"] == "RANKING_INPUT"
    assert next_q["ranking_input"]["allowed_options"] == ["1000", "2000", "3000"]


def test_invalid_answer_does_not_commit_product_choice():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("300000"))
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    with pytest.raises(ValueError):
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer="5000",
        )
    assert service.get_product_contribution_choices(session.search_session_id) == ()


def test_invalid_answer_does_not_mark_question_answered():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("300000"))
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    with pytest.raises(ValueError):
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer="-1000",
        )
    assert question.question_id not in service.get_search_status(
        session.search_session_id
    ).answered_question_ids


def test_invalid_answer_preserves_active_question():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("300000"))
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    with pytest.raises(ValueError):
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer={"bad": "type"},
        )
    assert service.get_search_status(session.search_session_id).active_question_id == question.question_id
    retried = service.get_next_question(session.search_session_id)
    assert retried is not None and retried.question_id == question.question_id


def test_infeasible_answer_does_not_mutate_ranking_state():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("300000"))
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    runtime = service._runtime(session.search_session_id)
    before_ranking = deepcopy(runtime.ranking)
    before_evaluations = deepcopy(runtime.evaluations)
    with pytest.raises(ValueError):
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer="5000",
        )
    runtime = service._runtime(session.search_session_id)
    assert runtime.ranking == before_ranking
    assert runtime.evaluations == before_evaluations


def test_product_a_feasibility_does_not_mutate_product_b_choice():
    a = kakao_26_week_product().model_copy(
        update={
            "product_id": "V043-ISO-A",
            "name": "V043 Isolation A",
            "metadata": kakao_26_week_product().metadata.model_copy(
                update={"product_id": "V043-ISO-A", "product_name": "V043 Isolation A"},
                deep=True,
            ),
        },
        deep=True,
    )
    b = kakao_26_week_product().model_copy(
        update={
            "product_id": "V043-ISO-B",
            "name": "V043 Isolation B",
            "metadata": kakao_26_week_product().metadata.model_copy(
                update={"product_id": "V043-ISO-B", "product_name": "V043 Isolation B"},
                deep=True,
            ),
        },
        deep=True,
    )
    service = _service(a, b)
    session = _session(service, _kakao_intent("300000", top_k=2))
    q1 = service.get_next_question(session.search_session_id)
    assert q1 is not None and q1.ranking_input is not None
    service.submit_user_answer(session.search_session_id, question_id=q1.question_id, answer="3000")
    q2 = service.get_next_question(session.search_session_id)
    assert q2 is not None and q2.ranking_input is not None
    service.submit_user_answer(session.search_session_id, question_id=q2.question_id, answer="2000")
    before = {
        (item.product_id, item.field): item.value
        for item in service.get_product_contribution_choices(session.search_session_id)
    }
    service.evaluate_candidates(session.search_session_id)
    after = {
        (item.product_id, item.field): item.value
        for item in service.get_product_contribution_choices(session.search_session_id)
    }
    assert before == after
    assert before[(q1.ranking_input.product_id, "preferred_start_amount")] == Decimal("3000")
    assert before[(q2.ranking_input.product_id, "preferred_start_amount")] == Decimal("2000")


def test_global_plan_remains_global_after_feasibility_evaluation():
    service = _service(kakao_26_week_product())
    session = _session(service, _kakao_intent("300000", desired="300000"))
    before = deepcopy(service.get_search_intent(session.search_session_id).contribution_plan)
    service.evaluate_candidates(session.search_session_id)
    after = service.get_search_intent(session.search_session_id).contribution_plan
    assert after == before
    assert after is not None
    assert after.frequency == ContributionFrequency.MONTHLY
    assert after.maximum_affordable_periodic_amount == Decimal("300000")


def test_max_realizable_rate_still_asks_when_hard_affordability_blocks_all_options():
    service = _service(kakao_26_week_product())
    session = _session(
        service,
        _kakao_intent("80000", objective=RankingObjective.MAX_REALIZABLE_RATE),
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.question_kind == "CONTRIBUTION_FEASIBILITY"
    assert question.feasibility_clarification is not None
    assert question.feasibility_clarification.minimum_required_affordability == Decimal("90000")


def test_rest_invalid_ranking_input_returns_400_without_state_commit():
    product = kakao_26_week_product()
    service = _service(product)
    session = _session(service, _kakao_intent("300000"))
    rest = RestApplicationAdapter(service)
    question = rest.handle(
        "GET", f"/search-sessions/{session.search_session_id}/questions/next"
    )
    assert question.status_code == 200
    before = _business_snapshot(service, session.search_session_id)
    rejected = rest.handle(
        "POST",
        f"/search-sessions/{session.search_session_id}/answers",
        {"question_id": question.body["question_id"], "answer": "10000"},
    )
    assert rejected.status_code == 400
    assert _business_snapshot(service, session.search_session_id) == before
    retry = rest.handle(
        "GET", f"/search-sessions/{session.search_session_id}/questions/next"
    )
    assert retry.status_code == 200
    assert retry.body["question_id"] == question.body["question_id"]
