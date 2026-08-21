from __future__ import annotations

from fastapi.testclient import TestClient

from eligibility.application_service import ApplicationService
from eligibility.conversation import ConversationOrchestrator
from eligibility.fixtures.hana_run import hana_run_product
from eligibility.fixtures.ibk_parent_benefit import ibk_parent_benefit_product
from eligibility.fixtures.kakao_26_week import kakao_26_week_product
from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
from eligibility.fixtures.user_001 import USER_ID, user_001
from eligibility.schema.conversation import (
    ConversationAction,
    ConversationOperation,
    ConversationPlan,
)
from eligibility.web.app import create_app


class ShowResultsOrchestrator:
    def interpret(self, message: str, *, context):
        return ConversationPlan(
            actions=[ConversationAction(operation=ConversationOperation.SHOW_CURRENT_RESULTS)]
        )


def service(*, conversational: bool = False) -> ApplicationService:
    return ApplicationService(
        [
            shinhan_youth_first_product(),
            kakao_26_week_product(),
            ibk_parent_benefit_product(),
            hana_run_product(),
        ],
        user_fact_stores={USER_ID: user_001()},
        conversation_orchestrator=ShowResultsOrchestrator() if conversational else None,
    )


def create_session(client: TestClient) -> dict:
    response = client.post(
        "/api/search-sessions",
        json={
            "user_id": USER_ID,
            "natural_language_query": "1년 정도 월 30만원을 넣을 적금 중 나에게 좋은 상품을 찾아줘.",
            "as_of": "2026-08-20",
            "subscription_date": "2026-08-20",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_web_index_and_runtime_metadata() -> None:
    client = TestClient(create_app(service=service()))
    page = client.get("/")
    assert page.status_code == 200
    assert "금융상품 AI 추천" in page.text

    runtime = client.get("/api/runtime")
    assert runtime.status_code == 200
    body = runtime.json()
    assert body["backend_version"] == "0.4.6"
    assert body["product_count"] == 4
    assert body["sample_user_id"] == USER_ID


def test_web_flow_exposes_structured_state_question_list_and_detail() -> None:
    client = TestClient(create_app(service=service()))
    session = create_session(client)
    session_id = session["search_session_id"]

    snapshot = client.get(f"/api/search-sessions/{session_id}/state")
    assert snapshot.status_code == 200
    state = snapshot.json()
    assert state["intent"]["contribution_plan"]["desired_periodic_amount"] == "300000"
    assert any(
        item["fact_type"] == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
        for item in state["user_declarations"]
    )

    question_response = client.get(f"/api/search-sessions/{session_id}/questions/next")
    assert question_response.status_code == 200
    question = question_response.json()
    assert question["question_kind"] == "RANKING_INPUT"
    assert question["ranking_input"]["required_field"] == "preferred_start_amount"

    rec_response = client.get(f"/api/search-sessions/{session_id}/recommendations")
    assert rec_response.status_code == 200
    rec = rec_response.json()
    assert len(rec["top_products"]) == 4
    assert rec["top_products"][0]["product_name"] == "청년 처음적금"
    assert rec["top_products"][0]["estimated_after_tax_interest"] is not None

    answer = client.post(
        f"/api/search-sessions/{session_id}/answers",
        json={"question_id": question["question_id"], "answer": "3000"},
    )
    assert answer.status_code == 200

    snapshot_after = client.get(f"/api/search-sessions/{session_id}/state").json()
    assert snapshot_after["product_contribution_choices"][0]["value"] == "3000"

    rec_after = client.get(f"/api/search-sessions/{session_id}/recommendations").json()
    kakao = next(item for item in rec_after["top_products"] if item["product_name"] == "26주적금")
    assert kakao["estimated_total_principal"] is not None

    detail = client.get(
        f"/api/search-sessions/{session_id}/recommendations/{rec_after['top_products'][0]['product_id']}"
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["rate_breakdown"]
    assert detail_body["explanation"]
    assert detail_body["realizable_rate"] == rec_after["top_products"][0]["realizable_rate"]


def test_web_message_proxy_preserves_show_current_results_semantics() -> None:
    client = TestClient(create_app(service=service(conversational=True)))
    session = create_session(client)
    session_id = session["search_session_id"]
    active_question_id = session["active_question_id"]

    response = client.post(
        f"/api/search-sessions/{session_id}/messages",
        json={"message": "질문은 그만하고 지금 결과 보여줘."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["current_results_requested"] is True
    assert body["recommendations"] is not None
    assert body["next_question"] is None
    assert body["session"]["active_question_id"] == active_question_id


def test_delete_closes_session_and_state_endpoint_no_longer_resolves() -> None:
    client = TestClient(create_app(service=service()))
    session = create_session(client)
    session_id = session["search_session_id"]

    deleted = client.delete(f"/api/search-sessions/{session_id}")
    assert deleted.status_code == 204
    missing = client.get(f"/api/search-sessions/{session_id}/state")
    assert missing.status_code == 404
