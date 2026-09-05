"""Original runtime integration tests; only the external LLM is substituted."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

from eligibility.application_service import ApplicationService
from eligibility.audit import canonical_hash
from eligibility.conversation import ConversationOrchestrator
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.enums import (
    ComparisonOperator, EvaluationStatus, FactSemanticType, RankingObjective,
    PreSearchAnswerStatus, PreferenceValue,
)
from eligibility.schema.application_input import Preference
from eligibility.schema.product import NormalizedProductData, ProductDefinition
from eligibility.schema.search import PreSearchProfileEntry
from eligibility.schema.semantic import ClauseInterpretation, SemanticCompilation, SemanticExpression, ChildrenAnswer
from eligibility.search.pre_search import INSTITUTION_PRODUCT_HOLDING_HISTORY
from eligibility.search.semantic import (
    SemanticConditionCompiler, compact_semantic_memos, existing_question_family,
    first_transaction_holding_rules_out, has_unlinked_alternative_path,
    overlay_product, product_packet, validate_compilation, evaluate_expression,
    normalize_answer, user_inputs,
)
from eligibility.web.app import create_app
from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, USER_ID, base_store, make_intent, make_product


COUNT_TEXT = "자녀 2명 이상인 고객에게 우대금리 1%p"
AGE_TEXT = "가입시점 만 12세 이하 자녀가 있는 고객에게 우대금리 1%p"


def count_expr(text=COUNT_TEXT, expected=2):
    return SemanticExpression(op="CHILD_COUNT", source_quote=text, comparator="GTE", expected=expected)


def age_expr(text=AGE_TEXT, maximum=12):
    return SemanticExpression(op="CHILD_EXISTS", source_quote=text, child_filter={"max_age": maximum})


def semantic_product(pid, text=COUNT_TEXT, *, official=False, reward="1", base="2", unsupported=False):
    p = make_product(pid, base_rate=base)
    cap = Decimal(reward)
    raw = {
        "rule_id": f"{pid}-RAW-01", "title": "가족 우대", "source_clause_text": text,
        "self_report_eligible": not official,
        "condition": {"source_text": text},
        "reward": {"kind": "RANDOM_REWARD" if unsupported else "BONUS_RATE", "value": reward, "unit": "PERCENTAGE_POINT"},
        "source_ref_ids": [f"SOURCE-{pid}"],
    }
    normalized = NormalizedProductData(
        version=1, institution_name="테스트은행", sale_status="ON_SALE", raw_product={},
        term_policy={"kind": "FIXED", "value": 12, "unit": "MONTH"},
        return_policy={
            "return_kind": "INTEREST", "advertised_max_rate": {"value": str(Decimal(base) + cap)},
            "rate_entries": [{"rate_id": "BASE", "role": "BASE", "calculation": {"value": base, "unit": "PERCENT"}}],
            "preferential_policy": {"rules": [raw]},
        },
    )
    p = p.model_copy(update={
        "normalized": normalized,
        "advertised_max_rate": p.base_rate + cap, "preferential_rate_cap": cap,
        "metadata": p.metadata.model_copy(update={"advertised_max_rate": p.base_rate + cap, "preferential_rate_cap": cap}),
    }, deep=True)
    return ProductDefinition.model_validate(p.model_dump())


def semantic_product_from_rules(pid, specs, *, base="2", cap=None, relations=None):
    p = make_product(pid, base_rate=base)
    rules = []
    total = Decimal("0")
    for index, spec in enumerate(specs, start=1):
        text = spec["text"]
        reward = Decimal(str(spec["reward"]))
        total += reward
        rules.append({
            "rule_id": spec.get("rule_id", f"{pid}-RAW-{index:02d}"),
            "title": spec.get("title", "우대"),
            "source_clause_text": text,
            "self_report_eligible": True,
            "condition": {"source_text": text, **(spec.get("condition") or {})},
            "reward": {"kind": "BONUS_RATE", "value": str(reward), "unit": "PERCENTAGE_POINT"},
            "source_ref_ids": [f"SOURCE-{pid}-{index}"],
        })
    cap_value = Decimal(str(cap)) if cap is not None else total
    advertised = Decimal(base) + cap_value
    normalized = NormalizedProductData(
        version=1, institution_name="테스트은행", sale_status="ON_SALE", raw_product={},
        term_policy={"kind": "FIXED", "value": 12, "unit": "MONTH"},
        return_policy={
            "return_kind": "INTEREST", "advertised_max_rate": {"value": str(advertised)},
            "rate_entries": [{"rate_id": "BASE", "role": "BASE", "calculation": {"value": base, "unit": "PERCENT"}}],
            "preferential_policy": {"rules": rules, "relations": relations or []},
        },
    )
    p = p.model_copy(update={
        "normalized": normalized,
        "advertised_max_rate": advertised, "preferential_rate_cap": cap_value,
        "metadata": p.metadata.model_copy(update={"advertised_max_rate": advertised, "preferential_rate_cap": cap_value}),
    }, deep=True)
    return ProductDefinition.model_validate(p.model_dump())


def typed_count_product():
    p = make_product("TYPED-COUNT", reward_pp="0.5", bonus_fact_type="CHILD_COUNT",
                     bonus_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                     on_true_status=EvaluationStatus.SATISFIED)
    pref = p.preferential_rules[0]
    pref = pref.model_copy(update={"rule": pref.rule.model_copy(update={"operator": ComparisonOperator.GTE, "expected": 2})})
    return p.model_copy(update={"preferential_rules": [pref]})


class CompilerDouble:
    cache_key = "tests-semantic-v1"

    def __init__(self, expressions=None, failure=None):
        self.expressions = expressions or {}
        self.failure = failure
        self.calls = []

    def compile(self, packets):
        self.calls.append(packets)
        if self.failure:
            raise self.failure
        rows = []
        for packet in packets:
            for clause in packet.clauses:
                expr = self.expressions.get(packet.product_id)
                rows.append(ClauseInterpretation(
                    clause_id=clause.clause_id, source_hash=clause.source_hash,
                    source_quote=clause.text, expression=expr,
                    unresolved_reason=None if expr else "원문의 정의를 확인할 수 없음",
                ))
        return SemanticCompilation(clauses=rows)


def service_for(products, compiler, **kwargs):
    intent = kwargs.pop("intent", None)
    service = ApplicationService(products, user_fact_stores={USER_ID: base_store()},
                                 semantic_compiler=compiler, **kwargs)
    intent = intent or make_intent(objective=RankingObjective.MAX_REALIZABLE_RATE, top_k=3)
    session = service.create_search_session(user_id=USER_ID, intent=intent, as_of=AS_OF,
                                           subscription_date=SUBSCRIPTION_DATE)
    return service, session.search_session_id


def business_hash(service, runtime):
    snapshot = service._snapshot_runtime(runtime)
    snapshot["decision_ledger"] = [asdict(e) for e in snapshot["decision_ledger"].entries]
    return canonical_hash(snapshot)


def answer_children(service, sid, *, count, years=(), dates=()):
    children = [{"birth_year": year} for year in years] or [{"birth_date": d} for d in dates]
    service.submit_user_answer(sid, answer={"count": count, "children": children, "complete": len(children) == count})


def test_raw_and_typed_share_one_question_and_recalculate_without_catalog_mutation():
    products = [semantic_product("COUNT"), semantic_product("AGE", AGE_TEXT), typed_count_product()]
    before = canonical_hash([p.model_dump(mode="json") for p in products])
    compiler = CompilerDouble({"COUNT": count_expr(), "AGE": age_expr()})
    service, sid = service_for(products, compiler)
    q = service.get_next_question(sid)
    assert q.request.semantic_input == "CHILDREN"
    assert set(q.affected_product_ids) == {"COUNT", "AGE", "TYPED-COUNT"}
    assert "출생" in q.question
    assert q.answer_mode == "FREE_TEXT"
    assert q.question_spec.value_schema["type"] == "object"
    answer_children(service, sid, count=2, years=[2017, 2025])
    results = service.get_top_recommendations(sid)
    rates = {x.product_id: x.realizable_rate for x in results.top_products}
    assert rates == {"COUNT": Decimal("3"), "AGE": Decimal("3"), "TYPED-COUNT": Decimal("2.5")}
    assert all(x.confirmed_rate == Decimal("2") for x in service._runtime(sid).evaluations.values())
    assert service.get_next_question(sid) is None
    assert len(compiler.calls) == 1
    assert len(compiler.calls[0]) == 2
    assert canonical_hash([p.model_dump(mode="json") for p in products]) == before
    assert results.recommendation_status == "PROVISIONAL"  # AI parsing is not official certification.
    for pid in rates:
        detail = service.get_product_recommendation_detail(sid, pid, include_explanation=False)
        assert detail.realizable_rate == rates[pid]
    assert len(compiler.calls) == 1


def test_count_only_question_does_not_request_birth_information():
    service, sid = service_for([semantic_product("COUNT")], CompilerDouble({"COUNT": count_expr()}))
    assert "출생" not in service.get_next_question(sid).question
    answer_children(service, sid, count=2)
    assert service.get_next_question(sid) is None


def test_zero_children_removes_only_bonus_not_product():
    service, sid = service_for([semantic_product("AGE", AGE_TEXT)], CompilerDouble({"AGE": age_expr()}))
    answer_children(service, sid, count=0)
    candidate = service._runtime(sid).evaluations["AGE"]
    assert candidate.eligibility_status == EvaluationStatus.SATISFIED
    assert candidate.realizable_rate == candidate.user_specific_conditional_upper_rate == Decimal("2")
    assert service.get_next_question(sid) is None


def test_year_boundary_requires_date_not_invented_birthday():
    service, sid = service_for([semantic_product("AGE", AGE_TEXT)], CompilerDouble({"AGE": age_expr()}))
    answer_children(service, sid, count=1, years=[2013])
    q = service.get_next_question(sid)
    assert q is not None and "생년월일" in q.question
    assert service._runtime(sid).evaluations["AGE"].realizable_rate == Decimal("2")
    answer_children(service, sid, count=1, dates=["2013-12-01"])
    assert service._runtime(sid).evaluations["AGE"].realizable_rate == Decimal("3")
    assert len(service.semantic_compiler.calls) == 1


def test_incomplete_roster_cannot_prove_absence_or_exact_count():
    expr = age_expr()
    out = evaluate_expression(expr, {"CHILDREN": {"count": 2, "children": [{"birth_year": 1990}], "complete": False}}, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    assert out.value is None
    out = evaluate_expression(expr, {"CHILDREN": {"count": 2, "children": [{"birth_year": 2020}], "complete": False}}, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    assert out.value is True  # existence already proved; no need to ask about the other child.


def test_self_report_does_not_satisfy_official_document_approval():
    service, sid = service_for([semantic_product("COUNT", official=True)], CompilerDouble({"COUNT": count_expr()}))
    answer_children(service, sid, count=2)
    candidate = service._runtime(sid).evaluations["COUNT"]
    assert candidate.realizable_rate == candidate.confirmed_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3")
    assert any("OFFICIAL" in x.requested_by_rule_id for x in candidate.product_evaluation.missing_facts)
    assert service.get_next_question(sid) is None


def test_unknown_is_not_false_and_does_not_get_reasked():
    service, sid = service_for([semantic_product("COUNT")], CompilerDouble({"COUNT": count_expr()}))
    service.submit_user_answer(sid, answer="모르겠어요")
    candidate = service._runtime(sid).evaluations["COUNT"]
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3")
    assert service.get_next_question(sid) is None
    assert service.get_top_recommendations(sid).recommendation_status == "PROVISIONAL"


def test_presearch_defers_compilation_until_existing_questions_finish():
    compiler = CompilerDouble({"COUNT": count_expr()})
    service, sid = service_for([semantic_product("COUNT")], compiler, pre_search_enabled=True)
    assert compiler.calls == []
    assert service.get_next_question(sid).question_kind == "PRE_SEARCH_PROFILE"
    runtime = service._runtime(sid)
    # This isolates the transition; existing pre-search interpreters own each
    # answer and are separately exercised by the repository integration suite.
    for key, value in runtime.pre_search_profile.items():
        runtime.pre_search_profile[key] = value.model_copy(update={"answer_status": PreSearchAnswerStatus.ACKNOWLEDGED_UNKNOWN})
    service._run_pipeline(runtime)
    assert len(compiler.calls) == 1
    assert service.get_next_question(sid).request.semantic_input == "CHILDREN"


def test_failed_compilation_keeps_typed_questions_and_provisional_result():
    compiler = CompilerDouble(failure=TimeoutError("provider timeout"))
    service, sid = service_for([semantic_product("RAW"), make_product("TYPED", reward_pp="1")], compiler)
    assert service.get_next_question(sid).request.semantic_input is None
    assert service.get_top_recommendations(sid).semantic_review["status"] == "FAILED"
    assert service.get_top_recommendations(sid).recommendation_status == "PROVISIONAL"
    assert service._runtime(sid).semantic_cache == {}
    assert len(compiler.calls) == 1
    service.get_top_recommendations(sid)
    assert len(compiler.calls) == 1


def test_budget_defers_challengers_without_excluding_them_and_can_advance():
    products = [semantic_product(f"P{i}") for i in range(5)]
    compiler = CompilerDouble({p.product_id: count_expr() for p in products})
    service, sid = service_for(products, compiler, semantic_batch_size=2)
    assert len(service._runtime(sid).candidate_products) == 5
    assert service.get_top_recommendations(sid).semantic_review["pending_product_count"] == 3
    service.advance_semantic_review(sid)
    assert service.get_top_recommendations(sid).semantic_review["pending_product_count"] == 1
    service.advance_semantic_review(sid)
    assert service.get_top_recommendations(sid).semantic_review["pending_product_count"] == 0
    assert len(compiler.calls) == 3
    assert not service._runtime(sid).ranking.stability.stable


def test_read_endpoints_never_compile_or_mutate_business_state():
    compiler = CompilerDouble({"RAW": count_expr()})
    service, sid = service_for([semantic_product("RAW")], compiler)
    with TestClient(create_app(service=service)) as client:
        before = business_hash(service, service._runtime(sid))
        for tail in ["state", "recommendations", "questions/next", "recommendations/RAW", "recommendations/RAW/preview"]:
            response = client.get(f"/api/search-sessions/{sid}/{tail}")
            assert response.status_code == 200, response.text
        after = business_hash(service, service._runtime(sid))
        assert before == after
    assert len(compiler.calls) == 1


def test_revision_and_removal_invalidate_derived_results_not_source_cache():
    compiler = CompilerDouble({"RAW": count_expr()})
    service, sid = service_for([semantic_product("RAW")], compiler)
    answer_children(service, sid, count=2)
    reference = f"SEMANTIC-INPUT/{sid}/CHILDREN"
    service.revise_user_answer(sid, request_reference=reference, new_value={"count": 0, "children": [], "complete": True})
    assert service._runtime(sid).evaluations["RAW"].realizable_rate == Decimal("2")
    service.clear_user_declared_fact(sid, fact_type="SEMANTIC_INPUT::CHILDREN")
    assert service.get_next_question(sid).request.semantic_input == "CHILDREN"
    assert len(compiler.calls) == 1
    assert not any(f.fact_type.startswith("SEMANTIC_RESULT::") for f in service._runtime(sid).fact_store.facts)


def test_bad_answer_rolls_back_before_recalculation():
    service, sid = service_for([semantic_product("RAW")], CompilerDouble({"RAW": count_expr()}))
    runtime = service._runtime(sid)
    before = business_hash(service, runtime)
    with pytest.raises(ValueError):
        service.submit_user_answer(sid, answer={"count": 1, "children": [{"birth_year": 2020}, {"birth_year": 2022}], "complete": True})
    assert business_hash(service, runtime) == before


def test_message_answer_uses_existing_strict_answer_plan_and_actual_engine():
    adapter = MockLLMAdapter()
    compiler = CompilerDouble({"RAW": count_expr()})
    service, sid = service_for([semantic_product("RAW")], compiler,
                               conversation_orchestrator=ConversationOrchestrator(LLMGateway(adapter)))
    q = service.get_next_question(sid)
    adapter.enqueue(LLMPurpose.CONVERSATION_ORCHESTRATION, {
        "active_question_answer": {"question_id": q.question_id, "state": "DECLARED_FEASIBLE",
            "value": {"count": 2, "children": [{"birth_year": 2017}, {"birth_year": 2025}], "complete": True}},
        "additional_updates": [], "unresolved_fragments": [],
    })
    with TestClient(create_app(service=service)) as client:
        response = client.post(f"/api/search-sessions/{sid}/messages", json={"message": "아이 둘이고 2017년생, 2025년생이야"})
        assert response.status_code == 200, response.text
        result = client.get(f"/api/search-sessions/{sid}/recommendations").json()
        assert result["top_products"][0]["realizable_rate"] == "3"
    assert len(adapter.call_history) == 1


def test_real_llm_boundary_is_strict_and_does_not_send_entire_catalog():
    product = semantic_product("RAW")
    adapter = MockLLMAdapter()
    compiler = SemanticConditionCompiler(LLMGateway(adapter))
    packet = product_packet(product, compiler.cache_key)
    c = packet.clauses[0]
    adapter.enqueue(LLMPurpose.SEMANTIC_CONDITION_COMPILATION, {"clauses": [{
        "clause_id": c.clause_id, "source_hash": c.source_hash, "source_quote": c.text,
        "expression": count_expr().model_dump(mode="json"), "unresolved_reason": None,
    }]})
    assert len(compiler.compile([packet]).clauses) == 1
    assert len(adapter.call_history) == 1


@pytest.mark.parametrize("change", ["wrong_id", "wrong_hash", "wrong_quote", "duplicate", "missing", "invented_threshold"])
def test_invalid_compile_batch_is_rejected_atomically(change):
    p = product_packet(semantic_product("RAW"), "test")
    c = p.clauses[0]
    row = ClauseInterpretation(clause_id=c.clause_id, source_hash=c.source_hash, source_quote=c.text, expression=count_expr())
    rows = [row]
    if change == "wrong_id": rows = [row.model_copy(update={"clause_id": "OTHER"})]
    if change == "wrong_hash": rows = [row.model_copy(update={"source_hash": "OTHER"})]
    if change == "wrong_quote": rows = [row.model_copy(update={"source_quote": "OTHER"})]
    if change == "duplicate": rows *= 2
    if change == "missing": rows = []
    if change == "invented_threshold": rows = [row.model_copy(update={"expression": count_expr(expected=7)})]
    with pytest.raises(ValueError):
        validate_compilation(SemanticCompilation(clauses=rows), [p])


@pytest.mark.parametrize("op, expected", [("ALL", False), ("ANY", None)])
def test_partial_logical_conditions_preserve_unresolved_branch(op, expected):
    text = "자녀 2명 이상 및 당행 인증"
    expr = SemanticExpression(op=op, source_quote=text, children=[count_expr(text), SemanticExpression(op="UNKNOWN", source_quote="당행 인증")])
    out = evaluate_expression(expr, {"CHILDREN": {"count": 0, "children": [], "complete": True}}, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    assert out.value is expected


@pytest.mark.parametrize("payload", [
    {"count": True, "children": [], "complete": False},
    {"count": 1, "children": [{"birth_year": 2028}], "complete": True},
    {"count": 1, "children": [{"birth_year": 2020, "birth_date": "2021-01-01"}], "complete": True},
    {"count": 0, "children": [], "complete": "false"},
])
def test_family_answer_type_and_date_validation(payload):
    with pytest.raises(ValueError):
        normalize_answer("CHILDREN", payload, AS_OF)


def test_source_or_compiler_version_invalidates_cache_key():
    product = semantic_product("RAW")
    a = product_packet(product, "v1")
    assert a.cache_key != product_packet(product, "v2").cache_key
    changed = product.model_copy(deep=True)
    changed.normalized.return_policy["preferential_policy"]["rules"][0]["source_clause_text"] += " (조건 변경)"
    assert a.cache_key != product_packet(changed, "v1").cache_key


def test_official_source_children_product_is_found_without_full_catalog_loading():
    from eligibility.catalog.normalized_loader import _adapt_product
    root = Path(__file__).resolve().parents[1]
    index = json.loads((root / "data/financial_products/normalized/index.json").read_text())
    row = next(x for x in index["products"] if x["product_code"] == "INST-KR-000055-1-C6881F00E29")
    raw = json.loads((root / row["path"]).read_text())
    product = _adapt_product(raw, {"institution_id": raw["institution_id"], "official_name_ko": "애랑해 테스트 기관"}, {}, {}, {})
    packet = product_packet(product, "test")
    assert packet is not None
    child_clauses = [c for c in packet.clauses if "아이사랑" in c.text]
    assert child_clauses
    assert all(c.official_only for c in child_clauses)
    assert "만 12세 이하" in child_clauses[0].text
    assert "승인완료" in child_clauses[0].text


def opaque_product(pid="OPAQUE", *, official=False):
    product = semantic_product(pid, official=official)
    row = product.normalized.return_policy["preferential_policy"]["rules"][0]
    row["reward"]["kind"] = "ADD_RATE"
    row["condition"] = {"predicate": {"fact_key": "UNSTRUCTURED.SOURCE_CLAUSE_GATE", "operator": "EQUALS",
                                      "expected_value": True, "source_semantics": "SOURCE_CLAUSE_GATE"}}
    executable = make_product(pid, reward_pp="1", bonus_fact_type="UNSTRUCTURED_SOURCE_CLAUSE_GATE",
                              bonus_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                              on_true_status=EvaluationStatus.SATISFIED).preferential_rules[0]
    executable = executable.model_copy(update={"canonical_rule_id": row["rule_id"]})
    return product.model_copy(update={"preferential_rules": [executable]}, deep=True)


def test_opaque_boolean_gate_is_augmented_without_double_reward_or_rewriting_typed_comparison():
    product = opaque_product()
    original = product.model_dump(mode="json")
    compiler = CompilerDouble({"OPAQUE": count_expr()})
    service, sid = service_for([product], compiler)
    assert len(compiler.calls) == 1
    assert service.get_next_question(sid).request.semantic_input == "CHILDREN"
    answer_children(service, sid, count=2)
    candidate = service._runtime(sid).evaluations["OPAQUE"]
    assert candidate.realizable_rate == Decimal("3")
    assert len(candidate.product_evaluation.rates.applied_rewards) == 1
    assert product.model_dump(mode="json") == original
    detail = service.get_product_recommendation_detail(sid, "OPAQUE", include_explanation=False)
    assert detail.realizable_rate == candidate.realizable_rate
    assert detail.semantic_interpretations


def test_opaque_official_gate_cannot_be_promoted_by_self_report():
    service, sid = service_for([opaque_product(official=True)], CompilerDouble({"OPAQUE": count_expr()}))
    answer_children(service, sid, count=2)
    candidate = service._runtime(sid).evaluations["OPAQUE"]
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3")


def test_complete_typed_rules_are_not_compile_targets():
    product = opaque_product()
    product.normalized.return_policy["preferential_policy"]["rules"][0]["condition"]["predicate"]["source_semantics"] = "EXPLICIT_TYPED_CONDITION"
    assert product_packet(product, "v1") is None


def test_irrelevant_lower_ranked_product_does_not_trigger_llm():
    products = [make_product(f"HIGH{i}", base_rate="5") for i in range(3)]
    products.append(semantic_product("LOW", base="1", reward="1"))
    compiler = CompilerDouble({"LOW": count_expr()})
    service, sid = service_for(products, compiler)
    assert compiler.calls == []
    assert len(service._runtime(sid).candidate_products) == 4


def test_source_and_user_state_caches_are_session_isolated():
    compiler = CompilerDouble({"RAW": count_expr()})
    service, first = service_for([semantic_product("RAW")], compiler)
    answer_children(service, first, count=2)
    second = service.create_search_session(user_id=USER_ID, intent=make_intent(objective=RankingObjective.MAX_REALIZABLE_RATE),
                                           as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE).search_session_id
    assert service._runtime(second).evaluations["RAW"].realizable_rate == Decimal("2")
    assert service.get_next_question(second).request.semantic_input == "CHILDREN"
    assert len(compiler.calls) == 2


def test_retry_is_an_explicit_post_not_a_read_and_requires_boolean():
    compiler = CompilerDouble({"RAW": count_expr()}, failure=TimeoutError())
    service, sid = service_for([semantic_product("RAW")], compiler)
    with TestClient(create_app(service=service)) as client:
        compiler.failure = None
        for _ in range(2):
            assert client.get(f"/api/search-sessions/{sid}/recommendations").status_code == 200
        assert len(compiler.calls) == 1
        assert client.post(f"/api/search-sessions/{sid}/semantic-review", json={"retry_failed": "false"}).status_code == 400
        response = client.post(f"/api/search-sessions/{sid}/semantic-review", json={"retry_failed": True})
        assert response.status_code == 200
        assert response.json()["status"] == "COMPLETE"
    assert len(compiler.calls) == 2


def test_unsupported_reward_cannot_be_changed_into_cash_or_a_fixed_rate():
    product = semantic_product("RAW", unsupported=True)
    service, sid = service_for([product], CompilerDouble({"RAW": count_expr()}))
    candidate = service._runtime(sid).evaluations["RAW"]
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.product_evaluation.rates.applied_rewards == []
    assert service.get_top_recommendations(sid).semantic_review["unresolved_clause_count"] > 0


def test_roll_back_semantic_answer_and_cache_if_later_pipeline_fails(monkeypatch):
    service, sid = service_for([semantic_product("RAW")], CompilerDouble({"RAW": count_expr()}))
    runtime = service._runtime(sid)
    before = business_hash(service, runtime)
    def fail(*args, **kwargs):
        raise ValueError("injected pipeline failure")
    monkeypatch.setattr(service, "_retrieve", fail)
    with pytest.raises(ValueError, match="injected"):
        answer_children(service, sid, count=2)
    assert business_hash(service, runtime) == before


def test_field_budgets_leave_unread_products_provisional():
    compiler = CompilerDouble({"LONG": count_expr()})
    product = semantic_product("LONG", text=COUNT_TEXT + "\n" + "긴 원문 " * 3000)
    service, sid = service_for([product], compiler)
    assert compiler.calls == []
    result = service.get_top_recommendations(sid)
    assert result.semantic_review["status"] == "FAILED"
    assert result.recommendation_status == "PROVISIONAL"


def test_same_source_phrase_different_products_keeps_both_bindings():
    a, b = semantic_product("A"), semantic_product("B")
    pa, pb = product_packet(a, "v1"), product_packet(b, "v1")
    assert pa.clauses[0].clause_id != pb.clauses[0].clause_id
    assert pa.cache_key != pb.cache_key


def test_shared_answer_cannot_override_institution_verified_child_count():
    from tests.v04_helpers import verified_fact
    product = semantic_product("RAW")
    compiler = CompilerDouble({"RAW": count_expr()})
    service = ApplicationService([product], semantic_compiler=compiler,
        user_fact_stores={USER_ID: base_store(verified_fact("COUNT-VERIFIED", "CHILD_COUNT", 1))})
    session = service.create_search_session(user_id=USER_ID, intent=make_intent(), as_of=AS_OF)
    runtime = service._runtime(session.search_session_id)
    before = business_hash(service, runtime)
    with pytest.raises(ValueError, match="authoritative"):
        service._store_semantic_input(runtime, "CHILDREN", {"count": 2, "children": [], "complete": False})
    assert business_hash(service, runtime) == before
    assert runtime.evaluations["RAW"].realizable_rate == Decimal("2")


def test_subject_and_fact_expiry_are_respected_by_shared_input_projection():
    from tests.v04_helpers import verified_fact
    other_subject = verified_fact("OTHER-COUNT", "CHILD_COUNT", 3).model_copy(update={"subject_person_id": "OTHER"})
    expired = verified_fact("OLD-COUNT", "CHILD_COUNT", 4).model_copy(update={"valid_from": date(2019, 1, 1), "valid_to": date(2020, 1, 1)})
    assert "CHILDREN" not in user_inputs(base_store(other_subject, expired), AS_OF)


def test_pending_opaque_gate_stays_unknown_even_when_original_yes_exists():
    from eligibility.schema.enums import FactSourceType
    from tests.v04_helpers import verified_fact
    p = opaque_product()
    original_fact = verified_fact("OPAQUE-YES", p.preferential_rules[0].rule.fact_type, True,
        source_type=FactSourceType.USER_DECLARED, semantic_type=FactSemanticType.SELF_REPORTED_FACT)
    service = ApplicationService([p], semantic_compiler=CompilerDouble(failure=TimeoutError()),
        user_fact_stores={USER_ID: base_store(original_fact)})
    session = service.create_search_session(user_id=USER_ID, intent=make_intent(), as_of=AS_OF)
    candidate = service._runtime(session.search_session_id).evaluations[p.product_id]
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3")


def test_compiler_cannot_invent_a_marriage_cutoff_date():
    product = semantic_product("RAW", text="신혼부부 우대")
    packet = product_packet(product, "test")
    clause = packet.clauses[0]
    expr = SemanticExpression(op="COMPARE", variable="MARRIAGE_DATE", comparator="GTE",
        expected="2025-01-01", source_quote=clause.text)
    compiled = SemanticCompilation(clauses=[ClauseInterpretation(clause_id=clause.clause_id,
        source_hash=clause.source_hash, source_quote=clause.text, expression=expr)])
    with pytest.raises(ValueError, match="literal source"):
        validate_compilation(compiled, [packet])


def test_grounded_marriage_date_and_pregnancy_are_supported_without_financial_llm_output():
    for variable, text, expected, answer in [
        ("MARRIAGE_DATE", "2025년 1월 1일 이후 혼인 고객", "2025-01-01", "2026-01-01"),
        ("PREGNANT_SELF", "가입자 본인 임신 우대", True, True),
    ]:
        comparator = "GTE" if variable == "MARRIAGE_DATE" else "EQ"
        product = semantic_product("RAW", text=text)
        packet = product_packet(product, "test")
        clause = packet.clauses[0]
        expr = SemanticExpression(op="COMPARE", variable=variable, comparator=comparator,
            expected=expected, source_quote=text)
        compiled = SemanticCompilation(clauses=[ClauseInterpretation(clause_id=clause.clause_id,
            source_hash=clause.source_hash, source_quote=clause.text, expression=expr)])
        validate_compilation(compiled, [packet])
        result = evaluate_expression(expr, {variable: answer}, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
        assert result.value is True


@pytest.mark.parametrize("birth, subscription, expected", [
    ({"birth_date": "2025-01-01"}, date(2024, 1, 1), False),
    ({"birth_date": "2024-01-01"}, date(2024, 1, 1), True),
    ({"birth_year": 2024}, date(2024, 6, 1), None),
])
def test_child_must_already_be_born_at_condition_reference_date(birth, subscription, expected):
    expr = age_expr()
    result = evaluate_expression(expr, {"CHILDREN": {
        "count": 1, "children": [birth], "complete": True,
    }}, as_of=AS_OF, subscription_date=subscription)
    assert result.value is expected


def _with_empty_clause(product, rule_id="EMPTY-02"):
    product = product.model_copy(deep=True)
    product.normalized.return_policy["preferential_policy"]["rules"].append(
        {
            "rule_id": rule_id,
            "title": "원문 없는 우대",
            "source_clause_text": "   ",
            "condition": {},
            "reward": {"kind": "BONUS_RATE", "value": "1", "unit": "PERCENTAGE_POINT"},
        }
    )
    return ProductDefinition.model_validate(product.model_dump())


def test_empty_source_clause_is_skipped_without_failing_the_product():
    product = _with_empty_clause(semantic_product("MIXED"))
    packet = product_packet(product, "test")
    assert len(packet.clauses) == 1
    assert len(packet.empty_clauses) == 1
    assert packet.clauses[0].text == COUNT_TEXT
    assert packet.empty_clauses[0].text == ""


def test_empty_clause_does_not_block_readable_compile_or_rate_recalc():
    product = _with_empty_clause(semantic_product("MIXED"))
    compiler = CompilerDouble({"MIXED": count_expr()})
    service, sid = service_for([product], compiler)
    runtime = service._runtime(sid)
    assert runtime.semantic_review.error_code != "SOURCE_PACKET_UNSUPPORTED"
    assert len(compiler.calls) == 1
    assert len(compiler.calls[0][0].clauses) == 1
    views = runtime.evaluations["MIXED"].semantic_interpretations
    assert any(item.get("reason") == "EMPTY_SOURCE_CLAUSE" for item in views)
    q = service.get_next_question(sid)
    assert q is not None and q.request.semantic_input == "CHILDREN"
    answer_children(service, sid, count=2, years=[2017, 2025])
    assert service._runtime(sid).evaluations["MIXED"].realizable_rate == Decimal("3")


def test_empty_only_packet_is_unresolved_not_unsupported():
    product = semantic_product("EMPTY", text="   ")
    compiler = CompilerDouble({"EMPTY": count_expr()})
    service, sid = service_for([product], compiler)
    runtime = service._runtime(sid)
    assert compiler.calls == []
    assert runtime.semantic_review.status != "FAILED"
    assert runtime.semantic_review.error_code != "SOURCE_PACKET_UNSUPPORTED"
    views = runtime.evaluations["EMPTY"].semantic_interpretations
    assert any(item.get("reason") == "EMPTY_SOURCE_CLAUSE" for item in views)
    assert any(item.get("memo_code") == "UNRESOLVED" for item in views)


CARD_TEXT = "당행 신용카드 이용실적 충족 시 우대금리 1%p"
SALARY_TEXT = "급여이체 실적 충족 시 우대금리 1%p"
FIRST_TX_TEXT = "당행 첫거래 고객에게 우대금리 1%p"
MARKETING_TEXT = "상품 안내 및 마케팅 수신 동의 시 우대금리 1%p"


def test_existing_question_family_maps_unambiguous_clauses_only():
    assert existing_question_family(text=CARD_TEXT) == "CARD"
    assert existing_question_family(text=SALARY_TEXT) == "SALARY"
    assert existing_question_family(text=FIRST_TX_TEXT) == "FIRST_TRANSACTION"
    assert existing_question_family(text=MARKETING_TEXT) == "MARKETING"
    assert existing_question_family(text=COUNT_TEXT) is None
    assert existing_question_family(text="급여이체 또는 신용카드 이용실적") is None
    assert existing_question_family(text="행운카드 우대") is None


def _declined_intent(*fields: str):
    return make_intent(
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=3,
        preferences=[
            Preference(field=field, preference=PreferenceValue.PREFER_ABSENT)
            for field in fields
        ],
    )


@pytest.mark.parametrize("pid, text, field", [
    ("CARD", CARD_TEXT, "CARD_BENEFIT"),
    ("SALARY", SALARY_TEXT, "SALARY_BENEFIT"),
    ("FIRST", FIRST_TX_TEXT, "FIRST_TRANSACTION_BENEFIT"),
])
def test_declined_existing_benefit_rules_out_unread_clause_and_drops_upper(pid, text, field):
    product = semantic_product(pid, text)
    compiler = CompilerDouble({pid: count_expr()})
    service, sid = service_for([product], compiler, intent=_declined_intent(field))
    assert compiler.calls == []
    candidate = service._runtime(sid).evaluations[pid]
    original = Decimal("2") + Decimal("1")
    assert candidate.realizable_rate == candidate.user_specific_conditional_upper_rate == Decimal("2")
    assert candidate.product_evaluation.rates.advertised_max_rate == original
    assert any(item.get("memo_code") == "RULED_OUT" for item in candidate.semantic_interpretations)
    item = next(x for x in service.get_top_recommendations(sid).top_products if x.product_id == pid)
    assert item.advertised_max_rate == original
    assert item.user_specific_conditional_upper_rate == Decimal("2")
    assert any(memo.code == "RULED_OUT" for memo in item.semantic_memos)
    question = service.get_next_question(sid)
    assert question is None or question.request is None or question.request.semantic_input is None


def test_children_question_is_asked_without_duplicate_marketing_question():
    children = semantic_product("KIDS", COUNT_TEXT)
    marketing = semantic_product("ADS", MARKETING_TEXT)
    compiler = CompilerDouble({"KIDS": count_expr(), "ADS": count_expr()})
    service, sid = service_for([children, marketing], compiler)
    compiled_ids = {packet.product_id for batch in compiler.calls for packet in batch}
    assert "KIDS" in compiled_ids
    assert "ADS" not in compiled_ids
    question = service.get_next_question(sid)
    assert question is not None and question.request.semantic_input == "CHILDREN"
    assert "마케팅" not in (question.question or "")
    ads = service._runtime(sid).evaluations["ADS"]
    assert any(item.get("memo_code") == "NEEDS_OFFICIAL" for item in ads.semantic_interpretations)
    assert ads.user_specific_conditional_upper_rate == Decimal("3")
    assert ads.realizable_rate == Decimal("2")
    with TestClient(create_app(service=service)) as client:
        listing = client.get(f"/api/search-sessions/{sid}/recommendations").json()
        ads_item = next(item for item in listing["top_products"] if item["product_id"] == "ADS")
        assert any(memo["code"] == "NEEDS_OFFICIAL" for memo in ads_item["semantic_memos"])
        kids_detail = client.get(f"/api/search-sessions/{sid}/recommendations/KIDS").json()
        ads_detail = client.get(f"/api/search-sessions/{sid}/recommendations/ADS").json()
        assert any(item.get("memo_code") == "NEEDS_INPUT" for item in kids_detail["semantic_interpretations"])
        assert any(item.get("memo_code") == "NEEDS_OFFICIAL" for item in ads_detail["semantic_interpretations"])


def test_first_transaction_holding_history_rules_out_without_llm():
    product = semantic_product("FIRST", FIRST_TX_TEXT)
    packet = product_packet(product, "test")
    assert [c.existing_question_family for c in packet.linked_clauses] == ["FIRST_TRANSACTION"]
    assert packet.clauses == ()
    _, _, requests, views = overlay_product(
        product, packet, (), base_store(),
        as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE,
        holding_institution_names=["테스트은행(주)"],
    )
    assert requests == []
    assert any(item.get("memo_code") == "RULED_OUT" for item in views)
    compiler = CompilerDouble({"FIRST": count_expr()})
    service, sid = service_for([product], compiler)
    runtime = service._runtime(sid)
    runtime.pre_search_profile[INSTITUTION_PRODUCT_HOLDING_HISTORY] = PreSearchProfileEntry(
        question_key=INSTITUTION_PRODUCT_HOLDING_HISTORY,
        answer_status=PreSearchAnswerStatus.ANSWERED,
        value={"prior_product_holding_institutions": ["테스트 은행"]},
    )
    service._evaluate(runtime, list(runtime.candidate_products.values()))
    candidate = runtime.evaluations["FIRST"]
    assert compiler.calls == []
    assert candidate.realizable_rate == candidate.user_specific_conditional_upper_rate == Decimal("2")
    assert compact_semantic_memos(candidate.semantic_interpretations)[0]["code"] == "RULED_OUT"


def test_willing_card_clause_needs_official_confirmation_not_a_new_slot():
    product = semantic_product("CARD", CARD_TEXT)
    compiler = CompilerDouble({"CARD": count_expr()})
    service, sid = service_for(
        [product], compiler,
        intent=make_intent(
            objective=RankingObjective.MAX_REALIZABLE_RATE, top_k=3,
            preferences=[Preference(field="CARD_BENEFIT", preference=PreferenceValue.PREFER_PRESENT)],
        ),
    )
    assert compiler.calls == []
    candidate = service._runtime(sid).evaluations["CARD"]
    assert any(item.get("memo_code") == "NEEDS_OFFICIAL" for item in candidate.semantic_interpretations)
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3")
    question = service.get_next_question(sid)
    assert question is None or question.request is None or question.request.semantic_input != "CHILDREN"


def test_web_app_javascript_parses():
    app_js = Path("src/eligibility/web/static/app.js")
    result = subprocess.run(["node", "--check", str(app_js)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_first_transaction_holding_requires_matching_period_and_product_scope():
    assert first_transaction_holding_rules_out(FIRST_TX_TEXT) is True
    assert first_transaction_holding_rules_out("최근 6개월 당행 예적금 미보유 고객") is True
    assert first_transaction_holding_rules_out("최근 3년 당행 적금 거래가 없는 고객") is True
    assert first_transaction_holding_rules_out("최근 3개월 당행 예적금 미보유 고객") is False
    assert first_transaction_holding_rules_out("최근 1년 당행 입출금 거래가 없는 고객") is False
    assert first_transaction_holding_rules_out("우대금리 제공") is False


def test_mismatched_first_transaction_holding_stays_needs_official():
    product = semantic_product("FIRST", "최근 1년 당행 입출금 거래가 없는 고객에게 우대금리 1%p")
    packet = product_packet(product, "test")
    overlay, _, requests, views = overlay_product(
        product, packet, (), base_store(),
        as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE,
        holding_institution_names=["테스트은행(주)"],
    )
    assert requests == []
    assert any(item.get("memo_code") == "NEEDS_OFFICIAL" for item in views)
    assert overlay.advertised_max_rate == product.advertised_max_rate == Decimal("3")
    compiler = CompilerDouble({"FIRST": count_expr()})
    service, sid = service_for([product], compiler)
    runtime = service._runtime(sid)
    runtime.pre_search_profile[INSTITUTION_PRODUCT_HOLDING_HISTORY] = PreSearchProfileEntry(
        question_key=INSTITUTION_PRODUCT_HOLDING_HISTORY,
        answer_status=PreSearchAnswerStatus.ANSWERED,
        value={"prior_product_holding_institutions": ["테스트 은행"]},
    )
    service._evaluate(runtime, list(runtime.candidate_products.values()))
    candidate = runtime.evaluations["FIRST"]
    assert compiler.calls == []
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3")
    assert compact_semantic_memos(candidate.semantic_interpretations)[0]["code"] == "NEEDS_OFFICIAL"


def test_absent_holding_does_not_confirm_first_transaction():
    product = semantic_product("FIRST", FIRST_TX_TEXT)
    overlay, _, _, views = overlay_product(
        product, product_packet(product, "test"), (), base_store(),
        as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE,
        holding_institution_names=["다른은행"],
    )
    assert overlay.advertised_max_rate == product.advertised_max_rate
    assert any(item.get("memo_code") == "NEEDS_OFFICIAL" for item in views)
    assert not any(item.get("memo_code") == "RULED_OUT" for item in views)


def test_card_clause_with_other_path_is_not_ruled_out_by_declined_card():
    text = "당행 신용카드 이용실적 또는 자동이체 충족 시 우대금리 1%p"
    assert existing_question_family(text=text) == "CARD"
    assert has_unlinked_alternative_path(text, "CARD") is True
    product = semantic_product("CARD", text)
    compiler = CompilerDouble({"CARD": count_expr()})
    service, sid = service_for([product], compiler, intent=_declined_intent("CARD_BENEFIT"))
    assert compiler.calls == []
    candidate = service._runtime(sid).evaluations["CARD"]
    assert any(item.get("memo_code") == "NEEDS_OFFICIAL" for item in candidate.semantic_interpretations)
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3")
    assert candidate.product_evaluation.rates.advertised_max_rate == Decimal("3")


def test_remaining_exclusive_alternative_keeps_user_upper():
    product = semantic_product_from_rules(
        "MIXED",
        [
            {"title": "카드", "text": CARD_TEXT, "reward": "1"},
            {"title": "급여", "text": SALARY_TEXT, "reward": "1"},
        ],
        cap="1",
        relations=[{"type": "MAX_OF", "rule_ids": ["MIXED-RAW-01", "MIXED-RAW-02"]}],
    )
    compiler = CompilerDouble({"MIXED": count_expr()})
    service, sid = service_for([product], compiler, intent=_declined_intent("CARD_BENEFIT"))
    assert compiler.calls == []
    candidate = service._runtime(sid).evaluations["MIXED"]
    memos = {item.get("memo_code") for item in candidate.semantic_interpretations}
    assert "RULED_OUT" in memos
    assert "NEEDS_OFFICIAL" in memos
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3")
    assert candidate.product_evaluation.rates.advertised_max_rate == Decimal("3")


def test_global_cap_keeps_user_upper_after_one_bonus_is_ruled_out():
    product = semantic_product_from_rules(
        "CAP",
        [
            {"title": "카드", "text": CARD_TEXT, "reward": "1"},
            {"title": "급여", "text": SALARY_TEXT, "reward": "2"},
        ],
        cap="2",
    )
    compiler = CompilerDouble({"CAP": count_expr()})
    service, sid = service_for([product], compiler, intent=_declined_intent("CARD_BENEFIT"))
    candidate = service._runtime(sid).evaluations["CAP"]
    assert candidate.realizable_rate == Decimal("2")
    assert candidate.user_specific_conditional_upper_rate == Decimal("4")
    assert candidate.product_evaluation.rates.advertised_max_rate == Decimal("4")


def test_next_compile_batch_follows_reranked_frontier():
    high = semantic_product("HIGH", COUNT_TEXT, reward="5")
    later = semantic_product("LATER", COUNT_TEXT, reward="4")
    filler = [semantic_product(f"F{i}", COUNT_TEXT, reward="0.1") for i in range(3)]
    products = [high, *filler, later]
    compiler = CompilerDouble({item.product_id: count_expr() for item in products})
    service, sid = service_for(
        products, compiler, semantic_batch_size=1,
        intent=make_intent(objective=RankingObjective.MAX_REALIZABLE_RATE, top_k=1),
    )
    first_ids = [packet.product_id for packet in compiler.calls[0]]
    assert first_ids == ["HIGH"]
    answer_children(service, sid, count=0)
    compiled_ids = [packet.product_id for batch in compiler.calls for packet in batch]
    assert "LATER" in compiled_ids
    if "F0" in compiled_ids:
        assert compiled_ids.index("LATER") < compiled_ids.index("F0")
    assert service._runtime(sid).evaluations["HIGH"].user_specific_conditional_upper_rate == Decimal("2")
