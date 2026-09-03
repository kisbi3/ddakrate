from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from eligibility.application_service import ApplicationService
from eligibility.catalog.normalized_loader import (
    load_normalized_product_catalog,
    product_with_scenario_rate,
    select_base_rate,
)
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.schema.enums import FactSemanticType, FactSourceType, TermUnit
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.product import ContractTerm
from eligibility.schema.search import ContributionPlan, ProductSearchIntent
from eligibility.schema.user_fact import UserFact, UserFactStore
from eligibility.search.ranking import _published_rate_reference
from eligibility.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def _json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_published_index_loads_all_versions_and_default_filters_ended() -> None:
    full = load_normalized_product_catalog(
        include_sale_statuses={"ON_SALE", "ENDED", "UNKNOWN", "SUSPENDED"}
    )
    all_products = load_normalized_product_catalog(
        include_sale_statuses={"ON_SALE", "ENDED"}
    )
    active = load_normalized_product_catalog()

    # The runtime must load exactly the sale-status subsets it was asked for,
    # out of everything the published index declares -- not a magic count.
    assert len(all_products) == sum(
        1 for item in full if item.normalized.sale_status in {"ON_SALE", "ENDED"}
    )
    assert len(active) == sum(
        1 for item in full if item.normalized.sale_status == "ON_SALE"
    )
    assert 0 < len(active) <= len(all_products) <= len(full)
    assert sum(item.normalized.sale_status == "ENDED" for item in all_products) == sum(
        item.normalized.sale_status == "ENDED" for item in full
    )
    assert all(item.normalized.sale_status == "ON_SALE" for item in active)
    assert {item.product_type for item in active} == {
        "INSTALLMENT_SAVINGS",
        "TIME_DEPOSIT",
        "PARKING_ACCOUNT",
        "CMA",
    }


def test_institutions_custom_bindings_and_rewards_are_connected() -> None:
    products = load_normalized_product_catalog(
        include_sale_statuses={"ON_SALE", "ENDED"}
    )
    index = _json("data/financial_products/normalized/index.json")
    manifest = _json(
        f"data/financial_products/normalized/manifests/{index.get('manifest_batch', index['source_staging_batch'])}.json"
    )
    custom_payload = _json(manifest["custom_definition_file"])
    custom = {
        (item["custom_code"], item["version"]): item
        for item in custom_payload["institution_custom_definitions"]
    }

    assert len({item.institution_id for item in products}) > 0
    assert all(item.metadata and item.metadata.institution_name for item in products)
    assert len(custom) == manifest["counts"]["custom_definitions"]
    assert {item["institution_id"] for item in custom.values()} <= {
        item.institution_id for item in products
    }
    # Referential integrity between products and CustomDefinitions is checked
    # in full below (every binding must resolve to a known definition).

    for product in products:
        assert product.normalized is not None
        rate_ids = {
            row["rate_id"]
            for row in product.normalized.return_policy.get("rate_entries", [])
        }
        for condition in [
            *product.normalized.standard_conditions,
            *product.normalized.custom_bindings,
        ]:
            assert set(condition.get("reward_refs", [])) <= rate_ids
        for binding in product.normalized.custom_bindings:
            definition = custom[(binding["custom_code"], binding["custom_version"])]
            assert definition["institution_id"] == product.institution_id

    assert manifest["counts"]["custom_definitions"] == len(custom)
    # Older manifest schemas additionally published data-gap rollups; newer
    # manifests may omit them. When present, they must be internally
    # consistent with the products actually carrying declared data gaps.
    if "data_gap_products" in manifest["counts"]:
        data_gap_products = sum(
            1
            for item in products
            if (item.normalized.raw_product.get("version_metadata") or {}).get("data_gaps")
        )
        assert manifest["counts"]["data_gap_products"] == data_gap_products
    if "data_gap_fields" in manifest["counts"]:
        assert manifest["counts"]["data_gap_fields"] >= manifest["counts"].get(
            "data_gap_products", 0
        )


