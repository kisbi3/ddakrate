from __future__ import annotations
from datetime import date
from pathlib import Path
from decimal import Decimal
import json
from eligibility.catalog.loader import load_product_catalog, load_product_definition
from eligibility.application_service import ApplicationService
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
from eligibility.fixtures.kakao_26_week import kakao_26_week_product
from eligibility.fixtures.ibk_parent_benefit import ibk_parent_benefit_product
from eligibility.fixtures.hana_run import hana_run_product
from eligibility.schema.enums import RankingObjective
from eligibility.schema.search import ContributionPlan, ProductSearchIntent
from eligibility.schema.user_fact import UserFactStore
from eligibility.schema.evaluation import EvaluationContext

ROOT=Path(__file__).resolve().parents[1]/"data"/"product_catalog"

def _catalog(): return load_product_catalog(ROOT)

def test_catalog_contains_50_products(): assert len(_catalog())==50
def test_all_catalog_products_validate_as_product_definition():
    for path in (ROOT/"products").glob("*.json"): load_product_definition(path)
def test_all_product_ids_unique():
    ps=_catalog(); assert len({p.product_id for p in ps})==len(ps)
def test_all_metadata_identity_matches_product_definition():
    for p in _catalog():
        assert p.metadata is not None
        assert p.metadata.product_id==p.product_id and p.metadata.institution_id==p.institution_id and p.metadata.product_name==p.name
def test_all_products_have_official_source_reference():
    for p in _catalog(): assert p.metadata and p.metadata.source_reference and p.metadata.source_reference.source_url
def test_on_sale_products_have_verified_sale_source():
    manifest=json.loads((ROOT/"manifests"/"product_catalog_manifest.json").read_text(encoding="utf-8"))
    by={x["product_id"]:x for x in manifest["products"]}
    for p in _catalog():
        assert by[p.product_id]["official_sources"]
        assert by[p.product_id]["reviewed"] is True
def test_no_advertised_rate_exceeds_base_plus_cap():
    for p in _catalog(): assert p.advertised_max_rate <= p.base_rate + p.preferential_rate_cap
def test_all_action_paths_have_valid_lineage():
    # Strict ProductDefinition validation runs lineage validation.
    for p in _catalog(): p.model_validate(p.model_dump())
def test_all_product_features_are_typed():
    for p in _catalog():
        if p.metadata:
            assert all(f.feature_id and isinstance(f.feature_id,str) for f in p.metadata.features)
def test_catalog_loader_loads_all_products(): assert len(_catalog())==50
def test_golden_fixture_semantic_equivalence():
    expected={p.product_id:p for p in [shinhan_youth_first_product(),kakao_26_week_product(),ibk_parent_benefit_product(),hana_run_product()]}
    actual={p.product_id:p for p in _catalog()}
    for pid,p in expected.items(): assert actual[pid].model_dump()==p.model_dump()
    k=actual[kakao_26_week_product().product_id]
    assert getattr(k.preferential_rules[0].rule,"from_sequence",999) is None
def test_all_catalog_products_evaluator_smoke():
    engine=FinancialEligibilityEngine()
    store=UserFactStore(user_id="CATALOG-SMOKE")
    for p in _catalog():
        t=p.contract_term
        # Generic context; exact day/week maturity arithmetic is not needed for no-exception smoke.
        ctx=EvaluationContext(as_of=date(2026,8,20),subscription_date=date(2026,8,20),maturity_date=date(2031,8,20))
        out=engine.evaluate_product(p,store,ctx)
        assert out.product_id==p.product_id
def test_application_service_catalog_top5_smoke():
    products=_catalog()
    svc=ApplicationService(products,user_fact_stores={"CATALOG-SMOKE":UserFactStore(user_id="CATALOG-SMOKE")})
    intent=ProductSearchIntent(search_intent_id="INTENT-CATALOG-SMOKE",user_id="CATALOG-SMOKE",product_types=["INSTALLMENT_SAVINGS"],ranking_objective=RankingObjective.MAX_REALIZABLE_RATE,requested_top_k=5)
    session=svc.create_search_session(user_id="CATALOG-SMOKE",intent=intent,as_of=date(2026,8,20),subscription_date=date(2026,8,20))
    rec=svc.get_top_recommendations(session.search_session_id)
    assert 1 <= len(rec.top_products) <= 5
    assert all(x.product_id in {p.product_id for p in products} for x in rec.top_products)


