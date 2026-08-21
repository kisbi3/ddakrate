from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from eligibility.application import (
    UserAnswerRecord,
    UserAnswerSubmission,
    revise_user_answer as revise_answer_in_store,
    submit_user_answer as submit_answer_to_store,
)
from eligibility.conversation import ConversationOrchestrator
from eligibility.audit import (
    AuditEvent,
    AuditEventType,
    AuditSession,
    InMemoryAuditSink,
    canonical_hash,
)
from eligibility.schema.application_input import QuickInputProfile
from eligibility.schema.conversation import (
    ConversationAction,
    ConversationOperation,
    ConversationPlan,
    ConversationTurnResult,
)
from eligibility.schema.enums import (
    CapabilityState,
    FactRecordStatus,
    FactSemanticType,
    FactSourceType,
    ResolutionStrategy,
    SearchSessionStatus,
)
from eligibility.schema.product import ProductDefinition
from eligibility.schema.search import (
    CandidateEvaluation,
    CandidateFilterDecision,
    ClarificationRequest,
    IntentConflict,
    IntentPatch,
    ContributionPlanPatch,
    PlannedQuestion,
    ProductRecommendationDetail,
    ProductRecommendationResult,
    ProductContributionChoice,
    ProductSearchIntent,
    RankingResult,
    SearchSession,
    ContributionPlan,
)
from eligibility.schema.user_fact import FactProvenance, UserFact, UserFactStore
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.intent import (
    IntentConflictClarifier,
    IntentConflictValidator,
    IntentParser,
)
from eligibility.search.questions import RankingAwareQuestionPlanner
from eligibility.search.ranking import RankingService
from eligibility.search.recommendation import RecommendationService
from eligibility.search.retrieval import CandidateRetriever
from eligibility.search.contribution import apply_product_contribution_choices
from eligibility.working_note import InMemorySearchWorkingNoteStore, SearchWorkingNoteStore


_DEFAULT_CAPABILITY_FACT_MAP = {
    "CHANGE_SALARY_ACCOUNT": "SALARY_ACCOUNT_CHANGE_POSSIBLE",
    "CHANGE_CARD_SETTLEMENT_ACCOUNT": "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE",
    "NEW_CARD_ISSUANCE": "NEW_CARD_ISSUANCE_POSSIBLE",
    "BRANCH_VISIT": "BRANCH_VISIT_POSSIBLE",
    "JOIN_SUPERSOL": "SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING",
}


@dataclass
class _SearchRuntime:
    session: SearchSession
    intent: ProductSearchIntent
    base_fact_store: UserFactStore
    fact_store: UserFactStore
    as_of: date
    subscription_date: date
    audit: AuditSession
    conflicts: list[IntentConflict] = field(default_factory=list)
    clarification: ClarificationRequest | None = None
    candidate_products: dict[str, ProductDefinition] = field(default_factory=dict)
    filter_decisions: list[CandidateFilterDecision] = field(default_factory=list)
    evaluations: dict[str, CandidateEvaluation] = field(default_factory=dict)
    ranking: RankingResult | None = None
    recommendation: ProductRecommendationResult | None = None
    active_question: PlannedQuestion | None = None
    question_history: dict[str, PlannedQuestion] = field(default_factory=dict)
    request_history: dict[str, Any] = field(default_factory=dict)
    answer_records: list[UserAnswerRecord] = field(default_factory=list)
    declined_ranking_input_ids: set[str] = field(default_factory=set)
    product_contribution_choices: dict[tuple[str, str], ProductContributionChoice] = field(default_factory=dict)
    product_contribution_choice_history: list[ProductContributionChoice] = field(default_factory=list)
    reopened_question_ids: set[str] = field(default_factory=set)
    working_note_turn_sequence: int = 0