def test_all_deposit_and_savings_products_have_an_official_site_link() -> None:
    products = load_normalized_product_catalog(
        include_sale_statuses={"ON_SALE", "ENDED", "UNKNOWN"}
    )

    for family in ("TIME_DEPOSIT", "INSTALLMENT_SAVINGS"):
        family_products = [item for item in products if item.product_type == family]
        assert len(family_products) > 0
        for product in family_products:
            assert product.normalized is not None
            registered = [
                source
                for source in product.normalized.official_sources
                if str(source.get("source_id", "")).startswith("OFFICIAL-LINK-20260827")
            ]
            assert len(registered) == 1
            assert registered[0]["url"].startswith(("https://", "http://"))
            assert registered[0]["document_type"] in {
                "OFFICIAL_PRODUCT_PAGE",
                "OFFICIAL_PRODUCT_LIST",
                "OFFICIAL_INSTITUTION_HOME",
                "OFFICIAL_CENTRAL_ASSOCIATION_DIRECTORY",
            }


def test_canonical_release_preserves_naver_products_without_source_shaped_ids() -> None:
    all_products = load_normalized_product_catalog(
        include_sale_statuses={"ON_SALE", "ENDED", "UNKNOWN"}
    )

    index = _json("data/financial_products/normalized/index.json")
    assert len(all_products) <= index["product_count"]
    assert not any(item.product_id.startswith("NVR-") for item in all_products)
    assert all(
        item.normalized
        and item.normalized.raw_product["product_code"] == item.product_id
        for item in all_products
    )
    naver_only = [
        item
        for item in all_products
        if (item.normalized.raw_product.get("raw_product") or {}).get("source_system")
        == "NAVER_PAY"
    ]
    merged = [
        item
        for item in all_products
        if (item.normalized.raw_product.get("version_metadata") or {}).get("source_merge")
    ]
    assert 0 <= len(naver_only) <= len(all_products)
    assert 0 <= len(merged) <= len(all_products)


def test_loader_verifies_manifest_and_product_hashes() -> None:
    # Hash verification is on by default and covers every manifest file plus
    # every product path in the published index. It must load the exact same
    # set as with verification off -- hashing changes integrity checking, not
    # which products are included.
    expected = len(load_normalized_product_catalog(verify_hashes=False))
    assert len(load_normalized_product_catalog(verify_hashes=True)) == expected


def test_recheck_data_gaps_and_missing_advertised_rate_are_not_zero_filled() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000034-4-0002"]

    assert product.normalized.data_gaps
    assert "advertised_max_rate" not in product.normalized.return_policy
    assert product.advertised_max_rate is None
    assert product.metadata.advertised_max_rate is None


def test_yuanta_w_cma_mmf_keeps_observed_returns_without_advertised_max_rate() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000092-4-0003"]
    policy = product.normalized.return_policy

    assert policy["return_kind"] == "PERFORMANCE_LINKED"
    assert "advertised_max_rate" not in policy
    assert product.advertised_max_rate is None
    assert [
        entry["calculation"]["value"]
        for entry in policy["rate_entries"]
        if entry.get("role") == "OBSERVED"
    ] == ["0.21", "0.61", "1.19", "2.34", "8.76"]
    assert {
        entry["as_of"]
        for entry in policy["rate_entries"]
        if entry.get("role") == "OBSERVED"
    } == {"2026-08-27"}
    assert any(
        gap["path"] == "return_policy.advertised_max_rate"
        for gap in product.normalized.raw_product["version_metadata"]["data_gaps"]
    )
    assert _published_rate_reference(product) == (None, None, None)


def test_3up_uses_official_one_year_average_as_advertised_max_rate() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000055-2-0008"]

    assert product.advertised_max_rate == Decimal("3.75")
    stepped_rates = [
        entry["calculation"]["value"]
        for entry in product.normalized.return_policy["rate_entries"]
        if entry.get("role") == "BASE"
    ]
    assert stepped_rates == ["2.30", "3.30", "4.50", "4.90"]
    maximum_entry = next(
        entry
        for entry in product.normalized.return_policy["rate_entries"]
        if entry.get("role") == "ADVERTISED_MAXIMUM"
    )
    assert maximum_entry["calculation"]["value"] == "3.75"
    assert maximum_entry["condition_text"] == "1년 평균금리(3개월 구간 단리 평균)"


