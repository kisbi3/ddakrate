from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from eligibility.fixtures.future_goals import salary_envelope_6m_product
from eligibility.fixtures.hana_run import hana_run_product
from eligibility.fixtures.ibk_parent_benefit import ibk_parent_benefit_product
from eligibility.fixtures.kakao_26_week import kakao_26_week_product
from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
from eligibility.fixtures.user_001 import user_001, with_event_coupon_answer, with_supersol_answer
from eligibility.schema.application_input import Capability, NumericPreference, Preference, QuickInputProfile
from eligibility.schema.enums import (
    CapabilityState,
    FactSemanticType,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
)
from eligibility.schema.product import ProductDefinition


def test_shinhan_fixture_user_declared_semantics_are_migrated():
    store = user_001()
    by_type = {fact.fact_type: fact for fact in store.facts}
    assert by_type["SALARY_ACCOUNT_CHANGE_POSSIBLE"].semantic_type == FactSemanticType.FUTURE_INTENT
    assert by_type["CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE"].semantic_type == FactSemanticType.FUTURE_INTENT
    assert with_supersol_answer(store, True).facts[-1].semantic_type == FactSemanticType.FUTURE_INTENT
    assert with_event_coupon_answer(store, False).facts[-1].semantic_type == FactSemanticType.SELF_REPORTED_FACT


def test_four_regression_products_have_source_backed_metadata():
    products = [
        shinhan_youth_first_product(),
        kakao_26_week_product(),
        ibk_parent_benefit_product(),
        hana_run_product(),
    ]
    for product in products:
        assert product.metadata is not None
        assert product.metadata.product_id == product.product_id
        assert product.metadata.institution_id == product.institution_id
        assert product.metadata.source_reference is not None
        assert product.metadata.base_rate == product.base_rate
        assert product.metadata.advertised_max_rate == product.advertised_max_rate


def test_known_contribution_metadata_serializes_shinhan_and_kakao_without_guessing():
    shinhan = shinhan_youth_first_product().metadata
    kakao = kakao_26_week_product().metadata
    assert shinhan is not None and shinhan.contribution_policy is not None
    assert shinhan.contribution_policy.periodic_amount_min == Decimal("1000")
    assert shinhan.contribution_policy.periodic_amount_max == Decimal("300000")
    assert shinhan.contribution_policy.contribution_mode.value == "FLEXIBLE"
    assert kakao is not None and kakao.contribution_policy is not None
    assert kakao.contribution_policy.initial_amount_options == [
        Decimal("1000"), Decimal("2000"), Decimal("3000"), Decimal("5000"), Decimal("10000")
    ]
    assert kakao.contribution_policy.increment_amount is None
    assert kakao.contribution_policy.increment_amount_options == kakao.contribution_policy.initial_amount_options


def test_fixed_term_product_definition_metadata_mismatch_is_validation_error():
    payload = shinhan_youth_first_product().model_dump(mode="python")
    payload["metadata"]["min_term"] = {"value": 6, "unit": "MONTH"}
    payload["metadata"]["max_term"] = {"value": 6, "unit": "MONTH"}
    payload["metadata"]["available_terms"] = [{"value": 6, "unit": "MONTH"}]
    with pytest.raises(ValidationError, match="fixed term"):
        ProductDefinition.model_validate(payload)


def test_action_path_wrong_owning_rule_id_is_validation_error():
    payload = salary_envelope_6m_product().model_dump(mode="python")
    path = payload["preferential_rules"][0]["rule"]["future_achievement"]["action_paths"][0]
    path["rule_id"] = "SOME_OTHER_RULE"
    with pytest.raises(ValidationError, match="ActionPath.rule_id"):
        ProductDefinition.model_validate(payload)


def test_action_path_invalid_source_rule_node_id_is_validation_error():
    payload = salary_envelope_6m_product().model_dump(mode="python")
    path = payload["preferential_rules"][0]["rule"]["future_achievement"]["action_paths"][0]
    path["source_rule_node_id"] = "NON_EXISTENT_AST_NODE"
    with pytest.raises(ValidationError, match="source_rule_node_id"):
        ProductDefinition.model_validate(payload)


def test_quick_input_capability_can_and_cannot_conflict():
    with pytest.raises(ValidationError, match="Conflicting capability"):
        QuickInputProfile(
            capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN),
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT),
            ]
        )


def test_quick_input_prefer_present_and_absent_conflict():
    with pytest.raises(ValidationError, match="Conflicting preference"):
        QuickInputProfile(
            preferences=[
                Preference(field="FIRST_TRANSACTION_BONUS", preference=PreferenceValue.PREFER_PRESENT),
                Preference(field="FIRST_TRANSACTION_BONUS", preference=PreferenceValue.PREFER_ABSENT),
            ]
        )


def test_quick_input_contradictory_hard_numeric_constraints_conflict():
    with pytest.raises(ValidationError, match="Contradictory HARD numeric"):
        QuickInputProfile(
            numeric_preferences=[
                NumericPreference(
                    field="MONTHLY_CONTRIBUTION",
                    value=Decimal("300000"),
                    direction=NumericPreferenceDirection.AT_LEAST,
                    strictness=PreferenceStrictness.HARD,
                    currency="KRW",
                ),
                NumericPreference(
                    field="MONTHLY_CONTRIBUTION",
                    value=Decimal("200000"),
                    direction=NumericPreferenceDirection.AT_MOST,
                    strictness=PreferenceStrictness.HARD,
                    currency="KRW",
                ),
            ]
        )


def test_soft_numeric_preferences_are_not_promoted_to_hard_filter_conflicts():
    profile = QuickInputProfile(
        numeric_preferences=[
            NumericPreference(
                field="MONTHLY_CONTRIBUTION",
                value=Decimal("300000"),
                direction=NumericPreferenceDirection.AT_LEAST,
                strictness=PreferenceStrictness.SOFT,
            ),
            NumericPreference(
                field="MONTHLY_CONTRIBUTION",
                value=Decimal("200000"),
                direction=NumericPreferenceDirection.AT_MOST,
                strictness=PreferenceStrictness.SOFT,
            ),
        ]
    )
    assert len(profile.numeric_preferences) == 2
