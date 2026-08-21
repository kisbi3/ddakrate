from __future__ import annotations

from decimal import Decimal
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eligibility.schema.enums import (
    CapabilityState,
    HardConstraintValue,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
)


ApplicationScalar: TypeAlias = str | int | float | bool


class StrictApplicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HardConstraint(StrictApplicationInput):
    field: str
    constraint: HardConstraintValue
    expected: ApplicationScalar = True
    candidate_filter: Literal[True] = True


class Preference(StrictApplicationInput):
    field: str
    preference: PreferenceValue
    expected: ApplicationScalar = True
    candidate_filter: Literal[False] = False

    @property
    def implies_hard_constraint(self) -> bool:
        return False


class Capability(StrictApplicationInput):
    capability_id: str
    state: CapabilityState
    candidate_filter: Literal[False] = False

    @property
    def excludes_product(self) -> bool:
        """CANNOT removes an action path, not the product itself."""

        return False

    @property
    def action_path_available(self) -> bool | None:
        if self.state == CapabilityState.CAN:
            return True
        if self.state == CapabilityState.CANNOT:
            return False
        return None


class NumericPreference(StrictApplicationInput):
    field: str
    value: Decimal = Field(ge=0)
    direction: NumericPreferenceDirection
    strictness: PreferenceStrictness
    currency: str | None = None

    @property
    def is_hard_constraint(self) -> bool:
        return self.strictness == PreferenceStrictness.HARD


class QuickInputProfile(StrictApplicationInput):
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    numeric_preferences: list[NumericPreference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_conflicts(self) -> "QuickInputProfile":
        self._validate_capability_conflicts()
        self._validate_preference_conflicts()
        self._validate_hard_constraint_conflicts()
        self._validate_numeric_hard_conflicts()
        return self

    def _validate_capability_conflicts(self) -> None:
        seen: dict[str, CapabilityState] = {}
        for item in self.capabilities:
            prior = seen.get(item.capability_id)
            if prior is None:
                seen[item.capability_id] = item.state
                continue
            if prior != item.state:
                raise ValueError(
                    f"Conflicting capability states for {item.capability_id}: {prior} vs {item.state}"
                )
            raise ValueError(f"Duplicate capability input for {item.capability_id}")

    def _validate_preference_conflicts(self) -> None:
        seen: dict[tuple[str, str], PreferenceValue] = {}
        for item in self.preferences:
            key = (item.field, repr(item.expected))
            prior = seen.get(key)
            if prior is None:
                seen[key] = item.preference
                continue
            if prior != item.preference:
                raise ValueError(
                    f"Conflicting preference values for {item.field}: {prior} vs {item.preference}"
                )
            raise ValueError(f"Duplicate preference input for {item.field}")

    def _validate_hard_constraint_conflicts(self) -> None:
        seen: dict[tuple[str, str], HardConstraintValue] = {}
        for item in self.hard_constraints:
            key = (item.field, repr(item.expected))
            prior = seen.get(key)
            if prior is None:
                seen[key] = item.constraint
                continue
            if prior != item.constraint:
                raise ValueError(
                    f"Conflicting hard constraints for {item.field}: {prior} vs {item.constraint}"
                )
            raise ValueError(f"Duplicate hard constraint input for {item.field}")

    def _validate_numeric_hard_conflicts(self) -> None:
        groups: dict[tuple[str, str | None], list[NumericPreference]] = {}
        for item in self.numeric_preferences:
            if item.strictness != PreferenceStrictness.HARD:
                continue
            groups.setdefault((item.field, item.currency), []).append(item)
        for (field, currency), items in groups.items():
            lower = [i.value for i in items if i.direction == NumericPreferenceDirection.AT_LEAST]
            upper = [i.value for i in items if i.direction == NumericPreferenceDirection.AT_MOST]
            if lower and upper and max(lower) > min(upper):
                suffix = f" {currency}" if currency else ""
                raise ValueError(
                    f"Contradictory HARD numeric constraints for {field}{suffix}: "
                    f"AT_LEAST {max(lower)} exceeds AT_MOST {min(upper)}"
                )
