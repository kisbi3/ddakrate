"""Regression tests for the 7b76c6f recommendation audit; use real runtime modules."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import product as cartesian_product

import pytest
from fastapi.testclient import TestClient

from eligibility.application_service import ApplicationService
from eligibility.audit import canonical_hash
from eligibility.conversation import ConversationOrchestrator
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.enums import RankingObjective, TermUnit
from eligibility.schema.product import ContractTerm
from eligibility.schema.search import IntentPatch
from eligibility.search.contribution import resolve_term
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.questions import RankingAwareQuestionPlanner
from eligibility.search.ranking import RankingService
from eligibility.search.retrieval import CandidateRetriever
from eligibility.web.app import create_app
from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, USER_ID, base_store, make_intent, make_product


def _term_product():
    p = make_product("TERM-OPTIONS")
    terms = [ContractTerm(value=n, unit=TermUnit.MONTH) for n in (6, 12, 24)]
    return p.model_copy(update={"metadata": p.metadata.model_copy(update={
        "available_terms": terms, "min_term": terms[0], "max_term": terms[-1],
    })}, deep=True)


def _intent(boundary=12, strictness="PREFERRED", top_k=3):
    i = make_intent(selected_term_value=boundary, top_k=top_k,
                    objective=RankingObjective.MAX_REALIZABLE_RATE)
    return i.model_copy(update={"contribution_plan": i.contribution_plan.model_copy(
        update={"term_strictness": strictness})}, deep=True)


@pytest.mark.parametrize("months", [2, 12, 24, 36])
def test_active_kakao_deposit_supports_continuous_month_terms(normalized_catalog_session, months):
    p = next(p for p in normalized_catalog_session if p.product_id == "INST-KR-000830-2-0001")
    i = _intent(months, "EXACT").model_copy(update={"product_types": ["TIME_DEPOSIT"]})
    retained, decisions = CandidateRetriever().retrieve([p], i, as_of=date(2026, 9, 5))
    assert retained, decisions
    assert resolve_term(p, i.contribution_plan) == ContractTerm(value=months, unit=TermUnit.MONTH)
    assert p.normalized.term_policy["kind"] == "RANGE"
    assert p.contract_term.value == 12


@pytest.mark.parametrize("boundary,strictness,expected", [(9, "MAXIMUM", 6), (18, "MINIMUM", 24)])
def test_retrieved_term_bound_controls_actual_plan_and_rate(boundary, strictness, expected):
    p = _term_product()
    i = _intent(boundary, strictness)
    retained, _ = CandidateRetriever().retrieve([p], i, as_of=AS_OF)
    assert retained == [p]
    evaluated = MultiProductEvaluator().evaluate(retained, base_store(), i, as_of=AS_OF,
                                                subscription_date=SUBSCRIPTION_DATE)[p.product_id]
    assert evaluated.rate_evaluation.selected_term_value == expected
    assert evaluated.contribution_projection.term_match_status == "WITHIN_BOUNDS"
    assert evaluated.contribution_projection.contribution_count == expected
    assert evaluated.estimated_total_principal == i.contribution_plan.desired_periodic_amount * expected


@pytest.mark.parametrize("boundary,strictness", [(3, "MAXIMUM"), (36, "MINIMUM")])
def test_impossible_term_bounds_remain_filtered(boundary, strictness):
    retained, _ = CandidateRetriever().retrieve([_term_product()], _intent(boundary, strictness), as_of=AS_OF)
    assert retained == []


@pytest.mark.parametrize("boundary,strictness,expected", [(36, "MAXIMUM", 24), (3, "MINIMUM", 6)])
def test_range_bound_selects_inside_domain(boundary, strictness, expected):
    p = _term_product()
    p = p.model_copy(update={"metadata": p.metadata.model_copy(update={"available_terms": []})}, deep=True)
    assert resolve_term(p, _intent(boundary, strictness).contribution_plan).value == expected


def _interval_candidates(intervals):
    products = [make_product(pid, base_rate=str(lower), reward_pp=str(upper-lower))
                for pid, lower, upper in intervals]
    i = _intent(top_k=2)
    evaluations = MultiProductEvaluator().evaluate(products, base_store(), i, as_of=AS_OF,
                                                   subscription_date=SUBSCRIPTION_DATE)
    return products, evaluations


def test_top_k_uses_lowest_selected_lower_not_last_possible_rank():
    products, evaluations = _interval_candidates([("A", 1, 10), ("B", 8, 9), ("C", 7, 7)])
    ranking, _ = RankingService().rank(evaluations, {p.product_id: p for p in products},
                                      objective=RankingObjective.MAX_REALIZABLE_RATE, top_k=2)
    assert ranking.ordered_product_ids == ["A", "B", "C"]
    assert ranking.stability.stable_membership is False
    assert ranking.stability.current_kth_realizable_score == Decimal("1")


def test_frontier_keeps_challenger_to_any_selected_product():
    _, evaluations = _interval_candidates([("A", 1, 10), ("B", 8, 9), ("C", 7, 8), ("D", Decimal("6.5"), Decimal("6.5"))])
    assert "D" in RankingAwareQuestionPlanner().frontier_product_ids(evaluations, _intent(top_k=3))


def test_stable_membership_agrees_with_exhaustive_endpoint_completions():
    ranker = RankingService()
    for ranges in [((1, 10), (8, 9), (7, 7)), ((9, 10), (8, 9), (6, 7)), ((3, 8), (7, 7), (6, 6))]:
        ps, ev = _interval_candidates([(str(n), a, b) for n, (a,b) in enumerate(ranges)])
        r, _ = ranker.rank(ev, {p.product_id: p for p in ps}, objective=RankingObjective.MAX_REALIZABLE_RATE, top_k=2)
        if r.stability.stable_membership:
            selected = set(r.ordered_product_ids[:2])
            for values in cartesian_product(*ranges):
                actual = {str(n) for n in sorted(range(3), key=lambda n: (-values[n], n))[:2]}
                assert actual == selected


def _service(*plans):
    products = [make_product("VISIBLE", name="한빛알뜰적금"),
                make_product("NEVER-MENTIONED", name="달빛행복적금", product_type="TIME_DEPOSIT",
                             institution_id="HIDDEN_BANK")]
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: list(plans)})
    service = ApplicationService(products, user_fact_stores={USER_ID: base_store()},
        conversation_orchestrator=ConversationOrchestrator(LLMGateway(adapter)))
    session = service.create_search_session(user_id=USER_ID, intent=_intent(top_k=1).model_copy(update={"product_types": ["INSTALLMENT_SAVINGS"]}),
        as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    return service, session.search_session_id


def _business_hash(service, sid):
    r = service._runtime(sid)
    return canonical_hash({
        "session": r.session, "intent": r.intent, "facts": r.fact_store,
        "candidates": list(r.candidate_products), "evaluations": r.evaluations,
        "ranking": r.ranking, "recommendation": r.recommendation,
        "question": r.active_question, "question_history": r.question_history,
        "condition_states": r.condition_states, "turn": r.working_note_turn_sequence,
        "messages": r.recent_user_messages, "dialogue": r.visible_dialogue,
    })


@pytest.mark.parametrize("action", [
    {"operation": "SET_PRODUCT_EXCLUSION", "product_id": "NEVER-MENTIONED", "excluded": True},
    {"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {"upsert_excluded_institution_ids": ["HIDDEN_BANK"]}},
])
def test_llm_existing_but_ungrounded_target_rejected_atomically(action):
    service, sid = _service({"actions": [action]})
    before = _business_hash(service, sid)
    with pytest.raises(ValueError, match="grounded|GROUNDED"):
        service.handle_user_message(sid, message="기간만 6개월로 바꿔주세요")
    assert _business_hash(service, sid) == before


def test_explicit_product_name_remains_mutable():
    service, sid = _service({"actions": [{"operation": "SET_PRODUCT_EXCLUSION",
                                          "product_id": "VISIBLE", "excluded": True}]})
    service.handle_user_message(sid, message="한빛알뜰적금은 제외해주세요")
    assert "VISIBLE" in service.get_search_status(sid).excluded_product_ids


def test_prompt_has_no_unmentioned_product_allowlist():
    service, sid = _service()
    context = ConversationOrchestrator._project_context("기간만 바꿔주세요", service._conversation_context(service._runtime(sid)))
    assert "NEVER-MENTIONED" not in context["ALLOWED_OPERATIONS"]["product_ids"]
    assert "NEVER-MENTIONED" not in context["CURRENT_STATE_SNAPSHOT"].get("current_candidate_ids", [])


def test_completed_session_survives_rest_reads_without_business_changes():
    service, sid = _service()
    service.get_top_recommendations(sid, complete=True)
    before = _business_hash(service, sid)
    with TestClient(create_app(service=service)) as client:
        for suffix in ["recommendations", "questions/next", "recommendations/VISIBLE/preview", "recommendations", "state"]:
            response = client.get(f"/api/search-sessions/{sid}/{suffix}")
            assert response.status_code == 200, response.text
            assert _business_hash(service, sid) == before, suffix


def test_first_recommendation_and_detail_reads_are_pure():
    service, sid = _service()
    before = _business_hash(service, sid)
    service.get_product_recommendation_detail(sid, "VISIBLE", include_explanation=False)
    service.get_top_recommendations(sid)
    assert _business_hash(service, sid) == before


def test_detail_then_ranking_toggle_matches_fresh_control_session():
    a, sid = _service()
    b, control = _service()
    a.get_product_recommendation_detail(sid, "NEVER-MENTIONED", include_explanation=False)
    patch = IntentPatch(ranking_objective_patch=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST)
    for service, session_id in [(a, sid), (b, control)]:
        service.update_search_intent(session_id, patch=patch)
    assert a._runtime(sid).ranking.ordered_product_ids == b._runtime(control).ranking.ordered_product_ids
    assert "NEVER-MENTIONED" not in a._runtime(sid).evaluations


@pytest.mark.parametrize("message,expected_products,expected_institutions", [
    ("정기예금은 빼주세요", set(), set()),
    ("우리 조건은 기간만 바꿔주세요", set(), set()),
    ("하나만 더 보여주세요", set(), set()),
    ("우리은행 정기예금은 빼주세요", {"PRODUCT-A"}, {"BANK-A"}),
    ("PRODUCT-A를 제외해주세요", {"PRODUCT-A"}, set()),
    ("PRODUCT-A-OTHER를 제외해주세요", set(), set()),
])
def test_grounding_distinguishes_generic_words_and_exact_entities(message, expected_products, expected_institutions):
    from eligibility.search.operation_grounding import grounded_targets
    context = {
        "PRODUCT_CATALOG_SUMMARY": ["PRODUCT-A|BANK-A|정기예금", "PRODUCT-B|BANK-B|정기예금"],
        "INSTITUTION_CATALOG_SUMMARY": [
            {"institution_id": "BANK-A", "institution_name": "우리은행"},
            {"institution_id": "BANK-B", "institution_name": "하나은행"},
        ],
    }
    assert grounded_targets(message, context) == (expected_products, expected_institutions)


def test_visible_shared_rank_is_not_silently_resolved_to_one_product():
    response = {"actions": [{"operation": "SET_PRODUCT_EXCLUSION", "product_id": "RANK-A", "excluded": True}]}
    adapter = MockLLMAdapter({LLMPurpose.CONVERSATION_ORCHESTRATION: [response]})
    service = ApplicationService([make_product("RANK-A"), make_product("RANK-B")],
        user_fact_stores={USER_ID: base_store()},
        conversation_orchestrator=ConversationOrchestrator(LLMGateway(adapter)))
    session = service.create_search_session(user_id=USER_ID, intent=_intent(top_k=2),
        as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    sid = session.search_session_id
    assert [item.rank for item in service.get_top_recommendations(sid).top_products] == [1, 1]
    before = _business_hash(service, sid)
    with pytest.raises(ValueError, match="grounded"):
        service.handle_user_message(sid, message="1위 상품을 빼주세요")
    assert _business_hash(service, sid) == before


def test_explicitly_deferred_question_is_not_selected_by_get():
    service = ApplicationService([make_product("NEXT-A", reward_pp="1"), make_product("NEXT-B", reward_pp="2")],
                                 user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(user_id=USER_ID, intent=_intent(top_k=2),
        as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    sid = session.search_session_id
    assert service.get_next_question(sid) is not None
    service.skip_active_question(sid, select_next=False)
    before = _business_hash(service, sid)
    assert service.get_next_question(sid) is None
    assert _business_hash(service, sid) == before


def test_catalog_correction_preserves_historical_bytes_and_rates():
    import hashlib
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    index = json.loads((root / "data/financial_products/normalized/index.json").read_text())
    row = next(row for row in index["products"] if row["product_code"] == "INST-KR-000830-2-0001")
    active_path = root / row["path"]
    historical_path = active_path.with_name("v005.json")
    assert hashlib.sha256(historical_path.read_bytes()).hexdigest() == "37f9874ebd8782b858158ca46c328bac4f2372e19433d3be4647eb93729e5124"
    old = json.loads(historical_path.read_text())
    new = json.loads(active_path.read_text())
    assert new["return_policy"] == old["return_policy"]
    assert new["source_ref_ids"] == old["source_ref_ids"]
    assert new["version_metadata"]["previous_version"] == 5
    assert new["version"] == 6


def test_maximum_one_year_keeps_twelve_month_option():
    intent = _intent(1, "MAXIMUM")
    plan = intent.contribution_plan.model_copy(update={"selected_term_unit": TermUnit.YEAR})
    assert resolve_term(_term_product(), plan) == ContractTerm(value=12, unit=TermUnit.MONTH)


def test_pending_eligibility_cannot_certify_selected_membership():
    a = make_product("PENDING", base_rate="10")
    a = a.model_copy(update={"eligibility_rule": a.eligibility_rule.model_copy(update={"fact_type": "UNANSWERED_MANDATORY_FACT"})}, deep=True)
    ps = [a, make_product("KNOWN-B", base_rate="9"), make_product("KNOWN-C", base_rate="8")]
    ev = MultiProductEvaluator().evaluate(ps, base_store(), _intent(top_k=2), as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE)
    ranking, _ = RankingService().rank(ev, {p.product_id: p for p in ps}, objective=RankingObjective.MAX_REALIZABLE_RATE, top_k=2)
    assert ranking.stability.stable_membership is False


@pytest.mark.parametrize("message,accepted", [
    ("기간만 바꿔주세요", False),
    ("HIDDEN_BANK 제외 취소해줘", True),
])
def test_llm_institution_restore_requires_its_own_grounding(message, accepted):
    action = {"operation": "UPDATE_SEARCH_INTENT", "intent_patch": {
        "remove_excluded_institution_ids": ["HIDDEN_BANK"],
    }}
    service, sid = _service({"actions": [action]})
    service.update_search_intent(sid, patch=IntentPatch(
        upsert_excluded_institution_ids=["HIDDEN_BANK"]))
    before = _business_hash(service, sid)
    if accepted:
        service.handle_user_message(sid, message=message)
        assert service.get_search_intent(sid).excluded_institution_ids == []
    else:
        with pytest.raises(ValueError, match="grounded"):
            service.handle_user_message(sid, message=message)
        assert _business_hash(service, sid) == before
        assert service.get_search_intent(sid).excluded_institution_ids == ["HIDDEN_BANK"]
