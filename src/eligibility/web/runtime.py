from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from eligibility.application import QuestionGenerator
from eligibility.application_service import ApplicationService
from eligibility.audit import AuditEventType
from eligibility.conversation import ConversationOrchestrator
from eligibility.catalog import load_default_product_catalog
from eligibility.catalog.loader import CATALOG_MODE_ENV, CATALOG_PATH_ENV
from eligibility.catalog.normalized_loader import repository_root
from eligibility.llm import LLMConfigurationError, LLMGateway, LLMSettings
from eligibility.search.intent import IntentParser
from eligibility.search.questions import RankingAwareQuestionPlanner
from eligibility.search.recommendation import RecommendationService
from eligibility.web.debug_trace import DebugTraceStore


# Preserve the complete conversation, LLM boundary, state changes, questions,
# and final ranking while avoiding tens of thousands of repeated rule events in
# ordinary Web sessions. Full rule outcomes remain available on each evaluation.
_WEB_AUDIT_EVENT_TYPES = frozenset(
    {
        AuditEventType.LLM_CALL_STARTED,
        AuditEventType.LLM_CALL_COMPLETED,
        AuditEventType.LLM_CALL_FAILED,
        AuditEventType.FACT_CONFLICT_DETECTED,
        AuditEventType.USER_FACT_RECEIVED,
        AuditEventType.SEARCH_SESSION_CREATED,
        AuditEventType.SEARCH_INTENT_PARSED,
        AuditEventType.SEARCH_INTENT_UPDATED,
        AuditEventType.INTENT_CONFLICT_DETECTED,
        AuditEventType.CLARIFICATION_REQUESTED,
        AuditEventType.CLARIFICATION_RESOLVED,
        AuditEventType.QUESTION_SELECTED,
        AuditEventType.USER_ANSWER_SUPERSEDED,
        AuditEventType.USER_DECLARED_FACT_SUPERSEDED_BY_INTENT_UPDATE,
        AuditEventType.PRODUCT_CONTRIBUTION_INPUT_REQUESTED,
        AuditEventType.PRODUCT_CONTRIBUTION_INPUT_RECORDED,
        AuditEventType.CONTRIBUTION_FEASIBILITY_CLARIFICATION_CREATED,
        AuditEventType.CONTRIBUTION_ANSWER_REJECTED,
        AuditEventType.PRODUCT_EXCLUDED_BY_USER,
        AuditEventType.GLOBAL_AFFORDABILITY_UPDATED,
        AuditEventType.USER_STATE_REVISION_REQUESTED,
        AuditEventType.CONVERSATION_TURN_RECEIVED,
        AuditEventType.MUTABLE_SEARCH_STATE_UPDATED,
        AuditEventType.CONVERSATION_TURN_ROLLED_BACK,
        AuditEventType.PRODUCT_CONTRIBUTION_CHOICE_SUPERSEDED,
        AuditEventType.PRODUCT_CHOICE_UPDATED,
        AuditEventType.PRODUCT_EXCLUSION_CHANGED,
        AuditEventType.DEPENDENCY_INVALIDATED,
        AuditEventType.QUESTION_REOPENED,
        AuditEventType.AUTHORITATIVE_REVISION_REJECTED,
        AuditEventType.SEARCH_RECALCULATED,
        AuditEventType.MUTABLE_VALUE_CLEARED,
        AuditEventType.CURRENT_RESULTS_REQUESTED,
        AuditEventType.WORKING_NOTE_CREATED,
        AuditEventType.WORKING_NOTE_UPDATED,
        AuditEventType.WORKING_NOTE_REBUILT,
        AuditEventType.WORKING_NOTE_DELETED,
        AuditEventType.WORKING_NOTE_WRITE_FAILED,
        AuditEventType.RANKING_CALCULATED,
        AuditEventType.TOP_K_STABILITY_CHECKED,
        AuditEventType.RECOMMENDATION_CREATED,
        AuditEventType.PRODUCT_DETAIL_OPENED,
        AuditEventType.EXPLANATION_GENERATED,
    }
)