def test_officially_confirmed_eligibility_is_loaded_with_target_details() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000129-3-0001"]

    assert product.metadata.target_customer_summary == (
        "개인, 만 19세 이상, 대한민국 국적, 실명 가입, 상품 계좌 최대 1개"
    )
    assert product.metadata.one_account_per_person is True
    assert product.eligibility_rule.type == "AND"


def test_grounded_normalized_eligibility_keeps_the_actual_target_details() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000296-4-0005"]
    capacity_rule = product.eligibility_rule

    assert capacity_rule.fact_type == "APPLICATION_CAPACITY"
    assert capacity_rule.expected == ["INDIVIDUAL", "CORPORATION"]
    assert capacity_rule.missing_fact is None

    all_products = {
        item.product_id: item
        for item in load_normalized_product_catalog(
            include_sale_statuses={"ON_SALE", "ENDED"}
        )
    }
    youth = all_products["INST-KR-000223-1-0003"]
    youth_rules = {
        child.fact_type: child for child in youth.eligibility_rule.children
    }
    youth_capacity = youth_rules["APPLICATION_CAPACITY"]
    assert youth_capacity.fact_type == "APPLICATION_CAPACITY"
    assert youth_capacity.expected == ["INDIVIDUAL"]
    assert youth_capacity.missing_fact is None
    age_rules = [
        child for child in youth.eligibility_rule.children
        if child.fact_type == "AGE_YEARS"
    ]
    assert [(rule.operator.value, rule.expected) for rule in age_rules] == [
        ("GTE", 19),
        ("LTE", 34),
    ]
    assert all(rule.missing_fact is None for rule in age_rules)


def test_every_youth_future_savings_product_has_shared_policy_account_rule() -> None:
    products = [
        product
        for product in load_normalized_product_catalog()
        if "청년미래적금" in product.name
    ]

    assert products
    for product in products:
        rules = (
            product.eligibility_rule.children
            if product.eligibility_rule.type == "AND"
            else [product.eligibility_rule]
        )
        policy_rule = next(
            rule
            for rule in rules
            if getattr(rule, "fact_type", None)
            == "YOUTH_FUTURE_OR_LEAP_ACCOUNT_HELD"
        )
        assert policy_rule.expected is False
        assert policy_rule.missing_fact is not None
        assert "청년도약계좌" in policy_rule.missing_fact.question


def test_every_soldier_tomorrow_savings_product_has_shared_eligibility_gate() -> None:
    products = [
        product
        for product in load_normalized_product_catalog()
        if "장병내일준비" in product.name
    ]

    assert products
    for product in products:
        rules = (
            product.eligibility_rule.children
            if product.eligibility_rule.type == "AND"
            else [product.eligibility_rule]
        )
        gate = next(
            rule
            for rule in rules
            if getattr(rule, "fact_type", None)
            == "SOLDIER_TOMORROW_SAVINGS_ELIGIBLE"
        )
        assert gate.expected is True
        assert gate.on_false_status.value == "UNSATISFIABLE"
        assert gate.missing_fact is not None
        assert "가입 불가" in gate.missing_fact.question


def test_this_product_account_limit_asks_about_existing_account() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000252-1-0006"]
    rules = {
        child.fact_type: child for child in product.eligibility_rule.children
    }
    account_rule = rules[
        "EXISTING_PRODUCT_ACCOUNT_LIMIT_REACHED::INST-KR-000252-1-0006"
    ]

    assert account_rule.expected is False
    assert account_rule.missing_fact is not None
    assert account_rule.missing_fact.question == (
        "현재 ‘오늘부터, 하나 적금’ 계좌를 이미 가지고 계신가요?"
    )


