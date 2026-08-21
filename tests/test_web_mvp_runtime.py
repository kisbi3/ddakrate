from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from eligibility.application_service import ApplicationService
from eligibility.catalog import load_default_product_catalog
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
from eligibility.schema.enums import RankingObjective
from eligibility.schema.user_fact import UserFactStore
from eligibility.web.app import create_app
from tests.v04_helpers import make_intent, verified_fact


class ShowResultsOrchestrator:
    def interpret(self, message: str, *, context):
        return ConversationPlan(
            actions=[ConversationAction(operation=ConversationOperation.SHOW_CURRENT_RESULTS)]
        )


class ExplainQuestionOrchestrator:
    def interpret(self, message: str, *, context):
        return ConversationPlan(
            actions=[
                ConversationAction(
                    operation=ConversationOperation.EXPLAIN_ACTIVE_QUESTION
                )
            ]
        )


class PreSearchAnswerOrchestrator:
    def interpret(self, message: str, *, context):
        return ConversationPlan(
            actions=[
                ConversationAction(
                    operation=ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER,
                    answer=True,
                    rationale="현재 급여는 국민은행 계좌로 받고 있으며, 더 유리하면 변경 가능",
                )
            ]
        )


def service(*, conversational: bool = False, orchestrator=None) -> ApplicationService:
    return ApplicationService(
        [
            shinhan_youth_first_product(),
            kakao_26_week_product(),
            ibk_parent_benefit_product(),
            hana_run_product(),
        ],
        user_fact_stores={USER_ID: user_001()},
        conversation_orchestrator=(
            orchestrator
            if orchestrator is not None
            else ShowResultsOrchestrator() if conversational else None
        ),
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
    assert body["sample_user_id"] is None
    assert body["user_data_mode"] == "CONVERSATIONAL_INPUT"
    assert "샘플 사용자 데이터 연결" not in page.text
    assert "대화로 조건 확인" in page.text
    app_js = client.get("/static/app.js")
    assert "addButton('네', true)" in app_js.text
    assert "addButton('아니요', false)" in app_js.text
    assert "모르겠어요" in app_js.text
    assert "AI가 이해한 조건" in page.text
    assert "균형 기준" in page.text
    assert "금리순" in page.text
    assert "이자금순" in page.text
    assert "처음부터" in page.text
    assert "새로고침" in page.text
    assert 'placeholder="AI에게 질문하세요!"' in page.text
    assert "저장된 사용자 데이터 없이" not in page.text
    assert "아직 확인하지 않은 조건이 있어요" not in page.text
    assert 'id="question-dock"' not in page.text
    assert "preloadRecommendationDetails" in app_js.text
    assert "detailCache.get(productId)" in app_js.text
    assert "말씀하신 내용을 현재 검색 상태에 반영했어요" not in app_js.text


def test_private_debug_ui_exposes_turn_state_and_engine_boundaries() -> None:
    client = TestClient(create_app(service=service()))

    page = client.get("/debug")
    assert page.status_code == 200
    assert "AI → 멀티턴 → Engine 흐름 보기" in page.text
    assert "제출 화면 열기" in page.text

    session = create_session(client)
    session_id = session["search_session_id"]

    sessions = client.get("/api/debug/sessions")
    assert sessions.status_code == 200
    listed = sessions.json()["sessions"]
    assert listed[0]["search_session_id"] == session_id
    assert listed[0]["source_utterances"]

    detail = client.get(f"/api/debug/sessions/{session_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["requests"][0]["path"] == "/api/search-sessions"
    assert body["requests"][0]["request_payload"]["natural_language_query"]
    assert body["snapshot"]["multi_turn"]["working_note"]
    assert body["snapshot"]["multi_turn"]["active_question"]
    assert body["snapshot"]["engine"]["retrieval"]["input"]["intent"]
    assert body["snapshot"]["engine"]["retrieval"]["output"]["filter_decisions"]
    assert body["snapshot"]["engine"]["evaluation"]["output"]
    assert body["snapshot"]["engine"]["ranking"]["output"]
    assert body["snapshot"]["audit_timeline"]


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
    assert state["search_progress"] == {
        "recalculation_round": 1,
        "catalog_product_count": 4,
        "candidate_product_count": 4,
        "viable_product_count": 4,
        "visible_top_k_count": 4,
        "visible_top_product_ids": [
            "SHINHAN_YOUTH_FIRST_SAVINGS_20260722",
            "IBK_PARENT_BENEFIT_SAVINGS_20260220",
            "HANA_RUN_SAVINGS_20260429",
            "KAKAOBANK_26_WEEK_SAVINGS_20260819",
        ],
        "ranking_stable": False,
        "has_next_question": True,
        "completed_question_count": 0,
        "estimated_total_question_count": 3,
        "estimate_is_dynamic": True,
    }

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
    assert rec["top_products"][0]["estimated_pre_tax_interest"] is not None

    answer = client.post(
        f"/api/search-sessions/{session_id}/answers",
        json={"question_id": question["question_id"], "answer": "3000"},
    )
    assert answer.status_code == 200

    snapshot_after = client.get(f"/api/search-sessions/{session_id}/state").json()
    assert snapshot_after["product_contribution_choices"][0]["value"] == "3000"
    assert snapshot_after["search_progress"]["recalculation_round"] == 2
    assert snapshot_after["search_progress"]["viable_product_count"] <= 4

    rec_after = client.get(f"/api/search-sessions/{session_id}/recommendations").json()
    kakao = next(item for item in rec_after["top_products"] if item["product_name"] == "26주적금")
    assert kakao["estimated_total_principal"] is not None

    detail_path = (
        f"/api/search-sessions/{session_id}/recommendations/"
        f"{rec_after['top_products'][0]['product_id']}"
    )
    preview = client.get(f"{detail_path}/preview")
    assert preview.status_code == 200
    assert preview.json()["rate_breakdown"]
    assert preview.json()["explanation"] is None

    detail = client.get(detail_path)
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


def test_web_can_switch_to_pre_tax_interest_ranking() -> None:
    client = TestClient(create_app(service=service()))
    session = create_session(client)
    session_id = session["search_session_id"]
    question_before = client.get(
        f"/api/search-sessions/{session_id}/questions/next"
    ).json()

    updated = client.patch(
        f"/api/search-sessions/{session_id}/intent",
        json={
            "patch": {
                "ranking_objective_patch": "MAX_ESTIMATED_PRE_TAX_INTEREST"
            }
        },
    )
    recommendations = client.get(
        f"/api/search-sessions/{session_id}/recommendations"
    )

    assert updated.status_code == 200
    assert recommendations.status_code == 200
    body = recommendations.json()
    assert body["ranking_objective"] == "MAX_ESTIMATED_PRE_TAX_INTEREST"
    assert body["top_products"][0]["estimated_pre_tax_interest"] is not None
    question_after = client.get(
        f"/api/search-sessions/{session_id}/questions/next"
    ).json()
    assert question_after["question_id"] == question_before["question_id"]


def test_unknown_answer_skips_without_creating_false_user_fact() -> None:
    client = TestClient(create_app(service=service()))
    session = create_session(client)
    session_id = session["search_session_id"]
    question = client.get(
        f"/api/search-sessions/{session_id}/questions/next"
    ).json()

    response = client.post(
        f"/api/search-sessions/{session_id}/answers",
        json={"question_id": question["question_id"], "answer": "UNKNOWN"},
    )

    assert response.status_code == 200
    state = client.get(f"/api/search-sessions/{session_id}/state").json()
    assert state["search_progress"]["completed_question_count"] == 1
    assert all(item["value"] is not None for item in state["user_declarations"])
    assert state["acknowledged_unknown_answers"] == [
        {
            "question_id": question["question_id"],
            "fact_type": (
                question["request"]["fact_type"]
                if question["request"] is not None
                else question["question_kind"]
            ),
            "question": question["question"],
            "affected_product_ids": question["affected_product_ids"],
        }
    ]
    next_question = client.get(
        f"/api/search-sessions/{session_id}/questions/next"
    ).json()
    assert next_question["question_id"] != question["question_id"]


def test_natural_language_can_request_active_question_explanation() -> None:
    client = TestClient(
        create_app(service=service(orchestrator=ExplainQuestionOrchestrator()))
    )
    session = create_session(client)
    session_id = session["search_session_id"]
    active_question_id = client.get(
        f"/api/search-sessions/{session_id}/questions/next"
    ).json()["question_id"]

    response = client.post(
        f"/api/search-sessions/{session_id}/messages",
        json={"message": "그게 뭐예요? 어느 은행 이야기예요?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assistant_message"]
    assert body["next_question"]["question_id"] == active_question_id
    assert body["session"]["active_question_id"] == active_question_id


def test_salary_profile_question_is_asked_before_product_specific_questions() -> None:
    app_service = ApplicationService(
        [shinhan_youth_first_product()],
        user_fact_stores={},
    )
    session = app_service.create_search_session(
        user_id="NEW-WEB-USER",
        intent=make_intent(user_id="NEW-WEB-USER"),
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
    )

    question = app_service.get_next_question(session.search_session_id)

    assert question is not None
    assert question.question_stage == "PRE_SEARCH"
    assert question.request is not None
    assert question.request.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
    assert "현재 급여를 어느 은행" in question.question
    assert "옮길 의향" in question.question

    app_service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer=True,
    )
    card_question = None
    for _ in range(8):
        next_question = app_service.get_next_question(session.search_session_id)
        assert next_question is not None
        assert next_question.question_stage is None
        if (
            next_question.request is not None
            and next_question.request.fact_type
            == "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE"
        ):
            card_question = next_question
            break
        app_service.submit_user_answer(
            session.search_session_id,
            question_id=next_question.question_id,
            answer=False,
        )

    assert card_question is not None and card_question.request is not None
    assert card_question.question_stage is None
    assert card_question.request.fact_type == "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE"
    assert "카드대금" in card_question.question
    assert "어느 은행" in card_question.question
    assert "신한은행" in (card_question.product_context or "")


def test_pre_search_ai_summary_is_exposed_in_understood_state() -> None:
    app_service = ApplicationService(
        [shinhan_youth_first_product()],
        user_fact_stores={},
        conversation_orchestrator=PreSearchAnswerOrchestrator(),
    )
    session = app_service.create_search_session(
        user_id="PROFILE-SUMMARY-USER",
        intent=make_intent(user_id="PROFILE-SUMMARY-USER"),
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
    )

    app_service.handle_user_message(
        session.search_session_id,
        message="현재 국민은행으로 받고 있고 더 유리하면 바꿀 수 있어요.",
    )
    state = app_service.get_mutable_search_state(session.search_session_id)

    assert state["pre_search_profile_answers"] == [
        {
            "fact_type": "SALARY_ACCOUNT_CHANGE_POSSIBLE",
            "summary": "현재 급여는 국민은행 계좌로 받고 있으며, 더 유리하면 변경 가능",
        }
    ]


def test_daily_manual_deposit_questions_include_cadence_and_contract_term() -> None:
    products = {
        product.product_id: product for product in load_default_product_catalog()
    }
    cases = [
        (
            "KAKAOBANK_ONE_MONTH_SAVINGS_20260818",
            "ELIGIBLE_KAKAOBANK_ONE_MONTH_SAVINGS_20260818",
            "31일 동안 매일 직접 입금하는 것을 꾸준히 할 수 있으세요?",
        ),
        (
            "BNK_KYONGNAM_TOUCH_UP_1M_2026",
            "ELIGIBLE_BNK_KYONGNAM_TOUCH_UP_1M_2026",
            "1개월 가입 기간 동안 매일 직접 입금해 25일 이상 채울 수 있으세요?",
        ),
    ]

    for index, (product_id, eligibility_fact, expected_question) in enumerate(cases):
        user_id = f"DAILY-QUESTION-{index}"
        store = UserFactStore(user_id=user_id).with_fact(
            verified_fact(
                f"DAILY-ELIGIBLE-{index}",
                eligibility_fact,
                True,
                user_id=user_id,
            )
        )
        app_service = ApplicationService(
            [products[product_id]],
            user_fact_stores={user_id: store},
        )
        session = app_service.create_search_session(
            user_id=user_id,
            intent=make_intent(
                user_id=user_id,
                objective=RankingObjective.MAX_REALIZABLE_RATE,
            ),
            as_of=date(2026, 8, 21),
            subscription_date=date(2026, 8, 21),
        )

        question = app_service.get_next_question(session.search_session_id)
        assert question is not None
        assert question.question == expected_question


def test_shared_toss_auto_transfer_question_names_the_twelve_month_period() -> None:
    products = {
        product.product_id: product for product in load_default_product_catalog()
    }
    app_service = ApplicationService(
        [
            products["TOSS_CHILD_SAVINGS_20260714"],
            products["TOSS_FREE_SAVINGS_12M_20260508"],
        ],
        user_fact_stores={},
    )
    session = app_service.create_search_session(
        user_id="TOSS-WEB-USER",
        intent=make_intent(user_id="TOSS-WEB-USER"),
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
    )

    question = app_service.get_next_question(session.search_session_id)

    assert question is not None and question.request is not None
    assert question.request.fact_type == "WILL_TOSS_MONTHLY_AUTO_TRANSFER_ALL"
    assert question.question == (
        "12개월 동안 매달 자동이체가 빠짐없이 실행되도록 유지하실 수 있나요?"
    )
    assert question.product_context is not None
    assert question.product_context.startswith("자동이체 공통 조건")
    assert "아이적금" in question.product_context
    assert "자유적금(12개월)" in question.product_context


def test_coupon_explanation_distinguishes_known_and_missing_event_data() -> None:
    app_service = ApplicationService(
        [shinhan_youth_first_product()],
        user_fact_stores={},
    )
    session = app_service.create_search_session(
        user_id="COUPON-WEB-USER",
        intent=make_intent(user_id="COUPON-WEB-USER"),
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
    )

    coupon_question = None
    for _ in range(10):
        question = app_service.get_next_question(session.search_session_id)
        assert question is not None
        if question.request and question.request.fact_type == "SPECIAL_RATE_COUPON_VALID":
            coupon_question = question
            break
        app_service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer=(
                True
                if question.request
                and question.request.fact_type
                == "SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y"
                else True
            ),
        )

    assert coupon_question is not None
    assert coupon_question.explanation is not None
    assert "현재 AI가 확인한 데이터" in coupon_question.explanation
    assert "상품설명서 p.3" in coupon_question.explanation
    assert "발급 대상의 세부 기준: 현재 카탈로그에 없음" in coupon_question.explanation
    assert "발급 기간·발급 수량·잔여 수량" in coupon_question.explanation
    assert "실시간 연동" in coupon_question.explanation


def test_generic_official_eligibility_is_not_delegated_back_to_the_user() -> None:
    product = next(
        item
        for item in load_default_product_catalog()
        if item.product_id == "IBK_LOVE_SHARING_SAVINGS_20260805"
    )
    app_service = ApplicationService([product], user_fact_stores={})
    session = app_service.create_search_session(
        user_id="TOP-REVIEW-USER",
        intent=make_intent(user_id="TOP-REVIEW-USER", top_k=1),
        as_of=date(2026, 8, 21),
        subscription_date=date(2026, 8, 21),
    )

    question = app_service.get_next_question(session.search_session_id)

    assert question is None
    evaluation = app_service.evaluate_candidates(session.search_session_id)[
        product.product_id
    ]
    assert evaluation.material_unknown_count == 1


def test_delete_closes_session_and_state_endpoint_no_longer_resolves() -> None:
    client = TestClient(create_app(service=service()))
    session = create_session(client)
    session_id = session["search_session_id"]

    deleted = client.delete(f"/api/search-sessions/{session_id}")
    assert deleted.status_code == 204
    missing = client.get(f"/api/search-sessions/{session_id}/state")
    assert missing.status_code == 404
