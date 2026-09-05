from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from eligibility.application import (
    UserAnswerRecord,
    UserAnswerSubmission,
    revise_user_answer as revise_answer_in_store,
    submit_user_answer as submit_answer_to_store,
)
from eligibility.conversation import ConversationOrchestrator
from eligibility.conversation_ledger import (
    Changeset,
    Decision,
    DecisionLedger,
    LedgerError,
    RecordStatus,
    RevertDecisions,
)
from eligibility.audit import (
    AuditEvent,
    AuditEventType,
    AuditSession,
    InMemoryAuditSink,
    canonical_hash,
)
from eligibility.schema.application_input import (
    Capability,
    HardConstraint,
    NumericPreference,
    Preference,
    QuickInputProfile,
)
from eligibility.schema.conversation import (
    AnswerPlan,
    ConversationAction,
    ConversationOperation,
    ConversationPlan,
    ConversationTurnResult,
    FlexibleConversationTurnPlan,
    PreSearchAnswerPlan,
    PreSearchProfileUpdate,
    ProductFeaturePolicy,
)
from eligibility.schema.enums import (
    CapabilityState,
    FactRecordStatus,
    FactSemanticType,
    FactSourceType,
    HardConstraintValue,
    RankingComparability,
    RankingObjective,
    ResolutionStrategy,
    SearchSessionStatus,
    PreSearchAnswerStatus,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
    ContributionFrequency,
    TermUnit,
    EvaluationStatus,
    UserConditionStatus,
)
from eligibility.schema.eligibility_text_review import (
    ProductEligibilityTextReview,
)
from eligibility.schema.evaluation import MissingFactRequest
from eligibility.schema.condition_requirement import UserConditionState
from eligibility.schema.product import ProductDefinition
from eligibility.catalog.normalized_loader import load_product_aliases
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
    PreSearchProfileEntry,
)
from eligibility.schema.user_fact import FactProvenance, UserFact, UserFactStore
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.intent import (
    IntentConflictClarifier,
    IntentConflictValidator,
    IntentParser,
)
from eligibility.search.questions import RankingAwareQuestionPlanner
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
    DeterministicPreSearchQuestionPlanner,
    pre_search_answer_examples,
)
from eligibility.search.ranking import RankingService, _institution_name
from eligibility.search.operation_grounding import grounded_targets
from eligibility.search.recommendation import RecommendationService
from eligibility.search.retrieval import CandidateRetriever
from eligibility.search.feature_policy import feature_present, random_promotion_rule_ids
from eligibility.search.query_tools import (
    ProductQuerySpec,
    ReadOnlyProductQueryTools,
)
from eligibility.question_policy import is_first_transaction_history_fact
from eligibility.eligibility_text_review import (
    EligibilityTextReviewer,
    eligibility_text,
)
from eligibility.search.contribution import (
    apply_product_contribution_choices,
    resolve_term,
    term_summary,
)
from eligibility.catalog.requirement_compiler import RequirementCompiler
from eligibility.working_note import InMemorySearchWorkingNoteStore, SearchWorkingNoteStore
from eligibility.application_debug import ApplicationDebugMixin
from eligibility.eligibility_review import EligibilityReviewMixin
from eligibility.pre_search_handler import PreSearchHandlerMixin


_DEFAULT_CAPABILITY_FACT_MAP = {
    "CHANGE_SALARY_ACCOUNT": "SALARY_ACCOUNT_CHANGE_POSSIBLE",
    "CHANGE_CARD_SETTLEMENT_ACCOUNT": "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE",
    "NEW_CARD_ISSUANCE": "NEW_CARD_ISSUANCE_POSSIBLE",
    "CARD_USAGE": "CARD_USAGE_POSSIBLE",
    "BRANCH_VISIT": "BRANCH_VISIT_POSSIBLE",
    "JOIN_SUPERSOL": "SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING",
}

_DEBUG_HISTORY_PRODUCT_LIMIT = 100
_DEBUG_REQUEST_PRODUCT_LIMIT = 200
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Seoul")


def _business_today() -> date:
    """Return today's date in the product's business timezone."""

    return datetime.now(_BUSINESS_TIMEZONE).date()


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
    structured_evaluations: dict[str, CandidateEvaluation] = field(default_factory=dict)
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
    skipped_question_ids: set[str] = field(default_factory=set)
    pre_search_profile_answers: dict[str, str] = field(default_factory=dict)
    pre_search_typed_facts: dict[str, Any] = field(default_factory=dict)
    pre_search_profile: dict[str, PreSearchProfileEntry] = field(default_factory=dict)
    condition_states: dict[str, UserConditionState] = field(default_factory=dict)
    recent_user_messages: list[str] = field(default_factory=list)
    working_note_turn_sequence: int = 0
    pending_assumption_answer: bool | None = None
    pending_assumption_original_question: PlannedQuestion | None = None
    session_origin: dict[str, Any] = field(default_factory=dict)
    visible_dialogue: list[dict[str, Any]] = field(default_factory=list)
    feature_policies: dict[str, ProductFeaturePolicy] = field(default_factory=dict)
    decision_ledger: DecisionLedger = field(default_factory=DecisionLedger)
    decision_sequence: int = 0
    changeset_sequence: int = 0
    question_presentation: dict[str, dict[str, Any]] = field(default_factory=dict)
    defer_pipeline: bool = False
    pipeline_requested_while_deferred: bool = False
    eligibility_text_review_cache: dict[str, ProductEligibilityTextReview] = field(
        default_factory=dict
    )
    eligibility_text_review_active_keys: dict[str, str] = field(default_factory=dict)
    eligibility_text_review_state: str = "DISABLED"
    eligibility_text_review_rounds: int = 0
    eligibility_text_review_pending_count: int = 0
    eligibility_text_review_frontier_ids: list[str] = field(default_factory=list)
    eligibility_text_review_assistant_message: str | None = None


