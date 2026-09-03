from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from eligibility.application_service import _SearchRuntime

_DEBUG_HISTORY_PRODUCT_LIMIT = 100
_DEBUG_REQUEST_PRODUCT_LIMIT = 200


class ApplicationDebugMixin:
    """Debug/inspection surface for :class:`ApplicationService`."""

    def get_debug_request_state(self, search_session_id: str) -> dict[str, Any]:
        """Return the compact before/after state used by the request inspector.

        This deliberately avoids constructing the full debug snapshot (all
        filter decisions and all evaluation summaries) twice for every Web API
        request. The complete live snapshot remains available from the explicit
        debug-session endpoint.
        """

        runtime = self._runtime(search_session_id)
        ranking = runtime.ranking
        candidate_product_ids = list(runtime.candidate_products)
        captured_candidate_ids = candidate_product_ids[
            :_DEBUG_REQUEST_PRODUCT_LIMIT
        ]
        active_question = (
            runtime.active_question.model_dump(mode="json")
            if runtime.active_question is not None
            else None
        )
        audit_event_count = len(
            self.audit_sink.read(trace_id=runtime.audit.trace_id)
        )
        return {
            "search_session_id": search_session_id,
            "session": {
                "status": runtime.session.status.value,
                "intent_version": runtime.session.intent_version,
                "ranking_run_id": runtime.session.ranking_run_id,
                "active_question_id": runtime.session.active_question_id,
                "answered_question_ids": list(
                    runtime.session.answered_question_ids
                ),
                "excluded_product_ids": list(
                    runtime.session.excluded_product_ids
                ),
            },
            "intent": runtime.intent.model_dump(mode="json"),
            "candidate_product_ids": captured_candidate_ids,
            "candidate_product_count": len(candidate_product_ids),
            "candidate_product_ids_truncated": (
                len(candidate_product_ids) > len(captured_candidate_ids)
            ),
            "ranking": {
                "objective": runtime.intent.ranking_objective.value,
                "top_k": runtime.intent.requested_top_k,
                "top_product_ids": (
                    [item.product_id for item in ranking.items]
                    if ranking is not None
                    else []
                ),
                "stability": (
                    ranking.stability.model_dump(mode="json")
                    if ranking is not None
                    else None
                ),
            },
            "eligibility_text_review": {
                "state": runtime.eligibility_text_review_state,
                "rounds": runtime.eligibility_text_review_rounds,
                "pending_count": runtime.eligibility_text_review_pending_count,
                "frontier_product_ids": list(
                    runtime.eligibility_text_review_frontier_ids
                ),
                "active_cache_product_ids": sorted(
                    runtime.eligibility_text_review_active_keys
                ),
            },
            "active_question": active_question,
            "pre_search_profile": {
                key: entry.model_dump(mode="json")
                for key, entry in runtime.pre_search_profile.items()
            },
            "search_progress": {
                "recalculation_round": (
                    1
                    + len(runtime.session.answered_question_ids)
                    + max(0, runtime.session.intent_version - 1)
                ),
                "catalog_product_count": len(self.products),
                "candidate_product_count": len(runtime.candidate_products),
                "viable_product_count": (
                    len(ranking.ordered_product_ids) if ranking is not None else 0
                ),
                "visible_top_k_count": (
                    len(ranking.items) if ranking is not None else 0
                ),
                "visible_top_product_ids": (
                    [item.product_id for item in ranking.items]
                    if ranking is not None
                    else []
                ),
                "ranking_stable": (
                    ranking.stability.stable if ranking is not None else False
                ),
                "has_next_question": runtime.active_question is not None,
                "completed_question_count": len(
                    runtime.session.answered_question_ids
                ),
                "estimate_is_dynamic": True,
            },
            "engine_checkpoint": {
                "retrieved_candidate_count": len(runtime.candidate_products),
                "evaluated_product_count": len(runtime.evaluations),
                "ranking_run_id": runtime.session.ranking_run_id,
                "question_planner_mode": (
                    "DETERMINISTIC_PRE_SEARCH"
                    if runtime.active_question is not None
                    and runtime.active_question.question_kind
                    == "PRE_SEARCH_PROFILE"
                    else "RANKING_AWARE_PRODUCT_VERIFICATION"
                ),
                "selected_question_id": (
                    runtime.active_question.question_id
                    if runtime.active_question is not None
                    else None
                ),
            },
            "audit_event_count": audit_event_count,
        }

    def list_debug_search_sessions(self) -> list[dict[str, Any]]:
        """Return lightweight session rows for the local orchestration inspector."""

        return [
            {
                "search_session_id": runtime.session.search_session_id,
                "status": runtime.session.status.value,
                "created_at": runtime.session.created_at.isoformat(),
                "updated_at": runtime.session.updated_at.isoformat(),
                "intent_version": runtime.session.intent_version,
                "candidate_count": len(runtime.candidate_products),
                "answered_question_count": len(runtime.session.answered_question_ids),
                "active_question": (
                    runtime.active_question.question
                    if runtime.active_question is not None
                    else None
                ),
                "source_utterances": list(runtime.intent.source_utterances),
            }
            for runtime in sorted(
                self._sessions.values(),
                key=lambda item: item.session.updated_at,
                reverse=True,
            )
        ]

    def get_debug_search_snapshot(self, search_session_id: str) -> dict[str, Any]:
        """Expose exact structured inputs/outputs used by the local Web runtime.

        This is deliberately an internal observability view, not a public product
        contract. It lets the private inspector explain where the LLM stops and
        deterministic retrieval/evaluation/ranking/question selection begins.
        """

        runtime = self._runtime(search_session_id)
        audit_events = self.audit_sink.read(trace_id=runtime.audit.trace_id)
        return {
            "session": runtime.session.model_dump(mode="json"),
            "multi_turn": {
                "source_utterances": list(runtime.intent.source_utterances),
                "working_note": self.working_note_store.load(search_session_id),
                "working_note_turn_sequence": runtime.working_note_turn_sequence,
                "active_question": (
                    runtime.active_question.model_dump(mode="json")
                    if runtime.active_question is not None
                    else None
                ),
                "answered_question_ids": list(runtime.session.answered_question_ids),
                "skipped_question_ids": sorted(runtime.skipped_question_ids),
                "question_history": [
                    question.model_dump(mode="json")
                    for question in runtime.question_history.values()
                ],
                "answer_records": [
                    record.model_dump(mode="json") for record in runtime.answer_records
                ],
                "pre_search_profile_answers": dict(runtime.pre_search_profile_answers),
                "pre_search_typed_facts": dict(runtime.pre_search_typed_facts),
                "pre_search_profile": {
                    key: entry.model_dump(mode="json")
                    for key, entry in runtime.pre_search_profile.items()
                },
                "condition_states": [
                    runtime.condition_states[key].model_dump(mode="json")
                    for key in sorted(runtime.condition_states)
                ],
                "session_origin": deepcopy(runtime.session_origin),
                "visible_dialogue": deepcopy(runtime.visible_dialogue),
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
            },
            "engine": {
                "call_order": [
                    "1. User input interpreter (LLM structured difference; write requests only)",
                    "2. ApplicationService operation validation and state update (deterministic)",
                    "3. CandidateRetriever (deterministic)",
                    "4. MultiProductEvaluator / FinancialEligibilityEngine (deterministic)",
                    "5. RankingService (deterministic)",
                    "5b. Top-ranked eligibility_text review (strict structured LLM + deterministic validation/AND)",
                    "6a. DeterministicPreSearchQuestionPlanner (pre-search selection and wording)",
                    "6b. RankingAwareQuestionPlanner (product-question selection by rate order)",
                    "7. QuestionGenerator (LLM wording for supported product questions only)",
                ],
                "orchestration_boundary": {
                    "llm_calls_engine_directly": False,
                    "write_request": [
                        "User message",
                        "LLM returns a strict structured interpretation",
                        "ApplicationService validates and executes domain operations",
                        "Deterministic retrieval/evaluation/ranking",
                        "Deterministic question selection",
                        "Optional LLM product-question wording",
                        "Web API response",
                    ],
                    "read_request": [
                        "Browser GET state/recommendations/status",
                        "Web API reads stored session or deterministic result",
                        "Web API response (normally no LLM call)",
                    ],
                    "active_question_owner": (
                        "DETERMINISTIC_PRE_SEARCH"
                        if runtime.active_question is not None
                        and runtime.active_question.question_kind == "PRE_SEARCH_PROFILE"
                        else "DETERMINISTIC_SELECTION_WITH_OPTIONAL_LLM_WORDING"
                        if runtime.active_question is not None
                        else "NONE"
                    ),
                },
                "retrieval": {
                    "input": {
                        "intent": runtime.intent.model_dump(mode="json"),
                        "as_of": runtime.as_of.isoformat(),
                        "catalog_product_ids": list(self.products),
                        "excluded_product_ids": list(runtime.session.excluded_product_ids),
                    },
                    "output": {
                        "candidate_product_ids": list(runtime.candidate_products),
                        "filter_decisions": [
                            decision.model_dump(mode="json")
                            for decision in runtime.filter_decisions
                        ],
                    },
                },
                "evaluation": {
                    "input": {
                        "candidate_product_ids": list(runtime.candidate_products),
                        "facts": runtime.fact_store.model_dump(mode="json"),
                        "subscription_date": runtime.subscription_date.isoformat(),
                        "product_contribution_choices": [
                            item.model_dump(mode="json")
                            for item in runtime.product_contribution_choices.values()
                        ],
                    },
                    "output": {
                        product_id: {
                            "product_id": evaluation.product_id,
                            "product_name": evaluation.product_evaluation.product_name,
                            "eligibility_status": (
                                evaluation.product_evaluation.eligibility_status.value
                            ),
                            "confirmed_rate": str(evaluation.confirmed_rate),
                            "realizable_rate": str(evaluation.realizable_rate),
                            "realizable_pre_tax_interest": (
                                str(evaluation.realizable_pre_tax_interest)
                            ),
                            "estimated_total_principal": (
                                str(evaluation.estimated_total_principal)
                            ),
                            "ranking_comparability": (
                                evaluation.ranking_comparability.value
                            ),
                            "unresolved_material_fact_ids": list(
                                evaluation.unresolved_material_fact_ids
                            ),
                            "contribution_projection": (
                                evaluation.contribution_projection.model_dump(mode="json")
                                if evaluation.contribution_projection is not None
                                else None
                            ),
                            "detail_endpoint": (
                                f"/api/debug/sessions/{search_session_id}/evaluations/"
                                f"{product_id}"
                            ),
                        }
                        for product_id, evaluation in runtime.evaluations.items()
                    },
                },
                "eligibility_text_review": {
                    "state": runtime.eligibility_text_review_state,
                    "rounds": runtime.eligibility_text_review_rounds,
                    "pending_count": runtime.eligibility_text_review_pending_count,
                    "frontier_product_ids": list(
                        runtime.eligibility_text_review_frontier_ids
                    ),
                    "active_cache_keys": dict(
                        runtime.eligibility_text_review_active_keys
                    ),
                    "reviews": {
                        product_id: review.model_dump(mode="json")
                        for product_id, cache_key in (
                            runtime.eligibility_text_review_active_keys.items()
                        )
                        if (
                            review := runtime.eligibility_text_review_cache.get(
                                cache_key
                            )
                        )
                        is not None
                    },
                },
                "ranking": {
                    "input": {
                        "objective": runtime.intent.ranking_objective.value,
                        "top_k": runtime.intent.requested_top_k,
                        "evaluated_product_ids": list(runtime.evaluations),
                    },
                    "output": (
                        runtime.ranking.model_dump(mode="json")
                        if runtime.ranking is not None
                        else None
                    ),
                },
                "question_planner": {
                    "input": {
                        "mode": (
                            "DETERMINISTIC_PRE_SEARCH"
                            if self.pre_search_enabled
                            and runtime.active_question is not None
                            and runtime.active_question.question_kind
                            == "PRE_SEARCH_PROFILE"
                            else "RANKING_AWARE_PRODUCT_VERIFICATION"
                        ),
                        "answered_question_ids": list(
                            runtime.session.answered_question_ids
                        ),
                        "suppressed_question_ids": sorted(runtime.skipped_question_ids),
                        "evaluated_product_ids": list(runtime.evaluations),
                    },
                    "output": (
                        runtime.active_question.model_dump(mode="json")
                        if runtime.active_question is not None
                        else None
                    ),
                },
            },
            "audit_timeline": [
                {
                    "event_id": event.event_id,
                    "occurred_at": event.occurred_at.isoformat(),
                    "component": event.component,
                    "event_type": event.event_type.value,
                    "entity_refs": dict(event.entity_refs),
                    "payload_deferred": True,
                }
                for event in audit_events[-200:]
            ],
            "audit_event_count": len(audit_events),
            "audit_timeline_truncated": len(audit_events) > 200,
            "audit_timeline_endpoint": (
                f"/api/debug/sessions/{search_session_id}/audit"
            ),
        }

    def get_debug_history_snapshot(self, search_session_id: str) -> dict[str, Any]:
        """Return an archived-review snapshot bounded independently of catalog size.

        The live inspector can still request the complete in-memory snapshot.
        Durable history keeps the complete conversation and engine summaries,
        plus the first 100 ranked products needed for review, instead of copying
        thousands of equivalent list rows into every turn snapshot.
        """

        snapshot = self.get_debug_search_snapshot(search_session_id)
        engine = snapshot["engine"]
        ranking_output = engine["ranking"]["output"] or {}
        ranked_ids = list(ranking_output.get("ordered_product_ids") or [])
        candidate_ids = list(
            engine["retrieval"]["output"].get("candidate_product_ids") or []
        )
        focus_ids = ranked_ids[:_DEBUG_HISTORY_PRODUCT_LIMIT]
        if len(focus_ids) < _DEBUG_HISTORY_PRODUCT_LIMIT:
            seen = set(focus_ids)
            focus_ids.extend(
                product_id
                for product_id in candidate_ids
                if product_id not in seen
            )
            focus_ids = focus_ids[:_DEBUG_HISTORY_PRODUCT_LIMIT]
        focus_id_set = set(focus_ids)

        retrieval_input = engine["retrieval"]["input"]
        catalog_ids = list(retrieval_input.get("catalog_product_ids") or [])
        retrieval_input["catalog_product_count"] = len(catalog_ids)
        retrieval_input["catalog_product_ids"] = catalog_ids[
            :_DEBUG_HISTORY_PRODUCT_LIMIT
        ]
        retrieval_input["catalog_product_ids_truncated"] = (
            len(catalog_ids) > _DEBUG_HISTORY_PRODUCT_LIMIT
        )

        retrieval_output = engine["retrieval"]["output"]
        filter_decisions = list(retrieval_output.get("filter_decisions") or [])
        retained_decisions = [
            decision
            for decision in filter_decisions
            if decision.get("product_id") in focus_id_set
        ][:_DEBUG_HISTORY_PRODUCT_LIMIT]
        if len(retained_decisions) < _DEBUG_HISTORY_PRODUCT_LIMIT:
            retained_product_ids = {
                decision.get("product_id") for decision in retained_decisions
            }
            retained_decisions.extend(
                decision
                for decision in filter_decisions
                if decision.get("product_id") not in retained_product_ids
            )
            retained_decisions = retained_decisions[:_DEBUG_HISTORY_PRODUCT_LIMIT]
        retrieval_output["candidate_product_count"] = len(candidate_ids)
        retrieval_output["candidate_product_ids"] = focus_ids
        retrieval_output["candidate_product_ids_truncated"] = (
            len(candidate_ids) > len(focus_ids)
        )
        retrieval_output["filter_decision_count"] = len(filter_decisions)
        retrieval_output["filter_decisions"] = retained_decisions
        retrieval_output["filter_decisions_truncated"] = (
            len(filter_decisions) > len(retained_decisions)
        )

        evaluation_input = engine["evaluation"]["input"]
        evaluated_ids = list(evaluation_input.get("candidate_product_ids") or [])
        evaluation_input["candidate_product_count"] = len(evaluated_ids)
        evaluation_input["candidate_product_ids"] = focus_ids
        evaluation_input["candidate_product_ids_truncated"] = (
            len(evaluated_ids) > len(focus_ids)
        )
        evaluation_output = engine["evaluation"]["output"]
        engine["evaluation"]["output_count"] = len(evaluation_output)
        engine["evaluation"]["output"] = {
            product_id: evaluation_output[product_id]
            for product_id in focus_ids
            if product_id in evaluation_output
        }
        engine["evaluation"]["output_truncated"] = (
            len(evaluation_output) > len(engine["evaluation"]["output"])
        )

        ranking_input = engine["ranking"]["input"]
        ranking_input_ids = list(ranking_input.get("evaluated_product_ids") or [])
        ranking_input["evaluated_product_count"] = len(ranking_input_ids)
        ranking_input["evaluated_product_ids"] = focus_ids
        ranking_input["evaluated_product_ids_truncated"] = (
            len(ranking_input_ids) > len(focus_ids)
        )
        if ranking_output:
            ranking_output["ordered_product_count"] = len(ranked_ids)
            ranking_output["ordered_product_ids"] = focus_ids
            ranking_output["ordered_product_ids_truncated"] = (
                len(ranked_ids) > len(focus_ids)
            )

        planner_input = engine["question_planner"]["input"]
        planner_ids = list(planner_input.get("evaluated_product_ids") or [])
        planner_input["evaluated_product_count"] = len(planner_ids)
        planner_input["evaluated_product_ids"] = focus_ids
        planner_input["evaluated_product_ids_truncated"] = (
            len(planner_ids) > len(focus_ids)
        )
        snapshot["history_snapshot"] = {
            "product_detail_limit": _DEBUG_HISTORY_PRODUCT_LIMIT,
            "product_lists_truncated": any(
                count > _DEBUG_HISTORY_PRODUCT_LIMIT
                for count in (len(catalog_ids), len(candidate_ids), len(evaluated_ids))
            ),
        }
        return snapshot

    def get_debug_audit_timeline(self, search_session_id: str) -> list[dict[str, Any]]:
        """Return the full audit payload only when the inspector asks for it."""

        runtime = self._runtime(search_session_id)
        return [
            event.model_dump(mode="json")
            for event in self.audit_sink.read(trace_id=runtime.audit.trace_id)
        ]

    def get_debug_candidate_evaluation(
        self,
        search_session_id: str,
        product_id: str,
    ) -> dict[str, Any]:
        """Return one full evaluation instead of embedding every one in a bundle."""

        runtime = self._runtime(search_session_id)
        try:
            evaluation = runtime.evaluations[product_id]
        except KeyError as exc:
            raise KeyError(
                f"evaluation not found: {search_session_id}/{product_id}"
            ) from exc
        return evaluation.model_dump(mode="json")
