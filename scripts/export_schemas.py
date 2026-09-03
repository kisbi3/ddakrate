from __future__ import annotations

import json
from pathlib import Path

from eligibility.audit import AuditEvent
from eligibility.application import UserAnswerRecord, UserAnswerSubmission
from eligibility.fx import FxEstimate, FxQuote, PlannedMonetaryAmount
from eligibility.goal import GoalInstance
from eligibility.ingestion import ProductKnowledgeDraft
from eligibility.schema.application_input import QuickInputProfile
from eligibility.schema.conversation import (
    AnswerPlan,
    ConversationAction,
    ConversationPlan,
    ConversationTurnResult,
    QuantitativeConditionValue,
)
from eligibility.schema.condition_requirement import (
    CompletionResult,
    ConditionRequirement,
    QuestionSpec,
    RequirementCompilationMetrics,
    RequirementCompilationResult,
    UserConditionState,
)
from eligibility.schema.evaluation import ProductEvaluation
from eligibility.schema.institution_service import InstitutionServiceDefinition
from eligibility.schema.product import ContributionPolicy, ProductFeature, ProductMetadata
from eligibility.schema.rule import (
    RULE_NODE_ADAPTER,
    ActionPath,
    FactAcceptancePolicy,
)
from eligibility.schema.search import (
    CandidateEvaluation,
    ClarificationRequest,
    ContributionPlan,
    ContributionOptionFeasibility,
    ContributionFeasibilityClarification,
    ProductContributionChoice,
    IntentConflict,
    IntentPatch,
    MissingRankingInput,
    PlannedQuestion,
    ProductRecommendationDetail,
    ProductRecommendationResult,
    ProductSearchIntent,
    RecommendationListItem,
    RankingResult,
    SearchSession,
)
from eligibility.schema.user_fact import RecurringPaymentEvent, UserFactStore
from eligibility.llm.grounding import CanonicalQuestionPayload
from eligibility.search.query_tools import ProductQuerySpec


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
SCHEMA_VERSION = "v0.4.6"


def write_schema(filename: str, schema: dict) -> None:
    destination = SCHEMA_DIR / filename
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(destination)


def versioned(name: str) -> str:
    return f"{name}.{SCHEMA_VERSION}.schema.json"


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    write_schema(versioned("rule_ast"), RULE_NODE_ADAPTER.json_schema())
    write_schema(versioned("user_fact_store"), UserFactStore.model_json_schema())
    write_schema(versioned("product_evaluation"), ProductEvaluation.model_json_schema())
    write_schema(
        versioned("institution_service"),
        InstitutionServiceDefinition.model_json_schema(),
    )
    write_schema(versioned("audit_event"), AuditEvent.model_json_schema())
    write_schema(
        versioned("product_knowledge_draft"),
        ProductKnowledgeDraft.model_json_schema(),
    )
    write_schema(versioned("goal_instance"), GoalInstance.model_json_schema())

    # Application-layer contracts added by the v0.3.1 hardening sprint.
    write_schema(versioned("product_metadata"), ProductMetadata.model_json_schema())
    write_schema(versioned("product_feature"), ProductFeature.model_json_schema())
    write_schema(
        versioned("contribution_policy"), ContributionPolicy.model_json_schema()
    )
    write_schema(
        versioned("recurring_payment_event"),
        RecurringPaymentEvent.model_json_schema(),
    )
    write_schema(
        versioned("planned_monetary_amount"),
        PlannedMonetaryAmount.model_json_schema(),
    )
    write_schema(versioned("fx_quote"), FxQuote.model_json_schema())
    write_schema(versioned("fx_estimate"), FxEstimate.model_json_schema())
    write_schema(
        versioned("quick_input_profile"), QuickInputProfile.model_json_schema()
    )
    write_schema(versioned("action_path"), ActionPath.model_json_schema())
    write_schema(
        versioned("fact_acceptance_policy"),
        FactAcceptancePolicy.model_json_schema(),
    )
    write_schema(
        versioned("user_answer_submission"),
        UserAnswerSubmission.model_json_schema(),
    )

    # Personalized search and recommendation contracts added by v0.4.
    write_schema(versioned("user_answer_record"), UserAnswerRecord.model_json_schema())
    write_schema(versioned("canonical_question_payload"), CanonicalQuestionPayload.model_json_schema())
    write_schema(versioned("contribution_plan"), ContributionPlan.model_json_schema())
    write_schema(
        versioned("contribution_option_feasibility"),
        ContributionOptionFeasibility.model_json_schema(),
    )
    write_schema(
        versioned("contribution_feasibility_clarification"),
        ContributionFeasibilityClarification.model_json_schema(),
    )
    write_schema(
        versioned("product_contribution_choice"),
        ProductContributionChoice.model_json_schema(),
    )
    write_schema(versioned("product_search_intent"), ProductSearchIntent.model_json_schema())
    write_schema(versioned("intent_patch"), IntentPatch.model_json_schema())
    write_schema(versioned("missing_ranking_input"), MissingRankingInput.model_json_schema())
    write_schema(versioned("intent_conflict"), IntentConflict.model_json_schema())
    write_schema(versioned("clarification_request"), ClarificationRequest.model_json_schema())
    write_schema(versioned("search_session"), SearchSession.model_json_schema())
    write_schema(versioned("candidate_evaluation"), CandidateEvaluation.model_json_schema())
    write_schema(versioned("planned_question"), PlannedQuestion.model_json_schema())
    write_schema(versioned("ranking_result"), RankingResult.model_json_schema())
    write_schema(versioned("recommendation_list_item"), RecommendationListItem.model_json_schema())
    write_schema(
        versioned("product_recommendation_result"),
        ProductRecommendationResult.model_json_schema(),
    )
    write_schema(
        versioned("product_recommendation_detail"),
        ProductRecommendationDetail.model_json_schema(),
    )
    write_schema(versioned("conversation_action"), ConversationAction.model_json_schema())
    write_schema(versioned("conversation_plan"), ConversationPlan.model_json_schema())
    write_schema(versioned("conversation_turn_result"), ConversationTurnResult.model_json_schema())
    write_schema(versioned("answer_plan"), AnswerPlan.model_json_schema())
    write_schema(
        versioned("quantitative_condition_value"),
        QuantitativeConditionValue.model_json_schema(),
    )
    write_schema(versioned("product_query_spec"), ProductQuerySpec.model_json_schema())
    write_schema(versioned("condition_requirement"), ConditionRequirement.model_json_schema())
    write_schema(versioned("user_condition_state"), UserConditionState.model_json_schema())
    write_schema(versioned("question_spec"), QuestionSpec.model_json_schema())
    write_schema(versioned("completion_result"), CompletionResult.model_json_schema())
    write_schema(
        versioned("requirement_compilation_metrics"),
        RequirementCompilationMetrics.model_json_schema(),
    )
    write_schema(
        versioned("requirement_compilation_result"),
        RequirementCompilationResult.model_json_schema(),
    )


if __name__ == "__main__":
    main()
