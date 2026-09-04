from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from eligibility.application_service import ApplicationService
from eligibility.catalog.normalized_loader import (
    _adapt_product,
    _load_institutions,
    product_with_scenario_rate,
    repository_root,
)
from eligibility.search.recommendation import (
    _canonical_preferential_conditions,
    _rate_snapshot_as_of,
)
from eligibility.search.contribution import ContributionPlanner
from eligibility.engine.rate_engine import RateEngine
from eligibility.schema.enums import (
    ContributionFrequency,
    EvaluationStatus,
    TermUnit,
    VerificationLevel,
)
from eligibility.schema.evaluation import RuleEvaluation
from eligibility.schema.product import ContractTerm
from eligibility.schema.search import ContributionPlan, ProductSearchIntent


ROOT = repository_root()
SHARD = ROOT / "data/ddakrate_parking_account_latest_full_20260830"
BUILD = (
    SHARD
    / "data/financial_products/category_builds/20260829-parking-account-01"
)
PUBLISHED_PARKING = ROOT / "data/financial_products/normalized/products/parking_account"


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _products():
    sources = {
        row["source_id"]: row
        for row in _read_json(ROOT / "data/financial_products/normalized/source_documents.json")[
            "source_documents"
        ]
    }
    sources.update(
        {
            row["source_id"]: row
            for row in _read_json(BUILD / "source_documents.fragment.json")["source_documents"]
        }
    )
    evidence = {
        row["evidence_ref_id"]: row
        for row in _read_json(ROOT / "data/financial_products/normalized/evidence_refs.json")[
            "evidence_refs"
        ]
    }
    evidence.update(
        {
            row["evidence_ref_id"]: row
            for row in _read_json(BUILD / "evidence_refs.fragment.json")["evidence_refs"]
        }
    )
    institutions = _load_institutions(ROOT)
    products = {}
    for row in _read_jsonl(BUILD / "index_rows.jsonl"):
        # row["path"] points into the shard's staging copy, which .gitignore excludes.
        # All 243 of those product files are byte-identical to the published catalog
        # except INST-KR-000216-3-0001, where the shard holds older rule titles
        # ("...조각") that the published version rewords and no test asserts on.
        # Read the published artifact so this exercises what actually ships.
        raw = _read_json(
            PUBLISHED_PARKING
            / row["institution_id"]
            / row["product_code"]
            / f"v{row['version']:03d}.json"
        )
        products[row["product_code"]] = _adapt_product(
            raw,
            institutions[row["institution_id"]],
            {},
            sources,
            evidence,
        )
    return products


def _scenario(product, balance: str):
    return product_with_scenario_rate(
        product,
        ContractTerm(value=1, unit=TermUnit.YEAR),
        balance=Decimal(balance),
        as_of=date(2026, 8, 31),
    )


def _satisfied_results(product):
    return [
        RuleEvaluation(
            rule_id=item.rule.rule_id,
            rule_name=item.rule.name,
            status=EvaluationStatus.SATISFIED,
            reason_code="TEST",
            verification_level=VerificationLevel.INSTITUTION_VERIFIED,
            evidence_levels=[VerificationLevel.INSTITUTION_VERIFIED],
        )
        for item in product.preferential_rules
    ]


def test_full_parking_shard_adapts_without_identity_loss():
    products = _products()
    assert len(products) == 220
    assert all(product.product_type == "PARKING_ACCOUNT" for product in products.values())
    assert sum(len(product.preferential_rules) for product in products.values()) == 272
    assert sum(bool(product.preferential_rules) for product in products.values()) == 114


def test_marginal_tranche_base_rate_is_balance_weighted():
    product = _scenario(_products()["INST-KR-000125-3-CBECD36EF2E"], "15000000000")
    assert product.base_rate == Decimal("2.333333333333333333333333333")


def test_non_executable_dynamic_schedule_never_leaks_into_ranking():
    product = _scenario(_products()["INST-KR-000856-3-CEBE213F637"], "15000000")
    assert product.base_rate is None


def test_capped_portion_scales_additive_reward_to_scenario_balance():
    product = _scenario(_products()["INST-KR-000010-3-0001"], "100000000")
    assert len(product.preferential_rules) == 1
    assert product.preferential_rules[0].reward.value == Decimal("0.350")


def test_exclusive_rules_use_highest_reward_without_double_counting():
    product = _scenario(_products()["INST-KR-000010-3-CA5A796E7E0"], "1000000")
    summary = RateEngine.calculate(product, _satisfied_results(product), [])
    assert summary.confirmed_rate == Decimal("2.00")
    assert summary.realizable_rate == Decimal("2.00")