def test_monthly_target_is_exact_or_explicitly_marked_as_unsupported():
    products = {product.product_id: product for product in _catalog()}
    selected = [
        products["HANA_CUSTOMER_CARE_SAVINGS_2026"],
        products["IBK_LOVE_SHARING_SAVINGS_20260805"],
    ]
    user_id = "TARGET-AMOUNT-USER"
    service = ApplicationService(
        selected,
        user_fact_stores={user_id: UserFactStore(user_id=user_id)},
    )
    intent = ProductSearchIntent(
        search_intent_id="INTENT-TARGET-AMOUNT",
        user_id=user_id,
        product_types=["INSTALLMENT_SAVINGS"],
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            frequency="MONTHLY",
            selected_term_value=12,
            selected_term_unit="MONTH",
        ),
    )
    session = service.create_search_session(
        user_id=user_id,
        intent=intent,
        as_of=date(2026, 8, 20),
        subscription_date=date(2026, 8, 20),
    )
    evaluations = service.evaluate_candidates(session.search_session_id)
    limited = evaluations["HANA_CUSTOMER_CARE_SAVINGS_2026"].contribution_projection
    exact = evaluations["IBK_LOVE_SHARING_SAVINGS_20260805"].contribution_projection

    assert limited.requested_periodic_amount == Decimal("300000")
    assert limited.planned_periodic_amount == Decimal("200000")
    assert limited.amount_match_status == "OUTSIDE_TOLERANCE"
    assert "목표 월 300,000원" in limited.planned_contribution_summary
    assert exact.planned_periodic_amount == Decimal("300000")
    assert exact.amount_match_status == "EXACT"
    assert exact.estimated_total_principal == Decimal("3600000")

def test_default_web_runtime_loads_normalized_on_sale_catalog(monkeypatch):
    from eligibility.web.runtime import build_web_runtime
    from eligibility.catalog.normalized_loader import load_normalized_product_catalog

    monkeypatch.setenv("LLM_PROVIDER", "MOCK")
    runtime = build_web_runtime()
    expected_count = len(load_normalized_product_catalog(verify_hashes=False))
    assert runtime.product_count == expected_count
    assert len(runtime.service.products) == expected_count
    assert runtime.sample_user_id is None
    assert runtime.user_data_mode == "CONVERSATIONAL_INPUT"
    assert runtime.service.user_fact_stores == {}


def test_packaged_catalog_mirror_is_loadable(monkeypatch):
    from eligibility.catalog.loader import load_default_product_catalog
    from eligibility.catalog import loader as catalog_loader

    packaged_root = Path(catalog_loader.__file__).resolve().parent / "data" / "product_catalog"
    monkeypatch.setenv("ELIGIBILITY_PRODUCT_CATALOG_PATH", str(packaged_root))
    products = load_default_product_catalog()
    assert len(products) == 50

def test_default_web_api_uses_normalized_catalog_and_returns_top5(monkeypatch):
    from fastapi.testclient import TestClient
    from eligibility.web.app import create_app

    monkeypatch.setenv("LLM_PROVIDER", "MOCK")
    client = TestClient(create_app())

    from eligibility.catalog.normalized_loader import load_normalized_product_catalog

    runtime = client.get("/api/runtime")
    assert runtime.status_code == 200
    expected_count = len(load_normalized_product_catalog(verify_hashes=False))
    assert runtime.json()["product_count"] == expected_count

    session = client.post(
        "/api/search-sessions",
        json={
            "user_id": "U001",
            "natural_language_query": "1년 정도 월 30만원을 넣을 적금 중 나에게 좋은 상품을 찾아줘.",
            "as_of": "2026-08-20",
            "subscription_date": "2026-08-20",
        },
    )
    assert session.status_code == 201
    search_session_id = session.json()["search_session_id"]

    recommendations = client.get(
        f"/api/search-sessions/{search_session_id}/recommendations"
    )
    assert recommendations.status_code == 200
    assert len(recommendations.json()["top_products"]) == 5


