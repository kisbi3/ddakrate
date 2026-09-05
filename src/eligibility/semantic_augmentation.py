"""Command-only semantic augmentation after deterministic pre-search.

A budget limits work per command, NOT the challenger set. Unread/failed source
packets keep results provisional. Reads never compile, advance or write caches.
"""
from __future__ import annotations

import json
from typing import Any

from eligibility.application import UserAnswerSubmission, submit_user_answer
from eligibility.audit import AuditEventType
from eligibility.schema.conversation import ProductFeaturePolicy
from eligibility.schema.enums import (
    FactRecordStatus, FactSemanticType, PreferenceValue, PreSearchAnswerStatus, ResolutionStrategy,
)
from eligibility.schema.evaluation import MissingFactRequest
from eligibility.schema.semantic import SemanticReviewState
from eligibility.search.pre_search import INSTITUTION_PRODUCT_HOLDING_HISTORY
from eligibility.search.semantic import (
    INPUT_PREFIX, QUESTIONS, TYPED_INPUTS, attach_requests, normalize_answer, overlay_product,
    product_packet, user_inputs, validate_compilation, validate_shared_input,
)


class SemanticAugmentationMixin:
    def _semantic_packet(self, product):
        if self.semantic_compiler is None:
            return None
        return product_packet(product, self.semantic_compiler.cache_key)

    @staticmethod
    def _declined_benefit_fields(runtime):
        fields = {
            item.field
            for item in runtime.intent.preferences
            if item.preference == PreferenceValue.PREFER_ABSENT
        }
        fields.update(
            feature_id
            for feature_id, policy in runtime.feature_policies.items()
            if policy == ProductFeaturePolicy.PREFER_ABSENT
        )
        return fields

    @staticmethod
    def _holding_institution_names(runtime):
        entry = runtime.pre_search_profile.get(INSTITUTION_PRODUCT_HOLDING_HISTORY)
        if entry is None or entry.answer_status != PreSearchAnswerStatus.ANSWERED:
            return ()
        names = entry.value.get("prior_product_holding_institutions", [])
        return tuple(name for name in names if isinstance(name, str) and name.strip())

    def _semantic_evaluation_inputs(self, runtime, products, fact_store):
        overlays = []
        details = {}
        all_facts = {fact.fact_id: fact for fact in fact_store.facts}
        declined = self._declined_benefit_fields(runtime)
        holdings = self._holding_institution_names(runtime)
        for product in products:
            packet = self._semantic_packet(product)
            interpretations = runtime.semantic_cache.get(packet.cache_key, ()) if packet else ()
            overlay, store, requests, views = overlay_product(
                product, packet, interpretations, fact_store,
                as_of=runtime.as_of, subscription_date=runtime.subscription_date,
                declined_benefit_fields=declined,
                holding_institution_names=holdings,
            )
            overlays.append(overlay)
            all_facts.update((fact.fact_id, fact) for fact in store.facts)
            details[product.product_id] = (overlay, requests, views)
        return overlays, fact_store.model_copy(update={"facts": list(all_facts.values())}), details

    def _semantic_frontier_packets(self, runtime):
        if runtime.ranking is None:
            return []
        frontier = set(self.question_planner.frontier_product_ids(runtime.evaluations, runtime.intent))
        frontier.update(runtime.ranking.ordered_product_ids[:runtime.intent.requested_top_k])
        # A missing upper bound is not proof of inferiority.
        frontier.update(pid for pid in runtime.ranking.ordered_product_ids
                        if runtime.evaluations[pid].user_specific_conditional_upper_rate is None)
        packets = []
        for pid in runtime.ranking.ordered_product_ids:
            if pid not in frontier:
                continue
            packet = self._semantic_packet(runtime.candidate_products[pid])
            if packet:
                packets.append(packet)
        return packets

    def _augment_semantic_conditions(self, runtime):
        if self.semantic_compiler is None:
            runtime.semantic_review = SemanticReviewState()
            return
        if self.pre_search_enabled and self.pre_search_question_planner.select_next(
            runtime.pre_search_profile, runtime.intent, runtime.candidate_products.values()
        ) is not None:
            runtime.semantic_review = SemanticReviewState(status="DEFERRED")
            return
        packets = self._semantic_frontier_packets(runtime)
        batch = []
        empty_only = []
        chars = 0
        clauses = 0
        for packet in packets:
            if packet.cache_key in runtime.semantic_cache or packet.cache_key in runtime.semantic_failures:
                continue
            if not packet.clauses:
                runtime.semantic_cache[packet.cache_key] = ()
                empty_only.append(packet)
                continue
            size = len(json.dumps(packet.payload, ensure_ascii=False))
            if size > self.semantic_max_characters or len(packet.clauses) > 48 or any(len(c.text) > 8000 for c in packet.clauses):
                runtime.semantic_failures[packet.cache_key] = "SOURCE_PACKET_UNSUPPORTED"
                continue
            if len(batch) >= self.semantic_batch_size or chars + size > self.semantic_max_characters or clauses + len(packet.clauses) > 48:
                continue
            batch.append(packet)
            chars += size
            clauses += len(packet.clauses)
        error_code = None
        evaluated = [runtime.candidate_products[p.product_id] for p in batch]
        evaluated.extend(runtime.candidate_products[p.product_id] for p in empty_only)
        if batch:
            try:
                compiled = validate_compilation(self.semantic_compiler.compile(batch), batch)
                staged = {}
                for packet in batch:
                    allowed = {c.clause_id for c in packet.clauses}
                    staged[packet.cache_key] = tuple(c for c in compiled.clauses if c.clause_id in allowed)
                # Full batch validation precedes any live cache mutation.
                runtime.semantic_cache.update(staged)
            except Exception:
                # The user command remains usable. No failed interpretation is
                # applied; existing typed questions remain available.
                error_code = "SEMANTIC_COMPILATION_FAILED"
                for packet in batch:
                    runtime.semantic_failures[packet.cache_key] = error_code
        if evaluated:
            self._evaluate(runtime, evaluated, merge=True)
            self._rank_once(runtime)
        # Recompute after the new evidence: demoted candidates can uncover an
        # entirely new challenger. A budget never certifies those unread rows.
        frontier_packets = self._semantic_frontier_packets(runtime)
        pending = [p for p in frontier_packets if p.cache_key not in runtime.semantic_cache and p.cache_key not in runtime.semantic_failures]
        failed = [p for p in frontier_packets if p.cache_key in runtime.semantic_failures]
        unresolved = 0
        interpreted = False
        for packet in frontier_packets:
            results = runtime.semantic_cache.get(packet.cache_key, ())
            for clause in results:
                if clause.expression is None:
                    unresolved += 1
                else:
                    interpreted = True
                    if '"UNKNOWN"' in clause.expression.model_dump_json():
                        unresolved += 1
            unresolved += sum(c.reward_pp is None for c in packet.clauses)
            unresolved += len(packet.empty_clauses)
        reviewed = [p.product_id for p in frontier_packets if p.cache_key in runtime.semantic_cache]
        runtime.semantic_review = SemanticReviewState(
            status="FAILED" if failed else "PENDING" if pending else "COMPLETE",
            reviewed_product_ids=reviewed, pending_product_count=len(pending),
            unresolved_clause_count=unresolved + sum(len(p.clauses) for p in failed),
            ai_interpreted=interpreted, calls_this_turn=int(bool(batch)),
            error_code=error_code or (runtime.semantic_failures[failed[0].cache_key] if failed else None),
        )
        runtime.audit.emit("SEMANTIC_AUGMENTATION", AuditEventType.SEMANTIC_CONDITIONS_ANALYZED,
                           output_data=runtime.semantic_review,
                           payload={"compiled_product_ids": [p.product_id for p in batch],
                                    "existing_typed_rules_mutated": False,
                                    "financial_values_from_llm": False})

    def advance_semantic_review(self, search_session_id: str, *, retry_failed: bool = False):
        runtime = self._runtime(search_session_id)
        if self.semantic_compiler is None:
            raise ValueError("SEMANTIC_COMPILER_DISABLED")
        if runtime.conflicts or runtime.ranking is None:
            raise ValueError("Resolve the search intent before semantic review")
        snapshot = self._snapshot_runtime(runtime)
        try:
            if retry_failed:
                runtime.semantic_failures.clear()
            self._rank(runtime)
            self._select_question(runtime)
            return runtime.semantic_review
        except Exception:
            self._restore_runtime(runtime, snapshot)
            raise

    def _store_semantic_input(self, runtime, variable: str, value: Any):
        value = normalize_answer(variable, value, runtime.as_of)
        validate_shared_input(runtime.fact_store, variable, value, runtime.as_of)
        projected_types = {key for key, mapped in TYPED_INPUTS.items() if mapped == variable}
        runtime.fact_store = runtime.fact_store.model_copy(update={"facts": [
            fact.model_copy(update={"record_status": FactRecordStatus.SUPERSEDED})
            if fact.fact_type in projected_types and fact.source_type == "USER_DECLARED"
            and fact.record_status == FactRecordStatus.ACTIVE else fact
            for fact in runtime.fact_store.facts
        ]}, deep=True)
        reference = f"SEMANTIC-INPUT/{runtime.session.search_session_id}/{variable}"
        request = MissingFactRequest(
            fact_type=INPUT_PREFIX + variable, action_id=INPUT_PREFIX + variable,
            semantic_input=variable, requested_by_rule_id=reference,
            missing_fact_id=reference, resolution_strategy=ResolutionStrategy.ASK_USER,
            expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            question=QUESTIONS[variable],
        )
        runtime.fact_store, _ = submit_user_answer(
            runtime.fact_store, request,
            UserAnswerSubmission(request_reference=reference, answer=value,
                                 answered_at=self._effective_answered_at(runtime, None)), audit=runtime.audit,
        )
        runtime.request_history[reference] = request
        runtime.answer_records = self._answer_records(runtime.fact_store)
        runtime.condition_states.pop(INPUT_PREFIX + variable, None)
        self._sync_condition_states_to_session(runtime)
        # Revisions can re-open a previously skipped shared question.
        runtime.skipped_question_ids.difference_update(
            q.question_id for q in runtime.question_history.values()
            if q.request is not None and q.request.semantic_input == variable
        )
        return value

    def _forget_semantic_input(self, runtime, variable: str):
        runtime.fact_store = runtime.fact_store.model_copy(update={"facts": [
            fact.model_copy(update={"record_status": FactRecordStatus.SUPERSEDED})
            if fact.fact_type == INPUT_PREFIX + variable and fact.source_type == "USER_DECLARED"
            and fact.record_status == FactRecordStatus.ACTIVE else fact
            for fact in runtime.fact_store.facts
        ]}, deep=True)

    def _submit_semantic_input(self, runtime, question, answer, *, select_next=True):
        snapshot = self._snapshot_runtime(runtime)
        variable = question.request.semantic_input
        try:
            if self._is_unknown_answer(answer) or (variable != "PREGNANT_SELF" and answer is False):
                # Preserve already supplied partial facts; explicit CLEAR is
                # the operation that retracts them.
                # Refusal is UNKNOWN, never "zero children" or a failed bonus.
                result = self.skip_active_question(runtime.session.search_session_id, select_next=False)
                if not runtime.defer_pipeline:
                    self._run_pipeline(runtime, select_question=select_next)
                return result if runtime.defer_pipeline else runtime.session
            self._store_semantic_input(runtime, variable, answer)
            self._commit_answered_question(runtime, question.question_id)
            if runtime.defer_pipeline:
                runtime.pipeline_requested_while_deferred = True
            else:
                self._run_pipeline(runtime, select_question=select_next)
            return runtime.session
        except Exception:
            self._restore_runtime(runtime, snapshot)
            raise

    @staticmethod
    def _semantic_incomplete(runtime):
        review = runtime.semantic_review
        return review.status in {"PENDING", "FAILED"} or review.unresolved_clause_count > 0
