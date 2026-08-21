from __future__ import annotations

import json
from collections import deque
from typing import Any

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from eligibility.llm import LLMGateway, LLMSettings, OpenAIResponsesAdapter
from eligibility.llm.json_schema import openai_strict_json_schema
from eligibility.llm.models import (
    LLMMessage,
    LLMPurpose,
    LLMRole,
    StructuredGenerationRequest,
)
from eligibility.llm.transport import HTTPResponse
from eligibility.conversation import ConversationOrchestrator
from eligibility.schema.conversation import ConversationPlan
from eligibility.schema.search import IntentPatch
from eligibility.search.intent import IntentParser
from eligibility.web.app import create_app
from eligibility.web.runtime import build_web_runtime
from eligibility.web.debug_trace import DebugTraceStore


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: int
    note: str | None = None


class FakeTransport:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        timeout_seconds: float,
    ) -> HTTPResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "timeout_seconds": timeout_seconds,
            }
        )
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        purpose=LLMPurpose.CONVERSATION_ORCHESTRATION,
        messages=[LLMMessage(role=LLMRole.USER, content="route this")],
        prompt_template_id="test",
        prompt_template_version="1",
        response_schema_version="1",
        response_schema_name="Answer",
        response_json_schema=Answer.model_json_schema(),
        temperature=0,
        timeout_seconds=2,
    )


def responses_success(text: str = '{"answer":7,"note":null}') -> HTTPResponse:
    body = {
        "id": "resp-openai-1",
        "status": "completed",
        "model": "gpt-5.6-terra",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": text, "annotations": []}
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    return HTTPResponse(200, json.dumps(body).encode(), {})


def test_openai_settings_use_responses_defaults_and_openai_key_fallback() -> None:
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "OPENAI",
            "OPENAI_API_KEY": "secret",
        }
    )

    assert settings.base_url == "https://api.openai.com/v1"
    assert settings.model == "gpt-5.6-luna"
    assert settings.api_key == "secret"
    assert settings.reasoning_effort == "low"
    assert isinstance(LLMGateway.from_settings(settings).client, OpenAIResponsesAdapter)


def test_openai_responses_structured_payload_and_parse() -> None:
    transport = FakeTransport([responses_success()])
    adapter = OpenAIResponsesAdapter(
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-terra",
        api_key="secret",
        max_retries=0,
        reasoning_effort="low",
        transport=transport,
    )

    response = adapter.generate_structured(request(), Answer)

    assert response.data.answer == 7
    assert response.data.note is None
    call = transport.calls[0]
    assert call["url"] == "https://api.openai.com/v1/responses"
    assert call["headers"]["Authorization"] == "Bearer secret"
    payload = call["json_body"]
    assert payload["store"] is False
    assert "temperature" not in payload
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    schema = payload["text"]["format"]["schema"]
    assert schema["required"] == ["answer", "note"]
    assert schema["additionalProperties"] is False
    assert "default" not in json.dumps(schema)


def test_debug_trace_records_exact_messages_schema_and_output() -> None:
    trace = DebugTraceStore()
    settings = LLMSettings.from_env({"LLM_PROVIDER": "MOCK"})
    gateway = LLMGateway.from_settings(
        settings,
        mock_responses={
            LLMPurpose.CONVERSATION_ORCHESTRATION: {
                "actions": [{"operation": "NO_OP"}]
            }
        },
        debug_observer=trace.observer(),
    )

    with trace.request(
        method="POST",
        path="/api/search-sessions/SEARCH-1/messages",
        payload={"message": "그게 뭐예요?"},
        search_session_id="SEARCH-1",
    ) as request_id:
        response = gateway.generate_structured(
            LLMPurpose.CONVERSATION_ORCHESTRATION,
            "CURRENT_CONTEXT: {}\nUSER_MESSAGE: 그게 뭐예요?",
            ConversationPlan,
        )
        trace.finish_request(
            request_id,
            status_code=200,
            response_payload=response.data.model_dump(mode="json"),
        )

    captured = trace.requests_for_session("SEARCH-1")[0]["llm_calls"][0]
    assert captured["request"]["messages"] == [
        {
            "role": "user",
            "content": "CURRENT_CONTEXT: {}\nUSER_MESSAGE: 그게 뭐예요?",
        }
    ]
    assert captured["request"]["response_schema_name"] == "ConversationPlan"
    assert captured["request"]["response_json_schema"]
    assert captured["output"]["actions"][0]["operation"] == "NO_OP"


def test_runtime_intent_and_conversation_calls_separate_system_and_user_messages() -> None:
    settings = LLMSettings.from_env({"LLM_PROVIDER": "MOCK"})
    gateway = LLMGateway.from_settings(
        settings,
        mock_responses={
            LLMPurpose.INTENT_PARSING: IntentPatch(
                upsert_product_types=["INSTALLMENT_SAVINGS"]
            ),
            LLMPurpose.CONVERSATION_ORCHESTRATION: {
                "actions": [{"operation": "NO_OP"}]
            },
        },
    )

    intent = IntentParser(gateway).parse("적금을 찾아줘", user_id="SYSTEM-PROMPT-USER")
    ConversationOrchestrator(gateway).interpret("그대로 보여줘", context={})

    intent_request, conversation_request = gateway.client.call_history
    assert intent_request.response_schema_name == "IntentPatch"
    assert [message.role.value for message in intent_request.messages] == ["system", "user"]
    assert "구조화 추출기" in intent_request.messages[0].content
    assert "CURRENT_SEARCH_INTENT" in intent_request.messages[1].content
    assert intent.capabilities == []
    assert intent.hard_constraints == []

    assert conversation_request.response_schema_name == "ConversationPlan"
    assert [message.role.value for message in conversation_request.messages] == [
        "system",
        "user",
    ]
    assert "구조화 라우터" in conversation_request.messages[0].content
    assert "CURRENT_CONTEXT" in conversation_request.messages[1].content


