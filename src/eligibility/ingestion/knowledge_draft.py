from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eligibility.schema.enums import (
    AchievementMode,
    EvaluationPhase,
    FactSemanticType,
    RewardType,
    RewardUnit,
    RulePurpose,
    TermUnit,
)


class StrictDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftProvenance(StrictDraftModel):
    document_id: str
    document_name: str
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    source_url: str | None = None
    source_text: str | None = None


class ProductMetadataDraft(StrictDraftModel):
    product_id: str | None = None
    institution_id: str | None = None
    name: str | None = None
    product_type: str | None = None
    contract_value: int | None = Field(default=None, gt=0)
    contract_unit: TermUnit | None = None
    base_rate: Decimal | None = Field(default=None, ge=0)
    advertised_max_rate: Decimal | None = Field(default=None, ge=0)
    preferential_rate_cap: Decimal | None = Field(default=None, ge=0)


class RewardDraft(StrictDraftModel):
    type: RewardType = RewardType.INTEREST_RATE
    value: Decimal = Field(ge=0)
    unit: RewardUnit = RewardUnit.PERCENTAGE_POINT


class RuleDraft(StrictDraftModel):
    rule_id: str
    name: str
    purpose: RulePurpose
    evaluation_phase: EvaluationPhase = EvaluationPhase.PRE_SUBSCRIPTION
    achievement_mode: AchievementMode | None = None
    ast: dict[str, Any]
    reward: RewardDraft | None = None
    provenance: list[DraftProvenance] = Field(default_factory=list)
    extraction_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ServiceReference(StrictDraftModel):
    institution_id: str
    service_name: str
    concept_name: str
    reference_type: str = "REQUIRES_SERVICE_DEFINITION"
    used_by_rule_ids: list[str] = Field(default_factory=list)
    provenance: list[DraftProvenance] = Field(default_factory=list)


class RequiredFactDraft(StrictDraftModel):
    fact_type: str
    semantic_type: FactSemanticType
    authority_hint: str | None = None
    used_by_rule_ids: list[str] = Field(default_factory=list)
    description: str | None = None


class UnresolvedItem(StrictDraftModel):
    issue_type: str
    description: str
    related_rule_ids: list[str] = Field(default_factory=list)
    provenance: list[DraftProvenance] = Field(default_factory=list)


class DraftWarning(StrictDraftModel):
    code: str
    message: str
    related_rule_ids: list[str] = Field(default_factory=list)


class ExtractionMetadata(StrictDraftModel):
    extractor_profile: str = "RULE_EXTRACTION"
    prompt_template_id: str | None = None
    prompt_template_version: str | None = None
    response_schema_version: str = "0.3.1"
    provider: str | None = None
    model: str | None = None
    model_version: str | None = None
    document_hash: str | None = None


class ProductKnowledgeDraft(StrictDraftModel):
    document_id: str
    product_metadata: ProductMetadataDraft = Field(default_factory=ProductMetadataDraft)
    rules: list[RuleDraft] = Field(default_factory=list)
    service_references: list[ServiceReference] = Field(default_factory=list)
    required_facts: list[RequiredFactDraft] = Field(default_factory=list)
    unresolved_items: list[UnresolvedItem] = Field(default_factory=list)
    warnings: list[DraftWarning] = Field(default_factory=list)
    extraction_metadata: ExtractionMetadata = Field(default_factory=ExtractionMetadata)
