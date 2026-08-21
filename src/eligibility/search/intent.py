from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eligibility.audit import canonical_hash
from eligibility.llm import LLMGateway, LLMPurpose
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
    TermUnit,
)
from eligibility.schema.search import (
    ClarificationRequest,
    ContributionPlan,
    IntentConflict,
    IntentPatch,
    ContributionPlanPatch,
    ProductSearchIntent,
)


class ProductSearchIntentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_types: list[str] = Field(default_factory=list)
    ranking_objective: RankingObjective = RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    numeric_preferences: list[NumericPreference] = Field(default_factory=list)
    contribution_plan: ContributionPlan | None = None
    requested_top_k: int = Field(default=5, ge=1, le=20)


class IntentParser:
    """LLM interface with a deterministic parser fallback for the MVP flow."""

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway

    def parse(self, utterance: str, *, user_id: str) -> ProductSearchIntent:
        utterance = utterance.strip()
        if not utterance:
            raise ValueError("utterance must not be empty")
        draft = self._parse_with_llm(utterance) if self.gateway is not None else None
        if draft is None:
            draft = self._parse_deterministically(utterance)
        now = datetime.now(timezone.utc)
        identity = {"user_id": user_id, "utterance": utterance, "at": now.isoformat()}
        return ProductSearchIntent(
            search_intent_id=f"INTENT-{canonical_hash(identity)[:16]}",
            user_id=user_id,
            source_utterances=[utterance],
            created_at=now,
            updated_at=now,
            **draft.model_dump(mode="python"),
        )

    def update(
        self,
        current: ProductSearchIntent,
        utterance: str,
    ) -> ProductSearchIntent:
        """Apply a follow-up utterance as an IntentPatch, never category replacement."""

        patch = self._parse_patch_with_llm(utterance) if self.gateway is not None else None
        if patch is None:
            patch = self._parse_patch_deterministically(utterance)
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

        if patch.contribution_plan_patch is not None:
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
            plan_payload.update(delta)
            # Keep the plan object only when it still carries a user-specific value.
            payload["contribution_plan"] = ContributionPlan.model_validate(plan_payload)

        if patch.ranking_objective_patch is not None:
            payload["ranking_objective"] = patch.ranking_objective_patch
        if patch.requested_top_k_patch is not None:
            payload["requested_top_k"] = patch.requested_top_k_patch

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

    def _parse_patch_with_llm(self, utterance: str) -> IntentPatch | None:
        assert self.gateway is not None
        prompt = (
            "사용자의 후속 금융상품 탐색 발화를 IntentPatch로 구조화하세요. "
            "사용자가 명시적으로 바꾼 key만 upsert/remove하고, 언급하지 않은 기존 값은 "
            "절대 삭제하지 마세요. remove는 사용자가 해당 조건을 더 이상 적용하지 않겠다고 "
            "명시한 경우에만 사용하세요. 정의된 schema 밖의 값을 만들지 마세요.\n" + utterance
        )
        try:
            return self.gateway.generate_structured(
                LLMPurpose.INTENT_PARSING,
                prompt,
                IntentPatch,
                metadata={"utterance_hash": canonical_hash(utterance), "operation": "PATCH"},
            ).data
        except Exception:
            return None

    @staticmethod
    def _parse_patch_deterministically(utterance: str) -> IntentPatch:
        draft = IntentParser._parse_deterministically(utterance)
        compact = re.sub(r"\s+", "", utterance)

        remove_hard: list[str] = []
        remove_pref: list[str] = []
        remove_cap: list[str] = []
        remove_numeric: list[str] = []
        remove_plan_fields: list[str] = []

        neutral = any(marker in compact for marker in ("상관없", "신경안", "조건빼", "조건제거", "취소"))
        if neutral and "영업점" in utterance:
            remove_hard.append("SUBSCRIPTION_CHANNEL")
            remove_cap.append("BRANCH_VISIT")
        if neutral and "카드" in utterance:
            remove_pref.append("NEW_CARD_REQUIRED")
            remove_cap.append("NEW_CARD_ISSUANCE")
        if neutral and "급여계좌" in utterance:
            remove_cap.append("CHANGE_SALARY_ACCOUNT")
        if neutral and "첫거래" in utterance:
            remove_hard.append("FIRST_TRANSACTION_BENEFIT")
            remove_pref.append("FIRST_TRANSACTION_BENEFIT")
        if neutral and any(marker in compact for marker in ("월납입", "납입금액", "금액")):
            remove_numeric.append("MONTHLY_CONTRIBUTION")
            remove_plan_fields.extend(["desired_periodic_amount", "maximum_affordable_periodic_amount"])

        # Explicit-neutral language means removal, not a contradictory upsert inferred
        # from generic negative words in the normal deterministic parser.
        upsert_hard = [] if neutral and "영업점" in utterance else draft.hard_constraints
        upsert_pref = [] if neutral and ("카드" in utterance or "첫거래" in utterance) else draft.preferences
        upsert_caps = [
            item for item in draft.capabilities
            if item.capability_id.upper() not in {value.upper() for value in remove_cap}
        ]

        contribution_patch = None
        if draft.contribution_plan is not None or remove_plan_fields:
            plan_values = (
                draft.contribution_plan.model_dump(mode="python", exclude_none=True)
                if draft.contribution_plan is not None
                else {}
            )
            contribution_patch = ContributionPlanPatch(
                **plan_values,
                remove_fields=list(dict.fromkeys(remove_plan_fields)),
            )

        objective_patch = None
        if any(marker in compact for marker in ("금리", "관리", "이자", "편한", "쉬운")):
            objective_patch = draft.ranking_objective

        return IntentPatch(
            upsert_product_types=draft.product_types,
            upsert_hard_constraints=upsert_hard,
            remove_hard_constraint_keys=list(dict.fromkeys(remove_hard)),
            upsert_preferences=upsert_pref,
            remove_preference_keys=list(dict.fromkeys(remove_pref)),
            upsert_capabilities=upsert_caps,
            remove_capability_keys=list(dict.fromkeys(remove_cap)),
            upsert_numeric_preferences=([] if remove_numeric else draft.numeric_preferences),
            remove_numeric_preference_keys=list(dict.fromkeys(remove_numeric)),
            contribution_plan_patch=contribution_patch,
            ranking_objective_patch=objective_patch,
        )

    def _parse_with_llm(self, utterance: str) -> ProductSearchIntentDraft | None:
        assert self.gateway is not None
        prompt = (
            "사용자 금융상품 탐색 발화를 ProductSearchIntentDraft로 구조화하세요. "
            "Hard Constraint, Preference, Capability, NumericPreference의 의미를 섞지 "
            "말고, 사용자가 말하지 않은 제한을 추가하지 마세요.\n" + utterance
        )
        try:
            return self.gateway.generate_structured(
                LLMPurpose.INTENT_PARSING,
                prompt,
                ProductSearchIntentDraft,
                metadata={"utterance_hash": canonical_hash(utterance)},
            ).data
        except Exception:
            return None

    @staticmethod
    def _parse_deterministically(utterance: str) -> ProductSearchIntentDraft:
        compact = re.sub(r"\s+", "", utterance)
        product_types: list[str] = []
        if "적금" in utterance:
            product_types.append("INSTALLMENT_SAVINGS")
        if "예금" in utterance and "적금" not in utterance:
            product_types.append("TIME_DEPOSIT")

        objective = RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST
        if re.search(r"금리.*(높|최고|제일)|(?:높|최고|제일).*금리", compact):
            objective = RankingObjective.MAX_REALIZABLE_RATE
        elif re.search(r"(관리|조건).*(쉽|편)|(?:쉽|편).*(관리|조건)", compact):
            objective = RankingObjective.MIN_ACTION_BURDEN

        term_value: int | None = None
        term_unit: TermUnit | None = None
        term_match = re.search(r"(\d+)\s*(년|개월|주|일)", utterance)
        if term_match:
            term_value = int(term_match.group(1))
            term_unit = {
                "년": TermUnit.YEAR,
                "개월": TermUnit.MONTH,
                "주": TermUnit.WEEK,
                "일": TermUnit.DAY,
            }[term_match.group(2)]

        amount_match = re.search(r"월\s*(\d+(?:\.\d+)?)\s*(만원|원)", utterance)
        amount: Decimal | None = None
        if amount_match:
            amount = Decimal(amount_match.group(1))
            if amount_match.group(2) == "만원":
                amount *= Decimal("10000")

        explicit_maximum = amount is not None and any(
            marker in compact
            for marker in ("월최대", "월한도", "까지가능", "까지만", "초과불가", "이상못")
        )
        explicit_desire = amount is not None and (
            not explicit_maximum
            or any(marker in compact for marker in ("넣고싶", "생각", "납입계획", "기준"))
        )
        desired = amount if explicit_desire else None
        maximum = amount if explicit_maximum else None

        contribution_plan = None
        if desired is not None or maximum is not None or term_value is not None:
            contribution_plan = ContributionPlan(
                desired_periodic_amount=desired,
                maximum_affordable_periodic_amount=maximum,
                frequency=(
                    ContributionFrequency.MONTHLY
                    if desired is not None or maximum is not None
                    else None
                ),
                selected_term_value=term_value,
                selected_term_unit=term_unit,
            )

        numeric_preferences: list[NumericPreference] = []
        if amount is not None:
            requires_capacity = any(
                marker in compact
                for marker in (
                    "납입이불가능한상품제외",
                    "넣을수없는상품제외",
                    "넣을수없는상품은제외",
                    "수용못하는상품제외",
                    "월납입한도미만제외",
                )
            )
            numeric_preferences.append(
                NumericPreference(
                    field="MONTHLY_CONTRIBUTION",
                    value=amount,
                    direction=(
                        NumericPreferenceDirection.AT_LEAST
                        if requires_capacity
                        else NumericPreferenceDirection.AROUND
                    ),
                    strictness=(
                        PreferenceStrictness.HARD
                        if requires_capacity
                        else PreferenceStrictness.SOFT
                    ),
                    currency="KRW",
                )
            )

        if term_value is not None and term_unit is not None:
            term_months = {
                TermUnit.YEAR: Decimal(term_value * 12),
                TermUnit.MONTH: Decimal(term_value),
                TermUnit.WEEK: Decimal(term_value * 7) / Decimal("30.4375"),
                TermUnit.DAY: Decimal(term_value) / Decimal("30.4375"),
            }[term_unit]
            if any(marker in compact for marker in ("이하만", "넘는상품제외", "초과제외")):
                direction = NumericPreferenceDirection.AT_MOST
                strictness = PreferenceStrictness.HARD
            elif any(marker in compact for marker in ("이상만", "미만제외")):
                direction = NumericPreferenceDirection.AT_LEAST
                strictness = PreferenceStrictness.HARD
            else:
                direction = NumericPreferenceDirection.AROUND
                strictness = PreferenceStrictness.SOFT
            numeric_preferences.append(
                NumericPreference(
                    field="TERM_MONTHS",
                    value=term_months,
                    direction=direction,
                    strictness=strictness,
                )
            )

        hard_constraints: list[HardConstraint] = []
        preferences: list[Preference] = []
        capabilities: list[Capability] = []

        if "영업점" in utterance and any(word in compact for word in ("제외", "싫", "안가", "방문불가")):
            hard_constraints.append(
                HardConstraint(
                    field="SUBSCRIPTION_CHANNEL",
                    constraint=HardConstraintValue.EXCLUDE,
                    expected="BRANCH",
                )
            )
            capabilities.append(
                Capability(capability_id="BRANCH_VISIT", state=CapabilityState.CANNOT)
            )

        if "카드" in utterance and any(word in compact for word in ("새로만들기싫", "발급싫", "신규카드싫", "카드새로")):
            preferences.append(
                Preference(
                    field="NEW_CARD_REQUIRED",
                    preference=PreferenceValue.PREFER_ABSENT,
                )
            )
            capabilities.append(
                Capability(capability_id="NEW_CARD_ISSUANCE", state=CapabilityState.CANNOT)
            )

        if "급여계좌" in utterance and "변경" in utterance:
            salary_match = re.search(r"급여계좌.{0,20}변경.{0,20}", compact)
            salary_context = salary_match.group(0) if salary_match else "급여계좌변경"
            state = (
                CapabilityState.CANNOT
                if any(word in salary_context for word in ("불가", "못", "싫", "안됨"))
                else CapabilityState.CAN
            )
            capabilities.append(
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=state)
            )

        if "첫거래" in utterance:
            if any(word in compact for word in ("있는상품만", "필수", "반드시")):
                hard_constraints.append(
                    HardConstraint(
                        field="FIRST_TRANSACTION_BENEFIT",
                        constraint=HardConstraintValue.REQUIRE,
                    )
                )
            elif any(word in compact for word in ("없는게좋", "없으면좋", "없는상품선호")):
                preferences.append(
                    Preference(
                        field="FIRST_TRANSACTION_BENEFIT",
                        preference=PreferenceValue.PREFER_ABSENT,
                    )
                )

        return ProductSearchIntentDraft(
            product_types=product_types,
            ranking_objective=objective,
            hard_constraints=hard_constraints,
            preferences=preferences,
            capabilities=capabilities,
            numeric_preferences=numeric_preferences,
            contribution_plan=contribution_plan,
            requested_top_k=5,
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
        prompt = (
            "다음 IntentConflict를 사용자가 이해하기 쉬운 한 문장으로 다시 물으세요. "
            "conflict_ids와 allowed_resolutions는 정확히 복사하고, 새로운 금융조건이나 "
            "해결 선택지를 추가하지 마세요.\n"
            + str(
                {
                    "conflicts": [item.model_dump(mode="json") for item in conflicts],
                    "conflict_ids": fallback.conflict_ids,
                    "allowed_resolutions": fallback.allowed_resolutions,
                }
            )
        )
        try:
            generated = self.gateway.generate_structured(
                LLMPurpose.INTENT_CLARIFICATION,
                prompt,
                GeneratedClarification,
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
            payload["preferences"] = [item for item in intent.preferences if item.field != field]
        elif resolution == "REMOVE_HARD_KEEP_PREFERENCE":
            payload["hard_constraints"] = [item for item in intent.hard_constraints if item.field != field]
        elif resolution == "SET_NEUTRAL":
            payload["hard_constraints"] = [item for item in intent.hard_constraints if item.field != field]
            payload["preferences"] = [
                item for item in intent.preferences if item.field != field
            ] + [Preference(field=field, preference=PreferenceValue.NEUTRAL)]
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
            payload["preferences"] = [item for item in intent.preferences if item.field != field] + [
                Preference(field=field, preference=preference)
            ]
        elif resolution in {"KEEP_REQUIRE", "KEEP_EXCLUDE"}:
            constraint = (
                HardConstraintValue.REQUIRE
                if resolution == "KEEP_REQUIRE"
                else HardConstraintValue.EXCLUDE
            )
            payload["hard_constraints"] = [
                item for item in intent.hard_constraints if item.field != field
            ] + [HardConstraint(field=field, constraint=constraint)]
        elif resolution == "REMOVE_BOTH":
            payload["hard_constraints"] = [item for item in intent.hard_constraints if item.field != field]
        elif resolution in {"KEEP_LOWER_BOUND", "KEEP_UPPER_BOUND", "MAKE_SOFT"}:
            filtered = [item for item in intent.numeric_preferences if item.field != field]
            candidates = [item for item in intent.numeric_preferences if item.field == field]
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
                item for item in intent.hard_constraints if item.field != field
            ]
        else:  # pragma: no cover - guarded by the allowed options above.
            raise ValueError(f"Unsupported resolution: {resolution}")

        payload["updated_at"] = datetime.now(timezone.utc)
        return ProductSearchIntent.model_validate(payload)