def test_mykids_eligibility_is_asked_with_bank_and_product_context():
    product = next(
        item
        for item in _catalog()
        if item.product_id == "KBANK_MYKIDS_SAVINGS_60M_20260819"
    )
    service = ApplicationService([product])
    intent = ProductSearchIntent(
        search_intent_id="INTENT-MYKIDS-CONTEXT",
        user_id="MYKIDS-USER",
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_REALIZABLE_RATE,
        requested_top_k=1,
    )
    session = service.create_search_session(user_id="MYKIDS-USER", intent=intent)

    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.request is not None
    assert question.request.fact_type == "KBANK_MYKIDS_ELIGIBLE"
    assert "케이뱅크" in question.product_context
    assert "만 17세 미만" in question.question
    assert "실제 가입 가능 여부" in question.explanation


def test_withus_eligibility_uses_official_customer_type_and_one_account_rules():
    product = next(
        item
        for item in _catalog()
        if item.product_id == "BNK_KYONGNAM_WITHUS_FREE_36M_2025"
    )
    assert product.eligibility_rule.name == "가입대상 및 1인 1좌"
    assert product.eligibility_rule.source is not None
    assert product.eligibility_rule.source.source_text == (
        "가입대상: 실명의 개인 및 개인사업자. 가입좌수: 1인 1좌."
    )

    service = ApplicationService([product])
    intent = ProductSearchIntent(
        search_intent_id="INTENT-WITHUS-ELIGIBILITY",
        user_id="WITHUS-USER",
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_REALIZABLE_RATE,
        requested_top_k=1,
    )
    session = service.create_search_session(user_id="WITHUS-USER", intent=intent)
    observed_questions = {}
    answers = {
        "BNK_WITHUS_REAL_NAME_INDIVIDUAL_OR_SOLE_PROPRIETOR": True,
        "BNK_WITHUS_EXISTING_ACCOUNT_HELD": False,
    }
    for _ in range(2):
        question = service.get_next_question(session.search_session_id)
        assert question is not None and question.request is not None
        fact_type = question.request.fact_type
        observed_questions[fact_type] = question.question
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer=answers[fact_type],
        )

    assert observed_questions == {
        "BNK_WITHUS_REAL_NAME_INDIVIDUAL_OR_SOLE_PROPRIETOR": (
            "BNK 위더스WithUs 자유적금은 실명의 개인 또는 개인사업자가 가입할 수 "
            "있어요. 본인 명의의 개인이나 개인사업자로 가입하시나요?"
        ),
        "BNK_WITHUS_EXISTING_ACCOUNT_HELD": (
            "현재 BNK 위더스WithUs 자유적금 계좌를 이미 보유하고 있나요?"
        ),
    }
    evaluation = service.evaluate_candidates(session.search_session_id)[product.product_id]
    assert evaluation.eligibility_status.value == "SATISFIED"


def test_equivalent_toss_auto_transfer_questions_are_grouped_once():
    product_ids = {
        "TOSS_CHILD_SAVINGS_20260714",
        "TOSS_FREE_SAVINGS_12M_20260508",
    }
    products = [item for item in _catalog() if item.product_id in product_ids]
    service = ApplicationService(products)
    intent = ProductSearchIntent(
        search_intent_id="INTENT-TOSS-SHARED-AUTO",
        user_id="TOSS-USER",
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_REALIZABLE_RATE,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("200000"),
            frequency="MONTHLY",
        ),
        requested_top_k=2,
    )
    session = service.create_search_session(user_id="TOSS-USER", intent=intent)

    question = service.get_next_question(session.search_session_id)
    while (
        question is not None
        and question.request is not None
        and question.request.fact_type
        in {
            "TOSS_CHILD_AGE_AND_ACCOUNT_ELIGIBLE",
            "ELIGIBLE_TOSS_FREE_SAVINGS_12M_20260508",
        }
    ):
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer=True,
        )
        question = service.get_next_question(session.search_session_id)

    assert question is not None and question.request is not None
    assert question.request.fact_type == "WILL_TOSS_MONTHLY_AUTO_TRANSFER_ALL"
    assert set(question.affected_product_ids) == product_ids
    assert question.question == (
        "12개월 동안 매달 자동이체가 빠짐없이 실행되도록 유지하실 수 있나요?"
    )
    assert question.product_context is not None
    assert question.product_context.startswith("자동이체 공통 조건")
