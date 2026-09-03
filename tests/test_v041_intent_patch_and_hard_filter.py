from __future__ import annotations

from decimal import Decimal

from eligibility.schema.application_input import Capability, HardConstraint, Preference
from eligibility.schema.enums import (
    CapabilityState,
    HardConstraintValue,
    PreferenceValue,
)
from eligibility.schema.product import ProductFeature
from eligibility.schema.search import ContributionPlanPatch, IntentPatch
from eligibility.search.intent import IntentParser
from eligibility.search.retrieval import CandidateRetriever

from tests.v04_helpers import AS_OF, make_intent, make_product


def _capability_map(intent):
    return {item.capability_id: item.state for item in intent.capabilities}


def test_intent_patch_preserves_unmentioned_capabilities():
    current = make_intent(
        capabilities=[
            Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN),
            Capability(capability_id="BRANCH_VISIT", state=CapabilityState.CAN),
            Capability(capability_id="NEW_CARD_ISSUANCE", state=CapabilityState.UNKNOWN),
        ]
    )

    updated = IntentParser.apply_patch(
        current,
        IntentPatch(
            upsert_capabilities=[
                Capability(
                    capability_id="NEW_CARD_ISSUANCE",
                    state=CapabilityState.CANNOT,
                )
            ]
        ),
    )

    assert _capability_map(updated) == {
        "CHANGE_SALARY_ACCOUNT": CapabilityState.CAN,
        "BRANCH_VISIT": CapabilityState.CAN,
        "NEW_CARD_ISSUANCE": CapabilityState.CANNOT,
    }


def test_intent_patch_updates_single_capability():
    current = make_intent(
        capabilities=[
            Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT),
            Capability(capability_id="BRANCH_VISIT", state=CapabilityState.CAN),
        ]
    )

    updated = IntentParser.apply_patch(
        current,
        IntentPatch(
            upsert_capabilities=[
                Capability(
                    capability_id="CHANGE_SALARY_ACCOUNT",
                    state=CapabilityState.CAN,
                )
            ]
        ),
    )

    assert _capability_map(updated)["CHANGE_SALARY_ACCOUNT"] == CapabilityState.CAN
    assert _capability_map(updated)["BRANCH_VISIT"] == CapabilityState.CAN


def test_intent_patch_removes_only_explicit_target():
    current = make_intent(
        hard_constraints=[
            HardConstraint(
                field="SUBSCRIPTION_CHANNEL",
                constraint=HardConstraintValue.EXCLUDE,
                expected="BRANCH",
            )
        ],
        preferences=[
            Preference(
                field="NEW_CARD_REQUIRED",
                preference=PreferenceValue.PREFER_ABSENT,
            )
        ],
        capabilities=[
            Capability(capability_id="BRANCH_VISIT", state=CapabilityState.CANNOT),
            Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN),
        ],
    )

    updated = IntentParser.apply_patch(
        current,
        IntentPatch(
            remove_hard_constraint_keys=["SUBSCRIPTION_CHANNEL"],
            remove_capability_keys=["BRANCH_VISIT"],
        ),
    )

    assert all(item.field != "SUBSCRIPTION_CHANNEL" for item in updated.hard_constraints)
    assert "BRANCH_VISIT" not in _capability_map(updated)
    assert _capability_map(updated)["CHANGE_SALARY_ACCOUNT"] == CapabilityState.CAN
    assert updated.preferences == current.preferences


def test_contribution_plan_patch_preserves_other_fields():
    current = make_intent(
        desired_amount="300000",
        maximum_amount="500000",
        selected_term_value=12,
    )

    updated = IntentParser.apply_patch(
        current,
        IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                desired_periodic_amount=Decimal("200000"),
            )
        ),
    )

    assert updated.contribution_plan is not None
    assert updated.contribution_plan.desired_periodic_amount == Decimal("200000")
    assert updated.contribution_plan.maximum_affordable_periodic_amount == Decimal("500000")
    assert updated.contribution_plan.selected_term_value == 12
    assert updated.contribution_plan.selected_term_unit == current.contribution_plan.selected_term_unit


def _with_features(product, features):
    return product.model_copy(
        update={
            "metadata": product.metadata.model_copy(update={"features": features}, deep=True)
        },
        deep=True,
    )


def _new_card_exclusion_intent():
    return make_intent(
        hard_constraints=[
            HardConstraint(
                field="NEW_CARD_REQUIRED",
                constraint=HardConstraintValue.EXCLUDE,
            )
        ]
    )


def test_new_card_exclusion_does_not_remove_existing_card_path_product():
    product = make_product(
        "EXISTING-CARD-PATH",
        reward_pp="0.5",
        rule_name="신한카드 결제 우대",
    )
    product = _with_features(
        product,
        [
            ProductFeature(
                feature_id="NEW_CARD_REQUIRED",
                present=False,
                required_for_subscription=False,
            )
        ],
    )

    retained, decisions = CandidateRetriever().retrieve(
        [product], _new_card_exclusion_intent(), as_of=AS_OF
    )

    assert retained == [product]
    assert decisions[0].retained is True


def test_required_capability_feature_can_filter_when_truly_mandatory():
    product = make_product("MANDATORY-NEW-CARD")
    product = _with_features(
        product,
        [
            ProductFeature(
                feature_id="NEW_CARD_REQUIRED",
                present=True,
                required_for_subscription=True,
                required_capabilities=["NEW_CARD_ISSUANCE"],
            )
        ],
    )

    retained, decisions = CandidateRetriever().retrieve(
        [product], _new_card_exclusion_intent(), as_of=AS_OF
    )

    assert retained == []
    assert decisions[0].reason_code == "HARD_FEATURE_VIOLATION"
    assert decisions[0].evidence["evidence_type"] == "TYPED_PRODUCT_FEATURE"


def test_preferential_card_rule_is_not_equivalent_to_new_card_requirement():
    product = make_product(
        "CARD-BONUS-ONLY",
        reward_pp="0.5",
        rule_name="신한카드 신규카드 카드 결제 우대",
    )

    retained, decisions = CandidateRetriever().retrieve(
        [product], _new_card_exclusion_intent(), as_of=AS_OF
    )

    assert retained == [product]
    assert decisions[0].retained is True


def test_hard_filter_does_not_depend_on_korean_or_english_keyword_matching():
    products = [
        make_product("KEYWORD-KR", reward_pp="0.5", rule_name="신규카드 발급 필수 카드 우대"),
        make_product("KEYWORD-EN", reward_pp="0.5", rule_name="NEW_CARD_REQUIRED CARD BONUS"),
    ]

    retained, decisions = CandidateRetriever().retrieve(
        products, _new_card_exclusion_intent(), as_of=AS_OF
    )

    assert {item.product_id for item in retained} == {"KEYWORD-KR", "KEYWORD-EN"}
    assert all(item.retained for item in decisions)
