from __future__ import annotations

import json
from typing import Iterable

from eligibility.audit import AuditEventType, AuditSession, canonical_hash
from eligibility.ingestion.document import DocumentSection
from eligibility.ingestion.knowledge_draft import ProductKnowledgeDraft
from eligibility.ingestion.review import DraftReviewState, ReviewRecord
from eligibility.ingestion.validators import (
    ProductKnowledgeSchemaValidator,
    SemanticValidator,
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)
from eligibility.llm import LLMGateway, LLMPurpose, LLMStructuredOutputError
from eligibility.llm.system_prompts import RULE_EXTRACTION_SYSTEM_PROMPT


class RuleExtractor:
    def __init__(
        self,
        gateway: LLMGateway,
        *,
        semantic_validator: SemanticValidator | None = None,
        audit: AuditSession | None = None,
    ) -> None:
        self.gateway = gateway
        self.audit = audit or gateway.audit
        self.semantic_validator = semantic_validator or SemanticValidator(self.audit)

    def extract(self, sections: Iterable[DocumentSection]) -> ReviewRecord:
        section_list = list(sections)
        if not section_list:
            raise ValueError("At least one DocumentSection is required")
        document_ids = {section.document_id for section in section_list}
        if len(document_ids) != 1:
            raise ValueError("One extraction call must contain exactly one document_id")
        document_id = section_list[0].document_id
        prompt_payload = {
            "document_id": document_id,
            "sections": [section.model_dump(mode="json") for section in section_list],
        }
        if self.audit is not None:
            self.audit.emit(
                "DOCUMENT_PARSER",
                AuditEventType.DOCUMENT_PARSED,
                entity_refs={"document_id": document_id},
                input_data=section_list,
                output_data=prompt_payload,
                payload={
                    "document_id": document_id,
                    "section_count": len(section_list),
                    "pages": sorted(
                        {section.page for section in section_list if section.page is not None}
                    ),
                },
            )

        try:
            response = self.gateway.generate_structured(
                LLMPurpose.RULE_EXTRACTION,
                json.dumps(prompt_payload, ensure_ascii=False, indent=2),
                ProductKnowledgeDraft,
                system_prompt=RULE_EXTRACTION_SYSTEM_PROMPT,
                metadata={
                    "input_document_id": document_id,
                    "input_document_hash": canonical_hash(prompt_payload),
                    "input_context_hash": canonical_hash(
                        [section.model_dump(mode="json") for section in section_list]
                    ),
                },
            )
        except LLMStructuredOutputError as exc:
            schema_report = ValidationReport(
                valid=False,
                issues=[
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="SCHEMA_VALIDATION_ERROR",
                        message=str(exc),
                    )
                ],
            )
            if self.audit is not None:
                self.audit.emit(
                    "SCHEMA_VALIDATOR",
                    AuditEventType.SCHEMA_VALIDATION_COMPLETED,
                    entity_refs={"document_id": document_id},
                    payload={"valid": False, "error_count": 1},
                )
            return ReviewRecord(
                state=DraftReviewState.INVALID_DRAFT,
                state_history=[DraftReviewState.DRAFT, DraftReviewState.INVALID_DRAFT],
                schema_report=schema_report,
            )

        profile = self.gateway.profiles[LLMPurpose.RULE_EXTRACTION]
        extraction_metadata = response.data.extraction_metadata.model_copy(
            update={
                "extractor_profile": LLMPurpose.RULE_EXTRACTION.value,
                "prompt_template_id": profile.prompt_template_id,
                "prompt_template_version": profile.prompt_template_version,
                "response_schema_version": profile.response_schema_version or "unspecified",
                "provider": response.provider,
                "model": response.model,
                "model_version": response.model_version,
                "document_hash": canonical_hash(prompt_payload),
            },
            deep=True,
        )
        normalized_response = response.data.model_copy(
            update={"extraction_metadata": extraction_metadata},
            deep=True,
        )
        draft, schema_report = ProductKnowledgeSchemaValidator.validate(
            normalized_response.model_dump(mode="json")
        )
        if self.audit is not None:
            self.audit.emit(
                "SCHEMA_VALIDATOR",
                AuditEventType.SCHEMA_VALIDATION_COMPLETED,
                entity_refs={"document_id": document_id},
                input_data=response.data,
                output_data=schema_report,
                payload={
                    "valid": schema_report.valid,
                    "error_count": len(schema_report.errors),
                },
            )
        if draft is None:
            return ReviewRecord(
                state=DraftReviewState.INVALID_DRAFT,
                state_history=[DraftReviewState.DRAFT, DraftReviewState.INVALID_DRAFT],
                schema_report=schema_report,
            )

        semantic_report = self.semantic_validator.validate(draft)
        history = [DraftReviewState.DRAFT, DraftReviewState.SCHEMA_VALID]
        if semantic_report.valid:
            history.append(DraftReviewState.SEMANTIC_VALID)
        history.append(DraftReviewState.REVIEW_REQUIRED)
        return ReviewRecord(
            draft=draft,
            state=DraftReviewState.REVIEW_REQUIRED,
            state_history=history,
            schema_report=schema_report,
            semantic_report=semantic_report,
            draft_hash=canonical_hash(draft),
        )