class ApplicationService:
    """Transport-independent personalized product search orchestration."""

    def __init__(
        self,
        products: Iterable[ProductDefinition],
        *,
        user_fact_stores: dict[str, UserFactStore] | None = None,
        intent_parser: IntentParser | None = None,
        conflict_validator: IntentConflictValidator | None = None,
        conflict_clarifier: IntentConflictClarifier | None = None,
        candidate_retriever: CandidateRetriever | None = None,
        evaluator: MultiProductEvaluator | None = None,
        question_planner: RankingAwareQuestionPlanner | None = None,
        ranking_service: RankingService | None = None,
        recommendation_service: RecommendationService | None = None,
        audit_sink: InMemoryAuditSink | None = None,
        capability_fact_map: dict[str, str] | None = None,
        conversation_orchestrator: ConversationOrchestrator | None = None,
        working_note_store: SearchWorkingNoteStore | None = None,
    ) -> None:
        self.products = {product.product_id: product for product in products}
        self.user_fact_stores = dict(user_fact_stores or {})
        self.intent_parser = intent_parser or IntentParser()
        self.conflict_validator = conflict_validator or IntentConflictValidator()
        self.conflict_clarifier = conflict_clarifier or IntentConflictClarifier()
        self.candidate_retriever = candidate_retriever or CandidateRetriever()
        self.evaluator = evaluator or MultiProductEvaluator()
        self.ranking_service = ranking_service or RankingService()
        self.question_planner = question_planner or RankingAwareQuestionPlanner(
            ranking_service=self.ranking_service
        )
        self.recommendation_service = recommendation_service or RecommendationService()
        self.audit_sink = audit_sink or InMemoryAuditSink()
        self.capability_fact_map = {
            **_DEFAULT_CAPABILITY_FACT_MAP,
            **(capability_fact_map or {}),
        }
        self.conversation_orchestrator = conversation_orchestrator
        self.working_note_store = working_note_store or InMemorySearchWorkingNoteStore()
        self._sessions: dict[str, _SearchRuntime] = {}

    # ------------------------------------------------------------------
    # Intent/session operations
    # ------------------------------------------------------------------
    def parse_search_intent(self, utterance: str, *, user_id: str) -> ProductSearchIntent:
        return self.intent_parser.parse(utterance, user_id=user_id)

    def validate_search_intent(self, intent: ProductSearchIntent) -> list[IntentConflict]:
        return self.conflict_validator.validate(intent)

    def create_search_session(
        self,
        *,
        user_id: str,
        utterance: str | None = None,
        intent: ProductSearchIntent | None = None,
        quick_input: QuickInputProfile | None = None,
        as_of: date | None = None,
        subscription_date: date | None = None,
    ) -> SearchSession:
        if intent is None:
            if utterance is None:
                raise ValueError("utterance or intent is required")
            intent = self.parse_search_intent(utterance, user_id=user_id)
        if intent.user_id != user_id:
            raise ValueError("intent.user_id does not match user_id")
        if quick_input is not None:
            # Direct structured Quick Input is authoritative over inferred values
            # for the same key, while unrelated inferred intent is preserved.
            intent = IntentParser.apply_patch(
                intent,
                IntentPatch(
                    upsert_hard_constraints=quick_input.hard_constraints,
                    upsert_preferences=quick_input.preferences,
                    upsert_capabilities=quick_input.capabilities,
                    upsert_numeric_preferences=quick_input.numeric_preferences,
                ),
            )

        now = datetime.now(timezone.utc)
        search_session_id = f"SEARCH-{canonical_hash({'intent': intent.search_intent_id, 'at': now.isoformat()})[:16]}"
        session = SearchSession(
            search_session_id=search_session_id,
            user_id=user_id,
            top_k=intent.requested_top_k,
            created_at=now,
            updated_at=now,
        )
        base_store = self.user_fact_stores.get(user_id, UserFactStore(user_id=user_id))
        audit = AuditSession(
            self.audit_sink,
            request_id=f"REQ-{canonical_hash(search_session_id)[:16]}",
            trace_id=f"TRACE-{canonical_hash({'search': search_session_id})[:16]}",
        )
        audit.bind_search_context(
            search_session_id=search_session_id,
            intent_version=1,
        )
        runtime = _SearchRuntime(
            session=session,
            intent=intent,
            base_fact_store=base_store,
            fact_store=base_store,
            as_of=as_of or date.today(),
            subscription_date=subscription_date or (as_of or date.today()),
            audit=audit,
        )
        self._sessions[search_session_id] = runtime
        self._safe_initialize_working_note(runtime)
        audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.SEARCH_SESSION_CREATED,
            entity_refs={"search_session_id": search_session_id, "user_id": user_id},
            output_data=session,
            payload={"top_k": intent.requested_top_k},
        )
        audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.SEARCH_INTENT_PARSED,
            entity_refs={"search_intent_id": intent.search_intent_id},
            output_data=intent,
            payload={"source_utterance_count": len(intent.source_utterances)},
        )
        self._run_pipeline(runtime)
        self._safe_update_working_note(runtime)
        return runtime.session


    def get_search_working_note(self, search_session_id: str) -> str | None:
        self._runtime(search_session_id)
        return self.working_note_store.load(search_session_id)

    def get_mutable_search_state(self, search_session_id: str) -> dict[str, Any]:
        """Read-only structured snapshot for Web presentation.

        This view is derived from the current ApplicationService runtime state; it
        never reads or parses the Search Working Note.  It intentionally exposes
        only mutable user/search state and user declarations, not authoritative
        MyData or institution evidence.
        """

        runtime = self._runtime(search_session_id)
        return self._mutable_state_summary(runtime)

    def close_search_session(self, search_session_id: str) -> None:
        runtime = self._runtime(search_session_id)
        self._safe_delete_working_note(runtime)
        self._sessions.pop(search_session_id, None)

    def clear_product_contribution_choice(
        self,
        search_session_id: str,
        *,
        product_id: str,
        field: str,
    ) -> SearchSession:
        """Return one mutable product-scoped choice to an unresolved state."""

        runtime = self._runtime(search_session_id)
        key = (product_id, field)
        old = runtime.product_contribution_choices.get(key)
        if old is None:
            self._clear_declined_ranking_input_for_product_field(
                runtime, product_id=product_id, field=field
            )
            self._run_pipeline(runtime)
            self._safe_update_working_note(runtime)
            return runtime.session

        rewritten = []
        for item in runtime.product_contribution_choice_history:
            if item.choice_id == old.choice_id and item.record_status == "ACTIVE":
                rewritten.append(item.model_copy(update={"record_status": "SUPERSEDED"}, deep=True))
            else:
                rewritten.append(item)
        runtime.product_contribution_choice_history = rewritten
        runtime.product_contribution_choices.pop(key, None)
        self._sync_product_choices_to_session(runtime)
        self._clear_declined_ranking_input_for_product_field(
            runtime, product_id=product_id, field=field
        )
        reopened = set()
        for question_id, question in runtime.question_history.items():
            ranking_input = question.ranking_input
            clarification = question.feasibility_clarification
            if ranking_input is not None and ranking_input.product_id == product_id and ranking_input.required_field == field:
                reopened.add(question_id)
            if clarification is not None and clarification.product_id == product_id and clarification.field == field:
                reopened.add(question_id)
        if reopened:
            runtime.session = runtime.session.model_copy(
                update={
                    "answered_question_ids": [
                        item for item in runtime.session.answered_question_ids if item not in reopened
                    ],
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.MUTABLE_VALUE_CLEARED,
            entity_refs={"product_id": product_id, "choice_id": old.choice_id},
            input_data=old,
            output_data={"active_choice": None},
            payload={"state_kind": "PRODUCT_CONTRIBUTION_CHOICE", "field": field},
        )
        self._run_pipeline(runtime)
        self._safe_update_working_note(runtime)
        return runtime.session

    def clear_user_declared_fact(
        self,
        search_session_id: str,
        *,
        fact_type: str,
    ) -> SearchSession:
        """Clear mutable self-reported/future-intent state without touching authoritative facts."""

        runtime = self._runtime(search_session_id)
        reverse_capability_map = {
            mapped_fact: capability_id for capability_id, mapped_fact in self.capability_fact_map.items()
        }
        capability_id = reverse_capability_map.get(fact_type)
        if capability_id is not None:
            from eligibility.schema.application_input import Capability
            active_user_ids = {
                item.fact_id
                for item in runtime.fact_store.active_facts
                if item.fact_type == fact_type
                and item.source_type == FactSourceType.USER_DECLARED
            }
            if active_user_ids:
                runtime.fact_store = runtime.fact_store.model_copy(
                    update={
                        "facts": [
                            item.model_copy(
                                update={"record_status": FactRecordStatus.SUPERSEDED},
                                deep=True,
                            )
                            if item.fact_id in active_user_ids
                            else item
                            for item in runtime.fact_store.facts
                        ]
                    },
                    deep=True,
                )
                runtime.answer_records = self._answer_records(runtime.fact_store)
            session = self.update_search_intent(
                search_session_id,
                patch=IntentPatch(
                    upsert_capabilities=[
                        Capability(capability_id=capability_id, state=CapabilityState.UNKNOWN)
                    ]
                ),
            )
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.MUTABLE_VALUE_CLEARED,
                entity_refs={"fact_type": fact_type, "capability_id": capability_id},
                output_data={"capability_state": "UNKNOWN"},
                payload={
                    "state_kind": "CAPABILITY",
                    "cleared_fact_ids": sorted(active_user_ids),
                },
            )
            self._safe_update_working_note(runtime)
            return session

        active = [item for item in runtime.fact_store.active_facts if item.fact_type == fact_type]
        mutable = [item for item in active if item.source_type == FactSourceType.USER_DECLARED]
        authoritative = [item for item in active if item.source_type != FactSourceType.USER_DECLARED]
        if not mutable:
            if authoritative:
                runtime.audit.emit(
                    "APPLICATION_SERVICE",
                    AuditEventType.AUTHORITATIVE_REVISION_REJECTED,
                    entity_refs={"fact_type": fact_type},
                    output_data={"accepted": False},
                    payload={"reason": "AUTHORITATIVE_FACT_IMMUTABLE_BY_USER"},
                )
                raise ValueError(
                    f"AUTHORITATIVE_FACT_IMMUTABLE_BY_USER: {fact_type} is backed by authoritative evidence"
                )
            return runtime.session

        ids = {item.fact_id for item in mutable}
        runtime.fact_store = runtime.fact_store.model_copy(
            update={
                "facts": [
                    item.model_copy(update={"record_status": FactRecordStatus.SUPERSEDED}, deep=True)
                    if item.fact_id in ids else item
                    for item in runtime.fact_store.facts
                ]
            },
            deep=True,
        )
        runtime.answer_records = self._answer_records(runtime.fact_store)
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.MUTABLE_VALUE_CLEARED,
            entity_refs={"fact_type": fact_type},
            output_data={"active_user_declaration": None},
            payload={"state_kind": "USER_DECLARED_FACT", "cleared_fact_ids": sorted(ids)},
        )
        self._run_pipeline(runtime)
        self._safe_update_working_note(runtime)
        return runtime.session

    def get_search_status(self, search_session_id: str) -> SearchSession:
        return self._runtime(search_session_id).session

    def get_search_intent(self, search_session_id: str) -> ProductSearchIntent:
        return self._runtime(search_session_id).intent

    def get_intent_conflicts(self, search_session_id: str) -> tuple[IntentConflict, ...]:
        return tuple(self._runtime(search_session_id).conflicts)

    def get_clarification_request(
        self, search_session_id: str
    ) -> ClarificationRequest | None:
        return self._runtime(search_session_id).clarification

    def get_answer_history(self, search_session_id: str) -> tuple[UserAnswerRecord, ...]:
        return tuple(self._runtime(search_session_id).answer_records)

    def get_product_contribution_choices(
        self, search_session_id: str
    ) -> tuple[ProductContributionChoice, ...]:
        runtime = self._runtime(search_session_id)
        return tuple(
            sorted(
                runtime.product_contribution_choices.values(),
                key=lambda item: (item.product_id, item.field, item.answered_at, item.choice_id),
            )
        )

    def get_product_contribution_choice_history(
        self, search_session_id: str
    ) -> tuple[ProductContributionChoice, ...]:
        runtime = self._runtime(search_session_id)
        return tuple(
            sorted(
                runtime.product_contribution_choice_history,
                key=lambda item: (item.product_id, item.field, item.version, item.answered_at),
            )
        )

    def set_product_contribution_choice(
        self,
        search_session_id: str,
        *,
        product_id: str,
        field: str,
        value: Any,
        answered_at: datetime | None = None,
    ) -> SearchSession:
        """Set the *current* product-scoped contribution value.

        Conversational UX should not care whether the user is answering for the
        first time, supplying a value after an earlier decline, or revising a
        committed choice.  This method normalizes all three cases to the same
        mutable-state semantics while preserving append-only history.
        """

        runtime = self._runtime(search_session_id)
        key = (product_id, field)
        old_choice = runtime.product_contribution_choices.get(key)
        effective_at = self._effective_answered_at(runtime, answered_at)

        if old_choice is not None:
            old_choice, new_choice = self._prepare_product_choice_revision(
                runtime,
                product_id=product_id,
                field=field,
                new_value=value,
                answered_at=effective_at,
            )
            self._commit_product_choice_revision(runtime, old_choice, new_choice)
            self._clear_declined_ranking_input_for_product_field(
                runtime, product_id=product_id, field=field
            )
            # v0.4.5 favors correctness and current-state semantics over
            # incremental optimization after a mutable-state change.
            self._run_pipeline(runtime)
            return runtime.session

        product = runtime.candidate_products.get(product_id)
        if product is None:
            if product_id in runtime.session.excluded_product_ids:
                raise ValueError(
                    "PRODUCT_CURRENTLY_EXCLUDED: include the product before setting its contribution choice"
                )
            if product_id not in self.products:
                raise KeyError(f"Unknown product_id: {product_id}")
            raise ValueError("Product contribution choice product is not an active candidate")

        allowed_options = self._feasible_revision_options(runtime, product, field)
        try:
            normalized, unit = self._normalize_ranking_input_answer(
                field,
                value,
                allowed_options=allowed_options,
            )
            payload = {
                "product_id": product_id,
                "field": field,
                "value": normalized,
                "version": 1,
                "at": effective_at.isoformat(),
            }
            source_question_id = self._matching_active_question_id(
                runtime, product_id=product_id, field=field
            ) or f"CONVERSATION-{canonical_hash(payload)[:16]}"
            new_choice = ProductContributionChoice(
                choice_id=f"PRODUCT-CHOICE-{canonical_hash(payload)[:16]}",
                product_id=product_id,
                field=field,
                value=normalized,
                unit=unit,
                source_question_id=source_question_id,
                answered_at=effective_at,
                version=1,
                record_status="ACTIVE",
            )
            existing_for_product = [
                item
                for item in runtime.product_contribution_choices.values()
                if item.product_id == product_id and item.field != field
            ]
            self.evaluator.validate_product_contribution_choice(
                product,
                runtime.intent.contribution_plan,
                existing_for_product,
                new_choice,
                subscription_date=runtime.subscription_date,
            )
        except (TypeError, ValueError) as exc:
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.CONTRIBUTION_ANSWER_REJECTED,
                entity_refs={"product_id": product_id, "field": field},
                input_data={"new_value": value},
                output_data={"accepted": False},
                payload={
                    "operation": "SET_PRODUCT_CONTRIBUTION_CHOICE",
                    "reason": str(exc),
                    "business_state_committed": False,
                },
            )
            raise ValueError(str(exc)) from exc

        active_question_id = self._matching_active_question_id(
            runtime, product_id=product_id, field=field
        )
        if active_question_id is not None:
            self._commit_answered_question(runtime, active_question_id)
        runtime.product_contribution_choices[key] = new_choice
        runtime.product_contribution_choice_history.append(new_choice)
        self._sync_product_choices_to_session(runtime)
        self._clear_declined_ranking_input_for_product_field(
            runtime, product_id=product_id, field=field
        )
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.PRODUCT_CHOICE_UPDATED,
            entity_refs={
                "product_id": product_id,
                "choice_id": new_choice.choice_id,
            },
            output_data=new_choice,
            payload={
                "field": field,
                "old_value": None,
                "new_value": str(new_choice.value),
                "source": "CONVERSATIONAL_MUTABLE_STATE",
            },
        )
        self._run_pipeline(runtime)
        return runtime.session

    def revise_product_contribution_choice(
        self,
        search_session_id: str,
        *,
        product_id: str,
        field: str,
        new_value: Any,
        answered_at: datetime | None = None,
    ) -> SearchSession:
        """Revise one committed product-scoped contribution choice atomically.

        The prior choice remains in append-only history as SUPERSEDED.  The
        current SearchSession exposes only the new ACTIVE choice.  Validation is
        performed against the current product policy, subscription date, and
        global affordability before any business state is committed.
        """

        runtime = self._runtime(search_session_id)
        old_choice, new_choice = self._prepare_product_choice_revision(
            runtime,
            product_id=product_id,
            field=field,
            new_value=new_value,
            answered_at=self._effective_answered_at(runtime, answered_at),
        )
        self._commit_product_choice_revision(runtime, old_choice, new_choice)
        return runtime.session

    def exclude_product(
        self,
        search_session_id: str,
        *,
        product_id: str,
        reason: str = "USER_REQUEST",
    ) -> SearchSession:
        return self.set_product_exclusion(
            search_session_id,
            product_id=product_id,
            excluded=True,
            reason=reason,
        )

    def set_product_exclusion(
        self,
        search_session_id: str,
        *,
        product_id: str,
        excluded: bool,
        reason: str = "USER_REQUEST",
    ) -> SearchSession:
        """Set current session-local product exclusion state.

        Exclusion and later re-inclusion are two values of the same mutable
        search state, not separate restore workflows.
        """

        runtime = self._runtime(search_session_id)
        if product_id not in self.products:
            raise KeyError(f"Unknown product_id: {product_id}")
        current = list(runtime.session.excluded_product_ids)
        if excluded:
            updated = list(dict.fromkeys([*current, product_id]))
        else:
            updated = [item for item in current if item != product_id]
        runtime.session = runtime.session.model_copy(
            update={
                "excluded_product_ids": updated,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.PRODUCT_EXCLUSION_CHANGED,
            entity_refs={"product_id": product_id},
            output_data={"excluded_product_ids": updated},
            payload={"reason": reason, "excluded": excluded},
        )
        if excluded:
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.PRODUCT_EXCLUDED_BY_USER,
                entity_refs={"product_id": product_id},
                output_data={"excluded_product_ids": updated},
                payload={"reason": reason},
            )
        self._run_pipeline(runtime)
        return runtime.session

    def revise_user_declared_fact(
        self,
        search_session_id: str,
        *,
        fact_type: str,
        new_value: Any,
        answered_at: datetime | None = None,
    ) -> SearchSession:
        """Revise only mutable USER_DECLARED facts; protect authoritative evidence."""

        runtime = self._runtime(search_session_id)
        active = [item for item in runtime.fact_store.active_facts if item.fact_type == fact_type]
        user_declared = [item for item in active if item.source_type == FactSourceType.USER_DECLARED]
        authoritative = [item for item in active if item.source_type != FactSourceType.USER_DECLARED]

        if not user_declared:
            if authoritative:
                runtime.audit.emit(
                    "APPLICATION_SERVICE",
                    AuditEventType.AUTHORITATIVE_REVISION_REJECTED,
                    entity_refs={"fact_type": fact_type},
                    input_data={"new_value": new_value},
                    output_data={"accepted": False},
                    payload={
                        "reason": "AUTHORITATIVE_FACT_IMMUTABLE_BY_USER",
                        "source_types": sorted({item.source_type.value for item in authoritative}),
                    },
                )
                raise ValueError(
                    f"AUTHORITATIVE_FACT_IMMUTABLE_BY_USER: {fact_type} is backed by authoritative evidence"
                )

            # Known capabilities are mutable search state even before they have
            # ever been materialized as a USER_DECLARED fact.
            reverse_capability_map = {
                mapped_fact: capability_id
                for capability_id, mapped_fact in _DEFAULT_CAPABILITY_FACT_MAP.items()
            }
            capability_id = reverse_capability_map.get(fact_type)
            if capability_id is not None:
                if not isinstance(new_value, bool):
                    raise ValueError("Intent capability revision requires a boolean value")
                from eligibility.schema.application_input import Capability

                return self.update_search_intent(
                    search_session_id,
                    patch=IntentPatch(
                        upsert_capabilities=[
                            Capability(
                                capability_id=capability_id,
                                state=(
                                    CapabilityState.CAN
                                    if new_value
                                    else CapabilityState.CANNOT
                                ),
                            )
                        ]
                    ),
                )

            # A current ASK_USER missing fact can be supplied naturally even if
            # it was never selected as the active question.  This makes current
            # state authoritative instead of old workflow history.
            requests = [
                request
                for candidate in runtime.evaluations.values()
                for request in candidate.product_evaluation.missing_facts
                if request.fact_type == fact_type
                and request.resolution_strategy == ResolutionStrategy.ASK_USER
            ]
            if requests:
                request = sorted(
                    requests,
                    key=lambda item: (item.requested_by_rule_id, self._request_reference(item)),
                )[0]
                request_reference = self._request_reference(request)
                runtime.fact_store, _active = submit_answer_to_store(
                    runtime.fact_store,
                    request,
                    UserAnswerSubmission(
                        request_reference=request_reference,
                        answer=new_value,
                        answered_at=self._effective_answered_at(runtime, answered_at),
                    ),
                    audit=runtime.audit,
                )
                runtime.request_history[request_reference] = request
                runtime.answer_records = self._answer_records(runtime.fact_store)
                self._run_pipeline(runtime)
                return runtime.session

            raise KeyError(f"No mutable user-declared fact found for fact_type: {fact_type}")

        target = sorted(user_declared, key=lambda item: (item.version, item.collected_at))[-1]
        request_reference = target.request_reference
        if request_reference and request_reference in runtime.request_history:
            return self.revise_user_answer(
                search_session_id,
                request_reference=request_reference,
                new_value=new_value,
                answered_at=answered_at,
            )

        prefix = f"INTENT-CAPABILITY/{runtime.session.search_session_id}/"
        if request_reference and request_reference.startswith(prefix):
            capability_id = request_reference[len(prefix) :]
            if not isinstance(new_value, bool):
                raise ValueError("Intent capability revision requires a boolean value")
            from eligibility.schema.application_input import Capability

            return self.update_search_intent(
                search_session_id,
                patch=IntentPatch(
                    upsert_capabilities=[
                        Capability(
                            capability_id=capability_id,
                            state=(CapabilityState.CAN if new_value else CapabilityState.CANNOT),
                        )
                    ]
                ),
            )

        raise ValueError(
            f"USER_DECLARED_FACT_NOT_REVISION_ADDRESSABLE: {fact_type} has no mutable request reference"
        )

    def resolve_intent_conflict(
        self,
        search_session_id: str,
        *,
        conflict_id: str,
        resolution: str,
    ) -> SearchSession:
        runtime = self._runtime(search_session_id)
        conflict = next(
            (item for item in runtime.conflicts if item.conflict_id == conflict_id),
            None,
        )
        if conflict is None:
            raise KeyError(f"Unknown conflict_id: {conflict_id}")
        runtime.intent = self.conflict_validator.apply_resolution(
            runtime.intent,
            conflict,
            resolution,
        )
        self._increment_intent_version(runtime)
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.CLARIFICATION_RESOLVED,
            entity_refs={"conflict_id": conflict_id},
            output_data={"resolution": resolution},
            payload={"resolution": resolution},
        )
        self._run_pipeline(runtime)
        return runtime.session

    def update_search_intent(
        self,
        search_session_id: str,
        *,
        utterance: str | None = None,
        intent: ProductSearchIntent | None = None,
        patch: IntentPatch | None = None,
    ) -> SearchSession:
        runtime = self._runtime(search_session_id)
        if intent is None:
            if patch is not None:
                intent = IntentParser.apply_patch(runtime.intent, patch)
            else:
                if utterance is None:
                    raise ValueError("utterance, intent, or patch is required")
                intent = self.intent_parser.update(runtime.intent, utterance)
        if intent.user_id != runtime.session.user_id:
            raise ValueError("updated intent user mismatch")
        runtime.intent = intent
        self._increment_intent_version(runtime)
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.SEARCH_INTENT_UPDATED,
            entity_refs={"search_intent_id": intent.search_intent_id},
            output_data=intent,
            payload={"intent_version": runtime.session.intent_version},
        )
        self._run_pipeline(runtime)
        return runtime.session

    def handle_user_message(
        self,
        search_session_id: str,
        *,
        message: str,
    ) -> ConversationTurnResult:
        """Interpret a natural-language follow-up and continue the search flow.

        The LLM only selects narrow domain operations.  Each operation is then
        validated and executed by deterministic ApplicationService code.  A
        multi-action conversational turn is treated transactionally at the
        business-state level: if any selected revision fails validation, all
        runtime state mutations from that turn are rolled back while audit events
        may still record the rejected attempt.
        """

        runtime = self._runtime(search_session_id)
        if self.conversation_orchestrator is None:
            raise ValueError("Conversational orchestration LLM is not configured")
        message = message.strip()
        if not message:
            raise ValueError("message must not be empty")

        runtime.working_note_turn_sequence += 1
        self._safe_record_working_note_turn(
            runtime,
            sequence=runtime.working_note_turn_sequence,
            utterance=message,
        )
        # Rebuild the summary from structured backend state before the LLM sees
        # the note. A stale note can never override current state.
        self._safe_update_working_note(runtime)
        runtime.audit.emit(
            "CONVERSATION_ORCHESTRATOR",
            AuditEventType.CONVERSATION_TURN_RECEIVED,
            entity_refs={"search_session_id": search_session_id},
            input_data={"message_hash": canonical_hash(message)},
            payload={"message_hash": canonical_hash(message)},
        )
        runtime.audit.emit(
            "CONVERSATION_ORCHESTRATOR",
            AuditEventType.USER_STATE_REVISION_REQUESTED,
            entity_refs={"search_session_id": search_session_id},
            input_data={"message_hash": canonical_hash(message)},
            payload={"message_hash": canonical_hash(message)},
        )
        plan = self.conversation_orchestrator.interpret(
            message,
            context=self._conversation_context(runtime),
        )
        snapshot = self._snapshot_runtime(runtime)
        executed: list[ConversationOperation] = []
        current_results_requested = any(
            action.operation == ConversationOperation.SHOW_CURRENT_RESULTS
            for action in plan.actions
        )
        priority = {
            ConversationOperation.UPDATE_SEARCH_INTENT: 10,
            ConversationOperation.SET_PRODUCT_EXCLUSION: 20,
            ConversationOperation.CLEAR_USER_DECLARED_FACT: 25,
            ConversationOperation.CLEAR_PRODUCT_CONTRIBUTION_CHOICE: 25,
            ConversationOperation.REVISE_USER_ANSWER: 30,
            ConversationOperation.REVISE_USER_DECLARED_FACT: 30,
            ConversationOperation.SET_PRODUCT_CONTRIBUTION_CHOICE: 40,
            ConversationOperation.REVISE_PRODUCT_CONTRIBUTION_CHOICE: 40,
            ConversationOperation.EXCLUDE_PRODUCT: 45,
            ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER: 50,
            ConversationOperation.SHOW_CURRENT_RESULTS: 90,
            ConversationOperation.NO_OP: 99,
        }
        try:
            ordered_actions = sorted(
                enumerate(plan.actions),
                key=lambda pair: (priority[pair[1].operation], pair[0]),
            )
            for _index, action in ordered_actions:
                self._execute_conversation_action(runtime, action)
                executed.append(action.operation)

            # Only persistent mutable-state changes trigger a full deterministic
            # re-search. SHOW_CURRENT_RESULTS and NO_OP are turn-local response
            # intents and must not mutate ranking/session state just by being asked.
            mutating_operations = {
                ConversationOperation.UPDATE_SEARCH_INTENT,
                ConversationOperation.SET_PRODUCT_EXCLUSION,
                ConversationOperation.CLEAR_USER_DECLARED_FACT,
                ConversationOperation.CLEAR_PRODUCT_CONTRIBUTION_CHOICE,
                ConversationOperation.REVISE_USER_ANSWER,
                ConversationOperation.REVISE_USER_DECLARED_FACT,
                ConversationOperation.SET_PRODUCT_CONTRIBUTION_CHOICE,
                ConversationOperation.REVISE_PRODUCT_CONTRIBUTION_CHOICE,
                ConversationOperation.EXCLUDE_PRODUCT,
                ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER,
            }
            state_changed = any(item in mutating_operations for item in executed)
            if state_changed:
                # Existing domain methods may already have refreshed a subset;
                # one final full pass favors correctness over premature dependency
                # optimization at MVP scale.
                self._run_pipeline(runtime)
                runtime.audit.emit(
                    "APPLICATION_SERVICE",
                    AuditEventType.MUTABLE_SEARCH_STATE_UPDATED,
                    entity_refs={"search_session_id": search_session_id},
                    output_data=self._mutable_state_summary(runtime),
                    payload={
                        "operations": [item.value for item in executed],
                        "full_research": True,
                    },
                )
                runtime.audit.emit(
                    "APPLICATION_SERVICE",
                    AuditEventType.SEARCH_RECALCULATED,
                    entity_refs={"search_session_id": search_session_id},
                    output_data={
                        "candidate_product_ids": runtime.session.candidate_product_ids,
                        "ranking_run_id": runtime.session.ranking_run_id,
                    },
                    payload={"trigger": "CONVERSATIONAL_MUTABLE_STATE"},
                )
            self._safe_update_working_note(runtime)
        except Exception as exc:
            self._restore_runtime(runtime, snapshot)
            runtime.audit.emit(
                "CONVERSATION_ORCHESTRATOR",
                AuditEventType.CONVERSATION_TURN_ROLLED_BACK,
                entity_refs={"search_session_id": search_session_id},
                input_data={"message_hash": canonical_hash(message)},
                output_data={"business_state_committed": False},
                payload={
                    "reason": str(exc),
                    "executed_before_failure": [item.value for item in executed],
                },
            )
            raise

        next_question = self.get_next_question(search_session_id)
        recommendations = None
        unresolved_warning = None
        if current_results_requested:
            runtime.audit.emit(
                "CONVERSATION_ORCHESTRATOR",
                AuditEventType.CURRENT_RESULTS_REQUESTED,
                entity_refs={"search_session_id": search_session_id},
                output_data={"active_question_retained": runtime.active_question is not None},
                payload={"persistent_state_changed": False},
            )
            if runtime.ranking is not None and not runtime.conflicts:
                recommendations = self.get_top_recommendations(search_session_id)
                if runtime.active_question is not None or not runtime.ranking.stability.stable:
                    unresolved_warning = (
                        "일부 미확인 조건에 따라 현재 Top 5 구성 또는 순위가 달라질 수 있습니다."
                    )
            # The question remains active in SearchSession but is not the response
            # priority for this turn. A later turn can resume it normally.
            next_question = None
        elif next_question is None and not runtime.conflicts:
            recommendations = self.get_top_recommendations(search_session_id)
        self._safe_update_working_note(runtime)
        return ConversationTurnResult(
            search_session_id=search_session_id,
            message=message,
            operations_executed=executed,
            session=runtime.session,
            next_question=next_question,
            recommendations=recommendations,
            current_results_requested=current_results_requested,
            unresolved_warning=unresolved_warning,
        )

    # ------------------------------------------------------------------
    # Candidate/evaluation/ranking operations
    # ------------------------------------------------------------------
    def retrieve_candidates(self, search_session_id: str) -> list[CandidateFilterDecision]:
        runtime = self._runtime(search_session_id)
        self._retrieve(runtime)
        return runtime.filter_decisions

    def evaluate_candidates(self, search_session_id: str) -> dict[str, CandidateEvaluation]:
        runtime = self._runtime(search_session_id)
        if not runtime.candidate_products:
            self._retrieve(runtime)
        self._evaluate(runtime, runtime.candidate_products.values())
        self._rank(runtime)
        return dict(runtime.evaluations)

    def get_next_question(self, search_session_id: str) -> PlannedQuestion | None:
        runtime = self._runtime(search_session_id)
        if runtime.conflicts:
            return None
        self._select_question(runtime)
        return runtime.active_question

    def submit_user_answer(
        self,
        search_session_id: str,
        *,
        answer: Any,
        answered_at: datetime | None = None,
        question_id: str | None = None,
    ) -> SearchSession:
        runtime = self._runtime(search_session_id)
        question = runtime.active_question
        if question is None:
            raise ValueError("There is no active question")
        if question_id is not None and question.question_id != question_id:
            raise ValueError("question_id does not match active question")
        if question.question_kind == "CONTRIBUTION_FEASIBILITY":
            return self._submit_contribution_feasibility_answer(
                runtime,
                question,
                answer=answer,
                answered_at=self._effective_answered_at(runtime, answered_at),
            )
        if question.question_kind == "RANKING_INPUT":
            return self._submit_ranking_input_answer(
                runtime,
                question,
                answer=answer,
                answered_at=self._effective_answered_at(runtime, answered_at),
            )
        assert question.request is not None
        request_ref = self._request_reference(question.request)
        submission = UserAnswerSubmission(
            request_reference=request_ref,
            answer=answer,
            answered_at=self._effective_answered_at(runtime, answered_at),
        )
        runtime.fact_store, active = submit_answer_to_store(
            runtime.fact_store,
            question.request,
            submission,
            audit=runtime.audit,
        )
        runtime.request_history[request_ref] = question.request
        runtime.answer_records = self._answer_records(runtime.fact_store)
        answered_ids = [*runtime.session.answered_question_ids, question.question_id]
        runtime.session = runtime.session.model_copy(
            update={
                "answered_question_ids": list(dict.fromkeys(answered_ids)),
                "active_question_id": None,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        runtime.active_question = None
        self._evaluate(
            runtime,
            [runtime.candidate_products[item] for item in question.affected_product_ids],
            merge=True,
        )
        self._rank(runtime)
        self._select_question(runtime)
        return runtime.session

    def revise_user_answer(
        self,
        search_session_id: str,
        *,
        request_reference: str,
        new_value: Any,
        answered_at: datetime | None = None,
    ) -> SearchSession:
        runtime = self._runtime(search_session_id)
        request = runtime.request_history.get(request_reference)
        if request is None:
            # Reconstruct from a prior question when possible.
            for question in runtime.question_history.values():
                if question.request is None:
                    continue
                if self._request_reference(question.request) == request_reference:
                    request = question.request
                    break
        if request is None:
            raise KeyError(f"Unknown request_reference: {request_reference}")
        runtime.fact_store, _, _ = revise_answer_in_store(
            runtime.fact_store,
            request,
            UserAnswerSubmission(
                request_reference=request_reference,
                answer=new_value,
                answered_at=self._effective_answered_at(runtime, answered_at),
            ),
            audit=runtime.audit,
        )
        runtime.answer_records = self._answer_records(runtime.fact_store)
        affected = [
            product
            for product in runtime.candidate_products.values()
            if self._product_uses_fact(product, request.fact_type)
        ]
        self._evaluate(runtime, affected, merge=True)
        self._rank(runtime)
        self._select_question(runtime)
        return runtime.session

    def get_top_recommendations(
        self,
        search_session_id: str,
        *,
        complete: bool = False,
    ) -> ProductRecommendationResult:
        runtime = self._runtime(search_session_id)
        if runtime.ranking is None:
            self._run_pipeline(runtime)
        assert runtime.ranking is not None
        runtime.recommendation = self.recommendation_service.build_result(
            search_session_id,
            runtime.ranking,
            audit=runtime.audit,
        )
        status = (
            SearchSessionStatus.COMPLETED
            if complete
            else (
                SearchSessionStatus.QUESTIONING
                if runtime.active_question is not None
                else SearchSessionStatus.RANKING_READY
            )
        )
        runtime.session = runtime.session.model_copy(
            update={
                "recommendation_id": runtime.recommendation.recommendation_id,
                "status": status,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        return runtime.recommendation

    def get_product_recommendation_detail(
        self,
        search_session_id: str,
        product_id: str,
        *,
        include_explanation: bool = True,
    ) -> ProductRecommendationDetail:
        runtime = self._runtime(search_session_id)
        recommendation = runtime.recommendation or self.get_top_recommendations(
            search_session_id
        )
        rank_by_id = {item.product_id: item.rank for item in recommendation.top_products}
        if product_id not in rank_by_id:
            raise KeyError("product_id is not in the current Top recommendations")
        return self.recommendation_service.build_detail(
            search_session_id=search_session_id,
            recommendation_id=recommendation.recommendation_id,
            product=runtime.candidate_products[product_id],
            candidate=runtime.evaluations[product_id],
            rank=rank_by_id[product_id],
            intent=runtime.intent,
            ranking=runtime.ranking,
            audit=runtime.audit,
            include_explanation=include_explanation,
        )

    def get_evaluation_trace(self, search_session_id: str) -> tuple[AuditEvent, ...]:
        runtime = self._runtime(search_session_id)
        return self.audit_sink.read(trace_id=runtime.audit.trace_id)

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------
    def _run_pipeline(self, runtime: _SearchRuntime) -> None:
        runtime.conflicts = self.validate_search_intent(runtime.intent)
        if runtime.conflicts:
            runtime.clarification = self.conflict_clarifier.generate(runtime.conflicts)
            runtime.session = runtime.session.model_copy(
                update={
                    "status": SearchSessionStatus.CLARIFICATION_REQUIRED,
                    "active_question_id": None,
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
            for conflict in runtime.conflicts:
                runtime.audit.emit(
                    "INTENT_CONFLICT_VALIDATOR",
                    AuditEventType.INTENT_CONFLICT_DETECTED,
                    entity_refs={"conflict_id": conflict.conflict_id, "field": conflict.field},
                    output_data=conflict,
                    payload={"conflict_type": conflict.conflict_type.value},
                )
            runtime.audit.emit(
                "INTENT_CONFLICT_VALIDATOR",
                AuditEventType.CLARIFICATION_REQUESTED,
                output_data=runtime.clarification,
                payload={"conflict_count": len(runtime.conflicts)},
            )
            return

        runtime.clarification = None
        runtime.fact_store = self._materialize_capabilities(runtime)
        self._retrieve(runtime)
        self._evaluate(runtime, runtime.candidate_products.values())
        # Declining a ranking-only input is current mutable search state.  A
        # full re-search rebuilds CandidateEvaluation objects, so restore the
        # DECLINED status before QuestionPlanner derives current information
        # needs.  A later explicit product choice clears this state.
        self._mark_declined_ranking_inputs(runtime)
        self._rank(runtime)
        self._select_question(runtime)

    def _retrieve(self, runtime: _SearchRuntime) -> None:
        retained, decisions = self.candidate_retriever.retrieve(
            self.products.values(),
            runtime.intent,
            as_of=runtime.as_of,
        )
        excluded = set(runtime.session.excluded_product_ids)
        retained = [item for item in retained if item.product_id not in excluded]
        decision_by_id = {item.product_id: item for item in decisions}
        for product_id in sorted(excluded):
            if product_id not in self.products:
                continue
            decision_by_id[product_id] = CandidateFilterDecision(
                product_id=product_id,
                retained=False,
                reason_code="USER_EXCLUDED_PRODUCT",
                evidence={"source": "SEARCH_SESSION"},
            )
        decisions = list(decision_by_id.values())
        runtime.candidate_products = {item.product_id: item for item in retained}
        runtime.filter_decisions = decisions
        runtime.session = runtime.session.model_copy(
            update={
                "candidate_product_ids": list(runtime.candidate_products),
                "status": SearchSessionStatus.CANDIDATES_RETRIEVED,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        for decision in decisions:
            runtime.audit.emit(
                "CANDIDATE_RETRIEVER",
                (
                    AuditEventType.CANDIDATE_RETAINED
                    if decision.retained
                    else AuditEventType.CANDIDATE_FILTERED
                ),
                entity_refs={"product_id": decision.product_id},
                output_data=decision,
                payload={"reason_code": decision.reason_code},
            )

    def _evaluate(
        self,
        runtime: _SearchRuntime,
        products: Iterable[ProductDefinition],
        *,
        merge: bool = False,
    ) -> None:
        evaluated = self.evaluator.evaluate(
            products,
            runtime.fact_store,
            runtime.intent,
            as_of=runtime.as_of,
            subscription_date=runtime.subscription_date,
            audit=runtime.audit,
            product_contribution_choices=self._product_choice_map(runtime),
        )
        runtime.evaluations = (
            {**runtime.evaluations, **evaluated} if merge else evaluated
        )

    def _rank(self, runtime: _SearchRuntime) -> None:
        ranking, ranked = self.ranking_service.rank(
            runtime.evaluations,
            runtime.candidate_products,
            objective=runtime.intent.ranking_objective,
            top_k=runtime.intent.requested_top_k,
            audit=runtime.audit,
        )
        runtime.ranking = ranking
        runtime.evaluations = ranked
        runtime.recommendation = None
        runtime.session = runtime.session.model_copy(
            update={
                "ranking_run_id": ranking.ranking_run_id,
                "status": SearchSessionStatus.RANKING_READY,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )

    def _select_question(self, runtime: _SearchRuntime) -> None:
        question = self.question_planner.select_next(
            runtime.evaluations,
            runtime.intent,
            answered_question_ids=set(runtime.session.answered_question_ids),
            audit=runtime.audit,
        )
        runtime.active_question = question
        self._refresh_ranking_stability(runtime, material_question=question is not None)
        if question is None:
            runtime.session = runtime.session.model_copy(
                update={
                    "active_question_id": None,
                    "status": SearchSessionStatus.RANKING_READY,
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
            return
        runtime.question_history[question.question_id] = question
        if (
            question.question_kind == "CONTRIBUTION_FEASIBILITY"
            and question.feasibility_clarification is not None
            and question.feasibility_clarification.current_choice_id is not None
            and question.question_id not in runtime.reopened_question_ids
        ):
            runtime.reopened_question_ids.add(question.question_id)
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.QUESTION_REOPENED,
                entity_refs={
                    "question_id": question.question_id,
                    "product_id": question.feasibility_clarification.product_id,
                    "choice_id": question.feasibility_clarification.current_choice_id,
                },
                output_data=question,
                payload={
                    "reason": "DEPENDENCY_CHANGED_AFTER_PRIOR_ANSWER",
                    "field": question.feasibility_clarification.field,
                },
            )
        if question.request is not None:
            ref = self._request_reference(question.request)
            runtime.request_history[ref] = question.request
        if question.question_kind == "RANKING_INPUT" and question.ranking_input is not None:
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.PRODUCT_CONTRIBUTION_INPUT_REQUESTED,
                entity_refs={
                    "question_id": question.question_id,
                    "product_id": question.ranking_input.product_id,
                    "ranking_input_id": question.ranking_input.input_id,
                },
                output_data=question.ranking_input,
                payload={
                    "required_field": question.ranking_input.required_field,
                    "affected_metric": question.ranking_input.affected_metric,
                },
            )
        runtime.session = runtime.session.model_copy(
            update={
                "active_question_id": question.question_id,
                "status": SearchSessionStatus.QUESTIONING,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )

    def _submit_ranking_input_answer(
        self,
        runtime: _SearchRuntime,
        question: PlannedQuestion,
        *,
        answer: Any,
        answered_at: datetime,
    ) -> SearchSession:
        ranking_input = question.ranking_input
        if ranking_input is None:
            raise ValueError("Ranking-input question is missing ranking_input payload")

        # Decline is itself a valid committed answer.  Invalid financial values,
        # however, must pass all validation before *any* business state changes.
        if self._is_declined_ranking_answer(answer):
            self._commit_answered_question(runtime, question.question_id)
            runtime.declined_ranking_input_ids.add(ranking_input.input_id)
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.PRODUCT_CONTRIBUTION_INPUT_RECORDED,
                entity_refs={
                    "ranking_input_id": ranking_input.input_id,
                    "product_id": ranking_input.product_id,
                    "question_id": question.question_id,
                },
                output_data={"ranking_input_status": "DECLINED"},
                payload={
                    "operation": "RANKING_INPUT_DECLINED",
                    "required_field": ranking_input.required_field,
                },
            )
            self._mark_declined_ranking_inputs(runtime)
            self._rank(runtime)
            self._select_question(runtime)
            return runtime.session

        try:
            normalized, unit = self._normalize_ranking_input_answer(
                ranking_input.required_field,
                answer,
                allowed_options=ranking_input.allowed_options,
            )
            choice_payload = {
                "product_id": ranking_input.product_id,
                "field": ranking_input.required_field,
                "value": normalized,
                "question_id": question.question_id,
            }
            choice = ProductContributionChoice(
                choice_id=f"PRODUCT-CHOICE-{canonical_hash(choice_payload)[:16]}",
                product_id=ranking_input.product_id,
                field=ranking_input.required_field,
                value=normalized,
                unit=unit,
                source_question_id=question.question_id,
                answered_at=answered_at,
            )
            key = (choice.product_id, choice.field)
            if key in runtime.product_contribution_choices:
                raise ValueError(
                    "An active product-specific contribution choice already exists; use revise_product_contribution_choice"
                )
            product = runtime.candidate_products.get(ranking_input.product_id)
            if product is None:
                raise ValueError("Ranking-input product is no longer an active candidate")
            existing = [
                item
                for item in runtime.product_contribution_choices.values()
                if item.product_id == product.product_id
            ]
            # Revalidate against the *current* subscription date, product policy,
            # and global affordability.  No audit/business state is mutated here.
            self.evaluator.validate_product_contribution_choice(
                product,
                runtime.intent.contribution_plan,
                existing,
                choice,
                subscription_date=runtime.subscription_date,
            )
        except (TypeError, ValueError) as exc:
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.CONTRIBUTION_ANSWER_REJECTED,
                entity_refs={
                    "ranking_input_id": ranking_input.input_id,
                    "product_id": ranking_input.product_id,
                    "question_id": question.question_id,
                },
                input_data={"answer": answer},
                output_data={"accepted": False},
                payload={
                    "required_field": ranking_input.required_field,
                    "reason": str(exc),
                    "business_state_committed": False,
                },
            )
            raise ValueError(str(exc)) from exc

        # Commit starts only after every validation above has passed.
        runtime.product_contribution_choices[(choice.product_id, choice.field)] = choice
        runtime.product_contribution_choice_history.append(choice)
        runtime.declined_ranking_input_ids.discard(ranking_input.input_id)
        self._sync_product_choices_to_session(runtime)
        self._commit_answered_question(runtime, question.question_id)
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.PRODUCT_CONTRIBUTION_INPUT_RECORDED,
            entity_refs={
                "ranking_input_id": ranking_input.input_id,
                "product_id": ranking_input.product_id,
                "question_id": question.question_id,
                "choice_id": choice.choice_id,
            },
            output_data=choice,
            payload={
                "operation": "PRODUCT_SCOPED_RANKING_INPUT_RESOLVED",
                "required_field": ranking_input.required_field,
                "global_contribution_plan_mutated": False,
                "validation_before_commit": True,
            },
        )
        product = runtime.candidate_products.get(ranking_input.product_id)
        if product is not None:
            self._evaluate(runtime, [product], merge=True)
        self._mark_declined_ranking_inputs(runtime)
        self._rank(runtime)
        self._select_question(runtime)
        return runtime.session

    def _submit_contribution_feasibility_answer(
        self,
        runtime: _SearchRuntime,
        question: PlannedQuestion,
        *,
        answer: Any,
        answered_at: datetime,
    ) -> SearchSession:
        clarification = question.feasibility_clarification
        if clarification is None:
            raise ValueError("Feasibility question is missing clarification payload")

        # A stale committed product choice may expose feasible replacement values
        # directly on the clarification.  Accept a raw option value as a concise
        # structured/Web answer, but validate it through the same atomic revision
        # path before the clarification is marked answered.
        known_resolution = None
        if isinstance(answer, str):
            known_resolution = answer.strip().upper()
        elif isinstance(answer, dict):
            known_resolution = str(answer.get("resolution", "")).strip().upper()
        if (
            clarification.feasible_options
            and clarification.field is not None
            and known_resolution not in clarification.allowed_resolutions
            and not isinstance(answer, dict)
        ):
            old_choice, new_choice = self._prepare_product_choice_revision(
                runtime,
                product_id=clarification.product_id,
                field=clarification.field,
                new_value=answer,
                answered_at=answered_at,
                allowed_options=clarification.feasible_options,
                source_question_id=question.question_id,
            )
            self._commit_answered_question(runtime, question.question_id)
            self._commit_product_choice_revision(runtime, old_choice, new_choice)
            return runtime.session

        if isinstance(answer, str):
            resolution = answer.strip().upper()
            payload: dict[str, Any] = {"resolution": resolution}
        elif isinstance(answer, dict):
            payload = dict(answer)
            resolution = str(payload.get("resolution", "")).strip().upper()
        else:
            raise ValueError("Feasibility answer must be a resolution string or object")
        if resolution not in clarification.allowed_resolutions:
            raise ValueError(
                f"resolution must be one of {list(clarification.allowed_resolutions)}"
            )

        if resolution == "CHANGE_PRODUCT_CONTRIBUTION_CHOICE":
            if clarification.field is None or not clarification.feasible_options:
                raise ValueError("No feasible product contribution replacement is available")
            if "new_value" not in payload:
                raise ValueError(
                    "CHANGE_PRODUCT_CONTRIBUTION_CHOICE requires new_value from feasible_options"
                )
            old_choice, new_choice = self._prepare_product_choice_revision(
                runtime,
                product_id=clarification.product_id,
                field=clarification.field,
                new_value=payload["new_value"],
                answered_at=answered_at,
                allowed_options=clarification.feasible_options,
                source_question_id=question.question_id,
            )
            self._commit_answered_question(runtime, question.question_id)
            self._commit_product_choice_revision(runtime, old_choice, new_choice)
            return runtime.session

        if resolution == "ADJUST_GLOBAL_AFFORDABILITY":
            if "maximum_affordable_periodic_amount" not in payload:
                raise ValueError(
                    "ADJUST_GLOBAL_AFFORDABILITY requires maximum_affordable_periodic_amount"
                )
            try:
                new_amount = Decimal(str(payload["maximum_affordable_periodic_amount"]))
            except Exception as exc:
                raise ValueError("maximum_affordable_periodic_amount must be numeric") from exc
            if new_amount <= 0:
                raise ValueError("maximum_affordable_periodic_amount must be positive")
            current_plan = runtime.intent.contribution_plan
            if current_plan is None:
                raise ValueError("Global ContributionPlan is required for affordability adjustment")
            frequency = payload.get("frequency", current_plan.frequency)
            patch = IntentPatch(
                contribution_plan_patch=ContributionPlanPatch(
                    maximum_affordable_periodic_amount=new_amount,
                    frequency=frequency,
                )
            )
            # Build/validate the tentative intent before touching runtime state.
            tentative_intent = IntentParser.apply_patch(runtime.intent, patch)
            if tentative_intent.contribution_plan is None:
                raise ValueError("Global ContributionPlan update failed")

            self._commit_answered_question(runtime, question.question_id)
            runtime.intent = tentative_intent
            self._increment_intent_version(runtime)
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.GLOBAL_AFFORDABILITY_UPDATED,
                entity_refs={
                    "product_id": clarification.product_id,
                    "question_id": question.question_id,
                },
                output_data=tentative_intent.contribution_plan,
                payload={
                    "old_amount": str(clarification.current_affordability_amount),
                    "new_amount": str(new_amount),
                    "frequency": (
                        tentative_intent.contribution_plan.frequency.value
                        if tentative_intent.contribution_plan.frequency is not None
                        else None
                    ),
                },
            )
            self._run_pipeline(runtime)
            return runtime.session

        # EXCLUDE_PRODUCT
        excluded = list(dict.fromkeys([
            *runtime.session.excluded_product_ids,
            clarification.product_id,
        ]))
        self._commit_answered_question(runtime, question.question_id)
        runtime.session = runtime.session.model_copy(
            update={
                "excluded_product_ids": excluded,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.PRODUCT_EXCLUDED_BY_USER,
            entity_refs={
                "product_id": clarification.product_id,
                "question_id": question.question_id,
            },
            output_data={"excluded_product_ids": excluded},
            payload={"reason": "CONTRIBUTION_AFFORDABILITY"},
        )
        self._run_pipeline(runtime)
        return runtime.session

    @staticmethod
    def _commit_answered_question(runtime: _SearchRuntime, question_id: str) -> None:
        answered_ids = [*runtime.session.answered_question_ids, question_id]
        runtime.session = runtime.session.model_copy(
            update={
                "answered_question_ids": list(dict.fromkeys(answered_ids)),
                "active_question_id": None,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        runtime.active_question = None

    @staticmethod
    def _is_declined_ranking_answer(answer: Any) -> bool:
        if answer is None:
            return True
        if isinstance(answer, str):
            return answer.strip().casefold() in {
                "decline",
                "skip",
                "refuse",
                "거절",
                "답변안함",
                "답변 안함",
            }
        if isinstance(answer, dict):
            return bool(answer.get("declined"))
        return False

    @staticmethod
    def _normalize_ranking_input_answer(
        required_field: str,
        answer: Any,
        *,
        allowed_options: list[Any],
    ) -> tuple[Any, str | None]:
        if required_field in {
            "desired_periodic_amount",
            "desired_daily_amount",
            "desired_weekly_amount",
            "desired_monthly_amount",
            "preferred_start_amount",
            "incremental_amount",
        }:
            try:
                value = Decimal(str(answer))
            except Exception as exc:
                raise ValueError(f"{required_field} must be numeric") from exc
            if value <= 0:
                raise ValueError(f"{required_field} must be positive")
            if allowed_options:
                allowed = {Decimal(str(item)) for item in allowed_options}
                if value not in allowed:
                    raise ValueError(
                        f"{required_field} must be one of {sorted(str(item) for item in allowed)}"
                    )
            return value, "KRW"
        if required_field == "selected_term":
            if not isinstance(answer, dict) or "value" not in answer or "unit" not in answer:
                raise ValueError("selected_term answer must contain value and unit")
            normalized = {"value": int(answer["value"]), "unit": str(answer["unit"])}
            if allowed_options and normalized not in allowed_options:
                raise ValueError("selected_term is not one of the allowed product terms")
            return normalized, normalized["unit"]
        raise ValueError(f"Unsupported ranking input field: {required_field}")

    @staticmethod
    def _product_choice_map(
        runtime: _SearchRuntime,
    ) -> dict[str, list[ProductContributionChoice]]:
        by_product: dict[str, list[ProductContributionChoice]] = {}
        for choice in runtime.product_contribution_choices.values():
            by_product.setdefault(choice.product_id, []).append(choice)
        return by_product

    @staticmethod
    def _sync_product_choices_to_session(runtime: _SearchRuntime) -> None:
        choices = sorted(
            runtime.product_contribution_choices.values(),
            key=lambda item: (item.product_id, item.field, item.answered_at, item.choice_id),
        )
        runtime.session = runtime.session.model_copy(
            update={
                "product_contribution_choices": choices,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )

    def _mark_declined_ranking_inputs(self, runtime: _SearchRuntime) -> None:
        if not runtime.declined_ranking_input_ids:
            return
        from eligibility.schema.enums import RankingInputStatus

        updated: dict[str, CandidateEvaluation] = {}
        for product_id, candidate in runtime.evaluations.items():
            inputs = [
                item.model_copy(
                    update={"status": RankingInputStatus.DECLINED},
                    deep=True,
                )
                if item.input_id in runtime.declined_ranking_input_ids
                else item
                for item in candidate.missing_ranking_inputs
            ]
            updated[product_id] = candidate.model_copy(
                update={"missing_ranking_inputs": inputs},
                deep=True,
            )
        runtime.evaluations = updated

    def _prepare_product_choice_revision(
        self,
        runtime: _SearchRuntime,
        *,
        product_id: str,
        field: str,
        new_value: Any,
        answered_at: datetime,
        allowed_options: list[Any] | None = None,
        source_question_id: str | None = None,
    ) -> tuple[ProductContributionChoice, ProductContributionChoice]:
        key = (product_id, field)
        old_choice = runtime.product_contribution_choices.get(key)
        if old_choice is None:
            raise KeyError(
                f"No active product contribution choice for {product_id}:{field}"
            )
        product = runtime.candidate_products.get(product_id)
        if product is None:
            raise ValueError("Product contribution choice product is not an active candidate")

        options = (
            list(allowed_options)
            if allowed_options is not None
            else self._feasible_revision_options(runtime, product, field)
        )
        try:
            normalized, unit = self._normalize_ranking_input_answer(
                field,
                new_value,
                allowed_options=options,
            )
            revision_payload = {
                "product_id": product_id,
                "field": field,
                "value": normalized,
                "version": old_choice.version + 1,
                "at": answered_at.isoformat(),
            }
            new_choice = ProductContributionChoice(
                choice_id=f"PRODUCT-CHOICE-{canonical_hash(revision_payload)[:16]}",
                product_id=product_id,
                field=field,
                value=normalized,
                unit=unit,
                source_question_id=(
                    source_question_id
                    or f"REVISION-{canonical_hash({'old': old_choice.choice_id, 'at': answered_at.isoformat()})[:16]}"
                ),
                answered_at=answered_at,
                version=old_choice.version + 1,
                record_status="ACTIVE",
                supersedes_choice_id=old_choice.choice_id,
            )
            other_choices = [
                item
                for item in runtime.product_contribution_choices.values()
                if (item.product_id, item.field) != key
                and item.product_id == product_id
            ]
            self.evaluator.validate_product_contribution_choice(
                product,
                runtime.intent.contribution_plan,
                other_choices,
                new_choice,
                subscription_date=runtime.subscription_date,
            )
        except (TypeError, ValueError) as exc:
            runtime.audit.emit(
                "APPLICATION_SERVICE",
                AuditEventType.CONTRIBUTION_ANSWER_REJECTED,
                entity_refs={
                    "product_id": product_id,
                    "field": field,
                    "prior_choice_id": old_choice.choice_id,
                },
                input_data={"new_value": new_value},
                output_data={"accepted": False},
                payload={
                    "operation": "REVISE_PRODUCT_CONTRIBUTION_CHOICE",
                    "reason": str(exc),
                    "business_state_committed": False,
                },
            )
            raise ValueError(str(exc)) from exc
        return old_choice, new_choice

    def _commit_product_choice_revision(
        self,
        runtime: _SearchRuntime,
        old_choice: ProductContributionChoice,
        new_choice: ProductContributionChoice,
    ) -> None:
        key = (new_choice.product_id, new_choice.field)

        # If the user directly revises while a stale-choice clarification is
        # active, that clarification is resolved by the successful revision.
        active = runtime.active_question
        if (
            active is not None
            and active.question_kind == "CONTRIBUTION_FEASIBILITY"
            and active.feasibility_clarification is not None
            and active.feasibility_clarification.product_id == new_choice.product_id
            and active.feasibility_clarification.field == new_choice.field
        ):
            self._commit_answered_question(runtime, active.question_id)

        superseded = old_choice.model_copy(
            update={"record_status": "SUPERSEDED"},
            deep=True,
        )
        replaced = False
        rewritten_history: list[ProductContributionChoice] = []
        for item in runtime.product_contribution_choice_history:
            if item.choice_id == old_choice.choice_id:
                rewritten_history.append(superseded)
                replaced = True
            else:
                rewritten_history.append(item)
        if not replaced:
            rewritten_history.append(superseded)
        rewritten_history.append(new_choice)
        runtime.product_contribution_choice_history = rewritten_history
        runtime.product_contribution_choices[key] = new_choice
        self._sync_product_choices_to_session(runtime)

        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.PRODUCT_CONTRIBUTION_CHOICE_SUPERSEDED,
            entity_refs={
                "product_id": new_choice.product_id,
                "old_choice_id": old_choice.choice_id,
                "new_choice_id": new_choice.choice_id,
            },
            input_data=old_choice,
            output_data=new_choice,
            payload={
                "field": new_choice.field,
                "old_value": str(old_choice.value),
                "new_value": str(new_choice.value),
                "old_version": old_choice.version,
                "new_version": new_choice.version,
            },
        )
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.DEPENDENCY_INVALIDATED,
            entity_refs={
                "product_id": new_choice.product_id,
                "choice_id": new_choice.choice_id,
            },
            payload={
                "dependencies": ["CASHFLOW", "AFFORDABILITY", "INTEREST", "RANKING"],
                "scope": "PRODUCT",
            },
        )

        product = runtime.candidate_products.get(new_choice.product_id)
        if product is not None:
            self._evaluate(runtime, [product], merge=True)
        self._mark_declined_ranking_inputs(runtime)
        self._rank(runtime)
        self._select_question(runtime)

    def _feasible_revision_options(
        self,
        runtime: _SearchRuntime,
        product: ProductDefinition,
        field: str,
    ) -> list[Any]:
        other_choices = [
            item
            for item in runtime.product_contribution_choices.values()
            if item.product_id == product.product_id and item.field != field
        ]
        effective = apply_product_contribution_choices(
            product,
            runtime.intent.contribution_plan,
            other_choices,
        )
        planned = self.evaluator.contribution_planner.build(
            product,
            effective,
            subscription_date=runtime.subscription_date,
        )
        ranking_input = next(
            (item for item in planned.missing_ranking_inputs if item.required_field == field),
            None,
        )
        if ranking_input is None:
            return []
        filtered, _rows, _clarification = self.evaluator.contribution_feasibility.analyze_ranking_input(
            product,
            runtime.intent.contribution_plan,
            ranking_input,
            planner=self.evaluator.contribution_planner,
            subscription_date=runtime.subscription_date,
        )
        if filtered is not None:
            return list(filtered.allowed_options)
        return []

    def _matching_active_question_id(
        self,
        runtime: _SearchRuntime,
        *,
        product_id: str,
        field: str,
    ) -> str | None:
        question = runtime.active_question
        if question is None or product_id not in question.affected_product_ids:
            return None
        if (
            question.question_kind == "RANKING_INPUT"
            and question.ranking_input is not None
            and question.ranking_input.product_id == product_id
            and question.ranking_input.required_field == field
        ):
            return question.question_id
        if (
            question.question_kind == "CONTRIBUTION_FEASIBILITY"
            and question.feasibility_clarification is not None
            and question.feasibility_clarification.product_id == product_id
            and question.feasibility_clarification.field == field
        ):
            return question.question_id
        return None

    @staticmethod
    def _clear_declined_ranking_input_for_product_field(
        runtime: _SearchRuntime,
        *,
        product_id: str,
        field: str,
    ) -> None:
        stale_ids: set[str] = set()
        candidate = runtime.evaluations.get(product_id)
        if candidate is not None:
            stale_ids.update(
                item.input_id
                for item in candidate.missing_ranking_inputs
                if item.product_id == product_id and item.required_field == field
            )
        for question in runtime.question_history.values():
            item = question.ranking_input
            if (
                item is not None
                and item.product_id == product_id
                and item.required_field == field
            ):
                stale_ids.add(item.input_id)
        runtime.declined_ranking_input_ids.difference_update(stale_ids)

    @staticmethod
    def _mutable_state_summary(runtime: _SearchRuntime) -> dict[str, Any]:
        user_declarations = [
            {
                "fact_type": fact.fact_type,
                "value": fact.value,
                "semantic_type": fact.semantic_type.value,
                "request_reference": fact.request_reference,
            }
            for fact in runtime.fact_store.active_facts
            if fact.source_type == FactSourceType.USER_DECLARED
        ]
        return {
            "intent": runtime.intent.model_dump(mode="json"),
            "user_declarations": user_declarations,
            "product_contribution_choices": [
                item.model_dump(mode="json")
                for item in sorted(
                    runtime.product_contribution_choices.values(),
                    key=lambda item: (item.product_id, item.field),
                )
            ],
            "excluded_product_ids": list(runtime.session.excluded_product_ids),
        }

    def _conversation_context(self, runtime: _SearchRuntime) -> dict[str, Any]:
        mutable_facts = []
        authoritative_facts = []
        for fact in runtime.fact_store.active_facts:
            row = {
                "fact_type": fact.fact_type,
                "value": fact.value,
                "source_type": fact.source_type.value,
                "semantic_type": fact.semantic_type.value,
                "request_reference": fact.request_reference,
            }
            if fact.source_type == FactSourceType.USER_DECLARED:
                mutable_facts.append(row)
            else:
                authoritative_facts.append(row)

        active_question = (
            runtime.active_question.model_dump(mode="json")
            if runtime.active_question is not None
            else None
        )
        recent_product_ids: list[str] = []
        if runtime.active_question is not None:
            recent_product_ids.extend(runtime.active_question.affected_product_ids)
        if runtime.product_contribution_choice_history:
            latest = max(
                runtime.product_contribution_choice_history,
                key=lambda item: (item.answered_at, item.version, item.choice_id),
            )
            recent_product_ids.append(latest.product_id)
        recent_product_ids = list(dict.fromkeys(recent_product_ids))

        top_k_summary: list[dict[str, Any]] = []
        if runtime.ranking is not None:
            for rank, product_id in enumerate(
                runtime.ranking.ordered_product_ids[: runtime.intent.requested_top_k],
                start=1,
            ):
                candidate = runtime.evaluations.get(product_id)
                product = runtime.candidate_products.get(product_id)
                if candidate is None or product is None:
                    continue
                top_k_summary.append(
                    {
                        "rank": rank,
                        "product_id": product_id,
                        "product_name": product.name,
                        "realizable_rate": str(candidate.realizable_rate),
                        "realizable_after_tax_interest": (
                            str(candidate.realizable_after_tax_interest)
                            if candidate.realizable_after_tax_interest is not None
                            else None
                        ),
                    }
                )
        return {
            "SEARCH_SESSION_SUMMARY": {
                "search_session_id": runtime.session.search_session_id,
                "intent_version": runtime.session.intent_version,
                "status": runtime.session.status.value,
            },
            "SEARCH_WORKING_NOTE": self.working_note_store.load(
                runtime.session.search_session_id
            ),
            "MUTABLE_SEARCH_STATE": self._mutable_state_summary(runtime),
            "AUTHORITATIVE_FACT_SUMMARY": authoritative_facts,
            "ACTIVE_QUESTION": active_question,
            "RECENT_PRODUCT_FOCUS": recent_product_ids,
            "TOP_K_SUMMARY": top_k_summary,
            "PRODUCT_CATALOG_SUMMARY": [
                {
                    "product_id": product.product_id,
                    "product_name": product.name,
                    "institution_id": product.institution_id,
                    "currently_candidate": product.product_id in runtime.candidate_products,
                    "currently_excluded": product.product_id in runtime.session.excluded_product_ids,
                }
                for product in self.products.values()
            ],
            "ALLOWED_APPLICATION_OPERATIONS": [item.value for item in ConversationOperation],
        }

    @staticmethod
    def _snapshot_runtime(runtime: _SearchRuntime) -> dict[str, Any]:
        fields = (
            "session",
            "intent",
            "base_fact_store",
            "fact_store",
            "conflicts",
            "clarification",
            "candidate_products",
            "filter_decisions",
            "evaluations",
            "ranking",
            "recommendation",
            "active_question",
            "question_history",
            "request_history",
            "answer_records",
            "declined_ranking_input_ids",
            "product_contribution_choices",
            "product_contribution_choice_history",
            "reopened_question_ids",
        )
        return {name: deepcopy(getattr(runtime, name)) for name in fields}

    @staticmethod
    def _restore_runtime(runtime: _SearchRuntime, snapshot: dict[str, Any]) -> None:
        for name, value in snapshot.items():
            setattr(runtime, name, value)
        runtime.audit.intent_version = runtime.session.intent_version
        runtime.audit.ranking_run_id = runtime.session.ranking_run_id
        runtime.audit.question_id = runtime.session.active_question_id
        runtime.audit.recommendation_id = runtime.session.recommendation_id

    def _execute_conversation_action(
        self,
        runtime: _SearchRuntime,
        action: ConversationAction,
    ) -> None:
        session_id = runtime.session.search_session_id
        if action.operation == ConversationOperation.UPDATE_SEARCH_INTENT:
            assert action.intent_patch is not None
            self.update_search_intent(session_id, patch=action.intent_patch)
            return
        if action.operation == ConversationOperation.SET_PRODUCT_CONTRIBUTION_CHOICE:
            assert action.product_id is not None and action.field is not None
            self.set_product_contribution_choice(
                session_id,
                product_id=action.product_id,
                field=action.field,
                value=action.new_value,
            )
            return
        if action.operation == ConversationOperation.SET_PRODUCT_EXCLUSION:
            assert action.product_id is not None and action.excluded is not None
            self.set_product_exclusion(
                session_id,
                product_id=action.product_id,
                excluded=action.excluded,
                reason="CONVERSATIONAL_MUTABLE_STATE",
            )
            return
        if action.operation == ConversationOperation.CLEAR_USER_DECLARED_FACT:
            assert action.fact_type is not None
            self.clear_user_declared_fact(session_id, fact_type=action.fact_type)
            return
        if action.operation == ConversationOperation.CLEAR_PRODUCT_CONTRIBUTION_CHOICE:
            assert action.product_id is not None and action.field is not None
            self.clear_product_contribution_choice(
                session_id,
                product_id=action.product_id,
                field=action.field,
            )
            return
        if action.operation == ConversationOperation.SHOW_CURRENT_RESULTS:
            return
        if action.operation == ConversationOperation.REVISE_PRODUCT_CONTRIBUTION_CHOICE:
            assert action.product_id is not None and action.field is not None
            self.revise_product_contribution_choice(
                session_id,
                product_id=action.product_id,
                field=action.field,
                new_value=action.new_value,
            )
            return
        if action.operation == ConversationOperation.REVISE_USER_ANSWER:
            assert action.request_reference is not None
            self.revise_user_answer(
                session_id,
                request_reference=action.request_reference,
                new_value=action.new_value,
            )
            return
        if action.operation == ConversationOperation.REVISE_USER_DECLARED_FACT:
            assert action.fact_type is not None
            self.revise_user_declared_fact(
                session_id,
                fact_type=action.fact_type,
                new_value=action.new_value,
            )
            return
        if action.operation == ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER:
            answer = action.answer
            if hasattr(answer, "model_dump"):
                answer = answer.model_dump(mode="python", exclude_none=True)
            self.submit_user_answer(session_id, answer=answer)
            return
        if action.operation == ConversationOperation.EXCLUDE_PRODUCT:
            assert action.product_id is not None
            self.exclude_product(session_id, product_id=action.product_id)
            return
        if action.operation == ConversationOperation.NO_OP:
            return
        raise ValueError(f"Unsupported conversation operation: {action.operation}")

    def _refresh_ranking_stability(
        self,
        runtime: _SearchRuntime,
        *,
        material_question: bool,
    ) -> None:
        if runtime.ranking is None:
            return
        ordered = [
            runtime.evaluations[product_id]
            for product_id in runtime.ranking.ordered_product_ids
            if product_id in runtime.evaluations
        ]
        stability = self.ranking_service.check_stability(
            ordered,
            top_k=runtime.intent.requested_top_k,
            objective=runtime.intent.ranking_objective,
            material_internal_question_remaining=material_question,
        )
        runtime.ranking = runtime.ranking.model_copy(
            update={"stability": stability},
            deep=True,
        )

    def _materialize_capabilities(self, runtime: _SearchRuntime) -> UserFactStore:
        """Synchronize intent capabilities into effective USER_DECLARED facts.

        v0.4.2 treats an explicit capability update as a revision of the same
        semantic user declaration.  Prior USER_DECLARED SELF_REPORTED/FUTURE_INTENT
        facts of the mapped ``fact_type`` are superseded even when they originated
        from a different question reference.  Authoritative MyData/institution
        observations are never rewritten.
        """

        store = runtime.fact_store
        prefix = f"INTENT-CAPABILITY/{runtime.session.search_session_id}/"
        active_refs = {
            f"{prefix}{capability.capability_id}"
            for capability in runtime.intent.capabilities
            if capability.state != CapabilityState.UNKNOWN
        }

        # Explicit removal of an intent capability retires only the intent-created
        # declaration.  It does not erase independent authoritative evidence.
        rewritten: list[UserFact] = []
        for fact in store.facts:
            if (
                fact.record_status == FactRecordStatus.ACTIVE
                and fact.request_reference is not None
                and fact.request_reference.startswith(prefix)
                and fact.request_reference not in active_refs
            ):
                rewritten.append(
                    fact.model_copy(
                        update={"record_status": FactRecordStatus.SUPERSEDED},
                        deep=True,
                    )
                )
            else:
                rewritten.append(fact)
        store = store.model_copy(update={"facts": rewritten}, deep=True)

        user_semantics = {
            FactSemanticType.SELF_REPORTED_FACT,
            FactSemanticType.FUTURE_INTENT,
        }
        for capability in runtime.intent.capabilities:
            if capability.state == CapabilityState.UNKNOWN:
                continue
            fact_type = self.capability_fact_map.get(
                capability.capability_id,
                capability.capability_id,
            )
            value = capability.state == CapabilityState.CAN
            request_reference = f"{prefix}{capability.capability_id}"

            # Retire active user declarations of the same semantic fact that came
            # from other question references.  The same intent reference is left
            # to with_answer_fact so versioning remains monotonic on true<->false.
            cross_reference_superseded: list[UserFact] = []
            semantic_rewrite: list[UserFact] = []
            for existing_fact in store.facts:
                if (
                    existing_fact.record_status == FactRecordStatus.ACTIVE
                    and existing_fact.source_type == FactSourceType.USER_DECLARED
                    and existing_fact.semantic_type in user_semantics
                    and existing_fact.fact_type == fact_type
                    and existing_fact.request_reference != request_reference
                ):
                    superseded = existing_fact.model_copy(
                        update={"record_status": FactRecordStatus.SUPERSEDED},
                        deep=True,
                    )
                    semantic_rewrite.append(superseded)
                    cross_reference_superseded.append(superseded)
                else:
                    semantic_rewrite.append(existing_fact)
            store = store.model_copy(update={"facts": semantic_rewrite}, deep=True)

            existing = [
                item
                for item in store.active_facts
                if item.request_reference == request_reference
            ]
            same_reference_superseded: list[UserFact] = []
            if existing and existing[-1].value == value:
                active_fact = existing[-1]
            else:
                payload = {
                    "request_reference": request_reference,
                    "value": value,
                    "intent_version": runtime.session.intent_version,
                }
                fact = UserFact(
                    fact_id=f"INTENT-{canonical_hash(payload)[:20]}",
                    user_id=store.user_id,
                    fact_type=fact_type,
                    value=value,
                    valid_from=runtime.as_of,
                    source_type=FactSourceType.USER_DECLARED,
                    semantic_type=FactSemanticType.FUTURE_INTENT,
                    provenance=[
                        FactProvenance(
                            reference=request_reference,
                            description="Capability materialized from active ProductSearchIntent",
                            attributes={
                                "capability_id": capability.capability_id,
                                "intent_version": runtime.session.intent_version,
                            },
                        )
                    ],
                    collected_at=datetime.now(timezone.utc),
                    request_reference=request_reference,
                    supersedes_fact_id=(
                        cross_reference_superseded[-1].fact_id
                        if cross_reference_superseded
                        else None
                    ),
                )
                store, same_reference_superseded = store.with_answer_fact(fact)
                active_fact = next(
                    item
                    for item in reversed(store.facts)
                    if item.request_reference == request_reference
                    and item.record_status == FactRecordStatus.ACTIVE
                )

            for prior in [*cross_reference_superseded, *same_reference_superseded]:
                runtime.audit.emit(
                    "APPLICATION_SERVICE",
                    AuditEventType.USER_DECLARED_FACT_SUPERSEDED_BY_INTENT_UPDATE,
                    entity_refs={
                        "prior_fact_id": prior.fact_id,
                        "new_fact_id": active_fact.fact_id,
                        "fact_type": fact_type,
                    },
                    input_data=prior,
                    output_data=active_fact,
                    payload={
                        "capability_id": capability.capability_id,
                        "prior_value_hash": canonical_hash(prior.value),
                        "new_value_hash": canonical_hash(active_fact.value),
                        "authoritative_fact_superseded": False,
                    },
                )

        runtime.answer_records = self._answer_records(store)
        return store

    def _increment_intent_version(self, runtime: _SearchRuntime) -> None:
        version = runtime.session.intent_version + 1
        runtime.session = runtime.session.model_copy(
            update={
                "intent_version": version,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        runtime.audit.bind_search_context(intent_version=version)

    @staticmethod
    def _effective_answered_at(
        runtime: _SearchRuntime,
        answered_at: datetime | None,
    ) -> datetime:
        """Make a web answer effective in the session's evaluation date.

        Production sessions normally evaluate ``date.today()``.  Historical or
        deterministic replay sessions can use an earlier ``as_of`` date; in that
        case a wall-clock timestamp from today would otherwise be future-dated
        and ignored by FactResolver.  Explicit timestamps are preserved.
        """

        if answered_at is not None:
            return answered_at
        now = datetime.now(timezone.utc)
        return datetime.combine(runtime.as_of, now.timetz())

    @staticmethod
    def _request_reference(request) -> str:
        from eligibility.application import UserAnswerMapper

        return UserAnswerMapper.request_reference(request)

    @staticmethod
    def _answer_records(store: UserFactStore) -> list[UserAnswerRecord]:
        records: list[UserAnswerRecord] = []
        for fact in store.facts:
            if fact.request_reference is None:
                continue
            records.append(
                UserAnswerRecord(
                    answer_record_id=f"ANS-{canonical_hash({'fact': fact.fact_id, 'version': fact.version})[:20]}",
                    request_reference=fact.request_reference,
                    fact_id=fact.fact_id,
                    version=fact.version,
                    status=fact.record_status,
                    supersedes_fact_id=fact.supersedes_fact_id,
                    answered_at=fact.collected_at,
                )
            )
        return sorted(records, key=lambda item: (item.request_reference, item.version))

    @staticmethod
    def _product_uses_fact(product: ProductDefinition, fact_type: str) -> bool:
        def walk(rule) -> bool:
            if getattr(rule, "fact_type", None) == fact_type:
                return True
            future = getattr(rule, "future_achievement", None)
            if future is not None:
                if future.intent_fact_type == fact_type:
                    return True
                if future.capability_rule is not None and walk(future.capability_rule):
                    return True
            for child in getattr(rule, "children", None) or []:
                if walk(child):
                    return True
            child = getattr(rule, "child", None)
            return child is not None and walk(child)

        return any(
            walk(rule)
            for rule in [
                product.eligibility_rule,
                *[item.rule for item in product.preferential_rules],
                *product.global_guards,
            ]
        )

    def _working_note_summary(self, runtime: _SearchRuntime) -> dict[str, Any]:
        top = []
        if runtime.ranking is not None:
            for rank, product_id in enumerate(
                runtime.ranking.ordered_product_ids[: runtime.intent.requested_top_k], start=1
            ):
                candidate = runtime.evaluations.get(product_id)
                product = runtime.candidate_products.get(product_id)
                if candidate is None or product is None:
                    continue
                top.append({
                    "rank": rank,
                    "product_id": product_id,
                    "product_name": product.name,
                    "realizable_rate": str(candidate.realizable_rate),
                    "estimated_after_tax_interest": (
                        str(candidate.realizable_after_tax_interest)
                        if candidate.realizable_after_tax_interest is not None else None
                    ),
                })
        unresolved = []
        if runtime.active_question is not None:
            unresolved.append({
                "question_id": runtime.active_question.question_id,
                "question_kind": runtime.active_question.question_kind,
                "question": runtime.active_question.question,
                "affected_product_ids": runtime.active_question.affected_product_ids,
            })
        return {
            "current_user_goal": {
                "product_types": runtime.intent.product_types,
                "ranking_objective": runtime.intent.ranking_objective.value,
                "requested_top_k": runtime.intent.requested_top_k,
            },
            "mutable_search_state": self._mutable_state_summary(runtime),
            "product_specific_choices": [
                item.model_dump(mode="json")
                for item in self.get_product_contribution_choices(runtime.session.search_session_id)
            ],
            "product_selection": {"excluded_product_ids": list(runtime.session.excluded_product_ids)},
            "unresolved_material_information": unresolved,
            "current_recommendation_summary": {
                "top_products": top,
                "stable": runtime.ranking.stability.stable if runtime.ranking is not None else None,
            },
            "active_question": (
                runtime.active_question.model_dump(mode="json")
                if runtime.active_question is not None else None
            ),
        }

    def _safe_initialize_working_note(self, runtime: _SearchRuntime) -> None:
        try:
            self.working_note_store.initialize(
                search_session_id=runtime.session.search_session_id,
                user_id=runtime.session.user_id,
            )
            runtime.audit.emit(
                "WORKING_NOTE_STORE",
                AuditEventType.WORKING_NOTE_CREATED,
                entity_refs={"search_session_id": runtime.session.search_session_id},
                payload={"source_of_truth": False},
            )
        except Exception as exc:  # continuity must not be a financial SPOF
            runtime.audit.emit(
                "WORKING_NOTE_STORE",
                AuditEventType.WORKING_NOTE_WRITE_FAILED,
                entity_refs={"search_session_id": runtime.session.search_session_id},
                payload={"operation": "initialize", "reason": str(exc)},
            )

    def _safe_record_working_note_turn(
        self,
        runtime: _SearchRuntime,
        *,
        sequence: int,
        utterance: str,
    ) -> None:
        try:
            self.working_note_store.record_user_turn(
                search_session_id=runtime.session.search_session_id,
                sequence=sequence,
                utterance=utterance,
            )
        except Exception as exc:
            runtime.audit.emit(
                "WORKING_NOTE_STORE",
                AuditEventType.WORKING_NOTE_WRITE_FAILED,
                entity_refs={"search_session_id": runtime.session.search_session_id},
                payload={"operation": "record_user_turn", "reason": str(exc)},
            )

    def _safe_update_working_note(self, runtime: _SearchRuntime) -> None:
        try:
            self.working_note_store.update_summary(
                search_session_id=runtime.session.search_session_id,
                summary=self._working_note_summary(runtime),
            )
            runtime.audit.emit(
                "WORKING_NOTE_STORE",
                AuditEventType.WORKING_NOTE_UPDATED,
                entity_refs={"search_session_id": runtime.session.search_session_id},
                payload={"source_of_truth": False},
            )
        except Exception as exc:
            runtime.audit.emit(
                "WORKING_NOTE_STORE",
                AuditEventType.WORKING_NOTE_WRITE_FAILED,
                entity_refs={"search_session_id": runtime.session.search_session_id},
                payload={"operation": "update_summary", "reason": str(exc)},
            )

    def _safe_delete_working_note(self, runtime: _SearchRuntime) -> None:
        try:
            self.working_note_store.delete(runtime.session.search_session_id)
            runtime.audit.emit(
                "WORKING_NOTE_STORE",
                AuditEventType.WORKING_NOTE_DELETED,
                entity_refs={"search_session_id": runtime.session.search_session_id},
                payload={"source_of_truth": False},
            )
        except Exception as exc:
            runtime.audit.emit(
                "WORKING_NOTE_STORE",
                AuditEventType.WORKING_NOTE_WRITE_FAILED,
                entity_refs={"search_session_id": runtime.session.search_session_id},
                payload={"operation": "delete", "reason": str(exc)},
            )

    def _runtime(self, search_session_id: str) -> _SearchRuntime:
        try:
            return self._sessions[search_session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown search_session_id: {search_session_id}") from exc