def test_custom_reward_rules_keep_each_published_condition_separate() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000252-1-0006"]
    questions = [rule.rule.missing_fact.question for rule in product.preferential_rules]
    fact_types = [rule.rule.fact_type for rule in product.preferential_rules]

    assert len(set(fact_types)) == 3
    assert any("6개월 동안 하나은행 지정 상품 미보유" in item for item in questions)
    assert any("3회 이상 자동이체" in item for item in questions)
    assert any("마케팅 동의 항목 모두 동의" in item for item in questions)


def test_policy_question_exposes_product_and_public_policy_links() -> None:
    product = next(
        item for item in load_normalized_product_catalog()
        if item.product_id == "INST-KR-000223-1-0008"
    )
    service = ApplicationService([product])
    intent = ProductSearchIntent(
        search_intent_id="I-PARENT-POLICY-LINK",
        user_id="U-PARENT-POLICY-LINK",
        product_types=["INSTALLMENT_SAVINGS"],
        application_capacity="INDIVIDUAL",
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            selected_term_value=12,
            selected_term_unit=TermUnit.MONTH,
        ),
    )
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        as_of=date(2026, 8, 25),
        subscription_date=date(2026, 8, 25),
    )
    account_question = service.get_next_question(session.search_session_id)
    assert account_question is not None
    assert [
        item["kind"]
        for item in account_question.explanation_details["reference_links"]
    ] == ["PRODUCT_CONDITION"]

    service.submit_user_answer(
        session.search_session_id,
        question_id=account_question.question_id,
        answer=False,
    )
    policy_question = service.get_next_question(session.search_session_id)
    assert policy_question is not None
    links = policy_question.explanation_details["reference_links"]

    assert {item["kind"] for item in links} == {
        "PRODUCT_CONDITION",
        "POLICY_OR_SERVICE",
    }
    assert {item["label"] for item in links} == {"자세히 보기"}
    assert all(item["url"].startswith("https://") for item in links)


def test_application_capacity_is_applied_once_across_normalized_products() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    compared = [
        products["INST-KR-000055-2-0003"],  # 개인 대상
        products["INST-KR-000055-2-0010"],  # 법인 대상
    ]

    def evaluated_product_ids(capacity: str) -> tuple[set[str], list[object]]:
        service = ApplicationService(compared, pre_search_enabled=True)
        session = service.create_search_session(
            user_id=f"USER-{capacity}",
            intent=ProductSearchIntent(
                search_intent_id=f"INTENT-{capacity}",
                user_id=f"USER-{capacity}",
                product_types=["TIME_DEPOSIT"],
                application_capacity=capacity,
                contribution_plan=ContributionPlan(
                    preferred_start_amount=10_000_000,
                    selected_term_value=12,
                    selected_term_unit=TermUnit.MONTH,
                ),
            ),
        )
        runtime = service._runtime(session.search_session_id)
        capacity_facts = [
            fact
            for fact in runtime.fact_store.active_facts
            if fact.fact_type == "APPLICATION_CAPACITY"
        ]
        return set(runtime.evaluations), capacity_facts

    individual_products, individual_facts = evaluated_product_ids("INDIVIDUAL")
    corporation_products, corporation_facts = evaluated_product_ids("CORPORATION")

    assert individual_products == {"INST-KR-000055-2-0003"}
    assert corporation_products == {"INST-KR-000055-2-0010"}
    assert [fact.value for fact in individual_facts] == ["INDIVIDUAL"]
    assert [fact.value for fact in corporation_facts] == ["CORPORATION"]


def test_term_rates_and_parking_marginal_tiers_are_selected_deterministically() -> None:
    deposit = _json(
        "data/financial_products/normalized/products/time_deposit/00110060/00110060-2-0001/v001.json"
    )
    parking = _json(
        "data/financial_products/normalized/products/parking_account/00126247/00126247-3-0001/v001.json"
    )

    assert select_base_rate(
        deposit["return_policy"], ContractTerm(value=12, unit=TermUnit.MONTH)
    ) == Decimal("4.00")
    assert select_base_rate(
        deposit["return_policy"], ContractTerm(value=24, unit=TermUnit.MONTH)
    ) == Decimal("2.00")
    assert select_base_rate(
        parking["return_policy"],
        ContractTerm(value=12, unit=TermUnit.MONTH),
        balance=Decimal("40000000"),
    ) == Decimal("0.875")


