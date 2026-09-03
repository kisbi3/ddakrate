from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from eligibility.application_service import ApplicationService
from eligibility.catalog.normalized_loader import load_normalized_product_catalog
from eligibility.schema.application_input import HardConstraint
from eligibility.schema.enums import (
    ContributionFrequency,
    HardConstraintValue,
    RankingObjective,
)
from eligibility.schema.search import ContributionPlan, IntentPatch, ProductSearchIntent
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.product_facts import project_product_search_facts
from eligibility.search.ranking import RankingService
from eligibility.search.rate_strategy import ProductFamilyRateStrategy
from eligibility.search.retrieval import CandidateRetriever
from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, USER_ID, base_store, make_product


@pytest.fixture(scope="module")
def published_products():
    return {
        product.product_id: product
        for product in load_normalized_product_catalog(verify_hashes=False)
    }


def _intent(
    *,
    hard_constraints: list[HardConstraint] | None = None,
    application_capacity: str | None = None,
) -> ProductSearchIntent:
    return ProductSearchIntent(
        search_intent_id="INTENT-FAMILY-CONTRACT",
        user_id=USER_ID,
        product_types=["CMA"],
        hard_constraints=hard_constraints or [],
        application_capacity=application_capacity,
        ranking_objective=RankingObjective.MAX_REALIZABLE_RATE,
    )


def test_product_family_strategies_keep_distinct_calculation_modes(
    published_products,
) -> None:
    strategy = ProductFamilyRateStrategy()
    expected_modes = {
        "INSTALLMENT_SAVINGS": "INSTALLMENT_CASHFLOW",
        "TIME_DEPOSIT": "LUMP_SUM_TERM",
        "PARKING_ACCOUNT": "ON_DEMAND_BALANCE",
        "CMA": "CMA_POSTED_YIELD",
    }
    for family, expected in expected_modes.items():
        product = make_product(f"MODE-{family}", product_type=family)
        prepared = strategy.prepare(
            product,
            term=product.contract_term,
            principal=Decimal("1000000"),
            customer_scope=None,
            as_of=AS_OF,
        )
        assert prepared.calculation_mode == expected

    performance = published_products["INST-KR-000034-4-0003"]
    prepared = strategy.prepare(
        performance,
        term=performance.contract_term,
        principal=Decimal("1000000"),
        customer_scope="INDIVIDUAL",
        as_of=date(2026, 8, 30),
    )
    assert prepared.calculation_mode == "CMA_PERFORMANCE_LINKED"
    assert prepared.product.base_rate is None


