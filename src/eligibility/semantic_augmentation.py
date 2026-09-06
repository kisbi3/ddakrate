"""Command-only semantic augmentation after deterministic pre-search.

A budget limits work per command, NOT the challenger set. Unread/failed source
packets keep results provisional. Reads never compile, advance or write caches.
"""
from __future__ import annotations

import json
import time
from typing import Any

from eligibility.application import UserAnswerSubmission, submit_user_answer
from eligibility.audit import AuditEventType
from eligibility.llm.client import LLMStructuredOutputError
from eligibility.schema.conversation import ProductFeaturePolicy
from eligibility.schema.enums import (
    FactRecordStatus, FactSemanticType, PreferenceValue, PreSearchAnswerStatus, ResolutionStrategy,
)
from eligibility.schema.evaluation import MissingFactRequest
from eligibility.schema.semantic import SemanticReviewState
from eligibility.search.pre_search import INSTITUTION_PRODUCT_HOLDING_HISTORY
from eligibility.search.ranking import RankingService
from eligibility.search.semantic import (
    INPUT_PREFIX, QUESTIONS, SEMANTIC_MAX_CALLS_PER_TURN, SEMANTIC_MAX_CLAUSE_CHARS,
    SEMANTIC_MAX_CLAUSES_PER_CALL, SEMANTIC_RETRY_SECONDS, TYPED_INPUTS,
    attach_requests, normalize_answer, overlay_product, packet_for_clauses,
    product_packet, split_packet_for_budget, user_inputs, validate_shared_input,
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
        intent = runtime.intent
        ordered = list(runtime.ranking.ordered_product_ids)
        top_k_ids = ordered[: intent.requested_top_k]
        visible = [
            runtime.evaluations[pid]
            for pid in top_k_ids
            if pid in runtime.evaluations
        ]
        possibles = [
            rate for rate in (RankingService._possible_rate(item) for item in visible)
            if rate is not None
        ]
        cutoff = min(possibles) if possibles else None
        window = max(intent.requested_top_k * 3, 12)
        packets = []
        for pid in ordered:
            if len(packets) >= window:
                break
            product = runtime.candidate_products.get(pid)
            if product is None:
                continue
            packet = self._semantic_packet(product)
            if packet is None:
                continue
            if pid in top_k_ids:
                packets.append(packet)
                continue
            candidate = runtime.evaluations.get(pid)
            if candidate is None or cutoff is None:
                continue
            possible = RankingService._possible_rate(candidate)
            if possible is not None and possible >= cutoff:
                packets.append(packet)
        return packets

    def _remaining_compile_clauses(self, runtime, packet):
        done = {item.clause_id for item in runtime.semantic_cache.get(packet.cache_key, ())}
        return [
            clause for clause in packet.clauses
            if clause.clause_id not in done and len(clause.text) <= SEMANTIC_MAX_CLAUSE_CHARS
        ]

    def _merge_semantic_cache(self, runtime, packet, rows):
        merged = {item.clause_id: item for item in runtime.semantic_cache.get(packet.cache_key, ())}
        merged.update({item.clause_id: item for item in rows})
        runtime.semantic_cache[packet.cache_key] = tuple(merged.values())

    def _compile_jobs(self, runtime, jobs):
        deadline = time.monotonic() + SEMANTIC_RETRY_SECONDS
        calls = 0
        pending_jobs = list(jobs)
        failed_packets = []
        while pending_jobs and calls < SEMANTIC_MAX_CALLS_PER_TURN and time.monotonic() < deadline:
            job = pending_jobs.pop(0)
            if not job:
                continue
            calls += 1
            try:
                compiled = self.semantic_compiler.compile(job)
                for packet in job:
                    allowed = {clause.clause_id for clause in packet.clauses}
                    rows = [row for row in compiled.clauses if row.clause_id in allowed]
                    self._merge_semantic_cache(runtime, packet, rows)
                    runtime.semantic_failures.pop(packet.cache_key, None)
            except LLMStructuredOutputError:
                if len(job) > 1:
                    pending_jobs[0:0] = [[packet] for packet in job]
                    continue
                packet = job[0]
                if len(packet.clauses) > 1:
                    mid = max(1, len(packet.clauses) // 2)
                    pending_jobs[0:0] = [
                        [packet_for_clauses(packet, tuple(packet.clauses[:mid]))],
                        [packet_for_clauses(packet, tuple(packet.clauses[mid:]))],
                    ]
                    continue
                runtime.semantic_failures[packet.cache_key] = "SEMANTIC_COMPILATION_FAILED"
                failed_packets.append(packet)
            except Exception:
                for packet in job:
                    runtime.semantic_failures[packet.cache_key] = "SEMANTIC_COMPILATION_FAILED"
                    failed_packets.append(packet)
        return calls, pending_jobs, failed_packets

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
        jobs = []
        empty_only = []
        current: list = []
        chars = 0
        clause_count = 0
        for packet in packets:
            remaining = self._remaining_compile_clauses(runtime, packet)
            if not packet.clauses:
                runtime.semantic_cache.setdefault(packet.cache_key, ())
                empty_only.append(packet)
                continue
            if packet.cache_key in runtime.semantic_failures:
                continue
            if not remaining:
                continue
            chunks, _oversized = split_packet_for_budget(
                packet_for_clauses(packet, tuple(remaining)),
                max_clauses=SEMANTIC_MAX_CLAUSES_PER_CALL,
                max_characters=self.semantic_max_characters,
                max_clause_chars=SEMANTIC_MAX_CLAUSE_CHARS,
            )
            stop = False
            for chunk in chunks:
                size = len(json.dumps(chunk.payload, ensure_ascii=False))
                if size > self.semantic_max_characters and len(chunk.clauses) == 1:
                    continue
                if current and (
                    len(current) >= self.semantic_batch_size
                    or chars + size > self.semantic_max_characters
                    or clause_count + len(chunk.clauses) > SEMANTIC_MAX_CLAUSES_PER_CALL
                ):
                    stop = True
                    break
                current.append(chunk)
                chars += size
                clause_count += len(chunk.clauses)
            if stop:
                break
        if current:
            jobs.append(current)
        calls = 0
        if jobs:
            calls, _leftover_jobs, _failed = self._compile_jobs(runtime, jobs)
        evaluated_ids = list(dict.fromkeys(
            [packet.product_id for job in jobs for packet in job]
            + [packet.product_id for packet in empty_only]
        ))
        unique = [runtime.candidate_products[pid] for pid in evaluated_ids if pid in runtime.candidate_products]
        if unique:
            self._evaluate(runtime, unique, merge=True)
            self._rank_once(runtime)
        frontier_packets = self._semantic_frontier_packets(runtime)
        pending = []
        failed = []
        unresolved = 0
        interpreted = False
        reviewed = []
        error_code = None
        for packet in frontier_packets:
            remaining = self._remaining_compile_clauses(runtime, packet)
            if packet.cache_key in runtime.semantic_failures:
                failed.append(packet)
                error_code = error_code or runtime.semantic_failures[packet.cache_key]
                unresolved += len(packet.clauses) or 1
                continue
            if remaining:
                pending.append(packet)
            results = runtime.semantic_cache.get(packet.cache_key, ())
            if packet.cache_key in runtime.semantic_cache:
                reviewed.append(packet.product_id)
            for clause in results:
                if clause.expression is None:
                    unresolved += 1
                else:
                    interpreted = True
                    if '"UNKNOWN"' in clause.expression.model_dump_json():
                        unresolved += 1
            unresolved += sum(c.reward_pp is None for c in packet.clauses)
            unresolved += sum(len(c.text) > SEMANTIC_MAX_CLAUSE_CHARS for c in packet.clauses)
            unresolved += len(packet.empty_clauses)
            unresolved += len(packet.untargeted_clauses)
        if failed and not interpreted and not pending:
            status = "FAILED"
        elif interpreted and (pending or failed):
            status = "PARTIAL"
        elif pending:
            status = "PENDING"
        else:
            status = "COMPLETE"
        runtime.semantic_review = SemanticReviewState(
            status=status,
            reviewed_product_ids=reviewed,
            pending_product_count=len(pending),
            unresolved_clause_count=unresolved,
            ai_interpreted=interpreted,
            calls_this_turn=calls,
            error_code=error_code,
        )
        runtime.audit.emit("SEMANTIC_AUGMENTATION", AuditEventType.SEMANTIC_CONDITIONS_ANALYZED,
                           output_data=runtime.semantic_review,
                           payload={"compiled_product_ids": [p.product_id for job in jobs for p in job],
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
        return review.status in {"PENDING", "PARTIAL", "FAILED"} or review.unresolved_clause_count > 0