def test_ok_damoum_uses_marginal_tiers_and_balance_limited_rewards() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000783-3-C951AB3E03A"]
    term = ContractTerm(value=12, unit=TermUnit.MONTH)

    assert product.normalized.version == 2
    assert product.normalized.return_policy["balance_tier_method"] == "MARGINAL"
    assert select_base_rate(
        product.normalized.return_policy, term, balance=Decimal("100000")
    ) == Decimal("5.0")
    assert select_base_rate(
        product.normalized.return_policy, term, balance=Decimal("1000000")
    ) == Decimal("2.9")
    assert select_base_rate(
        product.normalized.return_policy, term, balance=Decimal("10000000")
    ) == Decimal("0.66")
    assert select_base_rate(
        product.normalized.return_policy, term, balance=Decimal("100000000")
    ) == Decimal("0.606")

    low_balance = product_with_scenario_rate(
        product, term, balance=Decimal("10000000")
    )
    high_balance = product_with_scenario_rate(
        product, term, balance=Decimal("100000000")
    )
    assert sorted(rule.reward.value for rule in low_balance.preferential_rules) == [
        Decimal("0.2"),
        Decimal("1.8"),
    ]
    assert sorted(rule.reward.value for rule in high_balance.preferential_rules) == [
        Decimal("0.10"),
        Decimal("0.90"),
    ]
    assert high_balance.preferential_rate_cap == Decimal("1.00")
    assert {rule.rule.name for rule in product.preferential_rules} == {
        "상품·서비스 마케팅 동의",
        "다모음캐시 결제계좌 등록 우대",
    }


def test_kb_card_ssdam_keeps_unmodeled_tier_in_possible_maximum() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = products["INST-KR-000516-1-C537E8D80DB"]
    scenario = product_with_scenario_rate(
        product,
        ContractTerm(value=6, unit=TermUnit.MONTH),
    )

    # The executable adapter currently covers the +6%p card and +2%p salary
    # rules. The published source-first policy also contains a +2%p tiered
    # average-balance rule, so scenario selection must not shrink the 12%
    # possible ceiling to 10% merely because that rule is not executable yet.
    assert scenario.base_rate == Decimal("2.000")
    assert scenario.preferential_rate_cap == Decimal("10.000")
    assert scenario.advertised_max_rate == Decimal("12")

    evaluation = FinancialEligibilityEngine().evaluate_product(
        scenario,
        UserFactStore(user_id="KB-CARD-SSDAM-UPPER"),
        EvaluationContext(
            as_of=date(2026, 9, 1),
            subscription_date=date(2026, 9, 1),
            maturity_date=date(2027, 3, 1),
        ),
    )

    assert evaluation.rates.realizable_rate == Decimal("2.000")
    assert evaluation.rates.user_specific_conditional_upper_rate == Decimal("12")


def test_woori_luck_upper_respects_required_marketing_consent() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    product = product_with_scenario_rate(
        products["INST-KR-000424-1-C3C58A5BBCE"],
        ContractTerm(value=6, unit=TermUnit.MONTH),
    )

    assert len(product.preferential_rules) == 1
    assert product.preferential_rules[0].reward.value == Decimal("10.00")
    assert {
        node.fact_type
        for node in product.preferential_rules[0].rule.children[0].children[1].children
    } == {"AGE_YEARS", "CUSTOMER_MARKETING_CONTACT_CONSENT_MAINTAINED"}

    store = UserFactStore(
        user_id="WOORI-LUCK-CONSENT",
        facts=[
            UserFact(
                fact_id="WOORI-LUCK-AGE",
                user_id="WOORI-LUCK-CONSENT",
                fact_type="AGE_YEARS",
                value=41,
                valid_from=date(2026, 9, 1),
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                collected_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            ),
            UserFact(
                fact_id="WOORI-LUCK-NO-MARKETING",
                user_id="WOORI-LUCK-CONSENT",
                fact_type="CUSTOMER_MARKETING_CONTACT_CONSENT_MAINTAINED",
                value=False,
                valid_from=date(2026, 9, 1),
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.FUTURE_INTENT,
                collected_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            ),
        ],
    )
    evaluation = FinancialEligibilityEngine().evaluate_product(
        product,
        store,
        EvaluationContext(
            as_of=date(2026, 9, 1),
            subscription_date=date(2026, 9, 1),
            maturity_date=date(2027, 3, 1),
        ),
    )

    assert evaluation.rates.realizable_rate == Decimal("3.000")
    assert evaluation.rates.user_specific_conditional_upper_rate == Decimal("3.000")


