from __future__ import annotations

from decimal import Decimal

from eligibility.schema.application_input import (
    Capability,
    HardConstraint,
    NumericPreference,
    Preference,
    QuickInputProfile,
)
from eligibility.schema.enums import (
    CapabilityState,
    HardConstraintValue,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
)


def test_prefer_present_does_not_imply_require():
    preference = Preference(
        field="FIRST_TRANSACTION_BENEFIT",
        preference=PreferenceValue.PREFER_PRESENT,
    )
    hard = HardConstraint(
        field="FIRST_TRANSACTION_BENEFIT",
        constraint=HardConstraintValue.REQUIRE,
    )

    assert preference.candidate_filter is False
    assert preference.implies_hard_constraint is False
    assert hard.candidate_filter is True
    assert preference.model_dump(mode="json")["preference"] != hard.model_dump(mode="json")["constraint"]


def test_prefer_absent_does_not_imply_exclude():
    preference = Preference(
        field="FIRST_TRANSACTION_BENEFIT",
        preference=PreferenceValue.PREFER_ABSENT,
    )
    hard = HardConstraint(
        field="FIRST_TRANSACTION_BENEFIT",
        constraint=HardConstraintValue.EXCLUDE,
    )

    assert preference.candidate_filter is False
    assert hard.candidate_filter is True
    assert preference.preference == PreferenceValue.PREFER_ABSENT
    assert hard.constraint == HardConstraintValue.EXCLUDE


def test_cannot_capability_excludes_action_path_not_product():
    capability = Capability(
        capability_id="CHANGE_SALARY_ACCOUNT",
        state=CapabilityState.CANNOT,
    )

    assert capability.action_path_available is False
    assert capability.excludes_product is False
    assert capability.candidate_filter is False


def test_numeric_soft_and_hard_preferences_serialize_distinctly():
    soft = NumericPreference(
        field="MONTHLY_CONTRIBUTION",
        value=Decimal("300000"),
        direction=NumericPreferenceDirection.AT_LEAST,
        strictness=PreferenceStrictness.SOFT,
        currency="KRW",
    )
    hard = soft.model_copy(update={"strictness": PreferenceStrictness.HARD})
    profile = QuickInputProfile(numeric_preferences=[soft, hard])

    payload = profile.model_dump(mode="json")

    assert soft.is_hard_constraint is False
    assert hard.is_hard_constraint is True
    assert payload["numeric_preferences"][0]["strictness"] == "SOFT"
    assert payload["numeric_preferences"][1]["strictness"] == "HARD"
