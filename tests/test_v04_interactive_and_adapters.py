from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from eligibility.adapters import MCPToolAdapter, RestApplicationAdapter
from eligibility.application import UserAnswerMapper
from eligibility.application_service import ApplicationService
from eligibility.audit import AuditEventType
from eligibility.schema.application_input import Capability
from eligibility.schema.enums import CapabilityState, FactRecordStatus

from tests.v04_helpers import (
    AS_OF,
    SUBSCRIPTION_DATE,
    USER_ID,
    base_store,
    make_intent,
    make_product,
)


ANSWERED_AT = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)


def _create(service: ApplicationService, intent):
    return service.create_search_session(
        user_id=USER_ID,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )


def test_user_changes_monthly_contribution_and_reranks():
    large_capacity = make_product(
        "LARGE-CAPACITY",
        base_rate="3.0",
        periodic_max="300000",
    )
    high_rate_small = make_product(
        "HIGH-RATE-SMALL",
        base_rate="4.0",
        periodic_max="100000",
    )
    service = ApplicationService(
        [large_capacity, high_rate_small],
        user_fact_stores={USER_ID: base_store()},
    )
    session = _create(
        service,
        make_intent(desired_amount="300000", maximum_amount="500000", top_k=2),
    )

    initial = service.get_top_recommendations(session.search_session_id)
    assert initial.top_products[0].product_id == "LARGE-CAPACITY"
    initial_ranking_run_id = service.get_search_status(session.search_session_id).ranking_run_id

    updated = service.update_search_intent(
        session.search_session_id,
        utterance="월 10만원 기준으로 다시 보여줘.",
    )
    reranked = service.get_top_recommendations(session.search_session_id)

    assert updated.intent_version == 2
    assert reranked.top_products[0].product_id == "HIGH-RATE-SMALL"
    assert reranked.top_products[0].planned_contribution_summary == "월 100,000원 납입 계획"
    assert service.get_search_status(session.search_session_id).ranking_run_id != initial_ranking_run_id


def test_user_excludes_new_card_and_reranks():
    card_product = make_product(
        "NEW-CARD-PRODUCT",
        base_rate="3.0",
        reward_pp="2.0",
        bonus_fact_type="NEW_CARD_ISSUANCE_POSSIBLE",
        rule_name="신규카드 발급 우대",
    )
    simple_product = make_product("NO-CARD-PRODUCT", base_rate="4.0")
    initial_intent = make_intent(
        top_k=2,
        capabilities=[
            Capability(
                capability_id="NEW_CARD_ISSUANCE",
                state=CapabilityState.CAN,
            )
        ],
    )
    service = ApplicationService(
        [card_product, simple_product],
        user_fact_stores={USER_ID: base_store()},
    )
    session = _create(service, initial_intent)

    initial = service.get_top_recommendations(session.search_session_id)
    assert initial.top_products[0].product_id == "NEW-CARD-PRODUCT"

    service.update_search_intent(
        session.search_session_id,
        utterance="카드 새로 만드는 건 싫어.",
    )
    updated = service.get_top_recommendations(session.search_session_id)
    evaluations = service.evaluate_candidates(session.search_session_id)

    assert updated.top_products[0].product_id == "NO-CARD-PRODUCT"
    assert evaluations["NEW-CARD-PRODUCT"].realizable_rate == Decimal("3.0")
    active_capability = [
        item
        for item in service.get_search_intent(session.search_session_id).capabilities
        if item.capability_id == "NEW_CARD_ISSUANCE"
    ]
    assert active_capability[0].state == CapabilityState.CANNOT


def test_user_revises_capability_answer_and_reranks():
    salary_product = make_product(
        "SALARY-PRODUCT",
        base_rate="3.0",
        reward_pp="2.0",
        bonus_fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
        rule_name="급여계좌 변경 우대",
    )
    competitor = make_product("COMPETITOR", base_rate="4.5")
    service = ApplicationService(
        [salary_product, competitor],
        user_fact_stores={USER_ID: base_store()},
    )
    session = _create(service, make_intent(top_k=2))
    question = service.get_next_question(session.search_session_id)

    assert question is not None
    assert question.request.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
    request_reference = UserAnswerMapper.request_reference(question.request)
    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer=False,
        answered_at=ANSWERED_AT,
    )
    after_false = service.get_top_recommendations(session.search_session_id)
    assert after_false.top_products[0].product_id == "COMPETITOR"

    service.revise_user_answer(
        session.search_session_id,
        request_reference=request_reference,
        new_value=True,
        answered_at=datetime(2026, 8, 19, 13, tzinfo=timezone.utc),
    )
    after_true = service.get_top_recommendations(session.search_session_id)

    assert after_true.top_products[0].product_id == "SALARY-PRODUCT"
    assert service.evaluate_candidates(session.search_session_id)["SALARY-PRODUCT"].realizable_rate == Decimal("5.0")


def test_prior_versions_remain_auditable():
    product = make_product(
        "AUDIT-REVISION",
        base_rate="2.0",
        reward_pp="1.0",
        bonus_fact_type="REVISION_FACT",
    )
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    session = _create(service, make_intent(top_k=1))
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    reference = UserAnswerMapper.request_reference(question.request)

    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer=False,
        answered_at=ANSWERED_AT,
    )
    service.revise_user_answer(
        session.search_session_id,
        request_reference=reference,
        new_value=True,
        answered_at=datetime(2026, 8, 19, 13, tzinfo=timezone.utc),
    )

    history = [
        record
        for record in service.get_answer_history(session.search_session_id)
        if record.request_reference == reference
    ]
    assert [record.version for record in history] == [1, 2]
    assert [record.status for record in history] == [
        FactRecordStatus.SUPERSEDED,
        FactRecordStatus.ACTIVE,
    ]
    assert history[1].supersedes_fact_id == history[0].fact_id

    events = service.get_evaluation_trace(session.search_session_id)
    event_types = [event.event_type for event in events]
    assert AuditEventType.USER_ANSWER_SUPERSEDED in event_types
    assert AuditEventType.CANDIDATE_EVALUATED in event_types
    assert AuditEventType.RANKING_CALCULATED in event_types
    assert all(event.search_session_id == session.search_session_id for event in events)


def test_rest_and_mcp_adapters_share_application_service():
    product = make_product("ADAPTER-PRODUCT", base_rate="4.2")
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    rest = RestApplicationAdapter(service)
    mcp = MCPToolAdapter(service)

    created = rest.handle(
        "POST",
        "/search-sessions",
        {
            "user_id": USER_ID,
            "utterance": "1년 동안 월 30만원 넣을 적금 찾아줘.",
            "as_of": AS_OF.isoformat(),
            "subscription_date": SUBSCRIPTION_DATE.isoformat(),
        },
    )
    assert created.status_code == 201
    search_session_id = created.body["search_session_id"]

    status = mcp.get_search_status(search_session_id=search_session_id)
    recommendations = mcp.get_top_recommendations(search_session_id=search_session_id)
    detail = rest.handle(
        "GET",
        f"/search-sessions/{search_session_id}/recommendations/ADAPTER-PRODUCT",
    )
    trace = rest.handle("GET", f"/search-sessions/{search_session_id}/trace")

    assert status["search_session_id"] == search_session_id
    assert recommendations["top_products"][0]["product_id"] == "ADAPTER-PRODUCT"
    assert detail.status_code == 200
    assert detail.body["product_id"] == "ADAPTER-PRODUCT"
    assert trace.status_code == 200
    assert any(
        event["event_type"] == AuditEventType.RECOMMENDATION_CREATED.value
        for event in trace.body["events"]
    )