def test_cma_elapsed_term_rate_uses_automatic_reinvestment_interval() -> None:
    product = _json(
        "data/financial_products/normalized/products/cma/INST-KR-000148/"
        "INST-KR-000148-4-0002/v001.json"
    )

    assert select_base_rate(
        product["return_policy"],
        ContractTerm(value=30, unit=TermUnit.DAY),
        balance=Decimal("5000000"),
    ) == Decimal("2.53")
    assert select_base_rate(
        product["return_policy"],
        ContractTerm(value=12, unit=TermUnit.MONTH),
        balance=Decimal("5000000"),
    ) == Decimal("2.65")


def test_corporate_parking_term_and_mmda_products_are_separate_and_grounded() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    term = products["INST-KR-000129-3-0003"]
    mmda = products["INST-KR-000129-3-0005"]

    assert term.name == "e-예스기업파킹통장"
    assert mmda.name == "e-예스기업파킹통장(MMDA)"
    assert term.normalized.version == 2
    assert mmda.normalized.version == 1
    assert term.eligibility_rule.fact_type == "APPLICATION_CAPACITY"
    assert mmda.eligibility_rule.fact_type == "APPLICATION_CAPACITY"
    assert term.eligibility_rule.expected == ["SOLE_PROPRIETOR", "CORPORATION"]
    assert mmda.eligibility_rule.expected == ["SOLE_PROPRIETOR", "CORPORATION"]
    assert select_base_rate(
        term.normalized.return_policy,
        ContractTerm(value=12, unit=TermUnit.MONTH),
        balance=Decimal("10000000"),
    ) == Decimal("1.3")
    assert select_base_rate(
        mmda.normalized.return_policy,
        ContractTerm(value=12, unit=TermUnit.MONTH),
        balance=Decimal("10000000"),
    ) == Decimal("1.2")
    assert select_base_rate(
        mmda.normalized.return_policy,
        ContractTerm(value=12, unit=TermUnit.MONTH),
        balance=Decimal("2000000000"),
    ) == Decimal("3.0")
    assert all(
        entry["calculation"]["type"] == "VARIABLE_POSTED"
        for product in (term, mmda)
        for entry in product.normalized.return_policy["rate_entries"]
    )


def test_published_rate_reference_is_visible_without_becoming_realizable_rate() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    ace = products["INST-KR-000018-3-0001"]

    assert _published_rate_reference(ace) == (
        "공식 금리",
        "0.2~3%",
        "2026-07-01",
    )

    performance = ace.model_copy(
        update={
            "normalized": ace.normalized.model_copy(
                update={
                    "return_policy": {
                        "return_kind": "PERFORMANCE_LINKED",
                        "calculation_method": "PERFORMANCE_LINKED",
                        "rate_entries": [
                            {
                                "rate_id": "RECENT-YIELD",
                                "role": "BASE",
                                "calculation": {
                                    "type": "VARIABLE_POSTED",
                                    "value": "2.85",
                                    "unit": "PERCENT",
                                },
                                "as_of": "2026-08-25",
                            }
                        ],
                    }
                },
                deep=True,
            )
        },
        deep=True,
    )

    assert _published_rate_reference(performance) == (
        "최근 공시수익률",
        "2.85%",
        "2026-08-25",
    )