class ApplicationService(ApplicationDebugMixin, PreSearchHandlerMixin, EligibilityReviewMixin):
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
        pre_search_enabled: bool = False,
        audit_event_types: frozenset[AuditEventType] | None = None,
        eligibility_text_reviewer: EligibilityTextReviewer | None = None,
        eligibility_text_review_max_rounds: int = 10,
        product_aliases: dict[str, str] | None = None,
    ) -> None:
        self.products = {product.product_id: product for product in products}
        if product_aliases is None:
            try:
                product_aliases = load_product_aliases()
            except (FileNotFoundError, ValueError, OSError):
                product_aliases = {}
        self.product_aliases = {
            alias: canonical
            for alias, canonical in product_aliases.items()
            if alias not in self.products and canonical in self.products
        }
        # This is a read-only shadow index.  REVIEW_REQUIRED requirements are
        # observable but cannot affect eligibility, rate or ranking until a
        # reviewed family is explicitly activated in a later migration.
        self.requirement_compilation = RequirementCompiler().compile(
            self.products.values()
        )
        self.condition_requirements_by_product: dict[str, list[Any]] = {}
        for requirement in self.requirement_compilation.requirements:
            self.condition_requirements_by_product.setdefault(
                requirement.product_id,
                [],
            ).append(requirement)
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
        self.pre_search_enabled = pre_search_enabled
        self.audit_event_types = audit_event_types
        if eligibility_text_review_max_rounds < 1:
            raise ValueError("eligibility_text_review_max_rounds must be at least 1")
        self.eligibility_text_reviewer = eligibility_text_reviewer
        self.eligibility_text_review_max_rounds = eligibility_text_review_max_rounds
        self.pre_search_question_planner = DeterministicPreSearchQuestionPlanner()
        self._sessions: dict[str, _SearchRuntime] = {}

    def resolve_product_id(self, product_id: str) -> str:
        """Resolve a historical catalog ID to its active canonical ID."""

        return self.product_aliases.get(product_id, product_id)

    def get_catalog_product(self, product_id: str) -> ProductDefinition | None:
        """Look up an active product through canonical or historical ID."""

        return self.products.get(self.resolve_product_id(product_id))

    # ------------------------------------------------------------------
    # Intent/session operations
    # ------------------------------------------------------------------
    def parse_search_intent(self, utterance: str, *, user_id: str) -> ProductSearchIntent:
        return self.intent_parser.parse(utterance, user_id=user_id)

    def validate_search_intent(self, intent: ProductSearchIntent) -> list[IntentConflict]:
        return self.conflict_validator.validate(intent)

    def get_read_only_query_tools(
        self,
        search_session_id: str | None = None,
    ) -> ReadOnlyProductQueryTools:
        """Return the only catalog/session query surface intended for an LLM.

        The facade snapshots session state and exposes no mutation or raw SQL
        method.  A new snapshot must be requested after a committed turn.
        """

        condition_states: dict[str, UserConditionState] = {}
        if search_session_id is not None:
            condition_states = dict(self._runtime(search_session_id).condition_states)
        return ReadOnlyProductQueryTools(
            self.products.values(),
            requirements=self.requirement_compilation.requirements,
            condition_states=condition_states,
        )

    def query_products(
        self,
        query: ProductQuerySpec | dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute one validated, read-only product query."""

        spec = query if isinstance(query, ProductQuerySpec) else ProductQuerySpec.model_validate(query)
        return self.get_read_only_query_tools().search_products(spec)

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

        # The LLM-facing schema historically used preferred_start_amount for a
        # one-time amount. The cashflow engine uses desired_periodic_amount for
        # LUMP_SUM and ON_DEMAND policies, so normalize that semantic mismatch
        # at the application boundary without changing the published data.
        plan = intent.contribution_plan
        product_types = set(intent.product_types)
        if (
            plan is not None
            and product_types
            and product_types <= {"TIME_DEPOSIT", "PARKING_ACCOUNT", "CMA"}
            and plan.desired_periodic_amount is None
            and plan.preferred_start_amount is not None
        ):
            intent = intent.model_copy(
                update={
                    "contribution_plan": plan.model_copy(
                        update={
                            "desired_periodic_amount": plan.preferred_start_amount,
                            "preferred_start_amount": None,
                        },
                        deep=True,
                    )
                },
                deep=True,
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
            captured_event_types=self.audit_event_types,
        )
        audit.bind_search_context(
            search_session_id=search_session_id,
            intent_version=1,
        )
        initial_pre_search_profile = (
            self.pre_search_question_planner.initialize_profile(intent)
            if self.pre_search_enabled
            else {}
        )
        if initial_pre_search_profile:
            initially_answered = [
                f"PRESEARCH-{key}"
                for key, entry in initial_pre_search_profile.items()
                if entry.answer_status != PreSearchAnswerStatus.NOT_ASKED
            ]
            session = session.model_copy(
                update={"answered_question_ids": initially_answered},
                deep=True,
            )
        effective_as_of = as_of or _business_today()
        effective_subscription_date = subscription_date or effective_as_of
        runtime = _SearchRuntime(
            session=session,
            intent=intent,
            base_fact_store=base_store,
            fact_store=base_store,
            as_of=effective_as_of,
            subscription_date=effective_subscription_date,
            audit=audit,
            pre_search_profile=initial_pre_search_profile,
            session_origin={
                "original_utterance": (
                    utterance
                    or (intent.source_utterances[0] if intent.source_utterances else "")
                ),
                "initial_product_types": list(intent.product_types),
                "subscription_date": (
                    effective_subscription_date
                ).isoformat(),
                "initial_contribution_plan": (
                    intent.contribution_plan.model_dump(mode="json")
                    if intent.contribution_plan is not None
                    else None
                ),
                "authority": "USER_EXPLICIT" if utterance else "BACKEND_DERIVED",
            },
            visible_dialogue=(
                [{"role": "user", "content": utterance}]
                if utterance
                else []
            ),
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
        audit.emit(
            "REQUIREMENT_COMPILER",
            AuditEventType.DATA_COVERAGE_CHECKED,
            output_data=self._requirement_metrics_summary(),
            payload={
                "compiler_version": self.requirement_compilation.compiler_version,
                "activation_mode": "SHADOW_REVIEWED_ONLY",
                "verified_requirement_count": (
                    self.requirement_compilation.metrics.verified_count
                ),
            },
        )
        self._run_pipeline(runtime)
        if runtime.active_question is not None:
            runtime.visible_dialogue.append(
                {
                    "role": "assistant",
                    "content": runtime.active_question.question,
                    "question_id": runtime.active_question.question_id,
                }
            )
            runtime.question_presentation[runtime.active_question.question_id] = {
                "last_presented_turn": 0,
                "presented_count": 1,
                "deferred_count": 0,
            }
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
            self._clear_condition_state_for_fact_type(runtime, fact_type)
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
        self._clear_condition_state_for_fact_type(runtime, fact_type)
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
            if runtime.defer_pipeline:
                # An earlier action in the same staged changeset may have just
                # restored this product. The single final pipeline has not yet
                # rebuilt candidate_products, so validate against the catalog
                # object and current staged exclusion state.
                product = self.products[product_id]
            else:
                raise ValueError(
                    "Product contribution choice product is not an active candidate"
                )

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
                self._sync_condition_state_for_request(
                    runtime,
                    request,
                    value=new_value,
                )
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
        product_types_before_update = set(runtime.intent.product_types)
        active_question_before_update = runtime.active_question
        if patch is not None:
            patch = self._canonicalize_institution_constraint_patch(patch)
        ranking_only_patch = (
            patch is not None
            and patch.ranking_objective_patch is not None
            and patch.model_dump(exclude_none=True, exclude_defaults=True)
            == {"ranking_objective_patch": patch.ranking_objective_patch}
        )
        if intent is None:
            if patch is not None:
                intent = IntentParser.apply_patch(
                    runtime.intent,
                    patch,
                    source_utterance=utterance,
                )
            else:
                if utterance is None:
                    raise ValueError("utterance, intent, or patch is required")
                intent = self.intent_parser.update(runtime.intent, utterance)
        if intent.user_id != runtime.session.user_id:
            raise ValueError("updated intent user mismatch")
        runtime.intent = intent
        if set(intent.product_types) != product_types_before_update:
            self._invalidate_pre_search_for_product_type_change(
                runtime,
                previous=product_types_before_update,
                current=set(intent.product_types),
            )
        self._increment_intent_version(runtime)
        runtime.audit.emit(
            "APPLICATION_SERVICE",
            AuditEventType.SEARCH_INTENT_UPDATED,
            entity_refs={"search_intent_id": intent.search_intent_id},
            output_data=intent,
            payload={"intent_version": runtime.session.intent_version},
        )
        if ranking_only_patch:
            # Ranking objectives do not change retrieval, eligibility, cashflow,
            # or user facts. Reuse current deterministic evaluations and rerank
            # only; this keeps the UI toggle sub-second and avoids question LLMs.
            self._rank(runtime)
        else:
            self._run_pipeline(runtime)
        if ranking_only_patch and active_question_before_update is not None:
            runtime.active_question = active_question_before_update
            runtime.session = runtime.session.model_copy(
                update={
                    "active_question_id": active_question_before_update.question_id,
                    "status": SearchSessionStatus.QUESTIONING,
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
            self._refresh_ranking_stability(runtime, material_question=True)
        return runtime.session

    @staticmethod
    def _invalidate_pre_search_for_product_type_change(
        runtime: _SearchRuntime,
        *,
        previous: set[str],
        current: set[str],
    ) -> None:
        """Reopen only profile dimensions whose meaning depends on product type."""

        if not runtime.pre_search_profile:
            return
        now = datetime.now(timezone.utc)
        product_type_entry = runtime.pre_search_profile[PRODUCT_TYPE]
        runtime.pre_search_profile[PRODUCT_TYPE] = product_type_entry.model_copy(
            update={
                "answer_status": PreSearchAnswerStatus.ANSWERED,
                "value": {"product_types": sorted(current)},
                "source_text": (
                    runtime.intent.source_utterances[-1]
                    if runtime.intent.source_utterances
                    else product_type_entry.source_text
                ),
                "answered_at": now,
            },
            deep=True,
        )

        def contribution_mode(types: set[str]) -> str:
            if types and types <= {"PARKING_ACCOUNT", "CMA"}:
                return "LIQUID_BALANCE"
            if types == {"TIME_DEPOSIT"}:
                return "LUMP_SUM"
            if types == {"INSTALLMENT_SAVINGS"}:
                return "PERIODIC"
            return "MIXED"

        dependent: set[str] = set()
        if contribution_mode(previous) != contribution_mode(current):
            dependent.add(CONTRIBUTION_AND_TERM)
        for key in dependent:
            entry = runtime.pre_search_profile[key]
            runtime.pre_search_profile[key] = entry.model_copy(
                update={
                    "answer_status": PreSearchAnswerStatus.NOT_ASKED,
                    "value": {},
                    "source_text": None,
                    "rationale": None,
                    "answered_at": None,
                },
                deep=True,
            )

        protection = runtime.pre_search_profile[PROTECTION_AND_CMA_SCOPE]
        protection_is_applicable = {"PARKING_ACCOUNT", "CMA"}.issubset(current)
        if protection_is_applicable:
            if protection.answer_status == PreSearchAnswerStatus.NOT_APPLICABLE:
                runtime.pre_search_profile[PROTECTION_AND_CMA_SCOPE] = (
                    protection.model_copy(
                        update={
                            "answer_status": PreSearchAnswerStatus.NOT_ASKED,
                            "value": {},
                            "source_text": None,
                            "rationale": None,
                            "answered_at": None,
                        },
                        deep=True,
                    )
                )
        else:
            runtime.pre_search_profile[PROTECTION_AND_CMA_SCOPE] = (
                protection.model_copy(
                    update={
                        "answer_status": PreSearchAnswerStatus.NOT_APPLICABLE,
                        "value": {},
                        "source_text": (
                            runtime.intent.source_utterances[-1]
                            if runtime.intent.source_utterances
                            else protection.source_text
                        ),
                        "rationale": "현재 상품군에는 파킹통장과 CMA 비교 질문이 적용되지 않음",
                        "answered_at": now,
                    },
                    deep=True,
                )
            )

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

        # Short clarification requests are common when a formal eligibility
        # condition is first shown.  They should keep the same question open,
        # return its grounded explanation immediately, and never depend on a
        # model choosing between "explain" and "skip".
        if (
            runtime.active_question is not None
            and self._is_active_question_explanation_request(message)
        ):
            explanation = runtime.active_question.explanation
            runtime.recent_user_messages = [
                *runtime.recent_user_messages,
                message,
            ][-5:]
            runtime.visible_dialogue.extend(
                [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": explanation},
                ]
            )
            runtime.visible_dialogue = runtime.visible_dialogue[-10:]
            return ConversationTurnResult(
                search_session_id=search_session_id,
                message=message,
                operations_executed=[ConversationOperation.EXPLAIN_ACTIVE_QUESTION],
                session=runtime.session,
                next_question=runtime.active_question,
                recommendations=None,
                assistant_message=explanation,
            )

        if (
            runtime.active_question is not None
            and runtime.active_question.question_kind == "PROMOTION_RISK_PREFERENCE"
        ):
            return self._handle_random_promotion_preference(runtime, message=message)

        if (
            self.pre_search_enabled
            and runtime.active_question is not None
            and runtime.active_question.question_kind == "PRE_SEARCH_PROFILE"
            and getattr(self.conversation_orchestrator, "interpret_pre_search", None)
            is not None
        ):
            return self._handle_pre_search_message(runtime, message=message)

        answer_plan_interpreter = getattr(
            self.conversation_orchestrator,
            "interpret_answer_plan",
            None,
        )
        if (
            runtime.active_question is not None
            and runtime.active_question.question_kind == "FINANCIAL_FACT"
            and answer_plan_interpreter is not None
        ):
            return self._handle_answer_plan_message(
                runtime,
                message=message,
                interpreter=answer_plan_interpreter,
            )

        flexible_interpreter = getattr(
            self.conversation_orchestrator,
            "interpret_turn",
            None,
        )
        if flexible_interpreter is not None:
            return self._handle_flexible_message(
                runtime,
                message=message,
                interpreter=flexible_interpreter,
            )

        if (
            self.pre_search_enabled
            and runtime.active_question is not None
            and runtime.active_question.question_kind == "PRE_SEARCH_PROFILE"
        ):
            return self._handle_pre_search_message(runtime, message=message)

        runtime.working_note_turn_sequence += 1
        runtime.recent_user_messages = [*runtime.recent_user_messages, message][-2:]
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
        explanation_requested = any(
            action.operation == ConversationOperation.EXPLAIN_ACTIVE_QUESTION
            for action in plan.actions
        )
        required_confirmation_deferred = False
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
            ConversationOperation.SKIP_ACTIVE_QUESTION: 50,
            ConversationOperation.PROPOSE_ACTIVE_QUESTION_ANSWER: 55,
            ConversationOperation.EXPLAIN_ACTIVE_QUESTION: 80,
            ConversationOperation.SHOW_CURRENT_RESULTS: 90,
            ConversationOperation.NO_OP: 99,
        }
        try:
            ordered_actions = sorted(
                enumerate(plan.actions),
                key=lambda pair: (priority[pair[1].operation], pair[0]),
            )
            for _index, action in ordered_actions:
                if (
                    action.operation == ConversationOperation.SKIP_ACTIVE_QUESTION
                    and runtime.active_question is not None
                    and runtime.active_question.confirmation_required
                ):
                    required_confirmation_deferred = True
                    executed.append(ConversationOperation.NO_OP)
                    continue
                self._execute_conversation_action(runtime, action, message=message)
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
                ConversationOperation.SKIP_ACTIVE_QUESTION,
            }
            state_changed = any(item in mutating_operations for item in executed)
            if state_changed:
                # Every mutating domain method already performs the required
                # deterministic evaluation/ranking work. Answer/skip operations
                # deliberately defer only next-question selection, so finish that
                # once here instead of running the entire pipeline a second time.
                if runtime.active_question is None and not runtime.conflicts:
                    self._select_question(runtime)
                runtime.audit.emit(
                    "APPLICATION_SERVICE",
                    AuditEventType.MUTABLE_SEARCH_STATE_UPDATED,
                    entity_refs={"search_session_id": search_session_id},
                    output_data=self._mutable_state_summary(runtime),
                    payload={
                        "operations": [item.value for item in executed],
                        "full_research": True,
                        "duplicate_pipeline_avoided": True,
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
        generated_explanation = next(
            (
                action.assistant_message
                for action in plan.actions
                if action.operation == ConversationOperation.EXPLAIN_ACTIVE_QUESTION
                and action.assistant_message
            ),
            None,
        )
        assistant_message = (
            generated_explanation or runtime.active_question.explanation
            if explanation_requested and runtime.active_question is not None
            else (
                "이 조건은 가입 가능 여부를 결정하므로 확인이 필요해요. 확인할 수 "
                "없다면 이 상품을 추천에서 제외해 달라고 말씀해 주세요."
                if required_confirmation_deferred
                else None
            )
        )
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
                        "조건 검토가 진행 중이므로 현재 목록은 확정 Top 5가 아닌 후보 목록입니다."
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
            assistant_message=assistant_message,
        )

    @staticmethod
    def _is_active_question_explanation_request(message: str) -> bool:
        """Recognize terse Korean requests to explain the current question."""

        compact = re.sub(r"\s+", "", message).casefold()
        return any(
            marker in compact
            for marker in (
                "뭔소리",
                "무슨소리",
                "무슨말",
                "이게뭐",
                "이건뭐",
                "뭐야",
                "왜물어",
                "설명해",
                "설명좀",
            )
        )

    def _handle_answer_plan_message(
        self,
        runtime: _SearchRuntime,
        *,
        message: str,
        interpreter,
    ) -> ConversationTurnResult:
        """Apply a narrowly scoped condition answer as one atomic changeset.

        The model may identify the answer and explicit side effects, but it may
        reference only backend-issued question/variable IDs and institutions
        deterministically resolved from this utterance.  All mutations are
        deferred and the search pipeline is run exactly once after validation.
        """

        search_session_id = runtime.session.search_session_id
        active = runtime.active_question
        if active is None or active.question_kind != "FINANCIAL_FACT":
            raise ValueError("AnswerPlan requires an active financial-fact question")
        context = self._conversation_context(runtime)
        raw_plan = interpreter(message, context=context)
        plan = raw_plan if isinstance(raw_plan, AnswerPlan) else AnswerPlan.model_validate(raw_plan)

        # If the utterance was wholly outside this narrow contract, let the
        # general interpreter handle it.  No state has changed at this point.
        if (
            plan.active_question_answer is None
            and not plan.additional_updates
            and plan.unresolved_fragments
        ):
            flexible = getattr(self.conversation_orchestrator, "interpret_turn", None)
            if flexible is not None:
                return self._handle_flexible_message(
                    runtime,
                    message=message,
                    interpreter=flexible,
                )
        if plan.active_question_answer is None and not plan.additional_updates:
            raise ValueError("AnswerPlan did not contain a usable answer or update")

        requests_by_variable = self._condition_requests_by_variable(runtime)
        active_variable_id = (
            self.question_planner.question_family_id(active.request)
            if active.request is not None
            else None
        )
        if plan.active_question_answer is not None:
            answer = plan.active_question_answer
            if answer.question_id != active.question_id:
                raise ValueError("AnswerPlan question_id does not match the active question")
            if answer.state == UserConditionStatus.VERIFIED:
                raise ValueError("LLM answers cannot create VERIFIED condition state")

        institution_catalog = [
            {
                "institution_id": institution_id,
                "institution_name": institution_name,
            }
            for institution_id, institution_name in sorted(
                {
                    (
                        product.institution_id,
                        (
                            product.metadata.institution_name
                            if product.metadata is not None
                            and product.metadata.institution_name
                            else product.institution_id
                        ),
                    )
                    for product in self.products.values()
                }
            )
        ]
        _, resolved_institution_ids = grounded_targets(message, context)
        existing_institution_ids = {item["institution_id"] for item in institution_catalog}
        seen_active_variable = False
        for update in plan.additional_updates:
            if update.kind == "INSTITUTION_EXCLUSION":
                assert update.institution_id is not None and update.excluded is not None
                if update.institution_id not in existing_institution_ids:
                    raise ValueError(f"Unknown institution_id: {update.institution_id}")
                permitted = set(resolved_institution_ids)
                if update.institution_id not in permitted:
                    raise ValueError(
                        "Institution update was not grounded in the current user message"
                    )
                continue
            assert update.variable_id is not None and update.state is not None
            if update.variable_id not in requests_by_variable:
                raise ValueError(f"Unknown or non-pending variable_id: {update.variable_id}")
            if update.state == UserConditionStatus.VERIFIED:
                raise ValueError("LLM updates cannot create VERIFIED condition state")
            if update.variable_id == active_variable_id:
                seen_active_variable = True
        if seen_active_variable:
            raise ValueError("The active variable must be answered only once")

        snapshot = self._snapshot_runtime(runtime)
        executed: list[ConversationOperation] = []
        runtime.defer_pipeline = True
        runtime.pipeline_requested_while_deferred = False
        try:
            institution_upserts: list[str] = []
            institution_removals: list[str] = []
            for update in plan.additional_updates:
                if update.kind != "INSTITUTION_EXCLUSION":
                    continue
                assert update.institution_id is not None and update.excluded is not None
                target = institution_upserts if update.excluded else institution_removals
                target.append(update.institution_id)
            if institution_upserts or institution_removals:
                self.update_search_intent(
                    search_session_id,
                    patch=IntentPatch(
                        upsert_excluded_institution_ids=institution_upserts,
                        remove_excluded_institution_ids=institution_removals,
                    ),
                    utterance=message,
                )
                executed.append(ConversationOperation.UPDATE_SEARCH_INTENT)

            for update in plan.additional_updates:
                if update.kind != "USER_CONDITION":
                    continue
                assert update.variable_id is not None and update.state is not None
                effective_status = self._effective_answer_plan_condition_status(
                    update.variable_id,
                    update.state,
                    update.value,
                )
                self._apply_condition_variable_update(
                    runtime,
                    variable_id=update.variable_id,
                    status=effective_status,
                    value=update.value,
                    requests=requests_by_variable[update.variable_id],
                )
                executed.append(ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER)

            if plan.active_question_answer is not None:
                answer = plan.active_question_answer
                effective_state = self._effective_answer_plan_condition_status(
                    active_variable_id or "",
                    answer.state,
                    answer.value,
                )
                if effective_state in {
                    UserConditionStatus.ACKNOWLEDGED_UNKNOWN,
                    UserConditionStatus.WILLING_UNSPECIFIED,
                }:
                    self._set_condition_state(
                        runtime,
                        active,
                        status=effective_state.value,
                        value=answer.value,
                        interpreter_version="ANSWER_PLAN_V1",
                    )
                    self.skip_active_question(search_session_id, select_next=False)
                    executed.append(ConversationOperation.SKIP_ACTIVE_QUESTION)
                else:
                    if (
                        active_variable_id is not None
                        and self._is_quantitative_condition_variable(active_variable_id)
                    ):
                        self._apply_condition_variable_update(
                            runtime,
                            variable_id=active_variable_id,
                            status=effective_state,
                            value=answer.value,
                            requests=requests_by_variable[active_variable_id],
                        )
                        self._commit_answered_question(runtime, active.question_id)
                        executed.append(
                            ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER
                        )
                    else:
                        submitted_value = (
                            False
                            if effective_state == UserConditionStatus.DECLINED
                            else answer.value
                        )
                        self.submit_user_answer(
                            search_session_id,
                            question_id=answer.question_id,
                            answer=submitted_value,
                            select_next=False,
                            interpreter_version="ANSWER_PLAN_V1",
                        )
                        executed.append(
                            ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER
                        )

            runtime.defer_pipeline = False
            if executed or runtime.pipeline_requested_while_deferred:
                self._run_pipeline(runtime)
            runtime.recent_user_messages = [*runtime.recent_user_messages, message][-5:]
            runtime.visible_dialogue.extend(
                [
                    {"role": "user", "content": message},
                    {
                        "role": "assistant",
                        "content": "조건을 반영해 추천을 다시 계산했어요.",
                    },
                ]
            )
            runtime.visible_dialogue = runtime.visible_dialogue[-10:]
            self._safe_update_working_note(runtime)
        except Exception:
            runtime.defer_pipeline = False
            self._restore_runtime(runtime, snapshot)
            raise

        next_question = self.get_next_question(search_session_id)
        recommendations = (
            self.get_top_recommendations(search_session_id)
            if next_question is None and not runtime.conflicts
            else None
        )
        return ConversationTurnResult(
            search_session_id=search_session_id,
            message=message,
            operations_executed=list(dict.fromkeys(executed)),
            session=runtime.session,
            next_question=next_question,
            recommendations=recommendations,
            assistant_message=(
                "조건을 반영했어요. 다만 일부 표현은 이해하지 못했어요: "
                + ", ".join(plan.unresolved_fragments)
                if plan.unresolved_fragments
                else None
            ),
        )

    def _condition_requests_by_variable(
        self,
        runtime: _SearchRuntime,
    ) -> dict[str, list[MissingFactRequest]]:
        """Return current missing requests grouped by backend-owned variable ID."""

        grouped: dict[str, list[MissingFactRequest]] = {}
        seen: set[tuple[str, str]] = set()
        frontier_ids = (
            set(
                self.question_planner.frontier_product_ids(
                    runtime.evaluations,
                    runtime.intent,
                )
            )
            if runtime.evaluations
            else set()
        )
        for product_id, candidate in runtime.evaluations.items():
            if product_id not in frontier_ids:
                continue
            for request in candidate.product_evaluation.missing_facts:
                variable_id = self.question_planner.question_family_id(request)
                reference = self._request_reference(request)
                key = (variable_id, reference)
                if key in seen:
                    continue
                seen.add(key)
                grouped.setdefault(variable_id, []).append(request)
        if runtime.active_question is not None and runtime.active_question.request is not None:
            request = runtime.active_question.request
            variable_id = self.question_planner.question_family_id(request)
            reference = self._request_reference(request)
            if (variable_id, reference) not in seen:
                grouped.setdefault(variable_id, []).append(request)
        return grouped

    @staticmethod
    def _is_quantitative_condition_variable(variable_id: str) -> bool:
        key = variable_id.upper()
        return any(
            marker in key
            for marker in (
                "CARD_MONTHLY_SPEND",
                "CARD_SPEND_AMOUNT",
                "MONTHLY_SPEND_LIMIT",
            )
        )

    @classmethod
    def _effective_answer_plan_condition_status(
        cls,
        variable_id: str,
        status: UserConditionStatus,
        value: Any,
    ) -> UserConditionStatus:
        # A boolean false is an unambiguous negative answer even if the LLM
        # mislabeled it as DECLARED_FEASIBLE. Normalize this harmless semantic
        # mismatch instead of rejecting the user's answer with HTTP 400.
        if status == UserConditionStatus.DECLARED_FEASIBLE and value is False:
            return UserConditionStatus.DECLINED
        if (
            status == UserConditionStatus.DECLARED_FEASIBLE
            and cls._is_quantitative_condition_variable(variable_id)
            and cls._krw_amount(value) is None
        ):
            return UserConditionStatus.WILLING_UNSPECIFIED
        return status

    @staticmethod
    def _krw_amount(value: Any) -> Decimal | None:
        if isinstance(value, bool) or value is None:
            return None
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="python")
        if isinstance(value, dict):
            value = value.get("amount")
        if isinstance(value, (int, float, Decimal)):
            amount = Decimal(str(value))
            return amount if amount >= 0 else None
        if not isinstance(value, str):
            return None
        compact = value.replace(",", "").replace(" ", "")
        match = re.search(r"(\d+(?:\.\d+)?)만원", compact)
        if match:
            return Decimal(match.group(1)) * Decimal("10000")
        match = re.search(r"(\d+(?:\.\d+)?)원", compact)
        if match:
            return Decimal(match.group(1))
        if re.fullmatch(r"\d+(?:\.\d+)?", compact):
            return Decimal(compact)
        return None

    @staticmethod
    def _request_krw_threshold(request: MissingFactRequest) -> Decimal | None:
        text = (request.question or "").replace(",", "").replace(" ", "")
        matches = re.findall(r"(\d+(?:\.\d+)?)만원", text)
        if matches:
            return max(Decimal(item) * Decimal("10000") for item in matches)
        matches = re.findall(r"(\d+(?:\.\d+)?)원", text)
        if matches:
            return max(Decimal(item) for item in matches)
        return None

    def _apply_condition_variable_update(
        self,
        runtime: _SearchRuntime,
        *,
        variable_id: str,
        status: UserConditionStatus,
        value: Any,
        requests: list[MissingFactRequest],
        interpreter_version: str = "ANSWER_PLAN_V1",
    ) -> None:
        """Persist one explicit side-answer across every bound requirement."""

        if status in {
            UserConditionStatus.ACKNOWLEDGED_UNKNOWN,
            UserConditionStatus.WILLING_UNSPECIFIED,
        }:
            runtime.condition_states[variable_id] = UserConditionState(
                variable_id=variable_id,
                status=status,
                value=value,
                source_turn_id=runtime.audit.request_id,
                interpreter_version=interpreter_version,
            )
        else:
            amount = (
                self._krw_amount(value)
                if self._is_quantitative_condition_variable(variable_id)
                else None
            )
            answer = False if status == UserConditionStatus.DECLINED else value
            for request in requests:
                request_answer = answer
                if amount is not None:
                    threshold = self._request_krw_threshold(request)
                    if threshold is None:
                        # The value is retained in UserConditionState, but an
                        # unreviewed sentence without a threshold cannot be
                        # converted into a boolean financial fact.
                        continue
                    request_answer = amount >= threshold
                reference = self._request_reference(request)
                runtime.fact_store, _ = submit_answer_to_store(
                    runtime.fact_store,
                    request,
                    UserAnswerSubmission(
                        request_reference=reference,
                        answer=request_answer,
                        answered_at=self._effective_answered_at(runtime, None),
                    ),
                    audit=runtime.audit,
                )
                runtime.request_history[reference] = request
            runtime.condition_states[variable_id] = UserConditionState(
                variable_id=variable_id,
                status=status,
                value=(amount if amount is not None else answer),
                source_turn_id=runtime.audit.request_id,
                interpreter_version=interpreter_version,
            )
            runtime.answer_records = self._answer_records(runtime.fact_store)
        runtime.session = runtime.session.model_copy(
            update={
                "condition_states": [
                    runtime.condition_states[key]
                    for key in sorted(runtime.condition_states)
                ],
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        runtime.pipeline_requested_while_deferred = True

    def _handle_flexible_message(
        self,
        runtime: _SearchRuntime,
        *,
        message: str,
        interpreter,
    ) -> ConversationTurnResult:
        """Validate and atomically execute one complete single-call turn plan."""

        search_session_id = runtime.session.search_session_id
        snapshot = self._snapshot_runtime(runtime)
        try:
            # Only a review executed during this turn may contribute user-facing
            # copy. An approved message from session creation or a previous turn is
            # never replayed as if it were new.
            runtime.eligibility_text_review_assistant_message = None
            context = self._conversation_context(runtime)
            runtime.working_note_turn_sequence += 1
            turn_id = runtime.working_note_turn_sequence
            runtime.recent_user_messages = [
                *runtime.recent_user_messages,
                message,
            ][-5:]
            self._safe_record_working_note_turn(
                runtime,
                sequence=turn_id,
                utterance=message,
            )
            runtime.audit.emit(
                "CONVERSATION_ORCHESTRATOR",
                AuditEventType.CONVERSATION_TURN_RECEIVED,
                entity_refs={"search_session_id": search_session_id},
                input_data={"message_hash": canonical_hash(message)},
                payload={
                    "message_hash": canonical_hash(message),
                    "context_schema_version": "flexible-conversation-v1",
                    "context_hash": canonical_hash(context),
                },
            )
            raw_plan = interpreter(message, context=context)
            plan = (
                raw_plan
                if isinstance(raw_plan, FlexibleConversationTurnPlan)
                else FlexibleConversationTurnPlan.model_validate(raw_plan)
            )
            plan = self._normalize_legacy_pre_search_actions(runtime, plan)
            self._validate_flexible_turn_plan(runtime, plan, message=message)
        except Exception as exc:
            # Interpreter/contract failures happen before the action execution
            # transaction below. They still must not consume a conversation turn
            # or leave review/message state behind.
            self._restore_runtime(runtime, snapshot)
            runtime.audit.emit(
                "CONVERSATION_ORCHESTRATOR",
                AuditEventType.CONVERSATION_TURN_ROLLED_BACK,
                entity_refs={"search_session_id": search_session_id},
                input_data={"message_hash": canonical_hash(message)},
                output_data={"business_state_committed": False},
                payload={
                    "reason": str(exc),
                    "assistant_message_exposed": False,
                },
            )
            raise

        runtime.changeset_sequence += 1
        changeset_id = f"CHG-{runtime.changeset_sequence:05d}"
        before = self._decision_state_projection(runtime)
        executed: list[ConversationOperation] = []
        revert_ids: list[str] = []
        current_results_requested = any(
            action.operation == ConversationOperation.SHOW_CURRENT_RESULTS
            for action in plan.actions
        )
        runtime.defer_pipeline = True
        runtime.pipeline_requested_while_deferred = False
        try:
            for update in plan.profile_updates:
                question = self._pre_search_question_for_key(runtime, update.question_key)
                answer_plan = PreSearchAnswerPlan.model_validate(
                    update.model_dump(mode="python", exclude={"question_key"})
                )
                self._apply_pre_search_answer(
                    runtime,
                    question,
                    answer_plan,
                    source_text=message,
                )
                executed.append(ConversationOperation.SUBMIT_PRE_SEARCH_ANSWER)

            priority = {
                ConversationOperation.REVERT_DECISIONS: 5,
                ConversationOperation.UPDATE_SEARCH_INTENT: 10,
                ConversationOperation.SET_PRODUCT_FEATURE_POLICY: 15,
                ConversationOperation.CLEAR_PRODUCT_FEATURE_POLICY: 15,
                ConversationOperation.SET_PRODUCT_EXCLUSION: 20,
                ConversationOperation.CLEAR_USER_DECLARED_FACT: 25,
                ConversationOperation.CLEAR_PRODUCT_CONTRIBUTION_CHOICE: 25,
                ConversationOperation.REVISE_USER_ANSWER: 30,
                ConversationOperation.REVISE_USER_DECLARED_FACT: 30,
                ConversationOperation.SET_PRODUCT_CONTRIBUTION_CHOICE: 40,
                ConversationOperation.REVISE_PRODUCT_CONTRIBUTION_CHOICE: 40,
                ConversationOperation.EXCLUDE_PRODUCT: 45,
                ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER: 50,
                ConversationOperation.SKIP_ACTIVE_QUESTION: 50,
                ConversationOperation.PROPOSE_ACTIVE_QUESTION_ANSWER: 55,
                ConversationOperation.EXPLAIN_ACTIVE_QUESTION: 80,
                ConversationOperation.SHOW_CURRENT_RESULTS: 90,
                ConversationOperation.NO_OP: 99,
            }
            ordered = sorted(
                enumerate(plan.actions),
                key=lambda pair: (priority[pair[1].operation], pair[0]),
            )
            for _index, action in ordered:
                if action.operation == ConversationOperation.REVERT_DECISIONS:
                    self._apply_decision_reverts(runtime, action.decision_ids)
                    revert_ids.extend(action.decision_ids)
                else:
                    self._execute_conversation_action(runtime, action, message=message)
                executed.append(action.operation)

            runtime.defer_pipeline = False
            mutating = bool(plan.profile_updates) or any(
                operation
                not in {
                    ConversationOperation.SHOW_CURRENT_RESULTS,
                    ConversationOperation.NO_OP,
                    ConversationOperation.EXPLAIN_ACTIVE_QUESTION,
                    ConversationOperation.PROPOSE_ACTIVE_QUESTION_ANSWER,
                }
                for operation in executed
            )
            if mutating or runtime.pipeline_requested_while_deferred:
                self._run_pipeline(runtime)
                runtime.audit.emit(
                    "APPLICATION_SERVICE",
                    AuditEventType.SEARCH_RECALCULATED,
                    entity_refs={"search_session_id": search_session_id},
                    output_data={
                        "candidate_product_ids": runtime.session.candidate_product_ids,
                        "ranking_run_id": runtime.session.ranking_run_id,
                    },
                    payload={
                        "trigger": "FLEXIBLE_CONVERSATION_CHANGESET",
                        "single_pipeline_pass": True,
                    },
                )

            pending_by_id = {
                question.question_id: question
                for question in self._pending_questions(runtime)
            }
            # The deterministic planner owns question order and presentation.
            # A conversational model may interpret the user's answer, but it
            # cannot suppress or replace the next workflow question. The sole
            # temporary presentation exception is an explicit request to show
            # current results; the active question remains pending for the next
            # turn in that case.
            question_to_present = (
                None if current_results_requested else runtime.active_question
            )
            if question_to_present is not None:
                presentation = dict(
                    runtime.question_presentation.get(
                        question_to_present.question_id,
                        {},
                    )
                )
                presentation.update(
                    {
                        "last_presented_turn": turn_id,
                        "presented_count": int(presentation.get("presented_count", 0)) + 1,
                    }
                )
                runtime.question_presentation[question_to_present.question_id] = presentation

            after = self._decision_state_projection(runtime)
            reverted_targets = {
                entry.target
                for entry in runtime.decision_ledger.entries
                if entry.decision_id in set(revert_ids)
            }
            decisions: list[Decision] = []
            for target in sorted(set(before) | set(after)):
                if before.get(target) == after.get(target) or target in reverted_targets:
                    continue
                runtime.decision_sequence += 1
                decisions.append(
                    Decision(
                        decision_id=f"DEC-{runtime.decision_sequence:05d}",
                        changeset_id=changeset_id,
                        turn_id=turn_id,
                        operation=self._decision_operation_for_target(target),
                        target=target,
                        before=before.get(target),
                        after=after.get(target),
                        reversible=not target.startswith("question_presentation"),
                        source_text=message,
                    )
                )
            reverts = (
                (RevertDecisions(tuple(revert_ids)),)
                if revert_ids
                else ()
            )
            runtime.decision_ledger.commit(
                Changeset(
                    changeset_id=changeset_id,
                    turn_id=turn_id,
                    decisions=tuple(decisions),
                    reverts=reverts,
                )
            )

            response_assistant_message = self._combine_llm_assistant_messages(
                plan.assistant_message,
                runtime.eligibility_text_review_assistant_message,
            )
            runtime.visible_dialogue.append({"role": "user", "content": message})
            if response_assistant_message:
                runtime.visible_dialogue.append(
                    {"role": "assistant", "content": response_assistant_message}
                )
            if question_to_present is not None:
                runtime.visible_dialogue.append(
                    {
                        "role": "assistant",
                        "content": question_to_present.question,
                        "question_id": question_to_present.question_id,
                    }
                )
            runtime.visible_dialogue = runtime.visible_dialogue[-10:]
            self._safe_update_working_note(runtime)
        except Exception as exc:
            runtime.defer_pipeline = False
            self._restore_runtime(runtime, snapshot)
            runtime.audit.emit(
                "CONVERSATION_ORCHESTRATOR",
                AuditEventType.CONVERSATION_TURN_ROLLED_BACK,
                entity_refs={"search_session_id": search_session_id},
                input_data={"message_hash": canonical_hash(message)},
                output_data={"business_state_committed": False},
                payload={
                    "reason": str(exc),
                    "assistant_message_exposed": False,
                },
            )
            raise

        recommendations = None
        unresolved_warning = None
        if current_results_requested:
            if runtime.ranking is not None and not runtime.conflicts:
                recommendations = self.get_top_recommendations(search_session_id)
                if pending_by_id or not runtime.ranking.stability.stable:
                    unresolved_warning = (
                        "조건 검토가 진행 중이므로 현재 목록은 확정 Top 5가 아닌 후보 목록입니다."
                    )
        elif not pending_by_id and runtime.active_question is None and not runtime.conflicts:
            recommendations = self.get_top_recommendations(search_session_id)
        return ConversationTurnResult(
            search_session_id=search_session_id,
            message=message,
            operations_executed=executed,
            session=runtime.session,
            next_question=question_to_present,
            recommendations=recommendations,
            current_results_requested=current_results_requested,
            unresolved_warning=unresolved_warning,
            assistant_message=self._combine_llm_assistant_messages(
                plan.assistant_message,
                runtime.eligibility_text_review_assistant_message,
            ),
        )

    @staticmethod
    def _combine_llm_assistant_messages(*messages: str | None) -> str | None:
        approved = list(
            dict.fromkeys(
                message.strip()
                for message in messages
                if message is not None and message.strip()
            )
        )
        return "\n\n".join(approved) if approved else None

    @staticmethod
    def _normalize_legacy_pre_search_actions(
        runtime: _SearchRuntime,
        plan: FlexibleConversationTurnPlan,
    ) -> FlexibleConversationTurnPlan:
        """Translate an invalid legacy action into its backend-owned profile update.

        ``SUBMIT_PRE_SEARCH_ANSWER`` is intentionally not an action in the
        flexible-turn contract: it needs the deterministic question key.  Some
        model responses can nevertheless emit the older operation with the
        answer value.  When it unambiguously targets the active pre-search
        question, recover it rather than failing while sorting actions.
        """

        legacy_operations = {
            ConversationOperation.SUBMIT_PRE_SEARCH_ANSWER,
            ConversationOperation.ACKNOWLEDGE_PRE_SEARCH_UNKNOWN,
        }
        legacy_actions = [
            action for action in plan.actions if action.operation in legacy_operations
        ]
        active = runtime.active_question
        key = active.pre_search_key if active is not None else None
        profile_update_keys = {update.question_key for update in plan.profile_updates}
        duplicate_active_answer = (
            active is not None
            and active.question_kind == "PRE_SEARCH_PROFILE"
            and key is not None
            and key in profile_update_keys
            and any(
                action.operation
                == ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER
                for action in plan.actions
            )
        )
        if not legacy_actions and not duplicate_active_answer:
            return plan

        if (
            legacy_actions
            and (
                active is None
                or active.question_kind != "PRE_SEARCH_PROFILE"
                or key is None
            )
        ):
            raise ValueError("SUBMIT_PRE_SEARCH_ANSWER requires an active pre-search question")

        updates = list(plan.profile_updates)
        existing_keys = {update.question_key for update in updates}
        retained_actions = [
            action
            for action in plan.actions
            if action.operation not in legacy_operations
            and not (
                duplicate_active_answer
                and action.operation
                == ConversationOperation.SUBMIT_ACTIVE_QUESTION_ANSWER
            )
        ]
        for action in legacy_actions:
            if key in existing_keys:
                continue
            if action.operation == ConversationOperation.ACKNOWLEDGE_PRE_SEARCH_UNKNOWN:
                update_payload: dict[str, Any] = {
                    "question_key": key,
                    "resolution": "ACKNOWLEDGED_UNKNOWN",
                    "rationale": action.rationale,
                }
            else:
                answer_fields = {
                    PRODUCT_TYPE: "product_types",
                    APPLICATION_CAPACITY: "application_capacity",
                    BIRTH_DATE: "birth_date",
                    YOUTH_POLICY_ACCOUNT_HOLDING: "youth_policy_account_held",
                    SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY: "soldier_tomorrow_savings_eligible",
                    CONTRIBUTION_AND_TERM: "desired_amount_krw",
                    INSTITUTION_SCOPE: "institution_scope",
                    PROTECTION_AND_CMA_SCOPE: "liquid_product_scope",
                    COMMON_BENEFIT_WILLINGNESS: "willingness",
                    INSTITUTION_PRODUCT_HOLDING_HISTORY: "prior_product_holding_institutions",
                }
                field_name = answer_fields.get(key)
                if field_name is None or action.answer is None:
                    raise ValueError(
                        "SUBMIT_PRE_SEARCH_ANSWER must include an answer for the active question"
                    )
                answer = action.answer
                if key == PRODUCT_TYPE and isinstance(answer, str):
                    answer = [answer]
                update_payload = {
                    "question_key": key,
                    "resolution": "ANSWER",
                    field_name: answer,
                    "rationale": action.rationale,
                }
            updates.append(PreSearchProfileUpdate.model_validate(update_payload))
            existing_keys.add(key)
        return plan.model_copy(
            update={"profile_updates": updates, "actions": retained_actions},
            deep=True,
        )

    def _validate_flexible_turn_plan(
        self,
        runtime: _SearchRuntime,
        plan: FlexibleConversationTurnPlan,
        *,
        message: str,
    ) -> None:
        self._validate_operation_targets(runtime, plan.actions, message=message)
        if not plan.actions and not plan.profile_updates and not plan.assistant_message:
            raise ValueError("Flexible turn plan must contain an update, action, or message")
        if plan.profile_updates and not self.pre_search_enabled:
            raise ValueError("profile_updates require pre-search to be enabled")
        allowed_profile_keys = set(runtime.pre_search_profile)
        seen_keys: set[str] = set()
        for update in plan.profile_updates:
            if update.question_key not in allowed_profile_keys:
                raise ValueError(f"Unknown pre-search question key: {update.question_key}")
            if update.question_key in seen_keys:
                raise ValueError(
                    f"Duplicate profile update: {update.question_key}"
                )
            seen_keys.add(update.question_key)
        for action in plan.actions:
            if action.product_id is not None and action.product_id not in self.products:
                raise KeyError(f"Unknown product_id: {action.product_id}")
            if (
                action.intent_patch is not None
                and (
                    set(action.intent_patch.upsert_excluded_institution_ids)
                    | set(action.intent_patch.remove_excluded_institution_ids)
                )
                - {product.institution_id for product in self.products.values()}
            ):
                raise ValueError("Unknown institution_id in search intent patch")
            if action.operation in {
                ConversationOperation.SET_PRODUCT_FEATURE_POLICY,
                ConversationOperation.CLEAR_PRODUCT_FEATURE_POLICY,
            } and action.feature_id != "LOTTERY_BASED_BENEFIT":
                raise ValueError(f"Unsupported feature_id: {action.feature_id}")
            if action.operation == ConversationOperation.REVERT_DECISIONS:
                active_by_id = {
                    entry.decision_id: entry
                    for entry in runtime.decision_ledger.active_entries
                }
                for decision_id in action.decision_ids:
                    entry = active_by_id.get(decision_id)
                    if entry is None or not entry.reversible:
                        raise LedgerError(
                            f"decision is not reversible: {decision_id}"
                        )

    def _pending_questions(self, runtime: _SearchRuntime) -> list[PlannedQuestion]:
        pending = self._pending_pre_search_questions(runtime)
        if (
            runtime.active_question is not None
            and runtime.active_question.question_id
            not in {question.question_id for question in pending}
        ):
            pending.append(runtime.active_question)
        return pending

    @staticmethod
    def _decision_state_projection(runtime: _SearchRuntime) -> dict[str, Any]:
        state: dict[str, Any] = {
            "intent": runtime.intent.model_dump(mode="python"),
            "question_presentation": deepcopy(runtime.question_presentation),
        }
        for key, entry in runtime.pre_search_profile.items():
            state[f"pre_search:{key}"] = entry.model_dump(mode="python")
        for feature_id, policy in runtime.feature_policies.items():
            state[f"feature_policy:{feature_id}"] = policy.value
        for product_id in runtime.session.excluded_product_ids:
            state[f"product_exclusion:{product_id}"] = True
        for (product_id, field), choice in runtime.product_contribution_choices.items():
            state[f"product_choice:{product_id}:{field}"] = choice.model_dump(
                mode="python"
            )
        return state

    @staticmethod
    def _decision_operation_for_target(target: str) -> str:
        if target == "intent":
            return ConversationOperation.UPDATE_SEARCH_INTENT.value
        if target.startswith("pre_search:"):
            return ConversationOperation.SUBMIT_PRE_SEARCH_ANSWER.value
        if target.startswith("feature_policy:"):
            return ConversationOperation.SET_PRODUCT_FEATURE_POLICY.value
        if target.startswith("product_exclusion:"):
            return ConversationOperation.SET_PRODUCT_EXCLUSION.value
        if target.startswith("product_choice:"):
            return ConversationOperation.SET_PRODUCT_CONTRIBUTION_CHOICE.value
        return "QUESTION_PRESENTATION"

    def _apply_decision_reverts(
        self,
        runtime: _SearchRuntime,
        decision_ids: list[str],
    ) -> None:
        entries = {entry.decision_id: entry for entry in runtime.decision_ledger.entries}
        for decision_id in decision_ids:
            entry = entries.get(decision_id)
            if (
                entry is None
                or entry.record_status is not RecordStatus.ACTIVE
                or not entry.reversible
            ):
                raise LedgerError(f"decision is not reversible: {decision_id}")
            self._restore_decision_target(runtime, entry.target, entry.before)

    def _restore_decision_target(
        self,
        runtime: _SearchRuntime,
        target: str,
        before: Any,
    ) -> None:
        if target == "intent":
            runtime.intent = ProductSearchIntent.model_validate(before)
            self._increment_intent_version(runtime)
            return
        if target.startswith("pre_search:"):
            key = target.split(":", 1)[1]
            runtime.pre_search_profile[key] = PreSearchProfileEntry.model_validate(before)
            return
        if target.startswith("feature_policy:"):
            feature_id = target.split(":", 1)[1]
            if before is None:
                runtime.feature_policies.pop(feature_id, None)
            else:
                runtime.feature_policies[feature_id] = ProductFeaturePolicy(before)
            return
        if target.startswith("product_exclusion:"):
            product_id = target.split(":", 1)[1]
            excluded = set(runtime.session.excluded_product_ids)
            if before:
                excluded.add(product_id)
            else:
                excluded.discard(product_id)
            runtime.session = runtime.session.model_copy(
                update={"excluded_product_ids": sorted(excluded)},
                deep=True,
            )
            return
        if target.startswith("product_choice:"):
            _, product_id, field = target.split(":", 2)
            key = (product_id, field)
            if before is None:
                runtime.product_contribution_choices.pop(key, None)
            else:
                runtime.product_contribution_choices[key] = (
                    ProductContributionChoice.model_validate(before)
                )
            self._sync_product_choices_to_session(runtime)
            return
        raise LedgerError(f"Unsupported reversible decision target: {target}")

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
        # Question selection is a command-stage responsibility. An absent
        # question is a legitimate read result, not permission to advance the
        # workflow (or reopen a completed session) from a GET/prefetch.
        return runtime.active_question

    def submit_user_answer(
        self,
        search_session_id: str,
        *,
        answer: Any,
        answered_at: datetime | None = None,
        question_id: str | None = None,
        select_next: bool = True,
        interpreter_version: str = "BACKEND_DIRECT_V1",
    ) -> SearchSession:
        runtime = self._runtime(search_session_id)
        question = runtime.active_question
        if question is None:
            raise ValueError("There is no active question")
        if question_id is not None and question.question_id != question_id:
            raise ValueError("question_id does not match active question")
        if runtime.pending_assumption_answer is not None:
            if not isinstance(answer, bool):
                raise ValueError("A proposed assumption must be confirmed with yes or no")
            original = runtime.pending_assumption_original_question
            if original is None:
                raise ValueError("Proposed assumption is missing its original question")
            proposed_answer = runtime.pending_assumption_answer
            runtime.pending_assumption_answer = None
            runtime.pending_assumption_original_question = None
            runtime.active_question = original
            runtime.session = runtime.session.model_copy(
                update={
                    "active_question_id": original.question_id,
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
            if answer is False:
                # Rejecting the proposal restores the official question. It does
                # not silently commit the opposite financial fact.
                return runtime.session
            answer = proposed_answer
            question = original
        if question.question_kind == "PROMOTION_RISK_PREFERENCE":
            if not isinstance(answer, bool):
                raise ValueError("확률형 상품 포함 여부는 예 또는 아니요로 답해야 합니다")
            self._commit_answered_question(runtime, question.question_id)
            runtime.feature_policies["RANDOM_PROMOTION_RATE"] = (
                ProductFeaturePolicy.ALLOW
                if answer
                else ProductFeaturePolicy.EXCLUDE
            )
            self._run_pipeline(runtime)
            return runtime.session
        if question.question_kind == "PRE_SEARCH_PROFILE":
            raise ValueError(
                "사전 질문의 자연어 답변은 messages endpoint로 제출해야 합니다"
            )
        if self._is_unknown_answer(answer):
            self._set_condition_state(
                runtime,
                question,
                status="ACKNOWLEDGED_UNKNOWN",
                interpreter_version=interpreter_version,
            )
            return self.skip_active_question(
                search_session_id,
                select_next=select_next,
            )
        if self._is_willing_unspecified_answer(question, answer):
            # “카드 가능해요” is a willingness signal, not evidence that a
            # particular monthly threshold is met. Keep the condition in the
            # optimistic frontier, but do not apply its reward or ask it again.
            self._set_condition_state(
                runtime,
                question,
                status="WILLING_UNSPECIFIED",
                value=answer,
                interpreter_version=interpreter_version,
            )
            return self.skip_active_question(
                search_session_id,
                select_next=select_next,
            )
        if question.request is not None:
            variable_id = self.question_planner.question_family_id(question.request)
            amount = self._krw_amount(answer)
            if (
                self._is_quantitative_condition_variable(variable_id)
                and amount is not None
            ):
                requests = self._condition_requests_by_variable(runtime).get(
                    variable_id,
                    [question.request],
                )
                self._apply_condition_variable_update(
                    runtime,
                    variable_id=variable_id,
                    status=UserConditionStatus.DECLARED_FEASIBLE,
                    value=amount,
                    requests=requests,
                    interpreter_version=interpreter_version,
                )
                self._commit_answered_question(runtime, question.question_id)
                if runtime.defer_pipeline:
                    runtime.pipeline_requested_while_deferred = True
                    return runtime.session
                self._run_pipeline(runtime)
                return runtime.session
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
        if question.question_stage == "PRE_SEARCH":
            self._record_pre_search_profile_answer(runtime, question, answer)
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
        self._set_condition_state(
            runtime,
            question,
            status=(
                "DECLARED_FEASIBLE"
                if answer is not False
                else "DECLINED"
            ),
            value=answer,
            interpreter_version=interpreter_version,
        )
        # A canonical question may represent the same user variable on
        # multiple products.  Apply the validated answer to each bound
        # requirement atomically so one answer cannot leave sibling products
        # with a duplicate follow-up question.
        family_id = RankingAwareQuestionPlanner.question_family_id(question.request)
        propagated_refs: set[str] = {request_ref}
        for product_id in question.affected_product_ids:
            candidate = runtime.evaluations.get(product_id)
            if candidate is None:
                continue
            for sibling in candidate.product_evaluation.missing_facts:
                if self.question_planner.question_family_id(sibling) != family_id:
                    continue
                sibling_ref = self._request_reference(sibling)
                if sibling_ref in propagated_refs:
                    continue
                sibling_submission = UserAnswerSubmission(
                    request_reference=sibling_ref,
                    answer=answer,
                    answered_at=self._effective_answered_at(runtime, answered_at),
                )
                runtime.fact_store, _ = submit_answer_to_store(
                    runtime.fact_store,
                    sibling,
                    sibling_submission,
                    audit=runtime.audit,
                )
                runtime.request_history[sibling_ref] = sibling
                propagated_refs.add(sibling_ref)
        if question.request.fact_type.startswith("INSTITUTION_ACCOUNT_HELD::") and answer is False:
            # A confirmed absence of every current account at this institution
            # settles only the products that were inside this question's
            # bounded Top-10 verification scope. It intentionally says nothing
            # about historic first-transaction benefits, which have a different
            # time scope.
            institution_id = question.request.fact_type.split("::", 1)[1]
            for product_id in question.affected_product_ids:
                product = runtime.candidate_products.get(product_id)
                if product is None or product.institution_id != institution_id:
                    continue
                for missing in runtime.evaluations[product.product_id].product_evaluation.missing_facts:
                    if not missing.fact_type.startswith(
                        "EXISTING_PRODUCT_ACCOUNT_LIMIT_REACHED::"
                    ):
                        continue
                    runtime.fact_store, _ = submit_answer_to_store(
                        runtime.fact_store,
                        missing,
                        UserAnswerSubmission(
                            request_reference=self._request_reference(missing),
                            answer=False,
                            answered_at=self._effective_answered_at(runtime, answered_at),
                        ),
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
        if runtime.defer_pipeline:
            runtime.pipeline_requested_while_deferred = True
            return runtime.session
        self._evaluate(
            runtime,
            [runtime.candidate_products[item] for item in question.affected_product_ids],
            merge=True,
        )
        self._rank(runtime)
        if select_next:
            self._select_question(runtime)
        return runtime.session

    def propose_active_question_answer(
        self,
        search_session_id: str,
        *,
        proposed_answer: bool,
        confirmation_question: str,
    ) -> SearchSession:
        """Ask for confirmation without committing a tentative user fact."""

        runtime = self._runtime(search_session_id)
        original = runtime.active_question
        if original is None or original.question_kind != "FINANCIAL_FACT":
            raise ValueError("There is no financial fact question to confirm")
        text = confirmation_question.strip()
        if not text:
            raise ValueError("confirmation_question must not be empty")
        runtime.pending_assumption_answer = proposed_answer
        runtime.pending_assumption_original_question = original
        confirmation = original.model_copy(
            update={
                "question_id": (
                    f"{original.question_id}-ASSUME-"
                    f"{'TRUE' if proposed_answer else 'FALSE'}"
                ),
                "question": text,
                "answer_mode": "BINARY",
                "confirmation_required": True,
                "explanation": (
                    "아직 이 조건을 사실로 저장하지 않았어요. 가정을 확인하면 "
                    "그때 추천 계산에 반영합니다."
                ),
            },
            deep=True,
        )
        runtime.active_question = confirmation
        runtime.question_history[confirmation.question_id] = confirmation
        runtime.session = runtime.session.model_copy(
            update={
                "active_question_id": confirmation.question_id,
                "status": SearchSessionStatus.QUESTIONING,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        return runtime.session

    def skip_active_question(
        self,
        search_session_id: str,
        *,
        select_next: bool = True,
    ) -> SearchSession:
        """Keep a question unresolved while moving the conversation forward."""

        runtime = self._runtime(search_session_id)
        question = runtime.active_question
        if question is None:
            raise ValueError("There is no active question")
        if question.confirmation_required:
            return runtime.session
        if question.request is not None:
            family_id = self.question_planner.question_family_id(question.request)
            if family_id not in runtime.condition_states:
                self._set_condition_state(
                    runtime,
                    question,
                    status="ACKNOWLEDGED_UNKNOWN",
                )
        runtime.skipped_question_ids.add(question.question_id)
        self._commit_answered_question(runtime, question.question_id)
        if select_next:
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
        self._sync_condition_state_for_request(
            runtime,
            request,
            value=new_value,
        )
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
        if runtime.conflicts or runtime.ranking is None:
            raise ValueError("RECOMMENDATION_NOT_READY: resolve search conflicts before reading results")
        recommendation = self.recommendation_service.build_result(
            search_session_id,
            runtime.ranking,
            audit=runtime.audit,
        )
        acknowledged_ids: set[str] = set()
        for question_id in runtime.skipped_question_ids:
            question = runtime.question_history.get(question_id)
            if question is None:
                continue
            if question.request is not None:
                acknowledged_ids.add(
                    question.request.missing_fact_id
                    or f"{question.request.fact_type}:{question.request.requested_by_rule_id}"
                )
            if question.ranking_input is not None:
                acknowledged_ids.add(question.ranking_input.input_id)
            if question.feasibility_clarification is not None:
                acknowledged_ids.add(
                    question.feasibility_clarification.clarification_id
                )
        visible_ids = {
            item.product_id for item in recommendation.top_products
        }
        visible_unresolved = {
            unresolved_id
            for product_id in visible_ids
            for unresolved_id in runtime.evaluations[product_id].unresolved_material_fact_ids
        }
        unresolved_question_ids = visible_unresolved - acknowledged_ids
        acknowledged_visible = visible_unresolved & acknowledged_ids
        confirmed = (
            not unresolved_question_ids
            and all(
                runtime.evaluations[product_id].ranking_comparability
                == RankingComparability.COMPARABLE
                for product_id in visible_ids
            )
            and runtime.ranking.stability.stable
            and runtime.eligibility_text_review_state in {"DISABLED", "COMPLETE"}
        )
        recommendation = recommendation.model_copy(
            update={
                "recommendation_status": "CONFIRMED" if confirmed else "PROVISIONAL",
                "provisional_candidates": (
                    [] if confirmed else recommendation.top_products
                ),
                "confirmed_top_products": (
                    recommendation.top_products if confirmed else []
                ),
                "unresolved_question_count": len(unresolved_question_ids),
                "acknowledged_unknown_count": len(acknowledged_visible),
                "eligibility_text_review_state": (
                    runtime.eligibility_text_review_state
                ),
                "eligibility_text_review_rounds": (
                    runtime.eligibility_text_review_rounds
                ),
                "eligibility_text_review_pending_count": (
                    runtime.eligibility_text_review_pending_count
                ),
            },
            deep=True,
        )
        if complete:
            # Explicit backwards-compatible command, never used by GET routes.
            runtime.recommendation = recommendation
            runtime.session = runtime.session.model_copy(
                update={
                    "recommendation_id": recommendation.recommendation_id,
                    "status": SearchSessionStatus.COMPLETED,
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
        return recommendation

    def get_product_recommendation_detail(
        self,
        search_session_id: str,
        product_id: str,
        *,
        include_explanation: bool = True,
    ) -> ProductRecommendationDetail:
        product_id = self.resolve_product_id(product_id)
        runtime = self._runtime(search_session_id)
        recommendation = runtime.recommendation or self.get_top_recommendations(
            search_session_id
        )
        rank_by_id = {
            item.product_id: item.rank
            for item in (recommendation.ranked_products or recommendation.top_products)
        }
        product = runtime.candidate_products.get(product_id) or self.products.get(product_id)
        if product is None:
            raise KeyError(f"Unknown product_id: {product_id}")
        # A detail page may be opened from the full browse catalog, not only
        # from the current Top-K. Re-materialize session-wide facts and
        # evaluate that exact product so its detail never falls back to a
        # stale published maximum rate. Detail rendering is a read operation:
        # keep the one-product result local so opening a filtered/browse-only
        # product cannot add it to the session's candidate or ranking state.
        fact_store = self._materialize_capabilities(
            runtime,
            persist_answer_records=False,
            emit_supersession_audit=False,
        )
        previous_evaluation_id = runtime.audit.evaluation_id
        try:
            evaluated = self.evaluator.evaluate(
                [product],
                fact_store,
                self._intent_with_feature_policies(runtime),
                as_of=runtime.as_of,
                subscription_date=runtime.subscription_date,
                audit=runtime.audit,
                product_contribution_choices=self._product_choice_map(runtime),
            )
            candidate = evaluated[product_id]
            return self.recommendation_service.build_detail(
                search_session_id=search_session_id,
                recommendation_id=recommendation.recommendation_id,
                product=product,
                candidate=candidate,
                rank=rank_by_id.get(product_id, len(rank_by_id) + 1),
                intent=runtime.intent,
                ranking=runtime.ranking,
                condition_states=runtime.condition_states,
                audit=runtime.audit,
                include_explanation=include_explanation,
            )
        finally:
            runtime.audit.bind_evaluation_id(previous_evaluation_id)

    def get_evaluation_trace(self, search_session_id: str) -> tuple[AuditEvent, ...]:
        runtime = self._runtime(search_session_id)
        return self.audit_sink.read(trace_id=runtime.audit.trace_id)

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------
    def _run_pipeline(
        self,
        runtime: _SearchRuntime,
        *,
        select_question: bool = True,
    ) -> None:
        if runtime.defer_pipeline:
            runtime.pipeline_requested_while_deferred = True
            return
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
        if select_question:
            self._select_question(runtime)

    def _retrieve(self, runtime: _SearchRuntime) -> None:
        retained, decisions = self.candidate_retriever.retrieve(
            self.products.values(),
            runtime.intent,
            as_of=runtime.as_of,
        )
        excluded_by_feature: dict[str, str] = {}
        for feature_id, policy in runtime.feature_policies.items():
            if policy != ProductFeaturePolicy.EXCLUDE:
                continue
            for product in retained:
                if feature_present(product, feature_id):
                    excluded_by_feature[product.product_id] = feature_id
        if excluded_by_feature:
            retained = [
                product
                for product in retained
                if product.product_id not in excluded_by_feature
            ]
        excluded = set(runtime.session.excluded_product_ids)
        retained = [item for item in retained if item.product_id not in excluded]
        decision_by_id = {item.product_id: item for item in decisions}
        for product_id, feature_id in sorted(excluded_by_feature.items()):
            decision_by_id[product_id] = CandidateFilterDecision(
                product_id=product_id,
                retained=False,
                reason_code="USER_EXCLUDED_PRODUCT_FEATURE",
                evidence={
                    "feature_id": feature_id,
                    "policy": ProductFeaturePolicy.EXCLUDE.value,
                },
            )
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
            self._intent_with_feature_policies(runtime),
            as_of=runtime.as_of,
            subscription_date=runtime.subscription_date,
            audit=runtime.audit,
            product_contribution_choices=self._product_choice_map(runtime),
        )
        runtime.structured_evaluations = (
            {**runtime.structured_evaluations, **evaluated} if merge else evaluated
        )
        # Text reviews are a projection over the deterministic evaluations. A
        # recalculation always starts from that clean base and reapplies only a
        # cache entry whose product/text/fact snapshot key is still current.
        runtime.evaluations = dict(runtime.structured_evaluations)

    @staticmethod
    def _intent_with_feature_policies(
        runtime: _SearchRuntime,
    ) -> ProductSearchIntent:
        soft_preferences = [
            Preference(
                field=feature_id,
                preference=PreferenceValue.PREFER_ABSENT,
            )
            for feature_id, policy in runtime.feature_policies.items()
            if policy == ProductFeaturePolicy.PREFER_ABSENT
        ]
        if not soft_preferences:
            return runtime.intent
        existing = {
            item.field: item
            for item in runtime.intent.preferences
            if item.field not in {preference.field for preference in soft_preferences}
        }
        existing.update({item.field: item for item in soft_preferences})
        return runtime.intent.model_copy(
            update={"preferences": list(existing.values())},
            deep=True,
        )

    def _rank(self, runtime: _SearchRuntime) -> None:
        self._rank_once(runtime)
        self._review_top_ranked_eligibility_text(runtime)

    def _rank_once(self, runtime: _SearchRuntime) -> None:
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
        # A full question re-selection invalidates any conversational assumption
        # that was attached to the previous active question.
        runtime.pending_assumption_answer = None
        runtime.pending_assumption_original_question = None

        if self.pre_search_enabled:
            question = self.pre_search_question_planner.select_next(
                runtime.pre_search_profile,
                runtime.intent,
                runtime.candidate_products.values(),
            )
            if question is not None:
                runtime.active_question = question
                self._refresh_ranking_stability(runtime, material_question=True)
                runtime.question_history[question.question_id] = question
                runtime.session = runtime.session.model_copy(
                    update={
                        "active_question_id": question.question_id,
                        "status": SearchSessionStatus.QUESTIONING,
                        "completion_reason": None,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    deep=True,
                )
                return

        # Ask only after general conditions have been resolved: the resulting
        # top three is the set the user is actually being asked to accept.
        question = self._random_promotion_preference_question(runtime)
        if question is not None:
            runtime.active_question = question
            self._refresh_ranking_stability(runtime, material_question=True)
            runtime.question_history[question.question_id] = question
            runtime.session = runtime.session.model_copy(
                update={
                    "active_question_id": question.question_id,
                    "status": SearchSessionStatus.QUESTIONING,
                    "completion_reason": None,
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
            return

        question = self.question_planner.select_next(
            runtime.evaluations,
            runtime.intent,
            answered_question_ids=set(runtime.session.answered_question_ids),
            suppressed_question_ids=runtime.skipped_question_ids,
            suppressed_rule_ids=self._random_promotion_rule_ids(runtime),
            acknowledged_question_families={
                variable_id
                for variable_id, state in runtime.condition_states.items()
                if state.status != "NOT_ASKED"
            },
            audit=runtime.audit,
        )
        question = self._institution_account_question(runtime, question)
        if question is not None:
            question = self._with_question_context(runtime, question)
        runtime.active_question = question
        self._refresh_ranking_stability(runtime, material_question=question is not None)
        if question is None:
            if not runtime.evaluations or all(
                item.eligibility_status == EvaluationStatus.UNSATISFIABLE
                for item in runtime.evaluations.values()
            ):
                completion_reason = "INSUFFICIENT_ELIGIBLE_PRODUCTS"
            elif runtime.skipped_question_ids:
                completion_reason = "PROVISIONAL_USER_STOPPED"
            elif runtime.ranking is not None and runtime.ranking.stability.stable:
                completion_reason = "STABLE_TOP3"
            else:
                completion_reason = "PROVISIONAL_DATA_INCOMPLETE"
            runtime.session = runtime.session.model_copy(
                update={
                    "active_question_id": None,
                    "status": SearchSessionStatus.RANKING_READY,
                    "completion_reason": completion_reason,
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
                "completion_reason": None,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )

    def _random_promotion_preference_question(
        self,
        runtime: _SearchRuntime,
    ) -> PlannedQuestion | None:
        """Ask once before presenting a top-three chance-based rate as a fit."""

        if runtime.ranking is None or "RANDOM_PROMOTION_RATE" in runtime.feature_policies:
            return None
        top_three = runtime.ranking.ordered_product_ids[:3]
        affected = [
            product_id
            for product_id in top_three
            if (
                (product := runtime.candidate_products.get(product_id)) is not None
                and feature_present(product, "RANDOM_PROMOTION_RATE")
            )
        ]
        if not affected:
            return None
        question_id = "QUESTION-" + canonical_hash(
            {"random_promotion_rate": affected}
        )[:16]
        if question_id in runtime.session.answered_question_ids:
            return None
        names = [runtime.candidate_products[product_id].name for product_id in affected]
        product_label = ", ".join(names)
        return PlannedQuestion(
            question_id=question_id,
            question_kind="PROMOTION_RISK_PREFERENCE",
            question=(
                f"현재 조건에서 3위 안에 들 수 있는 {product_label}은(는) 당첨 결과에 따라 "
                "최고금리가 달라지는 확률형 상품이에요. 이런 상품도 추천에 포함할까요?"
            ),
            affected_product_ids=affected,
            score=Decimal("900000000000"),
            answer_mode="OPTIONS",
            answer_examples=["네, 확률형 상품도 볼게요.", "아니요, 확률형 상품은 제외해 주세요."],
            explanation="공식 최고금리는 비교에 반영하지만, 당첨으로 받는 우대금리는 보장되지 않습니다.",
            explanation_details={"feature_id": "RANDOM_PROMOTION_RATE"},
        )

    @staticmethod
    def _random_promotion_rule_ids(runtime: _SearchRuntime) -> set[str]:
        """Rules that are resolved by a draw, never by a customer answer."""

        return {
            rule_id
            for product in runtime.candidate_products.values()
            for rule_id in random_promotion_rule_ids(product)
        }

    def _handle_random_promotion_preference(
        self,
        runtime: _SearchRuntime,
        *,
        message: str,
    ) -> ConversationTurnResult:
        """Resolve the deterministic top-three chance-product preference."""

        negative = ("아니", "제외", "싫", "빼", "안 볼", "보지 않")
        positive = ("네", "괜찮", "포함", "볼게", "상관없")
        normalized = message.replace(" ", "")
        if any(token in normalized for token in negative):
            policy = ProductFeaturePolicy.EXCLUDE
            assistant_message = "확률형 우대금리가 있는 상품은 추천에서 제외했어요."
        elif any(token in normalized for token in positive):
            policy = ProductFeaturePolicy.ALLOW
            assistant_message = "확률형 상품도 포함해 볼게요. 당첨 시 최고금리라는 점은 계속 표시하겠습니다."
        else:
            return ConversationTurnResult(
                search_session_id=runtime.session.search_session_id,
                message=message,
                operations_executed=[ConversationOperation.NO_OP],
                session=runtime.session,
                next_question=runtime.active_question,
                assistant_message="확률형 상품을 포함할지, 제외할지 말씀해 주세요.",
            )

        self._commit_answered_question(runtime, runtime.active_question.question_id)
        runtime.feature_policies["RANDOM_PROMOTION_RATE"] = policy
        self._run_pipeline(runtime)
        return ConversationTurnResult(
            search_session_id=runtime.session.search_session_id,
            message=message,
            operations_executed=[ConversationOperation.SET_PRODUCT_FEATURE_POLICY],
            session=runtime.session,
            next_question=runtime.active_question,
            recommendations=None,
            assistant_message=assistant_message,
        )

    def _institution_account_question(
        self,
        runtime: _SearchRuntime,
        product_question: PlannedQuestion | None,
    ) -> PlannedQuestion | None:
        """Ask one current-account question per institution before product rows.

        A "no" answer safely clears only current product account limits for
        that institution.  A "yes" answer deliberately falls through to the
        product-specific question next, because it does not identify which
        product account is held.
        """

        if (
            product_question is None
            or product_question.request is None
            or not product_question.request.fact_type.startswith(
                "EXISTING_PRODUCT_ACCOUNT_LIMIT_REACHED::"
            )
            or not product_question.affected_product_ids
        ):
            return product_question
        product = runtime.candidate_products.get(product_question.affected_product_ids[0])
        if product is None:
            return product_question
        fact_type = f"INSTITUTION_ACCOUNT_HELD::{product.institution_id}"
        if any(fact.fact_type == fact_type for fact in runtime.fact_store.active_facts):
            return product_question
        institution_name = (
            product.metadata.institution_name
            if product.metadata is not None and product.metadata.institution_name
            else product.institution_id
        )
        question_scope = set(
            self.question_planner.frontier_product_ids(
                runtime.evaluations,
                runtime.intent,
            )
        )
        affected_product_ids = [
            item.product_id
            for item in runtime.candidate_products.values()
            if (
                item.institution_id == product.institution_id
                and item.product_id in question_scope
            )
        ]
        if not affected_product_ids:
            affected_product_ids = list(product_question.affected_product_ids)
        request = MissingFactRequest(
            fact_type=fact_type,
            resolution_strategy=ResolutionStrategy.ASK_USER,
            question=(
                f"현재 {institution_name}에 본인 명의로 보유한 계좌가 하나라도 있나요? "
                "없다면 이 금융기관의 1인 1계좌 제한 상품은 신규가입 가능으로 반영할게요."
            ),
            requested_by_rule_id="INSTITUTION_ACCOUNT_HOLDING",
            expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            grounding_terms=[institution_name, "현재 보유 계좌", "1인 1계좌"],
            missing_fact_id=(
                "MISSING-"
                + canonical_hash({"institution_account": product.institution_id})[:16]
            ),
        )
        return PlannedQuestion(
            question_id=(
                "QUESTION-"
                + canonical_hash({"institution_account": product.institution_id})[:16]
            ),
            question_kind="FINANCIAL_FACT",
            request=request,
            question=request.question or "",
            affected_product_ids=affected_product_ids,
            score=product_question.score + Decimal("1"),
            answer_mode="BINARY",
        )

    def _with_question_context(
        self,
        runtime: _SearchRuntime,
        question: PlannedQuestion,
    ) -> PlannedQuestion:
        from eligibility.search.ranking import _institution_name

        products = [
            runtime.candidate_products[product_id]
            for product_id in question.affected_product_ids
            if product_id in runtime.candidate_products
        ]
        labels = [
            f"{(product.metadata.institution_name if product.metadata and product.metadata.institution_name else _institution_name(product.institution_id))} · {product.name}"
            for product in products
        ]
        if not labels:
            product_context = None
        elif len(labels) <= 2:
            product_context = " / ".join(labels)
        else:
            product_context = f"{labels[0]} 외 {len(labels) - 1}개 상품"

        fact_type = question.request.fact_type if question.request is not None else ""
        rendered_question = question.question
        question_stage = None
        answer_mode = question.answer_mode
        confirmation_required = (
            question.request is not None
            and self._rule_purpose(
                products[0] if products else None,
                question.request.requested_by_rule_id,
            )
            == "ELIGIBILITY"
        )
        explanation_details: dict[str, Any] = {
            "fact_type": fact_type or None,
            "product_context": product_context,
            "institutions": sorted(
                {
                    (
                        product.metadata.institution_name
                        if product.metadata and product.metadata.institution_name
                        else _institution_name(product.institution_id)
                    )
                    for product in products
                }
            ),
            "products": [
                {"product_id": product.product_id, "product_name": product.name}
                for product in products
            ],
            "verification_route": (
                question.request.required_source
                if question.request is not None
                else None
            ),
            "source": None,
            "known_data": [],
            "data_gaps": [],
        }
        rule_source = (
            self._rule_source_reference(
                products[0] if products else None,
                question.request.requested_by_rule_id,
            )
            if question.request is not None
            else None
        )
        if rule_source is not None:
            explanation_details["source"] = rule_source
        external_profile_check = (
            question.request is not None
            and question.request.resolution_strategy
            in {
                ResolutionStrategy.QUERY_INSTITUTION,
                ResolutionStrategy.QUERY_MYDATA,
            }
        )
        grounding_terms = (
            question.request.grounding_terms
            if question.request is not None
            else []
        )
        first_transaction_check = (
            question.request is not None
            and is_first_transaction_history_fact(question.request)
        )
        parent_benefit_check = (
            any("부모급여" in term for term in grounding_terms)
            and not fact_type.startswith("EXISTING_PRODUCT_ACCOUNT_LIMIT_REACHED::")
        )
        if first_transaction_check:
            institutions = explanation_details["institutions"]
            institution_label = (
                institutions[0] if len(institutions) == 1 else "해당 금융기관"
            )
            if (
                question.request is not None
                and "FIRST_DEPOSIT_OR_SAVINGS_CUSTOMER"
                in question.request.fact_type.upper()
            ):
                rendered_question = (
                    f"현재 계좌 보유 여부와 별개로, 가입 전까지 {institution_label}의 "
                    "입출금·예금·적금 계좌를 만든 적이 없으신가요?"
                )
            else:
                rendered_question = (
                    f"{institution_label}의 첫거래 우대 기준을 확인할게요. "
                    f"{institution_label}의 해당 거래 이력이 없으신가요?"
                )
            explanation = (
                "이 질문은 현재 계좌를 갖고 있는지와 다른, 과거 거래 이력을 "
                "확인하는 질문이에요. 현재 계좌가 없더라도 예전에 해당 금융기관의 "
                "관련 계좌를 만든 적이 있다면 첫거래 우대에는 해당하지 않을 수 "
                "있습니다. 정확한 이력을 모르겠다면 모르겠다고 답해 주세요."
            )
            explanation_details.update(
                {
                    "condition_definition": "해당 금융기관 첫거래 우대",
                    "known_data": [
                        f"확인 기관: {institution_label}",
                        "확인 내용: 해당 금융기관의 기존 상품 거래 여부",
                    ],
                }
            )
        elif parent_benefit_check:
            rendered_question = question.question
            explanation = (
                "부모급여는 영아 양육 가구에 지급되는 정부 급여예요. 여기서는 그 "
                "제도 자체의 신청 자격을 새로 판정하는 것이 아니라, IBK 공식 "
                "상품조건에 적힌 부모급여 수급·입금 실적을 충족하는지 확인합니다. "
                "현재 수급 여부나 입금 내역을 모르면 모르겠다고 답해 주세요."
            )
            explanation_details.update(
                {
                    "condition_definition": "IBK 부모급여 수급·입금 실적",
                    "context_source": "https://www.mohw.go.kr/menu.es?mid=a10711030600",
                    "known_data": [
                        "정부 제도 맥락: 보건복지부 부모급여",
                        "상품 판정 범위: IBK 공식 상품설명서의 수급·입금 실적",
                    ],
                }
            )
        elif external_profile_check:
            rule_label = self._evaluation_rule_label(
                runtime,
                question.affected_product_ids[0]
                if question.affected_product_ids
                else None,
                question.request.requested_by_rule_id,
            )
            subject = product_context or "현재 비교 중인 상품"
            if not rule_label or rule_label == "공식 가입대상 충족":
                raise ValueError(
                    "Generic official eligibility must be datafied before it can "
                    "be presented as a user question"
                )
            rendered_question = f"{subject}의 '{rule_label}' 조건에 해당하시나요?"
            source = self._rule_source_reference(
                products[0] if products else None,
                question.request.requested_by_rule_id,
            )
            explanation_details.update(
                {
                    "condition_definition": rule_label,
                    "source": source,
                    "known_data": [
                        f"대상 조건: {rule_label}",
                        f"공식 확인 주체: {question.request.required_source or '해당 금융기관'}",
                    ],
                    "data_gaps": [
                        "현재 세션에는 사용자의 기관 조회 결과가 연결되어 있지 않음",
                        "source에 없는 앱 메뉴와 실시간 이벤트 상태는 안내 불가",
                    ],
                }
            )
            source_text = (
                f" 근거는 {source.get('document')}"
                + (
                    f"의 {source.get('section')}"
                    if source.get("section")
                    else ""
                )
                + "입니다."
                if source
                else ""
            )
            if fact_type == "SHINHAN_CARD_HELD":
                rendered_question = (
                    "본인 명의의 신한카드(신용카드 또는 체크카드)를 현재 "
                    "가지고 계신가요?"
                )
                explanation_details.update(
                    {
                        "condition_definition": (
                            "본인 명의 신한카드(신용/체크)를 보유하고, 본인 명의 "
                            "신한은행 입출금통장을 결제계좌로 사용"
                        ),
                        "known_data": [
                            "카드 종류: 본인 명의 신한카드 신용카드 또는 체크카드",
                            "결제계좌: 본인 명의 신한은행 입출금통장",
                            "실적 기준: 1원 이상 결제한 달이 6개월 이상",
                            "인정 기간: 적금 신규월부터 만기 전전월 말까지",
                        ],
                        "data_gaps": [
                            "현재 세션에는 신한카드 보유·결제 데이터가 연결되어 있지 않음"
                        ],
                    }
                )
                explanation = (
                    "여기서 '신한카드 보유'는 본인 명의의 신한 신용카드나 "
                    "체크카드를 현재 가지고 있다는 뜻이에요. 다만 카드 보유만으로 "
                    "우대가 적용되는 것은 아닙니다. 청년 처음적금 가입 후 본인 명의 "
                    "신한은행 입출금통장을 카드 결제계좌로 사용하고, 적금 신규월부터 "
                    "만기 전전월 말까지 1원 이상 결제한 달이 6개월 이상이어야 해요. "
                    f"{source_text.strip()} 이 질문은 그 경로의 첫 전제인 카드 보유 "
                    "여부를 확인하는 질문입니다."
                )
            else:
                explanation = (
                    f"'{rule_label}'을 충족했는지 확인하는 질문이에요.{source_text} "
                    "현재는 은행·마이데이터 조회가 연결되지 않아 해당 금융기관의 공식 "
                    "안내와 본인 거래내역에서 확인해야 합니다. 앱의 정확한 메뉴나 현재 "
                    "이벤트 대상처럼 source에 없는 내용은 추측하지 않으며, 확인하기 "
                    "어려우면 '모르겠어요'를 선택하면 해당 혜택을 제외해 계산합니다."
                )
        elif fact_type == "SUBSCRIPTION_CHANNEL_DIGITAL":
            rendered_question = "인터넷뱅킹이나 모바일뱅킹으로 가입할 예정인가요?"
            explanation = (
                "영업점에서 종이 통장을 발급받는 방식이 아니라, 인터넷뱅킹이나 "
                "모바일뱅킹에서 계좌를 개설할지를 묻는 질문이에요. 비대면으로 "
                "가입할 수 있다면 '네'를 선택하면 됩니다."
            )
        elif fact_type == "KB_CROSS_TRANSACTION_REQUIREMENT_POSSIBLE":
            rendered_question = (
                "가입 3개월이 지난 달에 급여이체 실적을 만들거나, "
                "KB국민카드를 30만원 이상 사용할 수 있나요?"
            )
            explanation = (
                "여기서 교차거래는 막연한 추가 거래가 아니라 둘 중 하나예요. "
                "가입 후 3개월이 지난 달에 KB국민은행이 인정하는 급여이체 실적을 "
                "만들거나, 같은 달 KB국민카드 매입실적 30만원 이상을 채우는 "
                "조건입니다. 둘 다 하지 않을 계획이면 '아니요'를 선택하세요."
            )
        elif fact_type == "QUALIFYING_HOME_LOAN_HELD_THROUGH_SAVINGS_TERMINATION":
            rendered_question = (
                "해당 은행의 주택담보대출이나 전세자금대출을 이미 이용 중이거나, "
                "적금 가입 기간 중 이용해 만기까지 유지할 계획이 있나요?"
            )
            explanation = (
                "적금 가입 전에 해당 은행의 주택담보대출·전세자금대출을 이미 "
                "이용 중이거나, 가입 기간 중 새로 대출받고 적금 만기 시점에도 "
                "그 대출을 보유하는지를 확인하는 조건이에요. 단순히 앞으로 "
                "대출을 알아볼 가능성만 있으면 충족으로 보지 않습니다."
            )
        elif fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE":
            answer_mode = "FREE_TEXT"
            explanation = (
                "현재 후보 상품에 실제로 적용되는 급여이체 우대조건을 확인하는 "
                "상품별 검증 질문이에요. 공통 의향과 달리 해당 상품의 은행, 인정 "
                "방식과 유지기간을 기준으로 답해 주세요."
            )
        elif fact_type == "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE":
            answer_mode = "FREE_TEXT"
            explanation = (
                "현재 후보에 남은 신한은행 상품의 카드 결제실적 우대를 확인하는 질문이에요. "
                "예: '현재 신한카드 대금은 국민은행 계좌에서 빠져나가고, 더 유리하면 "
                "신한은행으로 바꿀 수 있어요'처럼 현재 결제계좌 은행과 변경 의향을 "
                "함께 답하면 됩니다. 신한카드가 없다면 그 사실을 그대로 말해 주세요."
            )
        elif fact_type == "SPECIAL_RATE_COUPON_VALID":
            explanation = (
                "신한은행 '청년 처음적금'의 첫거래 또는 이벤트 우대에 적용되는 "
                "특별금리 우대쿠폰이에요.\n\n"
                "현재 AI가 확인한 데이터\n"
                "- 적용 대상: 신한은행이 발급했고 청년 처음적금에 사용할 수 있는 유효한 쿠폰 보유자\n"
                "- 우대 적용: 첫거래 조건과 이벤트 쿠폰 조건 중 하나를 충족하면 적용\n"
                "- 근거: 청년 처음적금 상품설명서 p.3 '[4] 첫거래 또는 이벤트 우대'\n"
                "- 발급 대상의 세부 기준: 현재 카탈로그에 없음\n"
                "- 발급 기간·발급 수량·잔여 수량: 현재 카탈로그와 실시간 연동에 없음\n"
                "- 정확한 발급 경로: 현재 공식 근거 데이터에 없음\n\n"
                "따라서 지금은 보유 여부만 판정할 수 있고, 대상·기간·수량을 AI가 "
                "추측해서 안내하면 안 됩니다. 이 항목들은 SHINHAN_EVENT_COUPON "
                "서비스 데이터를 추가 수집하거나 신한은행 실시간 연동이 되어야 정확히 안내할 수 있어요."
            )
        elif fact_type == "WILL_KAKAO_M1_MANUAL_DEPOSIT_DAY":
            period = (
                term_summary(resolve_term(products[0], runtime.intent.contribution_plan))
                if products
                else "31일"
            )
            rendered_question = (
                f"{period} 동안 매일 직접 입금하는 것을 꾸준히 할 수 있으세요?"
            )
            explanation = (
                "자동이체 횟수가 아니라 카카오뱅크 앱에서 매일 직접 입금해야 하는 "
                "조건이에요. 하루에 여러 번 입금해도 하루 실적으로 계산되는 조건이므로, "
                f"{period} 동안 하루씩 직접 입금할 수 있는지를 묻습니다."
            )
        elif fact_type == "WILL_KN_TOUCH_DEPOSIT_DAY":
            period = (
                term_summary(resolve_term(products[0], runtime.intent.contribution_plan))
                if products
                else "1개월"
            )
            target = max(
                [
                    int(value)
                    for value in re.findall(
                        r"(\d+)\s*(?:일|회)",
                        " ".join(
                            [
                                question.request.question or "",
                                *question.request.grounding_terms,
                            ]
                        ),
                    )
                ],
                default=25,
            )
            rendered_question = (
                f"{period} 가입 기간 동안 매일 직접 입금해 {target}일 이상 채울 수 있으세요?"
            )
            explanation = (
                f"Touch UP 적금의 가입기간은 {period}이고, 자동이체가 아니라 직접 "
                f"입금한 날짜를 세는 조건이에요. 여기서는 그 기간 안에 {target}일 이상 "
                "직접 입금할 수 있는지를 묻습니다."
            )
        elif fact_type == "WILL_TOSS_MONTHLY_AUTO_TRANSFER_ALL":
            resolved_terms = {
                resolve_term(product, runtime.intent.contribution_plan).model_dump_json()
                for product in products
            }
            term_labels = {
                term_summary(resolve_term(product, runtime.intent.contribution_plan))
                for product in products
            }
            if len(resolved_terms) == 1 and term_labels:
                period = next(iter(term_labels))
                rendered_question = (
                    f"{period} 동안 매달 자동이체가 빠짐없이 실행되도록 유지하실 수 있나요?"
                )
            else:
                rendered_question = (
                    "각 상품의 가입 기간 동안 매달 자동이체가 빠짐없이 실행되도록 "
                    "유지하실 수 있나요?"
                )
            product_context = (
                f"자동이체 공통 조건 · {product_context}"
                if product_context
                else "자동이체 공통 조건"
            )
            explanation = (
                "한 상품만을 위한 질문이 아니라, 같은 자동이체 유지 조건을 쓰는 현재 "
                "후보들에 공통으로 적용되는 질문이에요. 자동이체를 설정하는 것뿐 아니라 "
                "표시된 기간 동안 잔액 부족이나 이체 해지 없이 매월 성공할 수 있는지를 묻습니다."
            )
        elif fact_type == "KBANK_MYKIDS_ELIGIBLE":
            explanation = (
                "케이뱅크 마이키즈 적금은 가입 대상자가 만 17세 미만이고 "
                "마이키즈 서비스 가입 대상이어야 해요. 여기서는 본인이 아는 범위에서 "
                "답하고, 실제 가입 가능 여부는 케이뱅크에서 다시 확인합니다."
            )
        elif "SUPERSOL" in fact_type or "SuperSOL" in question.question:
            explanation = (
                "신한 SuperSOL은 신한금융그룹의 통합 금융 앱이에요. 이 질문은 앱의 "
                "정회원 가입, 최초 로그인과 필요한 기간 동안 회원 유지를 할 수 있는지 묻습니다."
            )
        else:
            subject = product_context or "현재 비교 중인 상품"
            explanation = (
                f"{subject}의 가입 가능 여부나 우대금리에 실제로 영향을 주는 "
                "행동을 확인하는 질문이에요. 질문에 표시된 대상·기간·행동을 "
                "기준으로 답하면 됩니다. 뜻을 더 알고 싶다고 말하면 상품 원문의 "
                "판정 기준을 풀어서 설명해 드립니다."
            )

        reference_links: list[dict[str, str]] = []
        source_for_link = explanation_details.get("source") or rule_source
        source_url = source_for_link.get("source_url") if source_for_link else None
        # Deliberate: a PRODUCT_CONDITION link sends the user to the issuer's own
        # page, never to the comparison service the row was crawled from. Most
        # deposit/savings products are currently Naver-grounded, so this suppresses
        # the "자세히 보기" link for the majority of them -- that is intended, not a
        # gap to close by relaxing the check.
        if source_url and "pay.naver.com/" not in source_url:
            reference_links.append(
                {
                    "kind": "PRODUCT_CONDITION",
                    "label": "자세히 보기",
                    "url": source_url,
                }
            )
        context_source = explanation_details.get("context_source")
        if context_source:
            if not any(item["url"] == context_source for item in reference_links):
                reference_links.append(
                    {
                        "kind": "POLICY_OR_SERVICE",
                        "label": "자세히 보기",
                        "url": context_source,
                    }
                )
        explanation_details["reference_links"] = reference_links
        return question.model_copy(
            update={
                "product_context": product_context,
                "explanation": explanation,
                "explanation_details": explanation_details,
                "question": rendered_question,
                "question_stage": question_stage,
                "answer_mode": answer_mode,
                "confirmation_required": confirmation_required,
            },
            deep=True,
        )

    @staticmethod
    def _rule_source_reference(
        product: ProductDefinition | None,
        rule_id: str,
    ) -> dict[str, Any] | None:
        if product is None:
            return None
        stack = [
            (product.eligibility_rule, None),
            *[(item.rule, None) for item in product.preferential_rules],
            *[(item, None) for item in product.global_guards],
        ]
        while stack:
            node, inherited_source = stack.pop()
            node_source = getattr(node, "source", None) or inherited_source
            if getattr(node, "rule_id", None) == rule_id:
                return (
                    node_source.model_dump(mode="json")
                    if node_source is not None
                    else None
                )
            stack.extend(
                (child_node, node_source)
                for child_node in (getattr(node, "children", None) or [])
            )
            child = getattr(node, "child", None)
            if child is not None:
                stack.append((child, node_source))
            future_achievement = getattr(node, "future_achievement", None)
            capability_rule = (
                getattr(future_achievement, "capability_rule", None)
                if future_achievement is not None
                else None
            )
            if capability_rule is not None:
                stack.append((capability_rule, node_source))
        return None

    @staticmethod
    def _rule_purpose(
        product: ProductDefinition | None,
        rule_id: str,
    ) -> str | None:
        if product is None:
            return None
        stack = [
            product.eligibility_rule,
            *[item.rule for item in product.preferential_rules],
            *product.global_guards,
        ]
        while stack:
            node = stack.pop()
            if getattr(node, "rule_id", None) == rule_id:
                purpose = getattr(node, "purpose", None)
                return purpose.value if purpose is not None else None
            stack.extend(getattr(node, "children", None) or [])
            child = getattr(node, "child", None)
            if child is not None:
                stack.append(child)
            future_achievement = getattr(node, "future_achievement", None)
            capability_rule = (
                getattr(future_achievement, "capability_rule", None)
                if future_achievement is not None
                else None
            )
            if capability_rule is not None:
                stack.append(capability_rule)
        return None

    @staticmethod
    def _evaluation_rule_label(
        runtime: _SearchRuntime,
        product_id: str | None,
        rule_id: str,
    ) -> str | None:
        if product_id is None or product_id not in runtime.evaluations:
            return None
        evaluation = runtime.evaluations[product_id].product_evaluation
        stack = [
            evaluation.eligibility,
            *evaluation.preferential_rule_results,
            *evaluation.global_guard_results,
        ]
        while stack:
            result = stack.pop()
            if result.rule_id == rule_id:
                return result.rule_name
            stack.extend(result.children)
        return None

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
    def _is_unknown_answer(answer: Any) -> bool:
        if answer is None:
            return True
        if not isinstance(answer, str):
            return False
        return answer.strip().casefold() in {
            "unknown",
            "skip",
            "모름",
            "모르겠어요",
            "잘 모르겠어요",
            "잘모르겠어요",
        }

    @staticmethod
    def _is_willing_unspecified_answer(
        question: PlannedQuestion,
        answer: Any,
    ) -> bool:
        """Detect willingness without inventing a numeric threshold.

        This path is intentionally narrow.  Plain boolean conditions still
        accept ``True`` as a real answer; only a numeric/amount question paired
        with a non-numeric willingness phrase becomes ``WILLING_UNSPECIFIED``.
        """

        if question.request is None or not isinstance(answer, str):
            return False
        text = answer.strip().casefold().replace(" ", "")
        if not text or not any(
            token in text
            for token in ("가능", "할게", "괜찮", "문제없", "사용할수")
        ):
            return False
        family_id = RankingAwareQuestionPlanner.question_family_id(question.request)
        numeric_family_markers = (
            "AMOUNT",
            "BALANCE",
            "COUNT",
            "DURATION",
            "LIMIT",
            "SPEND",
            "TERM",
        )
        # Remove the reward disclosure before looking for a threshold number;
        # every rate question may contain "+1.0%p", which is not evidence that
        # the requested user value itself is quantitative.
        prompt = re.sub(r"[+-]?\d+(?:\.\d+)?\s*%p?", "", question.request.question or "")
        if not (
            any(marker in family_id.upper() for marker in numeric_family_markers)
            or re.search(r"\d|금액|만원|원", prompt)
        ):
            return False
        # A response containing a concrete number should be interpreted by the
        # regular answer path (and validated by the evaluator), not suppressed.
        return not bool(re.search(r"\d", answer))

    def _set_condition_state(
        self,
        runtime: _SearchRuntime,
        question: PlannedQuestion,
        *,
        status: str,
        value: Any = None,
        interpreter_version: str = "BACKEND_DIRECT_V1",
    ) -> None:
        request = question.request
        if request is None:
            return
        variable_id = self.question_planner.question_family_id(request)
        state = UserConditionState(
            variable_id=variable_id,
            status=status,
            value=value,
            source_turn_id=runtime.audit.request_id,
            question_id=question.question_id,
            interpreter_version=interpreter_version,
        )
        runtime.condition_states[variable_id] = state
        self._sync_condition_states_to_session(runtime)

    def _sync_condition_state_for_request(
        self,
        runtime: _SearchRuntime,
        request: MissingFactRequest,
        *,
        value: Any,
    ) -> None:
        variable_id = self.question_planner.question_family_id(request)
        runtime.condition_states[variable_id] = UserConditionState(
            variable_id=variable_id,
            status=(
                UserConditionStatus.DECLINED
                if value is False
                else UserConditionStatus.DECLARED_FEASIBLE
            ),
            value=value,
            source_turn_id=runtime.audit.request_id,
            interpreter_version="BACKEND_REVISION_V1",
        )
        self._sync_condition_states_to_session(runtime)

    def _clear_condition_state_for_fact_type(
        self,
        runtime: _SearchRuntime,
        fact_type: str,
    ) -> None:
        matching_questions = [
            question
            for question in runtime.question_history.values()
            if question.request is not None and question.request.fact_type == fact_type
        ]
        variable_ids = {
            self.question_planner.question_family_id(question.request)
            for question in matching_questions
            if question.request is not None
        }
        variable_ids.update(
            self.question_planner.question_family_id(request)
            for request in runtime.request_history.values()
            if isinstance(request, MissingFactRequest) and request.fact_type == fact_type
        )
        for variable_id in variable_ids:
            runtime.condition_states.pop(variable_id, None)
        reopened_question_ids = {
            question.question_id for question in matching_questions
        }
        runtime.skipped_question_ids.difference_update(reopened_question_ids)
        if reopened_question_ids:
            runtime.session = runtime.session.model_copy(
                update={
                    "answered_question_ids": [
                        question_id
                        for question_id in runtime.session.answered_question_ids
                        if question_id not in reopened_question_ids
                    ]
                },
                deep=True,
            )
        self._sync_condition_states_to_session(runtime)

    @staticmethod
    def _sync_condition_states_to_session(runtime: _SearchRuntime) -> None:
        runtime.session = runtime.session.model_copy(
            update={
                "condition_states": [
                    runtime.condition_states[key]
                    for key in sorted(runtime.condition_states)
                ],
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )

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
        source = runtime.structured_evaluations or runtime.evaluations
        for product_id, candidate in source.items():
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
        runtime.structured_evaluations = updated
        runtime.evaluations = dict(updated)

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

    def _mutable_state_summary(self, runtime: _SearchRuntime) -> dict[str, Any]:
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
        if self.pre_search_enabled:
            products = list(runtime.candidate_products.values())
            pre_search_remaining_count = sum(
                1
                for key in QUESTION_ORDER
                if runtime.pre_search_profile[key].answer_status
                == PreSearchAnswerStatus.NOT_ASKED
                and self.pre_search_question_planner._is_applicable(
                    key,
                    products,
                    intent=runtime.intent,
                    profile=runtime.pre_search_profile,
                )
            )
            remaining_question_count = (
                pre_search_remaining_count
                if pre_search_remaining_count
                else len(
                    self.question_planner.score_candidates(
                        runtime.evaluations,
                        runtime.intent,
                        answered_question_ids=set(runtime.session.answered_question_ids),
                        suppressed_question_ids=runtime.skipped_question_ids,
                        suppressed_rule_ids=self._random_promotion_rule_ids(runtime),
                        acknowledged_question_families={
                            variable_id
                            for variable_id, state in runtime.condition_states.items()
                            if state.status != "NOT_ASKED"
                        },
                    )
                )
            )
        else:
            remaining_question_count = len(
                self.question_planner.score_candidates(
                    runtime.evaluations,
                    runtime.intent,
                    answered_question_ids=set(runtime.session.answered_question_ids),
                    suppressed_question_ids=runtime.skipped_question_ids,
                    suppressed_rule_ids=self._random_promotion_rule_ids(runtime),
                    acknowledged_question_families={
                        variable_id
                        for variable_id, state in runtime.condition_states.items()
                        if state.status != "NOT_ASKED"
                    },
                )
            )
        completed_question_count = len(runtime.session.answered_question_ids)
        return {
            "intent": runtime.intent.model_dump(mode="json"),
            "user_declarations": user_declarations,
            "pre_search_profile_answers": [
                {"fact_type": fact_type, "summary": summary}
                for fact_type, summary in sorted(
                    runtime.pre_search_profile_answers.items()
                )
            ],
            "pre_search_typed_facts": [
                {"fact_type": fact_type, "value": value}
                for fact_type, value in sorted(runtime.pre_search_typed_facts.items())
            ],
            "condition_states": [
                state.model_dump(mode="json")
                for state in (
                    runtime.condition_states[key]
                    for key in sorted(runtime.condition_states)
                )
            ],
            "pre_search_profile": [
                entry.model_dump(mode="json")
                for entry in (
                    runtime.pre_search_profile[key]
                    for key in QUESTION_ORDER
                    if key in runtime.pre_search_profile
                )
            ],
            "acknowledged_unknown_answers": [
                {
                    "question_id": question.question_id,
                    "fact_type": (
                        question.request.fact_type
                        if question.request is not None
                        else question.question_kind
                    ),
                    "question": question.question,
                    "affected_product_ids": question.affected_product_ids,
                }
                for question_id in sorted(runtime.skipped_question_ids)
                if (question := runtime.question_history.get(question_id)) is not None
            ],
            "product_contribution_choices": [
                item.model_dump(mode="json")
                for item in sorted(
                    runtime.product_contribution_choices.values(),
                    key=lambda item: (item.product_id, item.field),
                )
            ],
            "excluded_product_ids": list(runtime.session.excluded_product_ids),
            "feature_policies": {
                feature_id: policy.value
                for feature_id, policy in runtime.feature_policies.items()
            },
            "decision_ledger": [
                {
                    **asdict(entry),
                    "record_status": entry.record_status.value,
                }
                for entry in runtime.decision_ledger.entries
            ],
            "search_progress": {
                "recalculation_round": (
                    1
                    + len(runtime.session.answered_question_ids)
                    + max(0, runtime.session.intent_version - 1)
                ),
                "catalog_product_count": len(self.products),
                "candidate_product_count": len(runtime.candidate_products),
                "viable_product_count": (
                    len(runtime.ranking.ordered_product_ids)
                    if runtime.ranking is not None
                    else 0
                ),
                "visible_top_k_count": (
                    len(runtime.ranking.items) if runtime.ranking is not None else 0
                ),
                "visible_top_product_ids": (
                    [item.product_id for item in runtime.ranking.items]
                    if runtime.ranking is not None
                    else []
                ),
                "ranking_stable": (
                    runtime.ranking.stability.stable
                    if runtime.ranking is not None
                    else False
                ),
                "has_next_question": runtime.active_question is not None,
                "completed_question_count": completed_question_count,
                "estimated_total_question_count": (
                    completed_question_count + remaining_question_count
                ),
                "estimate_is_dynamic": True,
            },
        }

    @staticmethod
    def _record_pre_search_profile_answer(
        runtime: _SearchRuntime,
        question: PlannedQuestion,
        answer: Any,
    ) -> None:
        if question.request is None:
            return
        fact_type = question.request.fact_type
        if answer is True:
            summary = "필요하면 변경 가능"
        elif answer is False:
            summary = "변경하지 않음"
        else:
            summary = "확인 필요"
        runtime.pre_search_profile_answers[fact_type] = summary

    def _conversation_context(self, runtime: _SearchRuntime) -> dict[str, Any]:
        condition_variable_ids = sorted(
            self._condition_requests_by_variable(runtime)
        )
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

        active_question = None
        if runtime.active_question is not None:
            question = runtime.active_question
            active_question = {
                "question_id": question.question_id,
                "question_kind": question.question_kind,
                "question": question.question,
                "explanation": question.explanation,
                "explanation_details": question.explanation_details,
                "answer_examples": question.answer_examples,
                "grounding_terms": (
                    question.request.grounding_terms
                    if question.request is not None
                    else []
                ),
                "fact_type": (
                    question.request.fact_type if question.request is not None else None
                ),
                "condition_variable_id": (
                    self.question_planner.question_family_id(question.request)
                    if question.request is not None
                    else None
                ),
                "request_reference": (
                    self._request_reference(question.request)
                    if question.request is not None
                    else None
                ),
                "expected_semantic_type": (
                    question.request.expected_semantic_type.value
                    if question.request is not None
                    and question.request.expected_semantic_type is not None
                    else None
                ),
                "ranking_input": (
                    question.ranking_input.model_dump(mode="json")
                    if question.ranking_input is not None
                    else None
                ),
                "feasibility_clarification": (
                    question.feasibility_clarification.model_dump(mode="json")
                    if question.feasibility_clarification is not None
                    else None
                ),
                "affected_product_ids": question.affected_product_ids,
                "product_context": question.product_context,
                "pre_search_key": question.pre_search_key,
                "pending_assumption_answer": runtime.pending_assumption_answer,
                "question_spec": (
                    question.question_spec.model_dump(mode="json")
                    if question.question_spec is not None
                    else None
                ),
            }
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
            for item in runtime.ranking.items:
                product = runtime.candidate_products.get(item.product_id)
                if product is None:
                    continue
                top_k_summary.append(
                    {
                        "rank": item.rank,
                        "product_id": item.product_id,
                        "product_name": product.name,
                    }
                )
        pending_workflow = []
        for question in self._pending_questions(runtime):
            presentation = runtime.question_presentation.get(question.question_id, {})
            pending_workflow.append(
                {
                    "question_id": question.question_id,
                    "question_key": question.pre_search_key,
                    "question": question.question,
                    "status": "PENDING",
                    "mandatory": question.pre_search_key
                    in {
                        YOUTH_POLICY_ACCOUNT_HOLDING,
                        SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY,
                    },
                    "last_presented_turn": presentation.get("last_presented_turn"),
                    "deferred_count": presentation.get("deferred_count", 0),
                    "affected_product_ids": list(question.affected_product_ids),
                    "condition_variable_id": (
                        self.question_planner.question_family_id(question.request)
                        if question.request is not None
                        else None
                    ),
                }
            )
        visible_product_ids: list[str] = []
        for item in runtime.visible_dialogue:
            product_id = item.get("product_id")
            if isinstance(product_id, str):
                visible_product_ids.append(product_id)
            visible_product_ids.extend(item.get("product_ids") or [])
        if runtime.active_question is not None:
            visible_product_ids.extend(runtime.active_question.affected_product_ids)
        referenced_product_evidence = []
        for product_id in dict.fromkeys(visible_product_ids):
            product = self.products.get(product_id)
            if product is None:
                continue
            referenced_product_evidence.append(
                {
                    "product_id": product.product_id,
                    "product_name": product.name,
                    "product_type": product.product_type,
                    "typed_features": [
                        feature.model_dump(mode="json")
                        for feature in (
                            product.metadata.features
                            if product.metadata is not None
                            else []
                        )
                    ],
                    "preferential_application_mode": (
                        product.normalized.return_policy.get(
                            "preferential_application",
                            {},
                        ).get("mode")
                        if product.normalized is not None
                        else None
                    ),
                }
            )
        top_ranked_eligibility_evidence: list[dict[str, Any]] = []
        if runtime.ranking is not None:
            pending_eligibility_question_ids = {
                question.question_id
                for question in self._pending_questions(runtime)
                if question.request is not None
                and question.request.fact_type.startswith("ELIGIBILITY_TEXT::")
            }
            for item in self._eligibility_text_frontier(runtime):
                product = runtime.candidate_products[item.product_id]
                candidate = runtime.evaluations[item.product_id]
                normalized = product.normalized
                top_ranked_eligibility_evidence.append(
                    {
                        "rank": item.rank,
                        "product_id": product.product_id,
                        "product_name": product.name,
                        "eligibility_text": eligibility_text(product),
                        "structured_evaluation_status": (
                            runtime.structured_evaluations[product.product_id]
                            .eligibility_status.value
                        ),
                        "eligibility_text_review_status": (
                            candidate.eligibility_text_review_status.value
                            if candidate.eligibility_text_review_status is not None
                            else None
                        ),
                        "eligibility_text_review_reason_code": (
                            candidate.eligibility_text_review_reason_code
                        ),
                        "pending_question_ids": sorted(
                            question_id
                            for question_id in pending_eligibility_question_ids
                            if runtime.question_history.get(question_id) is not None
                            and product.product_id
                            in runtime.question_history[question_id].affected_product_ids
                        ),
                        "product_version": (
                            normalized.version if normalized is not None else None
                        ),
                        "eligibility_text_fingerprint": (
                            candidate.eligibility_text_review_fingerprint
                        ),
                    }
                )
        ledger_rows = []
        for entry in runtime.decision_ledger.entries:
            row = asdict(entry)
            row["record_status"] = entry.record_status.value
            ledger_rows.append(row)
        return {
            "SESSION_ORIGIN": deepcopy(runtime.session_origin),
            "CURRENT_STATE_SNAPSHOT": {
                "intent": runtime.intent.model_dump(mode="json"),
                "pre_search_profile": {
                    key: entry.model_dump(mode="json")
                    for key, entry in runtime.pre_search_profile.items()
                },
                "global_facts": {
                    "pre_search_typed_facts": dict(runtime.pre_search_typed_facts),
                    "user_declarations": mutable_facts,
                },
                "feature_policies": {
                    feature_id: policy.value
                    for feature_id, policy in runtime.feature_policies.items()
                },
                "excluded_product_ids": list(runtime.session.excluded_product_ids),
                "product_contribution_choices": [
                    item.model_dump(mode="json")
                    for item in runtime.product_contribution_choices.values()
                ],
                "pending_question_ids": [
                    item["question_id"] for item in pending_workflow
                ],
                "condition_variable_ids": condition_variable_ids,
                "current_candidate_ids": list(runtime.candidate_products),
                "result_stability": (
                    runtime.ranking.stability.model_dump(mode="json")
                    if runtime.ranking is not None
                    else None
                ),
            },
            "DECISION_LEDGER": ledger_rows,
            "PENDING_WORKFLOW": pending_workflow,
            "VISIBLE_DIALOGUE": deepcopy(runtime.visible_dialogue[-10:]),
            "REFERENCED_PRODUCT_EVIDENCE": referenced_product_evidence,
            "TOP_RANKED_ELIGIBILITY_EVIDENCE": top_ranked_eligibility_evidence,
            "_PRODUCT_EVIDENCE_INDEX": {
                product.product_id: {
                    "product_id": product.product_id,
                    "product_name": product.name,
                    "product_type": product.product_type,
                    "typed_features": [
                        feature.model_dump(mode="json")
                        for feature in (
                            product.metadata.features
                            if product.metadata is not None
                            else []
                        )
                    ],
                    "preferential_application_mode": (
                        product.normalized.return_policy.get(
                            "preferential_application",
                            {},
                        ).get("mode")
                        if product.normalized is not None
                        else None
                    ),
                }
                for product in self.products.values()
            },
            "ALLOWED_OPERATIONS": {
                "operation_ids": [item.value for item in ConversationOperation],
                "feature_ids": ["LOTTERY_BASED_BENEFIT"],
                "feature_policies": [item.value for item in ProductFeaturePolicy],
                "product_ids": list(self.products),
                "institution_ids": sorted(
                    {product.institution_id for product in self.products.values()}
                ),
                "pending_question_ids": [
                    item["question_id"] for item in pending_workflow
                ],
                "reversible_decision_ids": [
                    entry.decision_id
                    for entry in runtime.decision_ledger.active_entries
                    if entry.reversible
                ],
            },
            "SEARCH_SESSION_SUMMARY": {
                "search_session_id": runtime.session.search_session_id,
                "intent_version": runtime.session.intent_version,
                "status": runtime.session.status.value,
            },
            "SEARCH_WORKING_NOTE": {
                "recent_user_messages": list(runtime.recent_user_messages),
            },
            "CURRENT_SEARCH_INTENT": runtime.intent.model_dump(mode="json"),
            "PRE_SEARCH_PROFILE": {
                key: entry.model_dump(mode="json")
                for key, entry in runtime.pre_search_profile.items()
            },
            "MUTABLE_SEARCH_STATE": {
                "intent": runtime.intent.model_dump(
                    mode="json",
                    exclude={
                        "search_intent_id",
                        "user_id",
                        "source_utterances",
                        "created_at",
                        "updated_at",
                    },
                ),
                "user_declarations": mutable_facts,
                "pre_search_typed_facts": dict(runtime.pre_search_typed_facts),
                "product_contribution_choices": [
                    item.model_dump(mode="json")
                    for item in runtime.product_contribution_choices.values()
                ],
                "excluded_product_ids": list(runtime.session.excluded_product_ids),
                "excluded_institution_ids": list(runtime.intent.excluded_institution_ids),
                "acknowledged_unknown_question_ids": sorted(runtime.skipped_question_ids),
            },
            "AUTHORITATIVE_FACT_SUMMARY": authoritative_facts,
            "REQUIREMENT_INDEX_SUMMARY": {
                "compiler_version": self.requirement_compilation.compiler_version,
                "metrics": self._requirement_metrics_summary(),
                "active_review_statuses": ["VERIFIED"],
                "shadow_only_review_statuses": ["REVIEW_REQUIRED"],
            },
            "ACTIVE_QUESTION": active_question,
            "RECENT_PRODUCT_FOCUS": recent_product_ids,
            "TOP_K_SUMMARY": top_k_summary,
            "PRODUCT_CATALOG_SUMMARY": [
                f"{product.product_id}|{product.institution_id}|{product.name}"
                for product in self.products.values()
            ],
            "INSTITUTION_CATALOG_SUMMARY": [
                {
                    "institution_id": institution_id,
                    "institution_name": institution_name,
                }
                for institution_id, institution_name in sorted(
                    {
                        (
                            product.institution_id,
                            (
                                product.metadata.institution_name
                                if product.metadata is not None
                                and product.metadata.institution_name
                                else _institution_name(product.institution_id)
                            ),
                        )
                        for product in self.products.values()
                    }
                )
            ],
            "ALLOWED_APPLICATION_OPERATIONS": [
                item.value
                for item in ConversationOperation
                if item not in {
                    ConversationOperation.SUBMIT_PRE_SEARCH_ANSWER,
                    ConversationOperation.ACKNOWLEDGE_PRE_SEARCH_UNKNOWN,
                }
            ],
        }

    def _requirement_metrics_summary(self) -> dict[str, Any]:
        """Compact observability view; never inject large ID lists into prompts."""

        metrics = self.requirement_compilation.metrics
        return {
            "product_count": metrics.product_count,
            "source_condition_count": metrics.source_condition_count,
            "compiled_requirement_count": metrics.compiled_requirement_count,
            "verified_count": metrics.verified_count,
            "review_required_count": metrics.review_required_count,
            "rejected_count": metrics.rejected_count,
            "complete_count": metrics.complete_count,
            "partial_count": metrics.partial_count,
            "unavailable_count": metrics.unavailable_count,
            "unmapped_count": metrics.unmapped_count,
            "scope_counts": dict(metrics.scope_counts),
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
            "structured_evaluations",
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
            "skipped_question_ids",
            "pre_search_profile_answers",
            "pre_search_typed_facts",
            "pre_search_profile",
            "condition_states",
            "recent_user_messages",
            "working_note_turn_sequence",
            "pending_assumption_answer",
            "pending_assumption_original_question",
            "session_origin",
            "visible_dialogue",
            "feature_policies",
            "decision_ledger",
            "decision_sequence",
            "changeset_sequence",
            "question_presentation",
            "defer_pipeline",
            "pipeline_requested_while_deferred",
            "eligibility_text_review_cache",
            "eligibility_text_review_active_keys",
            "eligibility_text_review_state",
            "eligibility_text_review_rounds",
            "eligibility_text_review_pending_count",
            "eligibility_text_review_frontier_ids",
            "eligibility_text_review_assistant_message",
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

    def _validate_operation_targets(
        self,
        runtime: _SearchRuntime,
        actions: list[ConversationAction],
        *,
        message: str,
    ) -> None:
        scoped = [action for action in actions if action.product_id is not None or (
            action.intent_patch is not None and (
                action.intent_patch.upsert_excluded_institution_ids
                or action.intent_patch.remove_excluded_institution_ids
            )
        )]
        if not scoped:
            return
        product_ids, institution_ids = grounded_targets(message, self._conversation_context(runtime))
        for action in scoped:
            if action.product_id is not None:
                if action.product_id not in self.products:
                    raise KeyError(f"Unknown product_id: {action.product_id}")
                if action.product_id not in product_ids:
                    raise ValueError("Product update was not grounded in the current user message or an unambiguous conversational reference")
            if action.intent_patch is not None:
                targets = set(action.intent_patch.upsert_excluded_institution_ids) | set(action.intent_patch.remove_excluded_institution_ids)
                if targets - institution_ids:
                    raise ValueError("Institution update was not grounded in the current user message")

    def _execute_conversation_action(
        self,
        runtime: _SearchRuntime,
        action: ConversationAction,
        *,
        message: str | None = None,
    ) -> None:
        self._validate_operation_targets(runtime, [action], message=message or "")
        session_id = runtime.session.search_session_id
        if action.operation == ConversationOperation.UPDATE_SEARCH_INTENT:
            assert action.intent_patch is not None
            self.update_search_intent(
                session_id,
                patch=self._canonicalize_institution_constraint_patch(
                    action.intent_patch
                ),
                utterance=message,
            )
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
        if action.operation == ConversationOperation.SET_PRODUCT_FEATURE_POLICY:
            assert action.feature_id is not None
            assert action.feature_policy is not None
            runtime.feature_policies[action.feature_id] = action.feature_policy
            runtime.pipeline_requested_while_deferred = True
            return
        if action.operation == ConversationOperation.CLEAR_PRODUCT_FEATURE_POLICY:
            assert action.feature_id is not None
            runtime.feature_policies.pop(action.feature_id, None)
            runtime.pipeline_requested_while_deferred = True
            return
        if action.operation == ConversationOperation.REVERT_DECISIONS:
            raise ValueError("REVERT_DECISIONS must be executed by the turn changeset")
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
            active_question = runtime.active_question
            answer = action.answer
            if hasattr(answer, "model_dump"):
                answer = answer.model_dump(mode="python", exclude_none=True)
            # The conversation turn performs one authoritative full pipeline pass
            # after all actions. Avoid generating an intermediate question here;
            # it would be discarded immediately and adds an unnecessary LLM call.
            self.submit_user_answer(session_id, answer=answer, select_next=False)
            if (
                active_question is not None
                and active_question.request is not None
                and active_question.request.fact_type
                in {
                    "SALARY_ACCOUNT_CHANGE_POSSIBLE",
                    "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE",
                }
                and action.rationale
            ):
                runtime.pre_search_profile_answers[
                    active_question.request.fact_type
                ] = action.rationale.strip()
                bank = self._extract_bank_name(message or action.rationale)
                if bank is not None:
                    current_fact_type = (
                        "CURRENT_SALARY_BANK"
                        if active_question.request.fact_type
                        == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
                        else "CURRENT_CARD_PAYMENT_BANK"
                        if active_question.request.fact_type
                        == "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE"
                        else None
                    )
                    if current_fact_type is not None:
                        runtime.pre_search_typed_facts[current_fact_type] = bank
            return
        if action.operation == ConversationOperation.PROPOSE_ACTIVE_QUESTION_ANSWER:
            assert isinstance(action.answer, bool)
            assert action.confirmation_question is not None
            self.propose_active_question_answer(
                session_id,
                proposed_answer=action.answer,
                confirmation_question=action.confirmation_question,
            )
            return
        if action.operation == ConversationOperation.SKIP_ACTIVE_QUESTION:
            self.skip_active_question(session_id, select_next=False)
            return
        if action.operation == ConversationOperation.EXPLAIN_ACTIVE_QUESTION:
            if runtime.active_question is None:
                raise ValueError("There is no active question to explain")
            return
        if action.operation == ConversationOperation.EXCLUDE_PRODUCT:
            assert action.product_id is not None
            self.exclude_product(session_id, product_id=action.product_id)
            return
        if action.operation == ConversationOperation.NO_OP:
            return
        raise ValueError(f"Unsupported conversation operation: {action.operation}")

    @staticmethod
    def _canonicalize_institution_constraint_patch(patch: IntentPatch) -> IntentPatch:
        """Translate known LLM aliases into the retriever's typed vocabulary."""

        field_aliases = {
            "INSTITUTION_SCOPE": "INSTITUTION_SECTOR",
            "INSTITUTION_TYPE": "INSTITUTION_SECTOR",
            "FINANCIAL_INSTITUTION_TYPE": "INSTITUTION_SECTOR",
            "DEPOSIT_PROTECTED": "DEPOSIT_PROTECTION",
            "PRINCIPAL_PROTECTED": "DEPOSIT_PROTECTION",
            "PROTECTION": "DEPOSIT_PROTECTION",
            "FEE_FREE": "FEE_WAIVER_AVAILABLE",
            "TRANSFER_FEE_FREE": "FEE_WAIVER_AVAILABLE",
            "ATM_FEE_FREE": "FEE_WAIVER_AVAILABLE",
            "CMA_TYPE": "PRODUCT_SUBTYPE",
            "RATE_RETURN_KIND": "RETURN_KIND",
        }
        expected_aliases = {
            "PRIMARY_FINANCIAL_INSTITUTION": "BANK",
            "FIRST_SECTOR": "BANK",
            "FIRST_SECTOR_ONLY": "BANK",
            "BANKS_AND_SAVINGS_BANKS": "BANK|SAVINGS_BANK",
            "BANKS_AND_SECURITIES": "BANK|SECURITIES",
            "SECURITIES_ONLY": "SECURITIES",
        }
        constraints: list[HardConstraint] = []
        for item in patch.upsert_hard_constraints:
            source_field = item.field.strip().upper()
            field = field_aliases.get(source_field, source_field)
            expected = item.expected
            if field == "INSTITUTION_SECTOR" and isinstance(expected, str):
                normalized_expected = expected.strip().upper()
                expected = expected_aliases.get(normalized_expected, normalized_expected)
            elif field in {
                "PRODUCT_SUBTYPE",
                "RETURN_KIND",
                "PROTECTION_STATUS",
                "REINVESTMENT_MODE",
                "BALANCE_TIER_METHOD",
                "FUNDING_TYPE",
                "CONTRIBUTION_FREQUENCY",
                "INTEREST_PAYMENT_METHOD",
                "CUSTOMER_SCOPE",
                "APPLICATION_CAPACITY",
            } and isinstance(expected, str):
                expected = expected.strip().upper()
            constraints.append(
                item.model_copy(
                    update={"field": field, "expected": expected},
                    deep=True,
                )
            )
        remove_keys = [
            field_aliases.get(key.strip().upper(), key.strip().upper())
            for key in patch.remove_hard_constraint_keys
        ]
        return patch.model_copy(
            update={
                "upsert_hard_constraints": constraints,
                "remove_hard_constraint_keys": list(dict.fromkeys(remove_keys)),
            },
            deep=True,
        )

    @staticmethod
    def _extract_bank_name(message: str) -> str | None:
        aliases = (
            (("국민은행", "KB국민", "국민"), "KB국민은행"),
            (("신한은행", "신한"), "신한은행"),
            (("카카오뱅크", "카카오"), "카카오뱅크"),
            (("토스뱅크", "토스"), "토스뱅크"),
            (("기업은행", "IBK"), "IBK기업은행"),
            (("하나은행", "하나"), "하나은행"),
            (("우리은행", "우리"), "우리은행"),
            (("케이뱅크", "K뱅크"), "케이뱅크"),
            (("부산은행", "BNK부산"), "BNK부산은행"),
            (("경남은행", "BNK경남"), "BNK경남은행"),
        )
        found = [
            (message.find(alias), canonical)
            for names, canonical in aliases
            for alias in names
            if alias in message
        ]
        return min(found, key=lambda item: item[0])[1] if found else None

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
        runtime.audit.emit(
            "TOP3_FRONTIER_PLANNER",
            AuditEventType.TOP_K_STABILITY_CHECKED,
            output_data=stability,
            payload={
                "visible_top3_product_ids": runtime.ranking.ordered_product_ids[:3],
                "legacy_frontier_product_ids": self.question_planner.frontier_product_ids(
                    runtime.evaluations,
                    runtime.intent,
                ),
                "requirement_index_mode": "SHADOW_REVIEWED_ONLY",
                "verified_requirement_count": (
                    self.requirement_compilation.metrics.verified_count
                ),
                "material_question_remaining": material_question,
            },
        )

    def _materialize_capabilities(
        self,
        runtime: _SearchRuntime,
        *,
        persist_answer_records: bool = True,
        emit_supersession_audit: bool = True,
    ) -> UserFactStore:
        """Synchronize intent capabilities into effective USER_DECLARED facts.

        v0.4.2 treats an explicit capability update as a revision of the same
        semantic user declaration.  Prior USER_DECLARED SELF_REPORTED/FUTURE_INTENT
        facts of the mapped ``fact_type`` are superseded even when they originated
        from a different question reference.  Authoritative MyData/institution
        observations are never rewritten.
        """

        store = self._materialize_subscription_date(runtime, runtime.fact_store)
        store = self._materialize_application_capacity(runtime, store)
        store = self._materialize_institution_product_holding_history(runtime, store)
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

            if emit_supersession_audit:
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

        if persist_answer_records:
            runtime.answer_records = self._answer_records(store)
        return store

    def _materialize_institution_product_holding_history(
        self,
        runtime: _SearchRuntime,
        store: UserFactStore,
    ) -> UserFactStore:
        """Apply declared prior bank-product holdings to first-transaction rules.

        Selecting a bank means the customer cannot qualify for that bank's
        *absence-of-prior-products* benefit.  We only materialize ``False``:
        not selecting a bank is incomplete information and must never grant a
        first-transaction benefit.
        """

        entry = runtime.pre_search_profile.get(INSTITUTION_PRODUCT_HOLDING_HISTORY)
        selected_names = (
            entry.value.get("prior_product_holding_institutions", [])
            if entry is not None and entry.answer_status == PreSearchAnswerStatus.ANSWERED
            else []
        )
        selected = {
            self._institution_history_name_key(name)
            for name in selected_names
            if isinstance(name, str) and name.strip()
        }
        prefix = (
            "PRESEARCH-INSTITUTION-PRODUCT-HOLDING/"
            f"{runtime.session.search_session_id}/"
        )
        rewritten = [
            fact.model_copy(
                update={"record_status": FactRecordStatus.SUPERSEDED},
                deep=True,
            )
            if (
                fact.record_status == FactRecordStatus.ACTIVE
                and fact.request_reference is not None
                and fact.request_reference.startswith(prefix)
            )
            else fact
            for fact in store.facts
        ]
        updated = store.model_copy(update={"facts": rewritten}, deep=True)
        if not selected:
            return updated

        fact_types: set[str] = set()
        for product in self.products.values():
            metadata = product.metadata
            if (
                metadata is None
                or metadata.institution_sector not in {"BANK", "SAVINGS_BANK"}
                or self._institution_history_name_key(metadata.institution_name)
                not in selected
            ):
                continue
            fact_types.update(self._product_holding_fact_types(product))

        for fact_type in sorted(fact_types):
            reference = f"{prefix}{fact_type}"
            payload = {
                "request_reference": reference,
                "fact_type": fact_type,
                "value": False,
            }
            fact = UserFact(
                fact_id=f"PRESEARCH-{canonical_hash(payload)[:20]}",
                user_id=updated.user_id,
                fact_type=fact_type,
                value=False,
                valid_from=runtime.as_of,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                provenance=[
                    FactProvenance(
                        reference=reference,
                        description=(
                            "은행·저축은행 상품 보유 이력 사전 질문에서 확인한 "
                            "첫거래 우대 불가 사실"
                        ),
                        attributes={"institution_history_selected": True},
                    )
                ],
                collected_at=datetime.now(timezone.utc),
                request_reference=reference,
            )
            updated, _ = updated.with_answer_fact(fact)
        return updated

    @staticmethod
    def _institution_history_name_key(name: str | None) -> str:
        return re.sub(r"(?:\(주\)|주식회사|㈜|\s)+", "", name or "").casefold()

    @staticmethod
    def _product_holding_fact_types(product: ProductDefinition) -> set[str]:
        fact_types: set[str] = set()
        stack: list[object] = [
            rule.rule.model_dump(mode="json") for rule in product.preferential_rules
        ]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                fact_type = node.get("fact_type")
                if isinstance(fact_type, str) and "PRODUCT_HOLDING" in fact_type.upper():
                    fact_types.add(fact_type)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return fact_types

    @staticmethod
    def _materialize_subscription_date(
        runtime: _SearchRuntime,
        store: UserFactStore,
    ) -> UserFactStore:
        """Resolve a new account's opening date from the search session itself.

        A product's event window can depend on the date of the account the user
        is about to open. That is not an unknown personal-history fact, so it
        must never generate a question such as "이벤트 100만원 이하 ...".
        """

        reference = f"SYSTEM-ACCOUNT-OPEN-DATE/{runtime.session.search_session_id}"
        value = runtime.subscription_date.isoformat()
        current = [
            fact
            for fact in store.active_facts
            if fact.request_reference == reference
        ]
        if current and current[-1].value == value:
            return store
        rewritten = [
            fact.model_copy(
                update={"record_status": FactRecordStatus.SUPERSEDED},
                deep=True,
            )
            if fact.record_status == FactRecordStatus.ACTIVE
            and fact.request_reference == reference
            else fact
            for fact in store.facts
        ]
        payload = {"request_reference": reference, "value": value}
        opening_date = UserFact(
            fact_id=f"SYSTEM-{canonical_hash(payload)[:20]}",
            user_id=store.user_id,
            fact_type="ACCOUNT_OPEN_DATE",
            value=value,
            valid_from=runtime.as_of,
            source_type=FactSourceType.DERIVED,
            semantic_type=FactSemanticType.DERIVED_FACT,
            provenance=[
                FactProvenance(
                    reference=reference,
                    description="검색 세션의 신규 계좌 가입 예정일",
                    attributes={"subscription_date": value},
                )
            ],
            collected_at=datetime.now(timezone.utc),
            request_reference=reference,
        )
        return store.model_copy(update={"facts": [*rewritten, opening_date]}, deep=True)

    @staticmethod
    def _materialize_application_capacity(
        runtime: _SearchRuntime,
        store: UserFactStore,
    ) -> UserFactStore:
        """Expose shared deterministic onboarding facts once per session."""

        request_reference = (
            f"INTENT-APPLICATION-CAPACITY/{runtime.session.search_session_id}"
        )
        shared_prefix = (
            f"PRESEARCH-APPLICATION-CAPACITY/{runtime.session.search_session_id}/"
        )
        birth_date_reference = (
            f"PRESEARCH-BIRTH-DATE/{runtime.session.search_session_id}/AGE_YEARS"
        )
        youth_policy_reference = (
            "PRESEARCH-YOUTH-POLICY-ACCOUNT/"
            f"{runtime.session.search_session_id}/YOUTH_FUTURE_OR_LEAP_ACCOUNT_HELD"
        )
        soldier_eligibility_reference = (
            "PRESEARCH-SOLDIER-TOMORROW-SAVINGS/"
            f"{runtime.session.search_session_id}/SOLDIER_TOMORROW_SAVINGS_ELIGIBLE"
        )
        capacity = runtime.intent.application_capacity
        if capacity is None:
            rewritten = [
                fact.model_copy(
                    update={"record_status": FactRecordStatus.SUPERSEDED},
                    deep=True,
                )
                if fact.record_status == FactRecordStatus.ACTIVE
                and (
                    fact.request_reference == request_reference
                    or (
                        fact.request_reference is not None
                        and fact.request_reference.startswith(shared_prefix)
                    )
                )
                else fact
                for fact in store.facts
            ]
            return store.model_copy(update={"facts": rewritten}, deep=True)

        def upsert_shared_fact(
            current: UserFactStore,
            *,
            fact_type: str,
            value: Any,
            reference: str,
            description: str,
        ) -> UserFactStore:
            if any(
                fact.request_reference == reference and fact.value == value
                for fact in current.active_facts
            ):
                return current
            payload = {
                "request_reference": reference,
                "value": value,
                "intent_version": runtime.session.intent_version,
            }
            fact = UserFact(
                fact_id=f"PRESEARCH-{canonical_hash(payload)[:20]}",
                user_id=current.user_id,
                fact_type=fact_type,
                value=value,
                valid_from=runtime.as_of,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                provenance=[
                    FactProvenance(
                        reference=reference,
                        description=description,
                        attributes={"intent_version": runtime.session.intent_version},
                    )
                ],
                collected_at=datetime.now(timezone.utc),
                request_reference=reference,
            )
            updated, _ = current.with_answer_fact(fact)
            return updated

        updated = upsert_shared_fact(
            store,
            fact_type="APPLICATION_CAPACITY",
            value=capacity,
            reference=request_reference,
            description="가입 명의 사전 질문에서 확인한 전역 사용자 조건",
        )
        profile_entry = runtime.pre_search_profile.get(APPLICATION_CAPACITY)
        profile_value = profile_entry.value if profile_entry is not None else {}
        foreign_national = bool(profile_value.get("foreign_national", False))
        for fact_type, value in {
            "KOREAN_NATIONAL": not foreign_national,
            "REAL_NAME_SUBSCRIPTION_POSSIBLE": True,
        }.items():
            updated = upsert_shared_fact(
                updated,
                fact_type=fact_type,
                value=value,
                reference=f"{shared_prefix}{fact_type}",
                description="가입 명의 사전 질문에서 함께 확인한 공통 가입 조건",
            )
        birth_entry = runtime.pre_search_profile.get(BIRTH_DATE)
        age_years = (
            birth_entry.value.get("age_years")
            if birth_entry is not None
            and birth_entry.answer_status == PreSearchAnswerStatus.ANSWERED
            else None
        )
        if age_years is not None:
            updated = upsert_shared_fact(
                updated,
                fact_type="AGE_YEARS",
                value=age_years,
                reference=birth_date_reference,
                description="생년월일 사전 질문에서 가입 예정일 기준으로 계산한 만 나이",
            )
        else:
            updated = updated.model_copy(
                update={
                    "facts": [
                        fact.model_copy(
                            update={"record_status": FactRecordStatus.SUPERSEDED},
                            deep=True,
                        )
                        if fact.record_status == FactRecordStatus.ACTIVE
                        and fact.request_reference == birth_date_reference
                        else fact
                        for fact in updated.facts
                    ]
                },
                deep=True,
            )
        youth_entry = runtime.pre_search_profile.get(YOUTH_POLICY_ACCOUNT_HOLDING)
        youth_policy_account_held = (
            youth_entry.value.get("youth_policy_account_held")
            if youth_entry is not None
            and youth_entry.answer_status == PreSearchAnswerStatus.ANSWERED
            else None
        )
        if youth_policy_account_held is not None:
            updated = upsert_shared_fact(
                updated,
                fact_type="YOUTH_FUTURE_OR_LEAP_ACCOUNT_HELD",
                value=youth_policy_account_held,
                reference=youth_policy_reference,
                description="청년 정책형 적금 중복가입 여부 사전 질문에서 확인한 공통 조건",
            )
        else:
            updated = updated.model_copy(
                update={
                    "facts": [
                        fact.model_copy(
                            update={"record_status": FactRecordStatus.SUPERSEDED},
                            deep=True,
                        )
                        if fact.record_status == FactRecordStatus.ACTIVE
                        and fact.request_reference == youth_policy_reference
                        else fact
                        for fact in updated.facts
                    ]
                },
                deep=True,
            )
        soldier_entry = runtime.pre_search_profile.get(
            SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY
        )
        soldier_eligible = (
            soldier_entry.value.get("soldier_tomorrow_savings_eligible")
            if soldier_entry is not None
            and soldier_entry.answer_status == PreSearchAnswerStatus.ANSWERED
            else None
        )
        if soldier_eligible is not None:
            updated = upsert_shared_fact(
                updated,
                fact_type="SOLDIER_TOMORROW_SAVINGS_ELIGIBLE",
                value=soldier_eligible,
                reference=soldier_eligibility_reference,
                description="장병내일준비적금 복무 대상 여부 사전 질문에서 확인한 공통 조건",
            )
        else:
            updated = updated.model_copy(
                update={
                    "facts": [
                        fact.model_copy(
                            update={"record_status": FactRecordStatus.SUPERSEDED},
                            deep=True,
                        )
                        if fact.record_status == FactRecordStatus.ACTIVE
                        and fact.request_reference == soldier_eligibility_reference
                        else fact
                        for fact in updated.facts
                    ]
                },
                deep=True,
            )
        return updated

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

        Production sessions normally evaluate the Asia/Seoul business date.
        Historical or deterministic replay sessions can use an earlier
        ``as_of`` date; in that
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
        if fact_type.startswith(f"ELIGIBILITY_TEXT::{product.product_id}::"):
            return True

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
                    "estimated_pre_tax_interest": (
                        str(candidate.realizable_pre_tax_interest)
                        if candidate.realizable_pre_tax_interest is not None else None
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
