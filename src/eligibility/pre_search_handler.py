from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from eligibility.schema.application_input import (
    Capability,
    HardConstraint,
    NumericPreference,
    Preference,
)
from eligibility.schema.conversation import (
    ConversationOperation,
    ConversationTurnResult,
    PreSearchAnswerPlan,
)
from eligibility.schema.enums import (
    CapabilityState,
    HardConstraintValue,
    PreSearchAnswerStatus,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
    ContributionFrequency,
    TermUnit,
)
from eligibility.schema.search import ContributionPlanPatch, IntentPatch, PlannedQuestion
from eligibility.search.intent import IntentParser
from eligibility.search.pre_search import (
    APPLICATION_CAPACITY,
    BIRTH_DATE,
    YOUTH_POLICY_ACCOUNT_HOLDING,
    SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY,
    COMMON_BENEFIT_WILLINGNESS,
    INSTITUTION_PRODUCT_HOLDING_HISTORY,
    CONTRIBUTION_AND_TERM,
    INSTITUTION_SCOPE,
    PRODUCT_TYPE,
    PROTECTION_AND_CMA_SCOPE,
    QUESTION_ORDER,
    pre_search_answer_examples,
)

if TYPE_CHECKING:
    from eligibility.application_service import _SearchRuntime


class PreSearchHandlerMixin:
    """Pre-search question handling for :class:`ApplicationService`."""

    def _pending_pre_search_questions(
        self,
        runtime: _SearchRuntime,
    ) -> list[PlannedQuestion]:
        if not self.pre_search_enabled:
            return []
        products = list(runtime.candidate_products.values()) or list(self.products.values())
        pending: list[PlannedQuestion] = []
        for key in QUESTION_ORDER:
            entry = runtime.pre_search_profile.get(key)
            if entry is None or entry.answer_status != PreSearchAnswerStatus.NOT_ASKED:
                continue
            if not self.pre_search_question_planner._is_applicable(
                key,
                products,
                intent=runtime.intent,
                profile=runtime.pre_search_profile,
            ):
                continue
            pending.append(self._pre_search_question_for_key(runtime, key))
        return pending


    def _pre_search_question_for_key(
        self,
        runtime: _SearchRuntime,
        key: str,
    ) -> PlannedQuestion:
        if key not in runtime.pre_search_profile:
            raise KeyError(f"Unknown pre-search question key: {key}")
        products = list(runtime.candidate_products.values()) or list(self.products.values())
        affected_product_ids = list(runtime.candidate_products)
        explanation_details = {
            "question_owner": "DETERMINISTIC_BACKEND",
            "answer_interpreter": "LLM_FLEXIBLE_TURN_PLAN",
            "question_key": key,
        }
        if key == INSTITUTION_PRODUCT_HOLDING_HISTORY:
            affected_product_ids = [
                product.product_id
                for product in products
                if self.pre_search_question_planner._has_institution_product_holding_history_condition(product)
            ]
            explanation_details["institution_history_options"] = (
                self.pre_search_question_planner._institution_history_options(products)
            )
        return PlannedQuestion(
            question_id=f"PRESEARCH-{key}",
            question_kind="PRE_SEARCH_PROFILE",
            question=self.pre_search_question_planner._question(key, runtime.intent),
            affected_product_ids=affected_product_ids,
            score=Decimal(1_000_000 - QUESTION_ORDER.index(key)),
            product_context=None,
            explanation=self.pre_search_question_planner._explanation(key),
            explanation_details=explanation_details,
            question_stage="PRE_SEARCH",
            pre_search_key=key,
            answer_mode=(
                "OPTIONS"
                if key == INSTITUTION_PRODUCT_HOLDING_HISTORY
                else "FREE_TEXT"
            ),
            answer_examples=pre_search_answer_examples(
                key,
                runtime.intent.product_types,
                contribution_plan=runtime.intent.contribution_plan,
            ),
        )

    def _handle_pre_search_message(
        self,
        runtime: _SearchRuntime,
        *,
        message: str,
    ) -> ConversationTurnResult:
        """Interpret one natural answer to a backend-owned pre-search question."""

        interpreter = getattr(self.conversation_orchestrator, "interpret_pre_search", None)
        if interpreter is None:
            raise ValueError("Pre-search answer interpreter is not configured")
        runtime.working_note_turn_sequence += 1
        runtime.recent_user_messages = [*runtime.recent_user_messages, message][-2:]
        self._safe_record_working_note_turn(
            runtime,
            sequence=runtime.working_note_turn_sequence,
            utterance=message,
        )
        self._safe_update_working_note(runtime)
        plan: PreSearchAnswerPlan = interpreter(
            message,
            context=self._conversation_context(runtime),
        )
        active = runtime.active_question
        assert active is not None and active.pre_search_key is not None

        if plan.resolution == "EXPLAIN":
            return ConversationTurnResult(
                search_session_id=runtime.session.search_session_id,
                message=message,
                operations_executed=[ConversationOperation.EXPLAIN_ACTIVE_QUESTION],
                session=runtime.session,
                next_question=active,
                recommendations=None,
                assistant_message=plan.assistant_message,
            )
        if plan.resolution == "NOT_AN_ANSWER":
            general_plan = self.conversation_orchestrator.interpret(
                message,
                context=self._conversation_context(runtime),
            )
            filter_actions = [
                action
                for action in general_plan.actions
                if action.operation == ConversationOperation.UPDATE_SEARCH_INTENT
            ]
            if filter_actions:
                snapshot = self._snapshot_runtime(runtime)
                try:
                    for action in filter_actions:
                        self._execute_conversation_action(
                            runtime,
                            action,
                            message=message,
                        )
                    self._safe_update_working_note(runtime)
                except Exception:
                    self._restore_runtime(runtime, snapshot)
                    raise
                return ConversationTurnResult(
                    search_session_id=runtime.session.search_session_id,
                    message=message,
                    operations_executed=[
                        ConversationOperation.UPDATE_SEARCH_INTENT
                        for _action in filter_actions
                    ],
                    session=runtime.session,
                    next_question=runtime.active_question,
                    recommendations=None,
                )
            return ConversationTurnResult(
                search_session_id=runtime.session.search_session_id,
                message=message,
                operations_executed=[ConversationOperation.NO_OP],
                session=runtime.session,
                next_question=active,
                recommendations=None,
                assistant_message="현재 질문에 대한 답을 자연스럽게 말씀해 주세요.",
            )

        snapshot = self._snapshot_runtime(runtime)
        try:
            operation = self._apply_pre_search_answer(
                runtime,
                active,
                plan,
                source_text=message,
            )
            # Every accepted onboarding answer is part of the visible search
            # state. Recompute immediately so cards never lag one question
            # behind hard filters such as 가입 명의 or 기관 범위.
            self._run_pipeline(runtime, select_question=False)
            self._select_question(runtime)
            self._safe_update_working_note(runtime)
        except Exception:
            self._restore_runtime(runtime, snapshot)
            raise

        next_question = runtime.active_question
        recommendations = (
            self.get_top_recommendations(runtime.session.search_session_id)
            if next_question is None and not runtime.conflicts
            else None
        )
        return ConversationTurnResult(
            search_session_id=runtime.session.search_session_id,
            message=message,
            operations_executed=[operation],
            session=runtime.session,
            next_question=next_question,
            recommendations=recommendations,
        )

    def _apply_pre_search_answer(
        self,
        runtime: _SearchRuntime,
        question: PlannedQuestion,
        plan: PreSearchAnswerPlan,
        *,
        source_text: str,
    ) -> ConversationOperation:
        key = question.pre_search_key
        assert key is not None
        now = datetime.now(timezone.utc)
        value: dict[str, Any] = {}
        patch = IntentPatch()

        if (
            key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY
            and plan.resolution in {"ACKNOWLEDGED_UNKNOWN", "NOT_APPLICABLE"}
        ):
            plan = plan.model_copy(
                update={
                    "resolution": "ANSWER",
                    "soldier_tomorrow_savings_eligible": False,
                },
                deep=True,
            )

        if plan.resolution == "ACKNOWLEDGED_UNKNOWN":
            status = PreSearchAnswerStatus.ACKNOWLEDGED_UNKNOWN
            operation = ConversationOperation.ACKNOWLEDGE_PRE_SEARCH_UNKNOWN
        elif plan.resolution == "NOT_APPLICABLE":
            status = PreSearchAnswerStatus.NOT_APPLICABLE
            operation = ConversationOperation.SUBMIT_PRE_SEARCH_ANSWER
            if key == COMMON_BENEFIT_WILLINGNESS:
                patch, value = self._pre_search_patch(
                    key,
                    plan.model_copy(
                        update={"resolution": "ANSWER", "willingness": "UNWILLING"},
                        deep=True,
                    ),
                )
                value["applicability"] = "NOT_APPLICABLE"
        else:
            status = PreSearchAnswerStatus.ANSWERED
            operation = ConversationOperation.SUBMIT_PRE_SEARCH_ANSWER
            patch, value = self._pre_search_patch(
                key,
                plan,
                product_types=set(runtime.intent.product_types),
            )
            if key == BIRTH_DATE and plan.birth_date is not None:
                if plan.birth_date > runtime.subscription_date:
                    raise ValueError("생년월일은 가입 예정일보다 이후일 수 없습니다")
                reference = runtime.subscription_date
                value["age_years"] = (
                    reference.year
                    - plan.birth_date.year
                    - (
                        (reference.month, reference.day)
                        < (plan.birth_date.month, plan.birth_date.day)
                    )
                )

        if patch.model_dump(exclude_none=True, exclude_defaults=True):
            runtime.intent = IntentParser.apply_patch(
                runtime.intent,
                patch,
                source_utterance=source_text,
            )
            self._increment_intent_version(runtime)
        if key == CONTRIBUTION_AND_TERM and runtime.intent.contribution_plan is not None:
            contribution_plan = runtime.intent.contribution_plan
            value = {
                **value,
                **{
                    field: field_value
                    for field, field_value in {
                        "desired_amount_krw": contribution_plan.desired_periodic_amount,
                        "maximum_amount_krw": contribution_plan.maximum_affordable_periodic_amount,
                        "balance_or_lump_sum_krw": contribution_plan.preferred_start_amount,
                        "contribution_frequency": (
                            contribution_plan.frequency.value
                            if contribution_plan.frequency is not None
                            else None
                        ),
                        "term_value": contribution_plan.selected_term_value,
                        "term_unit": (
                            contribution_plan.selected_term_unit.value
                            if contribution_plan.selected_term_unit is not None
                            else None
                        ),
                        "term_strictness": contribution_plan.term_strictness,
                    }.items()
                    if field_value is not None
                },
            }
            if status == PreSearchAnswerStatus.ANSWERED:
                has_amount = any(
                    amount is not None
                    for amount in (
                        contribution_plan.desired_periodic_amount,
                        contribution_plan.maximum_affordable_periodic_amount,
                        contribution_plan.preferred_start_amount,
                    )
                )
                has_term = contribution_plan.term_strictness == "ANY" or (
                    contribution_plan.selected_term_value is not None
                    and contribution_plan.selected_term_unit is not None
                )
                # This profile row is a view over the canonical ContributionPlan.
                # A partial natural-language answer fills only its matching field;
                # the same deterministic question remains open for the missing one.
                if not (has_amount and has_term):
                    status = PreSearchAnswerStatus.NOT_ASKED

        runtime.pre_search_profile[key] = runtime.pre_search_profile[key].model_copy(
            update={
                "answer_status": status,
                "value": value,
                "source_text": source_text,
                "rationale": plan.rationale,
                "answered_at": now,
            },
            deep=True,
        )

        answered_ids = [*runtime.session.answered_question_ids, question.question_id]
        runtime.session = runtime.session.model_copy(
            update={
                "answered_question_ids": list(dict.fromkeys(answered_ids)),
                "active_question_id": None,
                "updated_at": now,
            },
            deep=True,
        )
        runtime.active_question = None
        return operation

    @staticmethod
    def _pre_search_patch(
        key: str,
        plan: PreSearchAnswerPlan,
        *,
        product_types: set[str] | None = None,
    ) -> tuple[IntentPatch, dict[str, Any]]:
        value = plan.model_dump(
            mode="json",
            exclude={"resolution", "rationale"},
            exclude_none=True,
            exclude_defaults=True,
        )
        if key == PRODUCT_TYPE:
            if not plan.product_types:
                raise ValueError("상품 형태에 대한 답을 이해하지 못했습니다")
            return IntentPatch(upsert_product_types=plan.product_types), value

        if key == APPLICATION_CAPACITY:
            if plan.application_capacity is None:
                raise ValueError("가입 명의에 대한 답을 이해하지 못했습니다")
            # This deterministic question tells foreign users to identify
            # themselves. No foreigner indication therefore selects the
            # Korean-national path and remains visible in the profile value.
            value["foreign_national"] = bool(plan.foreign_national)
            return IntentPatch(
                application_capacity_patch=plan.application_capacity
            ), value

        if key == BIRTH_DATE:
            if plan.birth_date is None:
                raise ValueError("생년월일을 이해하지 못했습니다")
            return IntentPatch(), value

        if key == YOUTH_POLICY_ACCOUNT_HOLDING:
            if plan.youth_policy_account_held is None:
                raise ValueError("청년 정책계좌 보유 여부를 이해하지 못했습니다")
            return IntentPatch(), value

        if key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY:
            if plan.soldier_tomorrow_savings_eligible is None:
                raise ValueError("장병내일준비적금 가입대상 여부를 이해하지 못했습니다")
            return IntentPatch(), value

        if key == CONTRIBUTION_AND_TERM:
            if (
                plan.desired_amount_krw is None
                and plan.maximum_amount_krw is None
                and plan.term_value is None
                and plan.term_strictness != "ANY"
            ):
                raise ValueError("금액 또는 기간에 대한 답을 이해하지 못했습니다")
            lump_or_balance_only = bool(product_types) and product_types <= {
                "TIME_DEPOSIT",
                "PARKING_ACCOUNT",
                "CMA",
            }
            frequency = (
                ContributionFrequency(plan.contribution_frequency)
                if plan.contribution_frequency is not None
                else ContributionFrequency.FLEXIBLE
                if lump_or_balance_only
                else ContributionFrequency.MONTHLY
                if plan.desired_amount_krw is not None or plan.maximum_amount_krw is not None
                else None
            )
            contribution_patch = ContributionPlanPatch(
                desired_periodic_amount=plan.desired_amount_krw,
                maximum_affordable_periodic_amount=plan.maximum_amount_krw,
                frequency=frequency,
                selected_term_value=plan.term_value,
                selected_term_unit=(TermUnit(plan.term_unit) if plan.term_unit else None),
                term_strictness=plan.term_strictness,
            )
            numeric: list[NumericPreference] = []
            if plan.desired_amount_krw is not None and not lump_or_balance_only:
                numeric.append(
                    NumericPreference(
                        field="MONTHLY_CONTRIBUTION",
                        value=plan.desired_amount_krw,
                        direction=NumericPreferenceDirection.AROUND,
                        strictness=PreferenceStrictness.SOFT,
                        currency="KRW",
                    )
                )
            if plan.term_value is not None and plan.term_unit is not None:
                months = {
                    "YEAR": Decimal(plan.term_value * 12),
                    "MONTH": Decimal(plan.term_value),
                    "WEEK": Decimal(plan.term_value * 7) / Decimal("30.4375"),
                    "DAY": Decimal(plan.term_value) / Decimal("30.4375"),
                }[plan.term_unit]
                direction = {
                    "MAXIMUM": NumericPreferenceDirection.AT_MOST,
                    "MINIMUM": NumericPreferenceDirection.AT_LEAST,
                }.get(plan.term_strictness, NumericPreferenceDirection.AROUND)
                numeric.append(
                    NumericPreference(
                        field="TERM_MONTHS",
                        value=months,
                        direction=direction,
                        strictness=(
                            PreferenceStrictness.HARD
                            if plan.term_strictness in {"EXACT", "MAXIMUM", "MINIMUM"}
                            else PreferenceStrictness.SOFT
                        ),
                    )
                )
            return IntentPatch(
                upsert_numeric_preferences=numeric,
                remove_numeric_preference_keys=(
                    ["TERM_MONTHS"] if plan.term_strictness == "ANY" else []
                ),
                contribution_plan_patch=contribution_patch,
            ), value

        if key == PROTECTION_AND_CMA_SCOPE:
            if plan.liquid_product_scope is None:
                raise ValueError("예금자보호와 CMA 비교 범위에 대한 답을 이해하지 못했습니다")
            if plan.liquid_product_scope == "PARKING_ONLY":
                return IntentPatch(
                    upsert_product_types=["PARKING_ACCOUNT"],
                    remove_product_types=["CMA"],
                ), value
            if plan.liquid_product_scope == "CMA_ONLY":
                return IntentPatch(
                    upsert_product_types=["CMA"],
                    remove_product_types=["PARKING_ACCOUNT"],
                ), value
            return IntentPatch(
                upsert_product_types=["PARKING_ACCOUNT", "CMA"],
            ), value

        if key == INSTITUTION_SCOPE:
            if plan.institution_scope is None:
                raise ValueError("금융기관 범위에 대한 답을 이해하지 못했습니다")
            allowed_sectors = {
                "FIRST_SECTOR_ONLY": "BANK",
                "BANKS_AND_SAVINGS_BANKS": "BANK|SAVINGS_BANK",
                "BANKS_AND_SECURITIES": "BANK|SECURITIES",
                "SECURITIES_ONLY": "SECURITIES",
            }
            if plan.institution_scope in allowed_sectors:
                return IntentPatch(
                    upsert_hard_constraints=[
                        HardConstraint(
                            field="INSTITUTION_SECTOR",
                            constraint=HardConstraintValue.REQUIRE,
                            expected=allowed_sectors[plan.institution_scope],
                        )
                    ]
                ), value
            return IntentPatch(
                remove_hard_constraint_keys=["INSTITUTION_SECTOR"]
            ), value

        if key == COMMON_BENEFIT_WILLINGNESS:
            action_willingness = {
                "FIRST_TRANSACTION_BENEFIT": (
                    plan.first_transaction_willingness or plan.willingness
                ),
                "SALARY_BENEFIT": (
                    plan.salary_transfer_willingness or plan.willingness
                ),
                "CARD_BENEFIT": plan.card_willingness or plan.willingness,
            }
            if not any(action_willingness.values()):
                raise ValueError("공통 우대 활용 의향을 이해하지 못했습니다")
            value["action_preferences"] = {
                feature: willingness
                for feature, willingness in action_willingness.items()
                if willingness is not None
            }
            preferences = [
                Preference(
                    field=feature,
                    preference=PreferenceValue.PREFER_ABSENT,
                )
                for feature, willingness in action_willingness.items()
                if willingness in {"UNWILLING", "NOT_APPLICABLE"}
            ]
            remove_preferences = [
                feature
                for feature, willingness in action_willingness.items()
                if willingness in {"WILLING", "CONDITIONAL"}
            ]
            salary_willingness = action_willingness["SALARY_BENEFIT"]
            capabilities = []
            if salary_willingness in {
                "WILLING",
                "UNWILLING",
                "NOT_APPLICABLE",
            }:
                capabilities.append(
                    Capability(
                        capability_id="CHANGE_SALARY_ACCOUNT",
                        state=(
                            CapabilityState.CAN
                            if salary_willingness == "WILLING"
                            else CapabilityState.CANNOT
                        ),
                    )
                )
            card_willingness = action_willingness["CARD_BENEFIT"]
            if card_willingness in {"WILLING", "UNWILLING", "NOT_APPLICABLE"}:
                card_state = (
                    CapabilityState.CAN
                    if card_willingness == "WILLING"
                    else CapabilityState.CANNOT
                )
                capabilities.extend(
                    [
                        Capability(capability_id="NEW_CARD_ISSUANCE", state=card_state),
                        Capability(capability_id="CARD_USAGE", state=card_state),
                        Capability(
                            capability_id="CHANGE_CARD_SETTLEMENT_ACCOUNT",
                            state=card_state,
                        ),
                    ]
                )
            return IntentPatch(
                upsert_preferences=preferences,
                remove_preference_keys=remove_preferences,
                upsert_capabilities=capabilities,
            ), value

        if key == INSTITUTION_PRODUCT_HOLDING_HISTORY:
            if (
                not plan.prior_product_holding_institutions
                and plan.no_prior_product_holding_institutions is None
            ):
                raise ValueError("최근 상품 보유 이력이 있었던 금융기관을 이해하지 못했습니다")
            # This is intentionally retained as a user-declared screening aid,
            # not written into bank-ledger facts. A bank must still verify a
            # first-transaction benefit before its rate is treated as confirmed.
            return IntentPatch(), value

        raise KeyError(key)