def test_accuon_money_gathering_uses_challenge_term_and_target_schedule():
    product = _products()["INST-KR-000055-3-C4424004A31"]

    assert product.normalized.product_subtype == "CHALLENGE_GOAL_ACCOUNT"
    assert product.normalized.term_policy == {
        "kind": "RANGE",
        "min_value": 4,
        "max_value": 26,
        "representative_value": 26,
        "unit": "WEEK",
        "source_ref_ids": ["EVD-PARK-20260829-INST-KR-000055-3-C4424004A31"],
    }
    assert product.contract_term == ContractTerm(value=26, unit=TermUnit.WEEK)
    assert product.base_rate == Decimal("2.000")

    planned = ContributionPlanner().build(
        product,
        ContributionPlan(
            desired_periodic_amount=Decimal("3000000"),
            frequency=ContributionFrequency.FLEXIBLE,
            selected_term_value=26,
            selected_term_unit=TermUnit.WEEK,
        ),
        subscription_date=date(2026, 9, 1),
    )

    assert planned.not_comparable_reason is None
    assert planned.core_plan is not None
    assert planned.core_plan.schedule_label == "WEEKLY_CHALLENGE_TARGET"
    assert planned.projection.term_summary == "26주"
    assert planned.projection.contribution_count == 26
    assert planned.projection.planned_periodic_amount == Decimal("115386")
    assert "2계좌(1,500,000원 + 1,500,000원)" in planned.projection.planned_contribution_summary
    assert planned.projection.estimated_total_principal == Decimal("3000036")


def test_replace_base_on_capped_tranche_uses_weighted_effective_rate():
    product = _scenario(_products()["INST-KR-000046-3-C54E2F4B43B"], "1000000")
    summary = RateEngine.calculate(product, _satisfied_results(product), [])
    assert summary.confirmed_rate == Decimal("0.905")


def test_internal_fallback_customer_scope_is_structured_from_official_review():
    product = _products()["INST-KR-000129-3-0002"]
    assert product.eligibility_rule.rule_id.endswith(":APPLICATION_CAPACITY")
    assert product.eligibility_rule.expected == ["CORPORATION", "SOLE_PROPRIETOR"]


def test_unstructured_relationship_text_does_not_become_a_generic_yes_no_gate():
    product = _products()["INST-KR-000204-3-C01818F99A4"]

    def fact_types(rule):
        yield getattr(rule, "fact_type", None)
        for child in getattr(rule, "children", []) or []:
            yield from fact_types(child)
        child = getattr(rule, "child", None)
        if child is not None:
            yield from fact_types(child)

    facts = set(fact_types(product.eligibility_rule))
    assert not any(
        fact and fact.startswith("NORMALIZED_ELIGIBILITY::") for fact in facts
    )
    assert (
        "EXISTING_PRODUCT_ACCOUNT_LIMIT_REACHED::INST-KR-000204-3-C01818F99A4"
        in facts
    )


def test_institution_account_question_clears_current_product_account_limit_only():
    product = _products()["INST-KR-000204-3-C01818F99A4"]
    service = ApplicationService([product], pre_search_enabled=False)
    session = service.create_search_session(
        user_id="INSTITUTION-ACCOUNT-USER",
        intent=ProductSearchIntent(
            search_intent_id="INSTITUTION-ACCOUNT-INTENT",
            user_id="INSTITUTION-ACCOUNT-USER",
            product_types=["PARKING_ACCOUNT"],
            application_capacity="INDIVIDUAL",
            contribution_plan=ContributionPlan(preferred_start_amount=3_000_000),
        ),
    )

    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.request is not None
    assert question.request.fact_type == "INSTITUTION_ACCOUNT_HELD::INST-KR-000204"
    assert "전북은행" in question.question

    service.submit_user_answer(session.search_session_id, answer=False)
    active = service._runtime(session.search_session_id).fact_store.active_facts
    assert any(
        fact.fact_type
        == "EXISTING_PRODUCT_ACCOUNT_LIMIT_REACHED::INST-KR-000204-3-C01818F99A4"
        and fact.value is False
        for fact in active
    )


def test_canonical_preferential_rules_are_available_to_product_detail():
    product = _products()["INST-KR-000223-3-0013"]
    conditions = _canonical_preferential_conditions(product.normalized.return_policy)
    assert [item["rule_id"] for item in conditions] == [
        "INST-KR-000223-3-0013-PREF-01",
        "INST-KR-000223-3-0013-PREF-02",
    ]
    assert conditions[0]["reward"]["kind"] == "ADD_RATE"
    assert conditions[0]["condition"]["operator"] == "OR"


def test_rate_snapshot_date_reads_canonical_calculation_location():
    product = _products()["INST-KR-000223-3-0013"]
    entry = product.normalized.return_policy["rate_entries"][0]
    assert _rate_snapshot_as_of(entry) == "2026-08-29"


def test_subscription_age_preferential_reuses_shared_age_years_fact():
    product = _products()["INST-KR-000341-3-0001"]

    def fact_types(rule):
        values = {rule.fact_type} if hasattr(rule, "fact_type") else set()
        for child in getattr(rule, "children", []):
            values.update(fact_types(child))
        return values

    age_rule = next(
        item.rule
        for item in product.preferential_rules
        if "17세 이상 39세 이하" in item.rule.name
    )

    assert fact_types(age_rule) == {"AGE_YEARS"}
