from __future__ import annotations

import json
import re
from typing import Any

from eligibility.audit import canonical_hash
from eligibility.llm import LLMGateway, LLMPurpose
from eligibility.llm.system_prompts import (
    ANSWER_PLAN_SYSTEM_PROMPT,
    CONVERSATION_ORCHESTRATION_SYSTEM_PROMPT,
    FLEXIBLE_CONVERSATION_TURN_SYSTEM_PROMPT,
    PRE_SEARCH_ANSWER_SYSTEM_PROMPT,
)
from eligibility.schema.conversation import (
    AnswerPlan,
    ConversationPlan,
    FlexibleConversationTurnPlan,
    PreSearchAnswerPlan,
)
from eligibility.search.institution_names import resolve_institution_references


class ConversationOrchestrator:
    """LLM-only natural-language router over narrow ApplicationService operations.

    The model decides what the user means and which domain operations to call. It
    never decides financial feasibility, rate, interest, eligibility, or ranking.
    Those remain deterministic backend responsibilities after the plan is returned.
    """

    def __init__(self, gateway: LLMGateway) -> None:
        self.gateway = gateway

    def interpret(self, message: str, *, context: dict[str, Any]) -> ConversationPlan:
        message = message.strip()
        if not message:
            raise ValueError("message must not be empty")
        return self._interpret_general(message, context)

    def interpret_turn(
        self,
        message: str,
        *,
        context: dict[str, Any],
    ) -> FlexibleConversationTurnPlan:
        """Interpret every explicit intent in one user turn with one LLM call."""

        message = message.strip()
        if not message:
            raise ValueError("message must not be empty")
        projected = self._project_context(message, context)
        prompt = (
            "CONVERSATION_CONTEXT_ENVELOPE:\n"
            + json.dumps(projected, ensure_ascii=False, indent=2, default=str)
            + "\n\nUSER_MESSAGE:\n<user_message>\n"
            + message
            + "\n</user_message>"
        )
        response = self.gateway.generate_structured(
            LLMPurpose.CONVERSATION_ORCHESTRATION,
            prompt,
            FlexibleConversationTurnPlan,
            system_prompt=FLEXIBLE_CONVERSATION_TURN_SYSTEM_PROMPT,
            metadata={
                "message_hash": canonical_hash(message),
                "context_hash": canonical_hash(projected),
                "context_schema_version": "flexible-conversation-v1",
                "single_call_turn": True,
            },
        )
        return response.data

    def _interpret_general(
        self,
        message: str,
        context: dict[str, Any],
    ) -> ConversationPlan:
        """Interpret the entire turn with one structured LLM call."""

        context = self._project_context(message, context)
        prompt = (
            "CURRENT_CONTEXT:\n"
            + json.dumps(context, ensure_ascii=False, indent=2, default=str)
            + "\n\nUSER_MESSAGE:\n<user_message>\n"
            + message
            + "\n</user_message>"
        )
        response = self.gateway.generate_structured(
            LLMPurpose.CONVERSATION_ORCHESTRATION,
            prompt,
            ConversationPlan,
            system_prompt=CONVERSATION_ORCHESTRATION_SYSTEM_PROMPT,
            metadata={
                "message_hash": canonical_hash(message),
                "context_hash": canonical_hash(context),
            },
        )
        return response.data

    def interpret_pre_search(
        self,
        message: str,
        *,
        context: dict[str, Any],
    ) -> PreSearchAnswerPlan:
        """Interpret one answer without allowing the model to choose a question."""

        message = message.strip()
        if not message:
            raise ValueError("message must not be empty")
        active = context.get("ACTIVE_QUESTION") or {}
        current_intent = context.get("CURRENT_SEARCH_INTENT") or {}
        profile = context.get("PRE_SEARCH_PROFILE") or {}
        active_key = active.get("pre_search_key")
        focused_context = {
            "question_id": active.get("question_id"),
            "question_key": active_key,
            "question": active.get("question"),
            "explanation": active.get("explanation"),
            "answer_examples": active.get("answer_examples") or [],
            "current_state": {
                "product_types": current_intent.get("product_types") or [],
                "contribution_plan": current_intent.get("contribution_plan"),
                "ranking_objective": current_intent.get("ranking_objective"),
                "active_profile_entry": profile.get(active_key) if active_key else None,
            },
        }
        prompt = (
            "ACTIVE_PRE_SEARCH_QUESTION:\n"
            + json.dumps(focused_context, ensure_ascii=False, separators=(",", ":"), default=str)
            + "\nUSER_MESSAGE:\n<user_message>"
            + message
            + "</user_message>"
        )
        response = self.gateway.generate_structured(
            LLMPurpose.CONVERSATION_ORCHESTRATION,
            prompt,
            PreSearchAnswerPlan,
            system_prompt=PRE_SEARCH_ANSWER_SYSTEM_PROMPT,
            metadata={
                "message_hash": canonical_hash(message),
                "active_question_id": active.get("question_id"),
                "pre_search_answer": True,
            },
        )
        result = response.data
        return result

    def interpret_answer_plan(
        self,
        message: str,
        *,
        context: dict[str, Any],
    ) -> AnswerPlan:
        """Interpret an active condition answer through its own strict schema."""

        message = message.strip()
        if not message:
            raise ValueError("message must not be empty")
        projected = self._project_context(message, context)
        active = projected.get("ACTIVE_QUESTION") or {}
        allowed = projected.get("ALLOWED_OPERATIONS") or {}
        focused = {
            "ACTIVE_QUESTION": active,
            "ALLOWED_VARIABLE_IDS": allowed.get("condition_variable_ids") or [],
            "ALLOWED_INSTITUTION_IDS": allowed.get("institution_ids") or [],
        }
        prompt = (
            "ANSWER_PLAN_CONTEXT:\n"
            + json.dumps(focused, ensure_ascii=False, separators=(",", ":"), default=str)
            + "\nUSER_MESSAGE:\n<user_message>"
            + message
            + "</user_message>"
        )
        response = self.gateway.generate_structured(
            LLMPurpose.CONVERSATION_ORCHESTRATION,
            prompt,
            AnswerPlan,
            system_prompt=ANSWER_PLAN_SYSTEM_PROMPT,
            metadata={
                "message_hash": canonical_hash(message),
                "active_question_id": active.get("question_id"),
                "answer_plan": True,
            },
        )
        return response.data

    @staticmethod
    def _project_context(message: str, context: dict[str, Any]) -> dict[str, Any]:
        projected = dict(context)
        catalog = list(projected.get("PRODUCT_CATALOG_SUMMARY") or [])
        compact = "".join(message.split()).casefold()
        institution_catalog = list(projected.get("INSTITUTION_CATALOG_SUMMARY") or [])
        institution_resolution = resolve_institution_references(
            message,
            institution_catalog,
        )
        resolved_institution_ids = {
            item["institution_id"] for item in institution_resolution.resolved
        }
        mentioned = []
        for row in catalog:
            parts = str(row).split("|", 2)
            institution_id = parts[1] if len(parts) == 3 else ""
            product_name = parts[2] if len(parts) == 3 else ""
            product_tokens = [
                token for token in re.split(r"[\s·()/_-]+", product_name)
                if len(token) >= 3
            ]
            if institution_id in resolved_institution_ids or any(
                token.casefold() in compact for token in product_tokens
            ):
                mentioned.append(row)
        projected["PRODUCT_CATALOG_SUMMARY"] = mentioned[:12]
        projected["INSTITUTION_CATALOG_SUMMARY"] = list(
            institution_resolution.resolved
        )[:12]
        projected["AMBIGUOUS_INSTITUTION_REFERENCES"] = list(
            institution_resolution.ambiguous
        )[:6]
        # Do not expose the full institution-ID allowlist to the model. It may
        # mutate only an institution resolved from this utterance, or undo an
        # institution exclusion already visible in mutable session state.
        allowed_operations = dict(projected.get("ALLOWED_OPERATIONS") or {})
        mutable_state = projected.get("MUTABLE_SEARCH_STATE") or {}
        currently_excluded = {
            str(item)
            for item in mutable_state.get("excluded_institution_ids", [])
        }
        allowed_operations["institution_ids"] = sorted(
            resolved_institution_ids | currently_excluded
        )
        projected["ALLOWED_OPERATIONS"] = allowed_operations
        evidence_index = projected.pop("_PRODUCT_EVIDENCE_INDEX", {}) or {}
        existing_evidence = list(projected.get("REFERENCED_PRODUCT_EVIDENCE") or [])
        existing_ids = {
            item.get("product_id")
            for item in existing_evidence
            if isinstance(item, dict)
        }
        mentioned_ids = [str(row).split("|", 1)[0] for row in mentioned[:12]]
        existing_evidence.extend(
            evidence_index[product_id]
            for product_id in mentioned_ids
            if product_id in evidence_index and product_id not in existing_ids
        )
        projected["REFERENCED_PRODUCT_EVIDENCE"] = existing_evidence[:16]
        return projected