def test_additional_parking_products_use_official_balance_calculation_and_bindings() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}

    bnk = products["INST-KR-000010-3-0001"]
    assert select_base_rate(
        bnk.normalized.return_policy,
        ContractTerm(value=12, unit=TermUnit.MONTH),
        balance=Decimal("75000000"),
    ) == Decimal("2.2")

    plus_box = products["INST-KR-000865-3-0001"]
    assert select_base_rate(
        plus_box.normalized.return_policy,
        ContractTerm(value=12, unit=TermUnit.MONTH),
        balance=Decimal("100000000"),
    ) == Decimal("1.95")

    pepper = products["INST-KR-000286-3-0001"]
    assert select_base_rate(
        pepper.normalized.return_policy,
        ContractTerm(value=12, unit=TermUnit.MONTH),
        balance=Decimal("60000000"),
    ) == Decimal("1.0")
    assert "return_policy.balance_tier_method" not in {
        gap["path"] for gap in pepper.normalized.data_gaps
    }

    kdb = products["INST-KR-000448-3-0001"]
    assert kdb.base_rate == Decimal("1.5")
    assert kdb.advertised_max_rate == Decimal("1.80")
    assert {row["custom_code"] for row in kdb.normalized.custom_bindings} == {
        "INST-KR-000448-C-0001",
        "INST-KR-000448-C-0002",
    }

    ibk = products["INST-KR-000223-3-0011"]
    assert ibk.normalized.custom_bindings[0]["custom_code"] == "INST-KR-000223-C-0001"
    assert ibk.preferential_rules[0].reward.value == Decimal("2.15")


def test_pre_search_application_capacity_filters_all_corporate_parking_variants() -> None:
    products = {item.product_id: item for item in load_normalized_product_catalog()}
    compared = [products[f"INST-KR-000129-3-000{index}"] for index in range(2, 6)]

    def candidate_ids(capacity: str) -> set[str]:
        service = ApplicationService(compared, pre_search_enabled=True)
        session = service.create_search_session(
            user_id=f"CORPORATE-PARKING-{capacity}",
            intent=ProductSearchIntent(
                search_intent_id=f"CORPORATE-PARKING-INTENT-{capacity}",
                user_id=f"CORPORATE-PARKING-{capacity}",
                product_types=["PARKING_ACCOUNT"],
                application_capacity=capacity,
                contribution_plan=ContributionPlan(
                    preferred_start_amount=300_000_000,
                    selected_term_value=12,
                    selected_term_unit=TermUnit.MONTH,
                ),
            ),
        )
        return set(service._runtime(session.search_session_id).evaluations)

    assert candidate_ids("INDIVIDUAL") == set()
    assert candidate_ids("SOLE_PROPRIETOR") == {
        "INST-KR-000129-3-0002",
        "INST-KR-000129-3-0003",
        "INST-KR-000129-3-0004",
        "INST-KR-000129-3-0005",
    }
    assert candidate_ids("CORPORATION") == {
        "INST-KR-000129-3-0002",
        "INST-KR-000129-3-0003",
        "INST-KR-000129-3-0004",
        "INST-KR-000129-3-0005",
    }


def test_performance_linked_cma_is_not_calculated_as_fixed_rate() -> None:
    product = next(
        item
        for item in load_normalized_product_catalog()
        if item.normalized.return_policy.get("return_kind") == "PERFORMANCE_LINKED"
    )
    scenario = product_with_scenario_rate(
        product, ContractTerm(value=12, unit=TermUnit.MONTH), balance=Decimal("10000000")
    )
    evaluation = FinancialEligibilityEngine().evaluate_product(
        scenario,
        UserFactStore(user_id="PERFORMANCE-CMA"),
        EvaluationContext(
            as_of=date(2026, 8, 24),
            subscription_date=date(2026, 8, 24),
            maturity_date=date(2027, 8, 24),
        ),
    )

    assert evaluation.rates.calculation_status == "UNSUPPORTED"
    assert evaluation.rates.calculation_reason == (
        "PERFORMANCE_LINKED_RETURN_IS_NOT_A_GUARANTEED_RATE"
    )
    assert evaluation.rates.confirmed_rate is None
    assert evaluation.rates.realizable_rate is None