@dataclass(frozen=True)
class WebRuntime:
    service: ApplicationService
    sample_user_id: str | None
    user_data_mode: str
    product_count: int
    llm_enabled: bool
    llm_provider: str
    llm_model: str
    llm_api_family: str
    llm_gateway: LLMGateway | None = None
    llm_configuration_error: str | None = None
    debug_trace_store: DebugTraceStore | None = None


def _catalog_history_metadata(product_count: int) -> dict[str, Any]:
    """Describe the published catalog used by a captured conversation."""

    configured = os.environ.get(CATALOG_PATH_ENV)
    mode = os.environ.get(CATALOG_MODE_ENV, "NORMALIZED").strip().upper()
    index_path: Path | None = None
    if configured:
        candidate = Path(configured).expanduser().resolve()
        index_path = candidate / "index.json" if candidate.is_dir() else candidate
    elif mode == "NORMALIZED":
        index_path = (
            repository_root()
            / "data/financial_products/normalized/index.json"
        )

    metadata: dict[str, Any] = {
        "mode": mode,
        "runtime_product_count": product_count,
    }
    if index_path is None or not index_path.is_file():
        return metadata
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return metadata
    if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
        return metadata
    metadata.update(
        {
            "index_path": str(index_path),
            "index_sha256": "sha256:" + hashlib.sha256(index_path.read_bytes()).hexdigest(),
            "index_version": payload.get("index_version"),
            "publication_status": payload.get("publication_status"),
            "source_staging_batch": payload.get("source_staging_batch"),
            "source_staging_revision": payload.get("source_staging_revision"),
            "published_product_count": payload.get("product_count"),
            "generated_at": payload.get("generated_at"),
        }
    )
    return metadata


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
    products = load_default_product_catalog()
    history_dir = Path(
        os.environ.get(
            "ELIGIBILITY_DEBUG_HISTORY_DIR",
            str(repository_root() / ".runtime/debug-history"),
        )
    )
    debug_trace_store = DebugTraceStore(
        history_dir=history_dir,
        runtime_metadata={
            "server_started_at": datetime.now(timezone.utc).isoformat(),
            "backend_version": "0.4.6",
            "llm_provider": settings.provider,
            "llm_model": settings.model,
            "catalog": _catalog_history_metadata(len(products)),
        },
    )
    if settings.provider != "MOCK":
        try:
            gateway = LLMGateway.from_settings(
                settings,
                debug_observer=debug_trace_store.observer(),
            )
        except LLMConfigurationError as exc:
            configuration_error = str(exc)

    service = ApplicationService(
        products,
        user_fact_stores={},
        intent_parser=IntentParser(gateway),
        question_planner=RankingAwareQuestionPlanner(
            # Questions are grounded entirely in the selected rule and its
            # missing fact.  Keep their wording deterministic so an answer
            # does not wait for a second, unnecessary LLM request.
            question_generator=QuestionGenerator(),
            # Start with the visible Top K and let the planner's optimistic-bound
            # frontier add only challengers that can still beat the current Kth
            # product. A fixed 3x cohort asked dozens of conditions that could no
            # longer affect the final Top 5.
            exploration_depth_multiplier=1,
        ),
        # Detail cards are structured and their "한눈에 요약" copy is derived
        # in the browser.  Do not call an LLM for an explanation the Web UI
        # does not render.
        recommendation_service=RecommendationService(),
        conversation_orchestrator=(ConversationOrchestrator(gateway) if gateway else None),
        pre_search_enabled=True,
        audit_event_types=_WEB_AUDIT_EVENT_TYPES,
    )
    return WebRuntime(
        service=service,
        sample_user_id=None,
        user_data_mode="CONVERSATIONAL_INPUT",
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
        debug_trace_store=debug_trace_store,
    )
