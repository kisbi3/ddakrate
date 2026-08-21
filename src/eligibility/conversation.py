from __future__ import annotations

import json
from typing import Any

from eligibility.audit import canonical_hash
from eligibility.llm import LLMGateway, LLMPurpose
from eligibility.llm.system_prompts import CONVERSATION_ORCHESTRATION_SYSTEM_PROMPT
from eligibility.schema.conversation import ConversationPlan


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
