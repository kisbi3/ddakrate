#!/usr/bin/env python3
"""Run the documented Web/OpenAI acceptance scenario against a live server."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any


def request_json(
    base_url: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int, float]:
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{path}: HTTP {exc.code}: {detail}") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return json.loads(raw), len(raw), elapsed_ms


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:57949")
    parser.add_argument("--as-of", default="2026-08-21")
    args = parser.parse_args()

    health, _, health_ms = request_json(args.base_url, "/api/llm/health")
    assert health["configured"] is True and health["healthy"] is True, health
    assert health["provider"] == "OPENAI", health

    session, _, create_ms = request_json(
        args.base_url,
        "/api/search-sessions",
        payload={
            "user_id": "OPENAI_ACCEPTANCE_USER",
            "natural_language_query": "1년 동안 월 30만원씩 적금하고 싶어.",
            "as_of": args.as_of,
            "subscription_date": args.as_of,
        },
    )
    session_id = session["search_session_id"]

    catalog, _, _ = request_json(args.base_url, "/api/catalog/products")
    assert catalog["count"] == 207, catalog

    def send(message: str) -> tuple[dict[str, Any], float]:
        result, _, elapsed = request_json(
            args.base_url,
            f"/api/search-sessions/{session_id}/messages",
            payload={"message": message},
        )
        return result, elapsed

    question, _, _ = request_json(
        args.base_url,
        f"/api/search-sessions/{session_id}/questions/next",
    )

    pre_search_answers = {
        "PRODUCT_TYPE": "적금 상품을 찾고 있어요.",
        "APPLICATION_CAPACITY": "개인 명의로 가입할 상품을 찾고 있어요.",
        "BIRTH_DATE": "1990년 1월 1일이에요.",
        "CONTRIBUTION_AND_TERM": "월 30만원씩 1년 동안 모으고 싶어요.",
        "PROTECTION_AND_CMA_SCOPE": "예금자보호되는 상품만 보고 싶어요.",
        "INSTITUTION_SCOPE": "저축은행을 포함해서 어디든 괜찮아요.",
        "COMMON_BENEFIT_WILLINGNESS": (
            "새 은행 거래는 괜찮고 급여계좌 변경은 혜택에 따라 가능하지만, "
            "카드는 새로 만들거나 쓰고 싶지 않아요."
        ),
    }
    pre_search_keys: list[str] = []
    pre_search_latencies: dict[str, float] = {}
    while question is not None and question.get("question_kind") == "PRE_SEARCH_PROFILE":
        key = question.get("pre_search_key")
        assert key in pre_search_answers, question
        pre_search_keys.append(key)
        turn, elapsed = send(pre_search_answers[key])
        pre_search_latencies[key] = elapsed
        question = turn.get("next_question")

    assert {
        "APPLICATION_CAPACITY",
        "BIRTH_DATE",
        "INSTITUTION_SCOPE",
        "COMMON_BENEFIT_WILLINGNESS",
    }.issubset(pre_search_keys), pre_search_keys

    recommendations, _, _ = request_json(
        args.base_url,
        f"/api/search-sessions/{session_id}/recommendations",
    )
    top = recommendations["top_products"]
    assert len(top) == 5, top
    assert recommendations["recommendation_status"] == "PROVISIONAL"
    assert all(item["requested_periodic_amount"] == "300000" for item in top)
    assert all(item["planned_periodic_amount"] == "300000" for item in top)
    assert all(item["monthly_equivalent_amount"] == "300000" for item in top)
    assert all(item["amount_match_status"] == "EXACT" for item in top)
    assert all(item["term_match_status"] in {"EXACT", "SELECTABLE_EXACT"} for item in top)
    assert all(item["estimated_total_principal"] == "3600000" for item in top)

    sorted_turn, sort_ms = send("금리순으로 보여줘.")
    # The catalog is already ranked by realizable rate. The live LLM may
    # therefore either keep the current results or explicitly show them again.
    assert tuple(sorted_turn["operations_executed"]) in {
        ("SHOW_CURRENT_RESULTS",),
        ("NO_OP",),
    }
    state, _, _ = request_json(
        args.base_url,
        f"/api/search-sessions/{session_id}/state",
    )
    assert state["intent"]["ranking_objective"] == "MAX_REALIZABLE_RATE"
    profile = {item["question_key"]: item for item in state["pre_search_profile"]}
    assert profile["COMMON_BENEFIT_WILLINGNESS"]["answer_status"] == "ANSWERED"
    assert profile["COMMON_BENEFIT_WILLINGNESS"]["value"]["action_preferences"] == {
        "FIRST_TRANSACTION_BENEFIT": "WILLING",
        "SALARY_BENEFIT": "CONDITIONAL",
        "CARD_BENEFIT": "UNWILLING",
    }
    assert state["search_progress"]["completed_question_count"] >= 6
    assert state["search_progress"]["estimated_total_question_count"] >= 6

    bundle, bundle_bytes, _ = request_json(
        args.base_url,
        f"/api/debug/sessions/{session_id}",
    )
    assert bundle_bytes < 2_000_000, bundle_bytes
    sort_trace = next(
        row
        for row in reversed(bundle["requests"])
        if (
            (row.get("request_payload") or {}).get("message")
            == "금리순으로 보여줘."
        )
    )
    common_trace = next(
        row
        for row in reversed(bundle["requests"])
        if (row.get("request_payload") or {}).get("message", "").startswith("새 은행 거래")
    )
    orchestration_calls = [
        call
        for call in common_trace["llm_calls"]
        if call["purpose"] == "CONVERSATION_ORCHESTRATION"
    ]
    assert len(orchestration_calls) == 1
    assert orchestration_calls[0]["request"]["response_schema_name"] == "PreSearchAnswerPlan"
    assert orchestration_calls[0]["request"]["metadata"]["pre_search_answer"] is True
    # The live catalog now includes 207 ON_SALE products and the pre-search
    # context is correspondingly larger than the former 155-product baseline.
    assert orchestration_calls[0]["token_usage"]["prompt_tokens"] < 4_000
    assert not any(call["purpose"] == "QUESTION_GENERATION" for call in common_trace["llm_calls"])
    for call in (
        call
        for row in bundle["requests"]
        for call in row.get("llm_calls", [])
    ):
        schema_ref = call["request"]["transport_schema_ref"]
        schema_text = json.dumps(bundle["schemas"][schema_ref], ensure_ascii=False)
        assert "(?=" not in schema_text and "(?!" not in schema_text
        transport = call["request"]["transport_payload"]
        assert transport["store"] is False
        assert transport["text"]["format"]["strict"] is True

    print(
        json.dumps(
            {
                "status": "PASS",
                "session_id": session_id,
                "llm_health_ms": health_ms,
                "initial_search_ms": create_ms,
                "deterministic_sort_ms": sort_ms,
                "pre_search_keys": pre_search_keys,
                "pre_search_latencies_ms": pre_search_latencies,
                "pre_search_answer_prompt_tokens": orchestration_calls[0]["token_usage"][
                    "prompt_tokens"
                ],
                "debug_bundle_bytes": bundle_bytes,
                "catalog_product_count": catalog["count"],
                "top_5_amount_match": "EXACT",
                "objective": state["intent"]["ranking_objective"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
