from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eligibility.audit import canonical_hash
from eligibility.llm import LLMGateway, LLMPurpose
from eligibility.llm.system_prompts import (
    INTENT_CLARIFICATION_SYSTEM_PROMPT,
    INTENT_PARSING_SYSTEM_PROMPT,
)
from eligibility.schema.application_input import (
    Capability,
    HardConstraint,
    NumericPreference,
    Preference,
)
from eligibility.schema.enums import (
    CapabilityState,
    ContributionFrequency,
    HardConstraintValue,
    IntentConflictType,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
    RankingObjective,
)
from eligibility.schema.search import (
    ClarificationRequest,
    ContributionPlan,
    IntentConflict,
    IntentPatch,
    ContributionPlanPatch,
    ProductSearchIntent,
)

class IntentParser:
    """LLM intent boundary; natural-language parsing never uses a keyword fallback."""

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway

    def parse(self, utterance: str, *, user_id: str) -> ProductSearchIntent:
        utterance = utterance.strip()
        if not utterance:
            raise ValueError("utterance must not be empty")
        now = datetime.now(timezone.utc)
        identity = {"user_id": user_id, "utterance": utterance, "at": now.isoformat()}
        baseline = ProductSearchIntent(
            search_intent_id=f"INTENT-{canonical_hash(identity)[:16]}",
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        if self.gateway is None:
            raise RuntimeError("Natural-language intent parsing requires an LLM gateway")
        patch = self._parse_patch_with_llm(utterance, current=baseline, initial=True)
        return self.apply_patch(baseline, patch, source_utterance=utterance)

    def update(
        self,
        current: ProductSearchIntent,
        utterance: str,
    ) -> ProductSearchIntent:
        """Apply a follow-up utterance as an IntentPatch, never category replacement."""

        if self.gateway is None:
            raise RuntimeError("Natural-language intent parsing requires an LLM gateway")
        patch = self._parse_patch_with_llm(utterance, current=current)
        return self.apply_patch(current, patch, source_utterance=utterance)

    @staticmethod
    def apply_patch(
        current: ProductSearchIntent,
        patch: IntentPatch,
        *,
        source_utterance: str | None = None,
    ) -> ProductSearchIntent:
        payload = current.model_dump(mode="python")

        product_types = [item for item in current.product_types if item not in set(patch.remove_product_types)]
        for item in patch.upsert_product_types:
            if item not in product_types:
                product_types.append(item)
        payload["product_types"] = product_types

        excluded_institution_ids = [
            item
            for item in current.excluded_institution_ids
            if item not in set(patch.remove_excluded_institution_ids)
        ]
        for item in patch.upsert_excluded_institution_ids:
            if item not in excluded_institution_ids:
                excluded_institution_ids.append(item)
        payload["excluded_institution_ids"] = excluded_institution_ids

        payload["hard_constraints"] = IntentParser._merge_keyed(
            current.hard_constraints,
            patch.upsert_hard_constraints,
            patch.remove_hard_constraint_keys,
            key=lambda item: item.field.upper(),
        )
        payload["preferences"] = IntentParser._merge_keyed(
            current.preferences,
            patch.upsert_preferences,
            patch.remove_preference_keys,
            key=lambda item: item.field.upper(),
        )
        payload["capabilities"] = IntentParser._merge_keyed(
            current.capabilities,
            patch.upsert_capabilities,
            patch.remove_capability_keys,
            key=lambda item: item.capability_id.upper(),
        )
        payload["numeric_preferences"] = IntentParser._merge_numeric_preferences(
            current.numeric_preferences,
            patch.upsert_numeric_preferences,
            patch.remove_numeric_preference_keys,
        )

        contribution_plan_changed = patch.contribution_plan_patch is not None
        if contribution_plan_changed:
            plan_payload = (
                current.contribution_plan.model_dump(mode="python")
                if current.contribution_plan is not None
                else {}
            )
            for field in patch.contribution_plan_patch.remove_fields:
                if field in ContributionPlan.model_fields:
                    plan_payload[field] = None
            delta = patch.contribution_plan_patch.model_dump(
                mode="python", exclude_none=True, exclude={"remove_fields"}
            )
            if delta.get("term_strictness") == "ANY":
                plan_payload["selected_term_value"] = None
                plan_payload["selected_term_unit"] = None
            elif (
                ("selected_term_value" in delta or "selected_term_unit" in delta)
                and plan_payload.get("term_strictness") == "ANY"
                and "term_strictness" not in delta
            ):
                delta["term_strictness"] = "PREFERRED"
            plan_payload.update(delta)
            # Keep the plan object only when it still carries a user-specific value.
            payload["contribution_plan"] = ContributionPlan.model_validate(plan_payload)

        # Amount and term make the displayed interest calculable, but they do
        # not express a request to change how products are ordered. Preserve
        # the current objective unless the user explicitly changes it (for
        # example, by asking for "이자금순").
        if patch.ranking_objective_patch is not None:
            payload["ranking_objective"] = patch.ranking_objective_patch
        if patch.requested_top_k_patch is not None:
            payload["requested_top_k"] = patch.requested_top_k_patch
        if patch.application_capacity_patch is not None:
            payload["application_capacity"] = patch.application_capacity_patch

        if source_utterance:
            payload["source_utterances"] = [*current.source_utterances, source_utterance]
        payload["updated_at"] = datetime.now(timezone.utc)
        return ProductSearchIntent.model_validate(payload)

    @staticmethod
    def _merge_keyed(current, upserts, removals, *, key):
        removal_keys = {item.upper() for item in removals}
        merged = [item for item in current if key(item) not in removal_keys]
        index = {key(item): pos for pos, item in enumerate(merged)}
        for item in upserts:
            item_key = key(item)
            if item_key in index:
                merged[index[item_key]] = item
            else:
                index[item_key] = len(merged)
                merged.append(item)
        return merged

    @staticmethod
    def _merge_numeric_preferences(
        current: list[NumericPreference],
        upserts: list[NumericPreference],
        removals: list[str],
    ) -> list[NumericPreference]:
        """Patch numeric constraints by semantic bound identity.

        ``AT_LEAST`` and ``AT_MOST`` for the same field form a valid range and
        must coexist. A patch replaces only the same ``(field, direction,
        currency)`` bound. A plain field removal removes all directions.
        """

        removal_keys = {item.upper() for item in removals}

        def identity(item: NumericPreference) -> tuple[str, str, str | None]:
            return (
                item.field.upper(),
                item.direction.value.upper(),
                item.currency.upper() if item.currency else None,
            )

        def removed(item: NumericPreference) -> bool:
            field, direction, _currency = identity(item)
            return field in removal_keys or f"{field}:{direction}" in removal_keys

        merged = [item for item in current if not removed(item)]
        index = {identity(item): pos for pos, item in enumerate(merged)}
        for item in upserts:
            item_key = identity(item)
            if item_key in index:
                merged[index[item_key]] = item
            else:
                index[item_key] = len(merged)
                merged.append(item)
        return merged

    def _parse_patch_with_llm(
        self,
        utterance: str,
        *,
        current: ProductSearchIntent,
        initial: bool = False,
    ) -> IntentPatch | None:
        assert self.gateway is not None
        prompt = (
            (
                "TASK_MODE: INITIAL_PRODUCT_TYPE_REQUIRED\n"
                if initial
                else "TASK_MODE: UPDATE_EXISTING_SEARCH\n"
            )
            + "CURRENT_SEARCH_INTENT:\n"
            + current.model_dump_json(indent=2)
            + "\n\nUSER_UTTERANCE:\n<user_utterance>\n"
            + utterance
            + "\n</user_utterance>"
        )
        generated = self.gateway.generate_structured(
            LLMPurpose.INTENT_PARSING,
            prompt,
            IntentPatch,
            system_prompt=INTENT_PARSING_SYSTEM_PROMPT,
            metadata={
                "utterance_hash": canonical_hash(utterance),
                "operation": "INITIAL_PATCH" if initial else "PATCH",
            },
        ).data
        patch = IntentPatch.model_validate(
            generated.model_dump(mode="python")
            if isinstance(generated, BaseModel)
            else generated
        )
        return self._hydrate_initial_contribution_plan(patch) if initial else patch

    @staticmethod
    def _hydrate_initial_contribution_plan(patch: IntentPatch) -> IntentPatch:
        """Bridge an extracted search amount into the cashflow plan.

        Structured models sometimes place a clearly stated amount only in
        NumericPreference. The deterministic pre-search flow and calculator
        read ContributionPlan, so retaining the amount in only one of those two
        representations would make the app ask for it a second time.
        """

        current_plan = patch.contribution_plan_patch or ContributionPlanPatch()
        if (
            current_plan.desired_periodic_amount is not None
            or current_plan.maximum_affordable_periodic_amount is not None
            or current_plan.preferred_start_amount is not None
        ):
            return patch

        amount_preference = next(
            (
                item
                for item in reversed(patch.upsert_numeric_preferences)
                if item.currency in {None, "KRW"}
                and item.field.upper()
                in {
                    "AMOUNT",
                    "BALANCE",
                    "DEPOSIT_AMOUNT",
                    "MONTHLY_CONTRIBUTION",
                }
            ),
            None,
        )
        if amount_preference is None:
            return patch

        product_types = set(patch.upsert_product_types)
        lump_or_balance = bool(product_types) and product_types <= {
            "TIME_DEPOSIT",
            "PARKING_ACCOUNT",
            "CMA",
        }
        update: dict[str, Any] = {
            "frequency": (
                current_plan.frequency
                or (
                    ContributionFrequency.FLEXIBLE
                    if lump_or_balance
                    else ContributionFrequency.MONTHLY
                )
            )
        }
        if amount_preference.direction == NumericPreferenceDirection.AT_MOST:
            update["maximum_affordable_periodic_amount"] = amount_preference.value
        else:
            update["desired_periodic_amount"] = amount_preference.value
        return patch.model_copy(
            update={
                "contribution_plan_patch": current_plan.model_copy(
                    update=update,
                    deep=True,
                )
            },
            deep=True,
        )

class GeneratedClarification(BaseModel):
    """LLM wording envelope bound to deterministic conflict resolutions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1)
    conflict_ids: list[str]
    allowed_resolutions: list[str]


class IntentConflictClarifier:
    """Natural-language wording over deterministic IntentConflict data.

    The LLM cannot add or remove resolution options.  Any schema drift or option
    mutation falls back to the deterministic clarification generated by the
    validator.
    """

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway

    def generate(self, conflicts: list[IntentConflict]) -> ClarificationRequest:
        fallback = IntentConflictValidator.clarification(conflicts)
        if self.gateway is None:
            return fallback
        prompt = "INTENT_CONFLICTS:\n" + str(
            {
                "conflicts": [item.model_dump(mode="json") for item in conflicts],
                "conflict_ids": fallback.conflict_ids,
                "allowed_resolutions": fallback.allowed_resolutions,
            }
        )
        try:
            generated = self.gateway.generate_structured(
                LLMPurpose.INTENT_CLARIFICATION,
                prompt,
                GeneratedClarification,
                system_prompt=INTENT_CLARIFICATION_SYSTEM_PROMPT,
                metadata={"conflict_hash": canonical_hash(fallback.question_payload)},
            ).data
        except Exception:
            return fallback
        if generated.conflict_ids != fallback.conflict_ids:
            return fallback
        if generated.allowed_resolutions != fallback.allowed_resolutions:
            return fallback
        return fallback.model_copy(update={"question": generated.question.strip()}, deep=True)


class IntentConflictValidator:
    """Deterministic cross-category intent conflict detection."""

    def validate(self, intent: ProductSearchIntent) -> list[IntentConflict]:
        conflicts: list[IntentConflict] = []
        conflicts.extend(self._capability_conflicts(intent.capabilities))
        conflicts.extend(self._preference_conflicts(intent.preferences))
        conflicts.extend(self._hard_conflicts(intent.hard_constraints))
        conflicts.extend(self._numeric_conflicts(intent.numeric_preferences))
        conflicts.extend(self._hard_preference_conflicts(intent.hard_constraints, intent.preferences))
        conflicts.extend(self._hard_capability_conflicts(intent.hard_constraints, intent.capabilities))
        return self._dedupe(conflicts)

    @staticmethod
    def _conflict(
        field: str,
        conflict_type: IntentConflictType,
        left: BaseModel | dict[str, Any],
        right: BaseModel | dict[str, Any],
        options: list[str],
    ) -> IntentConflict:
        left_payload = left.model_dump(mode="json") if isinstance(left, BaseModel) else left
        right_payload = right.model_dump(mode="json") if isinstance(right, BaseModel) else right
        identity = {
            "field": field,
            "type": conflict_type.value,
            "left": left_payload,
            "right": right_payload,
        }
        return IntentConflict(
            conflict_id=f"CONFLICT-{canonical_hash(identity)[:16]}",
            field=field,
            conflict_type=conflict_type,
            left_input=left_payload,
            right_input=right_payload,
            resolution_options=options,
        )

    def _capability_conflicts(self, items: list[Capability]) -> list[IntentConflict]:
        result: list[IntentConflict] = []
        by_field: dict[str, Capability] = {}
        for item in items:
            prior = by_field.get(item.capability_id)
            if prior is not None and prior.state != item.state:
                result.append(
                    self._conflict(
                        item.capability_id,
                        IntentConflictType.CAPABILITY_CONTRADICTION,
                        prior,
                        item,
                        ["KEEP_CAN", "KEEP_CANNOT", "SET_UNKNOWN"],
                    )
                )
            else:
                by_field[item.capability_id] = item
        return result

    def _preference_conflicts(self, items: list[Preference]) -> list[IntentConflict]:
        result: list[IntentConflict] = []
        by_field: dict[tuple[str, str], Preference] = {}
        for item in items:
            key = (item.field, repr(item.expected))
            prior = by_field.get(key)
            if prior is not None and prior.preference != item.preference:
                result.append(
                    self._conflict(
                        item.field,
                        IntentConflictType.PREFERENCE_CONTRADICTION,
                        prior,
                        item,
                        ["KEEP_PRESENT", "KEEP_ABSENT", "SET_NEUTRAL"],
                    )
                )
            else:
                by_field[key] = item
        return result

    def _hard_conflicts(self, items: list[HardConstraint]) -> list[IntentConflict]:
        result: list[IntentConflict] = []
        by_field: dict[tuple[str, str], HardConstraint] = {}
        for item in items:
            key = (item.field, repr(item.expected))
            prior = by_field.get(key)
            if prior is not None and prior.constraint != item.constraint:
                result.append(
                    self._conflict(
                        item.field,
                        IntentConflictType.HARD_CONSTRAINT_CONTRADICTION,
                        prior,
                        item,
                        ["KEEP_REQUIRE", "KEEP_EXCLUDE", "REMOVE_BOTH"],
                    )
                )
            else:
                by_field[key] = item
        return result

    def _numeric_conflicts(self, items: list[NumericPreference]) -> list[IntentConflict]:
        result: list[IntentConflict] = []
        grouped: dict[tuple[str, str | None], list[NumericPreference]] = {}
        for item in items:
            if item.strictness == PreferenceStrictness.HARD:
                grouped.setdefault((item.field, item.currency), []).append(item)
        for (field, _), group in grouped.items():
            lowers = [item for item in group if item.direction == NumericPreferenceDirection.AT_LEAST]
            uppers = [item for item in group if item.direction == NumericPreferenceDirection.AT_MOST]
            if lowers and uppers:
                lower = max(lowers, key=lambda item: item.value)
                upper = min(uppers, key=lambda item: item.value)
                if lower.value > upper.value:
                    result.append(
                        self._conflict(
                            field,
                            IntentConflictType.NUMERIC_HARD_CONTRADICTION,
                            lower,
                            upper,
                            ["KEEP_LOWER_BOUND", "KEEP_UPPER_BOUND", "MAKE_SOFT"],
                        )
                    )
        return result

    def _hard_preference_conflicts(
        self,
        hard: list[HardConstraint],
        preferences: list[Preference],
    ) -> list[IntentConflict]:
        result: list[IntentConflict] = []
        for h in hard:
            for p in preferences:
                if h.field != p.field or h.expected != p.expected:
                    continue
                contradictory = (
                    h.constraint == HardConstraintValue.REQUIRE
                    and p.preference == PreferenceValue.PREFER_ABSENT
                ) or (
                    h.constraint == HardConstraintValue.EXCLUDE
                    and p.preference == PreferenceValue.PREFER_PRESENT
                )
                if contradictory:
                    result.append(
                        self._conflict(
                            h.field,
                            IntentConflictType.HARD_SOFT_CONTRADICTION,
                            h,
                            p,
                            [
                                "KEEP_HARD_REMOVE_PREFERENCE",
                                "REMOVE_HARD_KEEP_PREFERENCE",
                                "SET_NEUTRAL",
                            ],
                        )
                    )
        return result

    def _hard_capability_conflicts(
        self,
        hard: list[HardConstraint],
        capabilities: list[Capability],
    ) -> list[IntentConflict]:
        result: list[IntentConflict] = []
        aliases = {
            "BRANCHVISITREQUIRED": "BRANCHVISIT",
            "REQUIRESBRANCH": "BRANCHVISIT",
            "SUBSCRIPTIONCHANNELBRANCH": "BRANCHVISIT",
            "NEWCARDREQUIRED": "NEWCARDISSUANCE",
            "CARDISSUANCEREQUIRED": "NEWCARDISSUANCE",
            "SALARYACCOUNTCHANGEREQUIRED": "CHANGESALARYACCOUNT",
            "CARDSETTLEMENTACCOUNTCHANGEREQUIRED": "CHANGECARDSETTLEMENTACCOUNT",
        }
        for h in hard:
            for capability in capabilities:
                normalized_hard = re.sub(r"[^A-Z0-9]", "", h.field.upper())
                normalized_cap = re.sub(r"[^A-Z0-9]", "", capability.capability_id.upper())
                normalized_hard = aliases.get(normalized_hard, normalized_hard)
                normalized_cap = aliases.get(normalized_cap, normalized_cap)
                if normalized_hard != normalized_cap:
                    continue
                if h.constraint == HardConstraintValue.REQUIRE and capability.state == CapabilityState.CANNOT:
                    result.append(
                        self._conflict(
                            h.field,
                            IntentConflictType.HARD_CAPABILITY_CONTRADICTION,
                            h,
                            capability,
                            ["KEEP_REQUIRE_SET_CAN", "REMOVE_REQUIRE_KEEP_CANNOT"],
                        )
                    )
        return result

    @staticmethod
    def _dedupe(conflicts: list[IntentConflict]) -> list[IntentConflict]:
        seen: set[str] = set()
        result: list[IntentConflict] = []
        for conflict in conflicts:
            if conflict.conflict_id not in seen:
                result.append(conflict)
                seen.add(conflict.conflict_id)
        return result

    @staticmethod
    def clarification(conflicts: list[IntentConflict]) -> ClarificationRequest:
        if not conflicts:
            raise ValueError("No conflicts to clarify")
        allowed = sorted({option for conflict in conflicts for option in conflict.resolution_options})
        descriptions = "; ".join(
            f"{conflict.field}: {conflict.conflict_type.value}" for conflict in conflicts
        )
        first = conflicts[0]
        if first.conflict_type == IntentConflictType.HARD_SOFT_CONTRADICTION:
            question = (
                f"{first.field} 조건을 반드시 적용할까요, 아니면 반대 선호만 유지할까요?"
            )
        else:
            question = f"입력한 조건에 충돌이 있습니다. 다음 중 하나를 선택해 주세요: {', '.join(allowed)}"
        return ClarificationRequest(
            clarification_id=f"CLARIFY-{canonical_hash([c.conflict_id for c in conflicts])[:16]}",
            conflict_ids=[conflict.conflict_id for conflict in conflicts],
            allowed_resolutions=allowed,
            question_payload={"conflicts": descriptions},
            question=question,
        )

    def apply_resolution(
        self,
        intent: ProductSearchIntent,
        conflict: IntentConflict,
        resolution: str,
    ) -> ProductSearchIntent:
        if resolution not in conflict.resolution_options:
            raise ValueError("Resolution is not allowed for this IntentConflict")
        payload = intent.model_dump(mode="python")
        field = conflict.field

        if resolution == "KEEP_HARD_REMOVE_PREFERENCE":
            payload["preferences"] = [
                item
                for item in intent.preferences
                if not self._belongs_to_conflict(item, field, conflict)
            ]
        elif resolution == "REMOVE_HARD_KEEP_PREFERENCE":
            payload["hard_constraints"] = [
                item
                for item in intent.hard_constraints
                if not self._belongs_to_conflict(item, field, conflict)
            ]
        elif resolution == "SET_NEUTRAL":
            payload["hard_constraints"] = [
                item
                for item in intent.hard_constraints
                if not self._belongs_to_conflict(item, field, conflict)
            ]
            payload["preferences"] = [
                item
                for item in intent.preferences
                if not self._belongs_to_conflict(item, field, conflict)
            ] + [
                Preference(
                    field=field,
                    expected=conflict.left_input.get("expected", True),
                    preference=PreferenceValue.NEUTRAL,
                )
            ]
        elif resolution in {"KEEP_CAN", "KEEP_CANNOT", "SET_UNKNOWN"}:
            state = {
                "KEEP_CAN": CapabilityState.CAN,
                "KEEP_CANNOT": CapabilityState.CANNOT,
                "SET_UNKNOWN": CapabilityState.UNKNOWN,
            }[resolution]
            payload["capabilities"] = [
                item for item in intent.capabilities if item.capability_id != field
            ] + [Capability(capability_id=field, state=state)]
        elif resolution in {"KEEP_PRESENT", "KEEP_ABSENT"}:
            preference = (
                PreferenceValue.PREFER_PRESENT
                if resolution == "KEEP_PRESENT"
                else PreferenceValue.PREFER_ABSENT
            )
            payload["preferences"] = [
                item
                for item in intent.preferences
                if not self._belongs_to_conflict(item, field, conflict)
            ] + [
                Preference(
                    field=field,
                    expected=conflict.left_input.get("expected", True),
                    preference=preference,
                )
            ]
        elif resolution in {"KEEP_REQUIRE", "KEEP_EXCLUDE"}:
            constraint = (
                HardConstraintValue.REQUIRE
                if resolution == "KEEP_REQUIRE"
                else HardConstraintValue.EXCLUDE
            )
            payload["hard_constraints"] = [
                item
                for item in intent.hard_constraints
                if not self._belongs_to_conflict(item, field, conflict)
            ] + [
                HardConstraint(
                    field=field,
                    expected=conflict.left_input.get("expected", True),
                    constraint=constraint,
                )
            ]
        elif resolution == "REMOVE_BOTH":
            payload["hard_constraints"] = [
                item
                for item in intent.hard_constraints
                if not self._belongs_to_conflict(item, field, conflict)
            ]
        elif resolution in {"KEEP_LOWER_BOUND", "KEEP_UPPER_BOUND", "MAKE_SOFT"}:
            filtered = [
                item
                for item in intent.numeric_preferences
                if not self._numeric_group_matches(item, field, conflict)
            ]
            candidates = [
                item
                for item in intent.numeric_preferences
                if self._numeric_group_matches(item, field, conflict)
            ]
            if resolution == "KEEP_LOWER_BOUND":
                candidates = [item for item in candidates if item.direction == NumericPreferenceDirection.AT_LEAST]
            elif resolution == "KEEP_UPPER_BOUND":
                candidates = [item for item in candidates if item.direction == NumericPreferenceDirection.AT_MOST]
            else:
                candidates = [item.model_copy(update={"strictness": PreferenceStrictness.SOFT}) for item in candidates]
            payload["numeric_preferences"] = [*filtered, *candidates]
        elif resolution == "KEEP_REQUIRE_SET_CAN":
            capability_id = str(conflict.right_input.get("capability_id", field))
            payload["capabilities"] = [
                item
                for item in intent.capabilities
                if item.capability_id != capability_id
            ] + [Capability(capability_id=capability_id, state=CapabilityState.CAN)]
        elif resolution == "REMOVE_REQUIRE_KEEP_CANNOT":
            payload["hard_constraints"] = [
                item
                for item in intent.hard_constraints
                if not self._belongs_to_conflict(item, field, conflict)
            ]
        else:  # pragma: no cover - guarded by the allowed options above.
            raise ValueError(f"Unsupported resolution: {resolution}")

        payload["updated_at"] = datetime.now(timezone.utc)
        return ProductSearchIntent.model_validate(payload)

    @staticmethod
    def _belongs_to_conflict(item: BaseModel, field: str, conflict: IntentConflict) -> bool:
        """Match only the conflicting expected value, preserving same-field variants."""

        if str(getattr(item, "field", "")).upper() != field.upper():
            return False
        expected = getattr(item, "expected", object())
        return expected == conflict.left_input.get("expected") or expected == conflict.right_input.get("expected")

    @staticmethod
    def _numeric_group_matches(
        item: NumericPreference,
        field: str,
        conflict: IntentConflict,
    ) -> bool:
        if item.field.upper() != field.upper():
            return False

        def currency(value: Any) -> str | None:
            return str(value).upper() if value is not None else None

        currencies = {
            currency(conflict.left_input.get("currency")),
            currency(conflict.right_input.get("currency")),
        }
        return currency(item.currency) in currencies