def test_evaluator_emits_common_rate_result_and_ranking_consumes_it() -> None:
    products = [
        make_product("RATE-LOW", base_rate="2.0"),
        make_product("RATE-HIGH", base_rate="3.0"),
    ]
    intent = ProductSearchIntent(
        search_intent_id="INTENT-COMMON-RATE",
        user_id=USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=RankingObjective.MAX_REALIZABLE_RATE,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("100000"),
            frequency=ContributionFrequency.MONTHLY,
        ),
    )
    evaluations = MultiProductEvaluator().evaluate(
        products,
        base_store(),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    low = evaluations["RATE-LOW"]
    assert low.rate_evaluation is not None
    assert low.rate_evaluation.calculation_mode == "INSTALLMENT_CASHFLOW"
    assert low.rate_evaluation.realizable_rate == low.realizable_rate
    assert low.rate_evaluation.rate_comparable is True
    assert low.rate_evaluation.interest_comparable is True
    assert low.rate_evaluation.scenario_principal == Decimal("1200000")

    ranking, _ = RankingService().rank(
        evaluations,
        {product.product_id: product for product in products},
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=2,
    )
    assert ranking.ordered_product_ids == ["RATE-HIGH", "RATE-LOW"]

    # The legacy copy API remains compatible while synchronizing the nested
    # common contract that ranking now reads.
    evaluations["RATE-LOW"] = low.model_copy(
        update={
            "realizable_rate": Decimal("4.0"),
            "user_specific_conditional_upper_rate": Decimal("4.0"),
        }
    )
    assert evaluations["RATE-LOW"].rate_evaluation.realizable_rate == Decimal("4.0")
    reranked, _ = RankingService().rank(
        evaluations,
        {product.product_id: product for product in products},
        objective=RankingObjective.MAX_REALIZABLE_RATE,
        top_k=2,
    )
    assert reranked.ordered_product_ids == ["RATE-LOW", "RATE-HIGH"]


def test_published_cma_projects_typed_common_search_facts(
    published_products,
) -> None:
    facts = project_product_search_facts(
        published_products["INST-KR-000034-4-0001"]
    )
    assert facts.product_family == "CMA"
    assert facts.product_subtype == "RP"
    assert facts.customer_scopes == ["INDIVIDUAL"]
    assert facts.return_kind == "POSTED_YIELD"
    assert facts.protection_status == "NOT_PROTECTED"
    assert facts.reinvestment_mode == "NONE"
    assert facts.fee_waiver_available is True


def test_explicit_protection_filter_uses_typed_fact_and_keeps_unknown(
    published_products,
) -> None:
    unprotected = published_products["INST-KR-000034-4-0001"]
    protected = published_products["INST-KR-000793-4-0002"]
    unknown = make_product("CMA-PROTECTION-UNKNOWN", product_type="CMA")
    intent = _intent(
        hard_constraints=[
            HardConstraint(
                field="DEPOSIT_PROTECTION",
                constraint=HardConstraintValue.REQUIRE,
                expected=True,
            )
        ]
    )
    retained, decisions = CandidateRetriever().retrieve(
        [unprotected, protected, unknown],
        intent,
        as_of=date(2026, 8, 30),
    )
    assert {product.product_id for product in retained} == {
        protected.product_id,
        unknown.product_id,
    }
    by_id = {decision.product_id: decision for decision in decisions}
    assert by_id[unprotected.product_id].reason_code == "HARD_PROTECTION_VIOLATION"


def test_application_capacity_filters_only_explicit_scope_mismatch(
    published_products,
) -> None:
    individual_only = published_products["INST-KR-000034-4-0001"]
    corporation_capable = published_products["INST-KR-000034-4-0003"]
    unknown_scope = published_products["INST-KR-000793-4-0002"]
    retained, decisions = CandidateRetriever().retrieve(
        [individual_only, corporation_capable, unknown_scope],
        _intent(application_capacity="CORPORATION"),
        as_of=date(2026, 8, 30),
    )
    assert {product.product_id for product in retained} == {
        corporation_capable.product_id,
        unknown_scope.product_id,
    }
    by_id = {decision.product_id: decision for decision in decisions}
    assert by_id[individual_only.product_id].reason_code == (
        "HARD_CUSTOMER_SCOPE_VIOLATION"
    )


def test_llm_constraint_aliases_canonicalize_before_retrieval() -> None:
    patch = IntentPatch(
        upsert_hard_constraints=[
            HardConstraint(
                field="principal_protected",
                constraint=HardConstraintValue.REQUIRE,
                expected=True,
            ),
            HardConstraint(
                field="cma_type",
                constraint=HardConstraintValue.REQUIRE,
                expected="rp",
            ),
        ],
        remove_hard_constraint_keys=["transfer_fee_free"],
    )
    canonical = ApplicationService._canonicalize_institution_constraint_patch(patch)
    assert [item.field for item in canonical.upsert_hard_constraints] == [
        "DEPOSIT_PROTECTION",
        "PRODUCT_SUBTYPE",
    ]
    assert canonical.upsert_hard_constraints[1].expected == "RP"
    assert canonical.remove_hard_constraint_keys == ["FEE_WAIVER_AVAILABLE"]
