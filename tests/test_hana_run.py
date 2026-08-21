from __future__ import annotations

from decimal import Decimal

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.hana_run import (
    hana_health_asset_management_service,
    hana_run_context,
    hana_run_product,
    hana_run_user,
)
from eligibility.schema.enums import EvaluationStatus


def _evaluate(distance_km: int = 523):
    return FinancialEligibilityEngine().evaluate_product(
        hana_run_product(),
        hana_run_user(distance_km),
        hana_run_context(),
    )


def test_hana_confirmed_running_distance_satisfies_generic_thresholds():
    result = _evaluate(523)

    assert result.eligibility_status == EvaluationStatus.SATISFIED
    assert [item.status for item in result.preferential_rule_results] == [
        EvaluationStatus.SATISFIED,
        EvaluationStatus.SATISFIED,
        EvaluationStatus.SATISFIED,
    ]
    assert result.rates.confirmed_rate == Decimal("4.3")
    assert result.rates.realizable_rate == Decimal("4.3")


def test_hana_distance_tier_is_incremental_and_deterministic():
    result = _evaluate(250)

    assert [item.status for item in result.preferential_rule_results] == [
        EvaluationStatus.SATISFIED,
        EvaluationStatus.SATISFIED,
        EvaluationStatus.UNSATISFIABLE,
    ]
    assert result.rates.confirmed_rate == Decimal("3.8")
    assert result.rates.user_specific_conditional_upper_rate == Decimal("3.8")


def test_hana_institution_service_is_separate_from_rule_dsl():
    service = hana_health_asset_management_service()
    product = hana_run_product()
    result = _evaluate(523)

    assert {item.fact_type for item in service.facts} >= {
        "HANA_HEALTH_ASSET_MANAGEMENT_ACTIVE",
        "HANA_CONFIRMED_RUNNING_DISTANCE_KM",
    }
    rule_types = {
        node["type"]
        for node in _walk_rule_dict(product.model_dump(mode="json"))
        if "type" in node and "rule_id" in node
    }
    assert rule_types <= {"AND", "DERIVED_COMPARE", "FACT_COMPARE"}
    distance_leaf = result.preferential_rule_results[-1].children[-1]
    references = {item.reference for item in distance_leaf.source_provenance}
    assert "hana-health/confirmed-distance/snapshot-20270415" in references


def _walk_rule_dict(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_rule_dict(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_rule_dict(child)
