from __future__ import annotations

from decimal import Decimal

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.ibk_parent_benefit import (
    ibk_family_aggregation_service,
    ibk_parent_benefit_context,
    ibk_parent_benefit_product,
    ibk_parent_benefit_user,
)
from eligibility.schema.enums import EvaluationStatus, FactSourceType


def _evaluate(store):
    return FinancialEligibilityEngine().evaluate_product(
        ibk_parent_benefit_product(),
        store,
        ibk_parent_benefit_context(),
    )


def test_related_person_fact_can_satisfy_rule():
    result = _evaluate(ibk_parent_benefit_user())
    rate_rule = result.preferential_rule_results[0]

    assert rate_rule.status == EvaluationStatus.SATISFIED
    assert rate_rule.children[0].status == EvaluationStatus.UNSATISFIABLE
    family_branch = rate_rule.children[1]
    assert family_branch.status == EvaluationStatus.SATISFIED
    related_count = family_branch.children[1]
    assert related_count.progress is not None
    assert related_count.progress.current == 6
    assert related_count.evidence["subject_person_ids"] == ["CHILD_01", "PARENT_01"]
    assert result.rates.confirmed_rate == Decimal("4.5")


def test_unverified_relationship_does_not_satisfy_rule():
    result = _evaluate(
        ibk_parent_benefit_user(
            relationship_source=FactSourceType.USER_DECLARED,
        )
    )
    rate_rule = result.preferential_rule_results[0]

    assert rate_rule.status == EvaluationStatus.UNKNOWN
    family_branch = rate_rule.children[1]
    related_count = family_branch.children[1]
    assert related_count.status == EvaluationStatus.UNKNOWN
    assert related_count.reason_code == "RELATED_PERSON_RELATIONSHIP_UNVERIFIED"
    assert result.rates.realizable_rate == Decimal("2.5")
    assert result.rates.user_specific_conditional_upper_rate == Decimal("4.5")


def test_ibk_family_registration_is_service_definition_not_dsl_operator():
    service = ibk_family_aggregation_service()
    product = ibk_parent_benefit_product()

    assert service.service_id == "IBK_PARENT_BENEFIT_FAMILY_AGGREGATION"
    assert {item.fact_type for item in service.facts} == {
        "IBK_FAMILY_AGGREGATION_REGISTERED"
    }
    serialized = product.model_dump_json()
    assert "IBK_FAMILY_AGGREGATION_REGISTERED" in serialized
    assert "HAS_IBK_FAMILY_AGGREGATION" not in serialized
