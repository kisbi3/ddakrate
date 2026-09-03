from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from threading import Event
import time

from fastapi.testclient import TestClient

from eligibility.application_service import ApplicationService
from eligibility.catalog import load_product_catalog
from pathlib import Path
from eligibility.conversation import ConversationOrchestrator
from eligibility.conversation_ledger import Changeset, Decision
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
from eligibility.schema.enums import RankingObjective, TermUnit
from eligibility.schema.search import ContributionPlanPatch, IntentPatch
from eligibility.schema.user_fact import UserFactStore
from eligibility.search.intent import IntentParser
from eligibility.web.app import create_app
from eligibility.web.debug_trace import (
    DebugTraceStore,
    assemble_conversation_turns,
)
from eligibility.web.runtime import WebRuntime
import eligibility.web.debug_trace as debug_trace_module
from tests.v04_helpers import ScriptedIntentPatchGateway, make_intent, verified_fact


LEGACY_CATALOG_ROOT = Path(__file__).resolve().parents[1] / "data" / "product_catalog"


def legacy_product_catalog():
    return load_product_catalog(LEGACY_CATALOG_ROOT)


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


class ProposedFalseOrchestrator:
    def interpret(self, message: str, *, context):
        return ConversationPlan(
            actions=[
                ConversationAction(
                    operation=ConversationOperation.PROPOSE_ACTIVE_QUESTION_ANSWER,
                    answer=False,
                    confirmation_question="그렇다면, 없다고 가정해 볼까요?",
                )
            ]
        )


def _session_creation_intent_parser() -> IntentParser:
    # "1년 정도 월 30만원을 넣을 적금 중 나에게 좋은 상품을 찾아줘." (about a 1-year
    # savings account, 300,000 KRW/month) -- the fixed natural_language_query
    # every `create_session` call in this module sends. Scripted as the
    # deterministic patch an LLM would extract; a `default` (rather than a
    # one-shot queue) lets every test's single session-creation call reuse the
    # same gateway instance.
    gateway = ScriptedIntentPatchGateway(
        default=IntentPatch(
            upsert_product_types=["INSTALLMENT_SAVINGS"],
            contribution_plan_patch=ContributionPlanPatch(
                desired_periodic_amount=Decimal("300000"),
                selected_term_value=12,
                selected_term_unit=TermUnit.MONTH,
            ),
        )
    )
    return IntentParser(gateway)


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
        intent_parser=_session_creation_intent_parser(),
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