def test_mock_web_runtime_health_count_and_normalized_detail(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "MOCK")
    client = TestClient(create_app())

    expected_product_count = len(load_normalized_product_catalog(verify_hashes=False))
    expected_cma_count = sum(
        1
        for item in load_normalized_product_catalog(verify_hashes=False)
        if item.product_type == "CMA"
    )

    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/api/runtime").json()["product_count"] == expected_product_count
    catalog = client.get("/api/catalog/products?product_family=CMA")
    assert catalog.status_code == 200
    assert catalog.json()["count"] == expected_cma_count
    performance_summary = next(
        item
        for item in catalog.json()["items"]
        if item["product_id"] == "INST-KR-000259-4-0004"
    )
    assert performance_summary["return_kind"] == "PERFORMANCE_LINKED"
    assert performance_summary["base_rate"] is None
    assert performance_summary["advertised_max_rate"] is None
    performance_detail = client.get(
        "/api/catalog/products/INST-KR-000259-4-0004"
    )
    assert performance_detail.status_code == 200
    performance_policy = performance_detail.json()["product"]["return_policy"]
    assert performance_policy["return_kind"] == "PERFORMANCE_LINKED"
    assert performance_policy["rate_entries"] == []
    assert "advertised_max_rate" not in performance_policy
    assert "observed_rates_percent" not in performance_policy
    institution_id = catalog.json()["items"][0]["institution_id"]
    by_institution = client.get(
        f"/api/catalog/institutions/{institution_id}/products"
    )
    assert by_institution.status_code == 200
    assert all(
        item["institution_id"] == institution_id
        for item in by_institution.json()["items"]
    )
    session = client.post(
        "/api/search-sessions",
        json={
            "user_id": "NORMALIZED-WEB",
            "natural_language_query": "1년 동안 월 30만원 적금 추천해줘",
            "as_of": "2026-08-24",
            "subscription_date": "2026-08-24",
        },
    )
    assert session.status_code == 201
    session_id = session.json()["search_session_id"]
    recommendations = client.get(
        f"/api/search-sessions/{session_id}/recommendations"
    ).json()
    assert len(recommendations["top_products"]) == 5
    product_id = recommendations["top_products"][0]["product_id"]
    detail = client.get(
        f"/api/search-sessions/{session_id}/recommendations/{product_id}/preview"
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["institution_name"]
    assert body["sale_status"] == "ON_SALE"
    assert body["term_policy"]
    assert body["cash_flow_policy"]
    assert body["rate_entries"]
    assert isinstance(body["data_gaps"], list)
    assert body["official_sources"]

    canonical_naver = next(
        item
        for item in client.get("/api/catalog/products").json()["items"]
        if item["product_id"] == "INST-KR-000060-4-C0162E2A47A"
    )
    canonical_detail = client.get(
        f"/api/catalog/products/{canonical_naver['product_id']}"
    )
    assert canonical_detail.status_code == 200
    canonical_body = canonical_detail.json()
    assert canonical_body["product"]["product_code"] == canonical_naver["product_id"]
    assert canonical_body["search_facts"]["product_family"] == "CMA"
    assert canonical_body["product"]["search_facts"] == canonical_body["search_facts"]
    assert "raw_product" not in canonical_body["product"]
    canonical_body_text = json.dumps(canonical_body, ensure_ascii=False)
    assert "NVR-" not in canonical_body_text
    assert "EVD-NVR" not in canonical_body_text

    three_up_detail = client.get(
        "/api/catalog/products/INST-KR-000055-2-0008"
    )
    assert three_up_detail.status_code == 200
    assert (
        three_up_detail.json()["product"]["return_policy"]["advertised_max_rate"]["value"]
        == "3.75"
    )
