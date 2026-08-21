from __future__ import annotations

from dataclasses import dataclass

from eligibility.application import QuestionGenerator
from eligibility.application_service import ApplicationService
from eligibility.conversation import ConversationOrchestrator
from eligibility.catalog import load_default_product_catalog
from eligibility.fixtures.user_001 import USER_ID, user_001
from eligibility.llm import LLMConfigurationError, LLMGateway, LLMSettings
from eligibility.search.intent import IntentParser
from eligibility.search.questions import RankingAwareQuestionPlanner
from eligibility.search.recommendation import GroundedResultExplainer, RecommendationService


@dataclass(frozen=True)
class WebRuntime:
    service: ApplicationService
    sample_user_id: str
    product_count: int
    llm_enabled: bool
    llm_provider: str
    llm_model: str
    llm_api_family: str
    llm_gateway: LLMGateway | None = None
    llm_configuration_error: str | None = None


def build_web_runtime() -> WebRuntime:
    """Build the Web MVP runtime from the executable product catalog and environment settings.

    The deployed MVP uses the same ApplicationService as REST/MCP.  When an
    external OpenAI-compatible LLM is configured, the same gateway is shared by
    intent parsing, question wording, result explanation, and conversational
    operation selection.  With the default MOCK setting, deterministic fallbacks
    remain available for initial intent/question/detail rendering, while free-form
    follow-up conversation is deliberately disabled instead of pretending that a
    mock model understood the user.
    """

    settings = LLMSettings.from_env()
    gateway: LLMGateway | None = None
    configuration_error: str | None = None
    if settings.provider != "MOCK":
        try:
            gateway = LLMGateway.from_settings(settings)
        except LLMConfigurationError as exc:
            configuration_error = str(exc)

    products = load_default_product_catalog()

    service = ApplicationService(
        products,
        user_fact_stores={USER_ID: user_001()},
        intent_parser=IntentParser(gateway),
        question_planner=RankingAwareQuestionPlanner(
            question_generator=QuestionGenerator(gateway)
        ),
        recommendation_service=RecommendationService(
            explainer=GroundedResultExplainer(gateway)
        ),
        conversation_orchestrator=(ConversationOrchestrator(gateway) if gateway else None),
    )
    return WebRuntime(
        service=service,
        sample_user_id=USER_ID,
        product_count=len(products),
        llm_enabled=gateway is not None,
        llm_provider=settings.provider,
        llm_model=settings.model,
        llm_api_family=(
            "RESPONSES" if settings.provider == "OPENAI"
            else "CHAT_COMPLETIONS" if settings.provider == "OPENAI_COMPATIBLE"
            else "MOCK"
        ),
        llm_gateway=gateway,
        llm_configuration_error=configuration_error,
    )