def test_debug_history_is_persisted_for_review_in_another_session(tmp_path) -> None:
    history_dir = tmp_path / "debug-history"
    trace_store = DebugTraceStore(
        history_dir=history_dir,
        runtime_metadata={
            "api_key": "must-not-be-written",
            "catalog": {"source_staging_batch": "20260823"},
        },
    )
    web_runtime = WebRuntime(
        service=service(),
        sample_user_id=None,
        user_data_mode="CONVERSATIONAL_INPUT",
        product_count=4,
        llm_enabled=False,
        llm_provider="MOCK",
        llm_model="mock",
        llm_api_family="MOCK",
        debug_trace_store=trace_store,
    )
    client = TestClient(create_app(runtime=web_runtime))

    response = client.post(
        "/api/search-sessions",
        json={
            "user_id": USER_ID,
            "natural_language_query": "매달 30만원씩 1년 동안 모으고 싶어요.",
            "answer_origin": "ANSWER_EXAMPLE",
            "answer_example_id": "PRESEARCH-PRODUCT_TYPE:EXAMPLE-1",
            "as_of": "2026-08-20",
            "subscription_date": "2026-08-20",
        },
    )
    assert response.status_code == 201
    session = response.json()
    session_id = session["search_session_id"]
    session_dir = history_dir / session_id
    assert trace_store.flush(timeout_seconds=5.0) is True

    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    request_rows = [
        json.loads(line)
        for line in (session_dir / "requests.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    snapshot = json.loads(
        (session_dir / "latest-session-snapshot.json").read_text(encoding="utf-8")
    )

    assert metadata["runtime"]["api_key"] == "[REDACTED]"
    assert metadata["runtime"]["catalog"]["source_staging_batch"] == "20260823"
    assert metadata["purpose"].startswith("Raw history for analysis")
    assert len(request_rows) == 1
    assert request_rows[0]["path"] == "/api/search-sessions"
    assert request_rows[0]["search_session_id"] == session_id
    assert request_rows[0]["request_payload"]["answer_origin"] == "ANSWER_EXAMPLE"
    assert request_rows[0]["request_payload"]["answer_example_id"] == (
        "PRESEARCH-PRODUCT_TYPE:EXAMPLE-1"
    )
    assert snapshot["search_session_id"] == session_id
    assert snapshot["snapshot"]["session"]["search_session_id"] == session_id
    assert snapshot["audit_events"]


def test_persisted_debug_history_redacts_free_text_account_numbers(tmp_path) -> None:
    trace_store = DebugTraceStore(history_dir=tmp_path)
    session_id = "SEARCH-0000000000000001"
    with trace_store.request(
        method="POST",
        path=f"/api/search-sessions/{session_id}/messages",
        payload={
            "message": (
                "2026-08-24에 계좌 110-123-456789를 확인했고 "
                "상품은 00101363-1-0001입니다."
            )
        },
        search_session_id=session_id,
    ) as request_id:
        trace_store.record_execution(request_id, state_after={"status": "ACTIVE"})
        trace_store.finish_request(
            request_id,
            status_code=200,
            response_payload={"authorization": "Bearer secret"},
        )
    assert trace_store.flush(timeout_seconds=5.0) is True

    saved = (tmp_path / session_id / "requests.jsonl").read_text(encoding="utf-8")
    assert "110-123-456789" not in saved
    assert "[REDACTED_ACCOUNT]" in saved
    assert "2026-08-24" in saved
    assert "00101363-1-0001" in saved
    assert "Bearer secret" not in saved


def test_persisted_redaction_traverses_shared_sensitive_redactor_once(monkeypatch) -> None:
    original = debug_trace_module.redact_sensitive
    call_count = 0

    def counted(value):
        nonlocal call_count
        call_count += 1
        return original(value)

    monkeypatch.setattr(debug_trace_module, "redact_sensitive", counted)
    redacted = debug_trace_module._redact_persisted_text(
        {"nested": [{"message": "계좌 110-123-456789"}]}
    )

    assert call_count == 1
    assert redacted["nested"][0]["message"] == "계좌 [REDACTED_ACCOUNT]"


def test_debug_history_writes_in_background_and_preserves_job_order(
    tmp_path, monkeypatch
) -> None:
    trace_store = DebugTraceStore(history_dir=tmp_path)
    session_id = "SEARCH-0000000000000003"
    request_started = Event()
    release_request = Event()
    completed_jobs: list[str] = []

    def slow_request_write(row) -> None:
        request_started.set()
        assert release_request.wait(timeout=2.0)
        completed_jobs.append("REQUEST")

    def snapshot_write(payload) -> None:
        completed_jobs.append("SNAPSHOT")

    monkeypatch.setattr(trace_store, "_persist_completed_request", slow_request_write)
    monkeypatch.setattr(trace_store, "_persist_session_snapshot_now", snapshot_write)

    with trace_store.request(
        method="POST",
        path=f"/api/search-sessions/{session_id}/messages",
        payload={"message": "테스트"},
        search_session_id=session_id,
    ) as request_id:
        trace_store.record_execution(request_id, state_after={"status": "ACTIVE"})
        started_at = time.perf_counter()
        trace_store.finish_request(request_id, status_code=200)
        enqueue_elapsed = time.perf_counter() - started_at

    assert enqueue_elapsed < 0.2
    assert request_started.wait(timeout=1.0)
    trace_store.persist_session_snapshot(
        session_id,
        snapshot={"session": {"search_session_id": session_id}},
        audit_events=[],
    )
    status = trace_store.persistence_status()
    assert status["mode"] == "BACKGROUND_QUEUE"
    assert status["pending_write_count"] == 2

    release_request.set()
    assert trace_store.flush(timeout_seconds=2.0) is True
    assert completed_jobs == ["REQUEST", "SNAPSHOT"]
    assert trace_store.persistence_status()["pending_write_count"] == 0


def test_debug_history_reports_total_and_kind_when_bounded_queue_drops_jobs(
    tmp_path, monkeypatch
) -> None:
    trace_store = DebugTraceStore(history_dir=tmp_path)
    writer_started = Event()
    release_writer = Event()

    def blocked_request_write(_row) -> None:
        writer_started.set()
        assert release_writer.wait(timeout=2.0)

    monkeypatch.setattr(trace_store, "_persist_completed_request", blocked_request_write)
    assert trace_store._enqueue_persistence("REQUEST", {"sequence": 0}) is True
    assert writer_started.wait(timeout=1.0)
    accepted = [
        trace_store._enqueue_persistence("REQUEST", {"sequence": sequence})
        for sequence in range(1, 260)
    ]
    status = trace_store.persistence_status()

    assert accepted.count(False) == 3
    assert status["dropped_write_count"] == 3
    assert status["dropped_write_count_by_kind"] == {"REQUEST": 3}
    assert status["last_dropped_at"] is not None
    assert status["error_count"] == 3

    release_writer.set()
    assert trace_store.flush(timeout_seconds=5.0) is True


def test_invalid_session_identifier_is_never_persisted_or_listed(tmp_path) -> None:
    trace_store = DebugTraceStore(history_dir=tmp_path)

    with trace_store.request(
        method="GET",
        path="/api/search-sessions/questions/next",
        payload=None,
        search_session_id="questions",
    ) as request_id:
        trace_store.finish_request(
            request_id,
            status_code=404,
            response_payload={"error": "NOT_FOUND"},
            search_session_id="questions",
        )

    assert not (tmp_path / "questions").exists()
    assert trace_store.archived_session_summaries() == []


def test_missing_valid_session_read_does_not_create_zero_turn_archive(tmp_path) -> None:
    trace_store = DebugTraceStore(history_dir=tmp_path)
    session_id = "SEARCH-0000000000000002"

    with trace_store.request(
        method="GET",
        path=f"/api/search-sessions/{session_id}",
        payload=None,
        search_session_id=session_id,
    ) as request_id:
        trace_store.finish_request(
            request_id,
            status_code=404,
            response_payload={"error": "NOT_FOUND"},
            search_session_id=session_id,
        )

    assert not (tmp_path / session_id).exists()
    assert trace_store.archived_session_summaries() == []


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
    catalog_item = client.get("/api/catalog/products").json()["items"][0]
    assert catalog_item["term_summary"]
    assert catalog_item["contribution_summary"]
    assert catalog_item["maximum_deposit_summary"]
    assert "샘플 사용자 데이터 연결" not in page.text
    assert "대화로 조건 확인" not in page.text
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert 'id="answer-examples-dock"' in page.text
    assert 'id="search-progress-fill"' in page.text
    assert "AI는 잘 이해했나?" in page.text
    assert "딱금리" in page.text
    assert '<div class="brand-mark" aria-hidden="true">%</div>' in page.text
    assert "내 조건에 딱 맞는 금융상품" in page.text
    assert 'class="message assistant-message intro-message"' not in page.text
    assert "원하는 기간과 납입금액, 중요하게 생각하는 조건" not in page.text
    assert 'id="starter-prompts"' not in page.text
    assert "월 30만원 · 1년" not in page.text
    assert "월 20만원 · 간편하게" not in page.text
    assert "실현 가능 금리 우선" not in page.text
    assert "균형 기준" not in page.text
    assert "금리순" not in page.text
    assert "이자금순" not in page.text
    assert 'id="ranking-toggle"' not in page.text
    assert 'id="understood-content" hidden' in page.text
    assert 'id="understood-toggle"' in page.text
    assert 'id="product-search-input"' in page.text
    assert "상품명 검색" in page.text
    assert 'id="institution-picker-button"' in page.text
    assert 'id="selected-institution-strip"' in page.text
    assert 'id="first-sector-filter"' in page.text
    assert 'id="savings-bank-filter"' in page.text
    assert 'class="institution-quick-filters"' in page.text
    assert 'id="institution-picker-modal"' in page.text
    assert 'id="institution-picker-body"' in page.text
    assert "처음부터" in page.text
    assert 'id="refresh-button"' not in page.text
    assert 'id="product-family-filter"' in page.text
    assert 'placeholder="AI에게 질문하세요!"' in page.text
    assert "저장된 사용자 데이터 없이" not in page.text
    assert "아직 확인하지 않은 조건이 있어요" not in page.text
    assert 'id="question-dock"' not in page.text
    assert 'id="detail-product-link"' in page.text
    assert 'id="detail-link-caption"' in page.text
    assert 'id="detail-institution-logo"' in page.text

    initial_question = client.get("/api/pre-search/initial-question")
    assert initial_question.status_code == 200
    assert initial_question.json() == {
        "question_id": "PRESEARCH-PRODUCT_TYPE",
        "question_kind": "PRE_SEARCH_PROFILE",
        "question": (
            "돈을 정기적으로 모으고 싶으신가요, 목돈을 일정 기간 맡기고 "
            "싶으신가요, 아니면 필요할 때 넣고 뺄 수 있는 계좌를 찾으시나요?"
        ),
        "question_stage": "PRE_SEARCH",
        "pre_search_key": "PRODUCT_TYPE",
        "answer_mode": "FREE_TEXT",
        "confirmation_required": False,
        "answer_examples": [
            "정기적으로 돈을 모으고 싶어요.",
            "목돈을 일정 기간 맡기고 싶어요.",
            "필요할 때 넣고 뺄 수 있는 계좌를 찾고 있어요.",
        ],
        "product_context": None,
    }


def test_private_debug_ui_exposes_turn_state_and_engine_boundaries() -> None:
    client = TestClient(create_app(service=service()))

    page = client.get("/debug")
    assert page.status_code == 200
    assert "AI → 멀티턴 → Engine 흐름 보기" not in page.text
    assert "PRIVATE RUNTIME INSPECTOR" not in page.text
    assert "답변 해석, 조회·상태 동기화, 질문 문구 생성" not in page.text
    assert "제출 화면 열기" in page.text
    assert "핵심 보기" not in page.text
    assert 'id="summary-strip"' in page.text
    assert page.text.index('id="summary-strip"') < page.text.index('class="debug-layout"')
    assert "실제 Prompt · JSON Schema · 원문 JSON · 전체 Audit" in page.text
    script = client.get("/static/debug.js?v=20260824-13")
    assert script.status_code == 200
    assert "사전 질문 답변 해석" in script.text
    assert "상품별 질문 문구 생성" in script.text
    assert "LLM 호출 없음" in script.text
    assert "선택한 요청 시점의 상태" in script.text
    assert "고정 예시가 아니라" in script.text
    assert "현재 기억 ·" in script.text
    assert "당시 사용자에게 이어서 한 질문" in script.text
    assert "AI가 이해한 조건" in script.text
    assert "이 Turn의 Prompt와 실제 JSON 상세 보기" in script.text
    assert "실제 사용자 화면의 질문과 응답" in script.text
    assert "APP · deterministic 질문" in script.text
    assert "LLM · 생성 질문" in script.text
    assert "LLM · 직접 응답" in script.text
    assert "사용자 · 자연어 입력" in script.text
    assert "기존 활성 질문 유지" in script.text
    assert "core-session-id" in page.text
    assert "저장 유실" in script.text

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
    assert body["source"] == "LIVE"
    assert body["read_only"] is False
    assert body["turns"][0]["kind"] == "SESSION_START"
    assert body["turns"][0]["request_ids"] == [
        body["requests"][0]["request_id"]
    ]
    assert body["requests"][0]["path"] == "/api/search-sessions"
    assert body["requests"][0]["request_payload"]["natural_language_query"]
    assert body["requests"][0]["state_before"] is None
    assert body["requests"][0]["state_after"]["search_session_id"] == session_id
    assert body["requests"][0]["state_after"]["engine_checkpoint"][
        "retrieved_candidate_count"
    ] == 4
    assert body["requests"][0]["application_events"]
    assert body["snapshot"]["multi_turn"]["working_note"]
    assert body["snapshot"]["multi_turn"]["active_question"]
    assert body["snapshot"]["engine"]["retrieval"]["input"]["intent"]
    assert body["snapshot"]["engine"]["retrieval"]["output"]["filter_decisions"]
    assert body["snapshot"]["engine"]["evaluation"]["output"]
    assert body["snapshot"]["engine"]["ranking"]["output"]
    assert (
        body["snapshot"]["engine"]["orchestration_boundary"][
            "llm_calls_engine_directly"
        ]
        is False
    )
    assert body["snapshot"]["audit_timeline"]
    assert body["snapshot"]["audit_timeline"][0]["payload_deferred"] is True

    audit = client.get(body["snapshot"]["audit_timeline_endpoint"])
    assert audit.status_code == 200
    assert audit.json()["events"][0]["payload"] is not None

    evaluation_summary = next(
        iter(body["snapshot"]["engine"]["evaluation"]["output"].values())
    )
    evaluation = client.get(evaluation_summary["detail_endpoint"])
    assert evaluation.status_code == 200
    assert evaluation.json()["product_evaluation"]["product_name"]


def test_debug_turns_group_followup_reads_under_user_writes() -> None:
    requests = [
        {
            "request_id": "WEB-start",
            "method": "POST",
            "path": "/api/search-sessions",
            "request_payload": {"natural_language_query": "1년 예금 찾아줘"},
            "started_at": "2026-08-24T00:00:00+00:00",
            "llm_calls": [{}],
            "application_events": [{"event_type": "RANKING_CALCULATED"}],
        },
        {
            "request_id": "WEB-question-get",
            "method": "GET",
            "path": "/api/search-sessions/SEARCH-1/questions/next",
            "llm_calls": [],
            "application_events": [],
        },
        {
            "request_id": "WEB-message",
            "method": "POST",
            "path": "/api/search-sessions/SEARCH-1/messages",
            "request_payload": {"message": "1년 동안 유지할 수 있어요"},
            "started_at": "2026-08-24T00:01:00+00:00",
            "llm_calls": [{}, {}],
            "application_events": [{"event_type": "QUESTION_SELECTED"}],
        },
        {
            "request_id": "WEB-state-get",
            "method": "GET",
            "path": "/api/search-sessions/SEARCH-1/state",
            "llm_calls": [],
            "application_events": [],
        },
    ]

    turns = assemble_conversation_turns(requests)

    assert len(turns) == 2
    assert turns[0]["user_input"] == "1년 예금 찾아줘"
    assert turns[0]["request_ids"] == ["WEB-start", "WEB-question-get"]
    assert turns[1]["kind"] == "NATURAL_LANGUAGE_MESSAGE"
    assert turns[1]["request_ids"] == ["WEB-message", "WEB-state-get"]
    assert turns[1]["llm_call_count"] == 2


def test_archived_debug_session_remains_visible_after_close_and_restart(
    tmp_path,
) -> None:
    history_dir = tmp_path / "debug-history"

    def runtime_with(trace_store: DebugTraceStore) -> WebRuntime:
        return WebRuntime(
            service=service(),
            sample_user_id=None,
            user_data_mode="CONVERSATIONAL_INPUT",
            product_count=4,
            llm_enabled=False,
            llm_provider="MOCK",
            llm_model="mock",
            llm_api_family="MOCK",
            debug_trace_store=trace_store,
        )

    first_trace_store = DebugTraceStore(history_dir=history_dir)
    first_client = TestClient(
        create_app(runtime=runtime_with(first_trace_store))
    )
    session_id = create_session(first_client)["search_session_id"]
    assert first_trace_store.flush(timeout_seconds=5.0) is True
    closed = first_client.delete(f"/api/search-sessions/{session_id}")
    assert closed.status_code == 204
    assert first_trace_store.flush(timeout_seconds=5.0) is True

    archived_list = first_client.get("/api/debug/sessions").json()["sessions"]
    archived = next(
        item for item in archived_list if item["search_session_id"] == session_id
    )
    assert archived["source"] == "ARCHIVED"
    assert archived["read_only"] is True
    assert archived["resumable"] is False
    assert archived["turn_count"] == 1

    second_client = TestClient(
        create_app(
            runtime=runtime_with(DebugTraceStore(history_dir=history_dir))
        )
    )
    restarted_list = second_client.get("/api/debug/sessions").json()["sessions"]
    assert any(
        item["search_session_id"] == session_id
        and item["source"] == "ARCHIVED"
        for item in restarted_list
    )

    detail = second_client.get(f"/api/debug/sessions/{session_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["source"] == "ARCHIVED"
    assert body["read_only"] is True
    assert body["resumable"] is False
    assert body["turns"][0]["kind"] == "SESSION_START"
    assert body["snapshot"]["session"]["search_session_id"] == session_id

    audit = second_client.get(f"/api/debug/sessions/{session_id}/audit")
    assert audit.status_code == 200
    assert audit.json()["events"]


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
        "estimated_total_question_count": 2,
        "estimate_is_dynamic": True,
    }

    question_response = client.get(f"/api/search-sessions/{session_id}/questions/next")
    assert question_response.status_code == 200
    question = question_response.json()
    assert question["question_kind"] == "FINANCIAL_FACT"
    assert question["request"]["fact_type"] == "SPECIAL_RATE_COUPON_VALID"

    rec_response = client.get(f"/api/search-sessions/{session_id}/recommendations")
    assert rec_response.status_code == 200
    rec = rec_response.json()
    assert len(rec["top_products"]) == 4
    assert rec["recommendation_status"] == "PROVISIONAL"
    assert rec["provisional_candidates"] == rec["top_products"]
    assert rec["confirmed_top_products"] == []
    assert rec["top_products"][0]["product_name"] == "청년 처음적금"
    assert rec["top_products"][0]["estimated_after_tax_interest"] is not None
    assert rec["top_products"][0]["estimated_pre_tax_interest"] is not None

    answer = client.post(
        f"/api/search-sessions/{session_id}/answers",
        json={"question_id": question["question_id"], "answer": False},
    )
    assert answer.status_code == 200

    snapshot_after = client.get(f"/api/search-sessions/{session_id}/state").json()
    coupon_fact = next(
        item
        for item in snapshot_after["user_declarations"]
        if item["fact_type"] == "SPECIAL_RATE_COUPON_VALID"
    )
    assert coupon_fact["value"] is False
    assert snapshot_after["search_progress"]["recalculation_round"] == 2
    assert snapshot_after["search_progress"]["viable_product_count"] <= 4

    rec_after = client.get(f"/api/search-sessions/{session_id}/recommendations").json()
    assert rec_after["ranking_objective"] == "MAX_REALIZABLE_RATE"

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
    assert detail_body["search_facts"]["product_family"] == "INSTALLMENT_SAVINGS"
    assert detail_body["rate_evaluation"]["calculation_mode"] == "INSTALLMENT_CASHFLOW"
    assert detail_body["rate_evaluation"]["realizable_rate"] == detail_body["realizable_rate"]


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


def test_state_after_conversational_message_is_json_serializable() -> None:
    application_service = service(conversational=True)
    client = TestClient(create_app(service=application_service))
    session = create_session(client)
    session_id = session["search_session_id"]
    application_service._runtime(session_id).decision_ledger.commit(
        Changeset(
            changeset_id="CHG-WEB-SERIALIZATION",
            turn_id=1,
            decisions=(
                Decision(
                    decision_id="DEC-WEB-SERIALIZATION",
                    changeset_id="CHG-WEB-SERIALIZATION",
                    turn_id=1,
                    operation="UPDATE_SEARCH_INTENT",
                    target="intent",
                    after={
                        "created_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
                        "amount": Decimal("3000000"),
                    },
                ),
            ),
        )
    )

    response = client.get(f"/api/search-sessions/{session_id}/state")

    assert response.status_code == 200
    body = response.json()
    assert body["decision_ledger"]
    assert isinstance(body["decision_ledger"][0]["after"]["created_at"], str)
    assert body["decision_ledger"][0]["after"]["amount"] == "3000000"


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


def test_tentative_fact_is_not_committed_until_user_confirms_assumption() -> None:
    app_service = ApplicationService(
        [shinhan_youth_first_product()],
        user_fact_stores={},
        conversation_orchestrator=ProposedFalseOrchestrator(),
    )
    session = app_service.create_search_session(
        user_id="ASSUMPTION-USER",
        intent=make_intent(user_id="ASSUMPTION-USER"),
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
    )
    original = app_service.get_next_question(session.search_session_id)
    assert original is not None
    facts_before = list(app_service._runtime(session.search_session_id).fact_store.facts)

    proposed = app_service.handle_user_message(
        session.search_session_id,
        message="아닌 것 같아요.",
    )

    assert proposed.next_question is not None
    assert proposed.next_question.question == "그렇다면, 없다고 가정해 볼까요?"
    assert list(app_service._runtime(session.search_session_id).fact_store.facts) == facts_before

    app_service.submit_user_answer(
        session.search_session_id,
        question_id=proposed.next_question.question_id,
        answer=True,
    )
    runtime = app_service._runtime(session.search_session_id)
    assert original.question_id in runtime.session.answered_question_ids
    assert runtime.pending_assumption_answer is None


def test_rejecting_assumption_restores_original_question_without_opposite_fact() -> None:
    app_service = ApplicationService(
        [shinhan_youth_first_product()],
        user_fact_stores={},
    )
    session = app_service.create_search_session(
        user_id="ASSUMPTION-REJECT-USER",
        intent=make_intent(user_id="ASSUMPTION-REJECT-USER"),
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
    )
    original = app_service.get_next_question(session.search_session_id)
    assert original is not None
    app_service.propose_active_question_answer(
        session.search_session_id,
        proposed_answer=False,
        confirmation_question="그렇다면, 없다고 가정해 볼까요?",
    )
    confirmation = app_service.get_next_question(session.search_session_id)
    assert confirmation is not None

    app_service.submit_user_answer(
        session.search_session_id,
        question_id=confirmation.question_id,
        answer=False,
    )

    restored = app_service.get_next_question(session.search_session_id)
    assert restored is not None and restored.question_id == original.question_id
    assert original.question_id not in app_service._runtime(
        session.search_session_id
    ).session.answered_question_ids


def test_salary_question_is_treated_as_product_specific_verification() -> None:
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
    assert question.question_stage is None
    assert question.request is not None
    assert question.request.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
    assert question.answer_mode == "FREE_TEXT"
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
    assert card_question.answer_mode == "FREE_TEXT"
    assert card_question.request.fact_type == "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE"
    assert "카드대금" in card_question.question
    assert "어느 은행" in card_question.question
    assert "신한은행" in (card_question.product_context or "")

    app_service.submit_user_answer(
        session.search_session_id,
        question_id=card_question.question_id,
        answer=True,
    )
    holding_question = None
    for _ in range(8):
        next_question = app_service.get_next_question(session.search_session_id)
        assert next_question is not None
        if (
            next_question.request is not None
            and next_question.request.fact_type == "SHINHAN_CARD_HELD"
        ):
            holding_question = next_question
            break
        app_service.submit_user_answer(
            session.search_session_id,
            question_id=next_question.question_id,
            answer=False,
        )

    assert holding_question is not None
    assert holding_question.answer_mode == "BINARY"
    assert holding_question.question == (
        "본인 명의의 신한카드(신용카드 또는 체크카드)를 현재 가지고 계신가요?"
    )
    assert "카드 보유만으로 우대가 적용되는 것은 아닙니다" in (
        holding_question.explanation or ""
    )
    assert "1원 이상 결제한 달이 6개월 이상" in (
        holding_question.explanation or ""
    )
    assert holding_question.explanation_details["source"]["section"] == (
        "[2] 신한카드 결제 우대"
    )
    assert holding_question.explanation_details["reference_links"] == [
        {
            "kind": "PRODUCT_CONDITION",
            "label": "자세히 보기",
            "url": holding_question.explanation_details["source"]["source_url"],
        }
    ]


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
    assert state["pre_search_typed_facts"] == [
        {"fact_type": "CURRENT_SALARY_BANK", "value": "KB국민은행"}
    ]


def test_daily_manual_deposit_questions_include_cadence_and_contract_term() -> None:
    products = {
        product.product_id: product for product in legacy_product_catalog()
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
        product.product_id: product for product in legacy_product_catalog()
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


def test_ibk_social_care_eligibility_is_datafied_before_asking_user() -> None:
    product = next(
        item
        for item in legacy_product_catalog()
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

    assert question is not None and question.request is not None
    assert question.request.fact_type == "IBK_LOVE_SHARING_ELIGIBLE"
    assert question.confirmation_required is True
    assert "기초생활수급자" in question.question
    assert "차상위계층 이하 만 65세 이상" in question.question
    assert "개인사업자는 가입할 수 없어요" in question.question

    app_service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer="UNKNOWN",
    )
    retained = app_service.get_next_question(session.search_session_id)
    assert retained is not None
    assert retained.question_id == question.question_id
    assert question.question_id not in app_service._runtime(
        session.search_session_id
    ).session.answered_question_ids
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
