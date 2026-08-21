from __future__ import annotations

from eligibility.llm.models import LLMProfile, LLMPurpose


DEFAULT_PROFILES: dict[LLMPurpose, LLMProfile] = {
    LLMPurpose.RULE_EXTRACTION: LLMProfile(
        purpose=LLMPurpose.RULE_EXTRACTION,
        prompt_template_id="financial-rule-extraction",
        prompt_template_version="0.3.1",
        response_schema_version="0.3.1",
        temperature=0.0,
        timeout_seconds=60.0,
    ),
    LLMPurpose.SERVICE_EXTRACTION: LLMProfile(
        purpose=LLMPurpose.SERVICE_EXTRACTION,
        prompt_template_id="institution-service-extraction",
        prompt_template_version="0.3.1",
        response_schema_version="0.3.1",
        temperature=0.0,
        timeout_seconds=60.0,
    ),
    LLMPurpose.INTENT_PARSING: LLMProfile(
        purpose=LLMPurpose.INTENT_PARSING,
        prompt_template_id="user-intent-parsing",
        prompt_template_version="0.4.7",
        response_schema_version="0.4.7",
        temperature=0.0,
        timeout_seconds=20.0,
    ),
    LLMPurpose.CONVERSATION_ORCHESTRATION: LLMProfile(
        purpose=LLMPurpose.CONVERSATION_ORCHESTRATION,
        prompt_template_id="conversation-state-orchestration",
        prompt_template_version="0.4.7",
        response_schema_version="0.4.7",
        temperature=0.0,
        timeout_seconds=20.0,
    ),
    LLMPurpose.INTENT_CLARIFICATION: LLMProfile(
        purpose=LLMPurpose.INTENT_CLARIFICATION,
        prompt_template_id="intent-conflict-clarification",
        prompt_template_version="0.4.7",
        response_schema_version="0.4.7",
        temperature=0.0,
        timeout_seconds=20.0,
    ),
    LLMPurpose.QUESTION_GENERATION: LLMProfile(
        purpose=LLMPurpose.QUESTION_GENERATION,
        prompt_template_id="missing-fact-question",
        prompt_template_version="0.4.7",
        response_schema_version="0.4.7",
        temperature=0.0,
        timeout_seconds=20.0,
    ),
    LLMPurpose.RESULT_EXPLANATION: LLMProfile(
        purpose=LLMPurpose.RESULT_EXPLANATION,
        prompt_template_id="evaluation-trace-explanation",
        prompt_template_version="0.4.7",
        response_schema_version=None,
        temperature=0.1,
        timeout_seconds=30.0,
    ),
}


def get_profile(purpose: LLMPurpose) -> LLMProfile:
    return DEFAULT_PROFILES[purpose]
