from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from eligibility.application_service import ApplicationService
from eligibility.schema.condition_requirement import ConditionRequirement, UserConditionState
from eligibility.schema.enums import UserConditionStatus
from eligibility.search.query_tools import (
    ProductQuerySpec,
    QueryContractError,
    ReadOnlyProductQueryTools,
    get_product_details,
    get_requirement_index,
    get_session_condition_state,
    search_products,
)

from tests.v04_helpers import AS_OF, USER_ID, base_store, make_intent, make_product


def _normalized_product(product_id: str, *, rate: str = "3.0") -> dict:
    return {
        "product_code": product_id,
        "institution_id": "BANK-1",
        "institution_name": "테스트은행",
        "product_family": "PARKING_ACCOUNT",
        "product_subtype": "DEMAND_DEPOSIT",
        "sale_policy": {"status": "ON_SALE"},
        "return_policy": {
            "return_kind": "POSTED_RATE",
            "rate_entries": [{"kind": "BASE", "rate": rate}],
            "preferential_policy": {"rules": [{"rule_id": "R-1", "title": "급여이체"}]},
        },
        "term_policy": {"maturity": "없음"},
        "fee_policy": {"withdrawal": "무료"},
        "official_sources": [{"source_id": "SRC-1"}],
        "data_gaps": [{"field": "interest_payment_method"}],
    }


def test_search_projects_allowlisted_fields_filters_and_deterministic_order():
    products = [
        make_product("P-2", name="두번째", base_rate="3.0"),
        make_product("P-1", name="첫번째", base_rate="3.0"),
        make_product("P-3", name="세번째", base_rate="2.0"),
    ]
    query = ProductQuerySpec(
        fields=["product_id", "name", "base_rate"],
        filters=[{"field": "base_rate", "operator": "GTE", "value": "3.0"}],
        order=[{"field": "base_rate", "direction": "DESC"}],
        limit=2,
    )

    assert search_products(products, query) == [
        {"product_id": "P-1", "name": "첫번째", "base_rate": Decimal("3.0")},
        {"product_id": "P-2", "name": "두번째", "base_rate": Decimal("3.0")},
    ]
    assert products[0].product_id == "P-2"  # the read-only call did not reorder input


def test_query_contract_rejects_sql_unknown_fields_operators_and_large_limits():
    with pytest.raises(ValueError):
        ProductQuerySpec.model_validate({"sql": "DROP TABLE products"})
    with pytest.raises(ValueError):
        ProductQuerySpec(fields=["raw_product"])
    with pytest.raises(ValueError):
        ProductQuerySpec(filters=[{"field": "name", "operator": "LIKE", "value": "x"}])
    with pytest.raises(ValueError):
        ProductQuerySpec(limit=101)

    with pytest.raises(QueryContractError):
        search_products([make_product("P-1")], {"filters": [{"field": "name", "operator": "GTE", "value": "x"}]})


def test_product_details_are_allowlisted_and_return_nested_policy_projections():
    product = _normalized_product("P-1")
    before = deepcopy(product)

    details = get_product_details(
        [product],
        ["P-1"],
        fields=["product_id", "institution_name", "term_policy", "preferential_conditions", "official_sources"],
    )

    assert details == [
        {
            "product_id": "P-1",
            "institution_name": "테스트은행",
            "term_policy": {"maturity": "없음"},
            "preferential_conditions": [{"rule_id": "R-1", "title": "급여이체"}],
            "official_sources": [{"source_id": "SRC-1"}],
        }
    ]
    assert product == before
    with pytest.raises(QueryContractError):
        get_product_details([product], ["P-1"], fields=["raw_product"])


def test_requirement_index_preserves_review_status_and_isolated_from_input():
    requirement = ConditionRequirement(
        requirement_id="req:P-1:R-1",
        product_id="P-1",
        scope="RATE_BENEFIT",
        variable_id="CARD_MONTHLY_SPEND_LIMIT",
        operator="GTE",
        threshold={"expected_value": 300000},
        review_status="REVIEW_REQUIRED",
        coverage_status="PARTIAL",
    )
    result = get_requirement_index([requirement])

    assert result[0]["review_status"] == "REVIEW_REQUIRED"
    assert result[0]["coverage_status"] == "PARTIAL"
    result[0]["threshold"]["expected_value"] = 1
    assert requirement.threshold["expected_value"] == 300000


def test_session_state_is_read_only_and_filterable():
    state = UserConditionState(
        variable_id="BANK_ACCOUNT:SHINHAN",
        status=UserConditionStatus.VERIFIED,
        value=True,
    )
    result = get_session_condition_state({state.variable_id: state})

    assert result[0]["variable_id"] == state.variable_id
    assert result[0]["status"] == UserConditionStatus.VERIFIED
    result[0]["value"] = False
    assert state.value is True


def test_facade_exposes_only_read_operations_and_copies_session_ledger():
    products = [_normalized_product("P-1")]
    state = {"BANK_ACCOUNT:SHINHAN": {"variable_id": "BANK_ACCOUNT:SHINHAN", "value": True}}
    tools = ReadOnlyProductQueryTools(products, condition_states=state)

    assert tools.search_products(ProductQuerySpec(fields=["product_id"])) == [{"product_id": "P-1"}]
    assert tools.get_session_condition_state()[0]["value"] is True
    state["BANK_ACCOUNT:SHINHAN"]["value"] = False
    assert tools.get_session_condition_state()[0]["value"] is True
    assert not hasattr(tools, "execute_sql")


def test_application_service_exposes_a_snapshot_only_query_boundary():
    product = make_product(
        "P-1",
        base_rate="3",
        reward_pp="1",
        bonus_fact_type="AUTO_TRANSFER",
    )
    service = ApplicationService(
        [product],
        user_fact_stores={USER_ID: base_store()},
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=1),
        as_of=AS_OF,
    )
    before = deepcopy(service._runtime(session.search_session_id).session)

    rows = service.query_products(
        {
            "fields": ["product_id", "base_rate"],
            "filters": [{"field": "base_rate", "operator": "GTE", "value": 3}],
        }
    )
    tools = service.get_read_only_query_tools(session.search_session_id)

    assert rows == [{"product_id": "P-1", "base_rate": Decimal("3")}]
    assert tools.get_requirement_index()[0]["review_status"] == "REVIEW_REQUIRED"
    assert not hasattr(tools, "execute_sql")
    assert service._runtime(session.search_session_id).session == before