def test_openai_responses_gpt56_luna_omits_unsupported_temperature() -> None:
    transport = FakeTransport([responses_success()])
    adapter = OpenAIResponsesAdapter(
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-luna",
        api_key="secret",
        max_retries=0,
        reasoning_effort="low",
        transport=transport,
    )

    adapter.generate_structured(request(), Answer)

    payload = transport.calls[0]["json_body"]
    assert "temperature" not in payload
    assert payload["reasoning"] == {"effort": "low"}


def test_openai_responses_non_gpt56_model_preserves_temperature() -> None:
    transport = FakeTransport([responses_success()])
    adapter = OpenAIResponsesAdapter(
        base_url="https://api.openai.com/v1",
        model="gpt-4.1",
        api_key="secret",
        max_retries=0,
        reasoning_effort=None,
        transport=transport,
    )

    adapter.generate_structured(request(), Answer)

    payload = transport.calls[0]["json_body"]
    assert payload["temperature"] == 0
    assert "reasoning" not in payload


def test_conversation_plan_schema_has_no_untyped_any_and_normalizes_for_strict_output() -> None:
    schema = openai_strict_json_schema(ConversationPlan.model_json_schema())

    empty_nodes: list[str] = []

    def walk(node: Any, path: str = "root") -> None:
        if isinstance(node, dict):
            if node == {}:
                empty_nodes.append(path)
            if "properties" in node:
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node["properties"].keys())
            assert "default" not in node
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(schema)
    assert empty_nodes == []


def test_conversation_plan_schema_removes_openai_unsupported_decimal_regex() -> None:
    schema = openai_strict_json_schema(ConversationPlan.model_json_schema())
    patterns: list[str] = []

    def collect_patterns(node: Any) -> None:
        if isinstance(node, dict):
            pattern = node.get("pattern")
            if isinstance(pattern, str):
                patterns.append(pattern)
            for value in node.values():
                collect_patterns(value)
        elif isinstance(node, list):
            for value in node:
                collect_patterns(value)

    collect_patterns(schema)
    assert all(
        token not in pattern
        for pattern in patterns
        for token in ("(?=", "(?!", "(?<=", "(?<!")
    )

    amount_schema = schema["$defs"]["ContributionPlanPatch"]["properties"][
        "desired_periodic_amount"
    ]
    assert {branch.get("type") for branch in amount_schema["anyOf"]} == {
        "number",
        "null",
    }


def test_openai_missing_key_keeps_web_runtime_safe_and_reports_configuration(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    runtime = build_web_runtime()
    assert runtime.llm_enabled is False
    assert runtime.llm_provider == "OPENAI"
    assert runtime.llm_api_family == "RESPONSES"
    assert "requires" in (runtime.llm_configuration_error or "")

    client = TestClient(create_app(runtime=runtime))
    health = client.get("/api/llm/health")
    assert health.status_code == 200
    body = health.json()
    assert body["configured"] is False
    assert body["healthy"] is False
    assert body["provider"] == "OPENAI"


def test_native_openai_responses_adapter_drives_conversation_orchestrator_end_to_end() -> None:
    from eligibility.application_service import ApplicationService
    from eligibility.conversation import ConversationOrchestrator
    from eligibility.fixtures.hana_run import hana_run_product
    from eligibility.fixtures.ibk_parent_benefit import ibk_parent_benefit_product
    from eligibility.fixtures.kakao_26_week import kakao_26_week_product
    from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
    from eligibility.fixtures.user_001 import USER_ID, user_001

    plan = json.dumps(
        {
            "actions": [
                {
                    "operation": "SHOW_CURRENT_RESULTS",
                    "intent_patch": None,
                    "product_id": None,
                    "field": None,
                    "new_value": None,
                    "request_reference": None,
                    "fact_type": None,
                    "answer": None,
                    "excluded": None,
                    "rationale": "사용자가 현재 결과를 요청함",
                }
            ]
        },
        ensure_ascii=False,
    )
    transport = FakeTransport([responses_success(plan)])
    gateway = LLMGateway(
        OpenAIResponsesAdapter(
            base_url="https://api.openai.com/v1",
            model="gpt-5.6-terra",
            api_key="secret",
            max_retries=0,
            transport=transport,
        )
    )
    service = ApplicationService(
        [
            shinhan_youth_first_product(),
            kakao_26_week_product(),
            ibk_parent_benefit_product(),
            hana_run_product(),
        ],
        user_fact_stores={USER_ID: user_001()},
        conversation_orchestrator=ConversationOrchestrator(gateway),
    )
    client = TestClient(create_app(service=service))
    session = client.post(
        "/api/search-sessions",
        json={
            "user_id": USER_ID,
            "natural_language_query": "1년 정도 월 30만원 적금 추천해줘",
            "as_of": "2026-08-20",
            "subscription_date": "2026-08-20",
        },
    ).json()

    result = client.post(
        f"/api/search-sessions/{session['search_session_id']}/messages",
        json={"message": "질문은 그만하고 지금 결과 보여줘."},
    )

    assert result.status_code == 200
    assert result.json()["current_results_requested"] is True
    assert transport.calls[0]["url"].endswith("/responses")
    assert transport.calls[0]["json_body"]["text"]["format"]["strict"] is True
