from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eligibility.audit import AuditEventType, AuditSession, canonical_hash
from eligibility.ingestion.knowledge_draft import ProductKnowledgeDraft
from eligibility.ingestion.validators import ValidationReport


class DraftReviewState(StrEnum):
    DRAFT = "DRAFT"
    INVALID_DRAFT = "INVALID_DRAFT"
    SCHEMA_VALID = "SCHEMA_VALID"
    SEMANTIC_VALID = "SEMANTIC_VALID"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACTIVE = "ACTIVE"


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft: ProductKnowledgeDraft | None = None
    state: DraftReviewState
    state_history: list[DraftReviewState] = Field(default_factory=list)
    schema_report: ValidationReport
    semantic_report: ValidationReport | None = None
    draft_hash: str | None = None
    reviewer_id: str | None = None
    review_note: str | None = None
    reviewed_at: datetime | None = None


class ActiveRuleVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_id: str
    draft: ProductKnowledgeDraft
    activated_at: datetime
    reviewer_id: str
    draft_hash: str


class InMemoryProductRuleStore:
    def __init__(self) -> None:
        self._versions: list[ActiveRuleVersion] = []

    def append_active(self, version: ActiveRuleVersion) -> None:
        self._versions.append(version)

    @property
    def versions(self) -> tuple[ActiveRuleVersion, ...]:
        return tuple(self._versions)


def approve_rule_draft(
    record: ReviewRecord,
    *,
    reviewer_id: str,
    note: str | None = None,
    audit: AuditSession | None = None,
) -> ReviewRecord:
    if record.draft is None:
        raise ValueError("Cannot approve a missing/invalid draft")
    if record.semantic_report is None or not record.semantic_report.valid:
        raise ValueError("Cannot approve a semantically invalid draft")
    if record.draft_hash is not None and canonical_hash(record.draft) != record.draft_hash:
        raise ValueError("Draft changed after validation; re-run validation before approval")
    if record.state not in {
        DraftReviewState.SEMANTIC_VALID,
        DraftReviewState.REVIEW_REQUIRED,
    }:
        raise ValueError(f"Draft in state {record.state} cannot be approved")
    reviewed_at = datetime.now(timezone.utc)
    approved = record.model_copy(
        update={
            "state": DraftReviewState.APPROVED,
            "state_history": [*record.state_history, DraftReviewState.APPROVED],
            "reviewer_id": reviewer_id,
            "review_note": note,
            "reviewed_at": reviewed_at,
        },
        deep=True,
    )
    if audit is not None:
        audit.emit(
            "HUMAN_REVIEW",
            AuditEventType.HUMAN_REVIEW_RECORDED,
            entity_refs={"document_id": record.draft.document_id},
            input_data=record,
            output_data=approved,
            payload={
                "decision": "APPROVE",
                "reviewer_id": reviewer_id,
                "draft_hash": record.draft_hash,
            },
        )
    return approved


def reject_rule_draft(
    record: ReviewRecord,
    *,
    reviewer_id: str,
    note: str,
    audit: AuditSession | None = None,
) -> ReviewRecord:
    rejected = record.model_copy(
        update={
            "state": DraftReviewState.REJECTED,
            "state_history": [*record.state_history, DraftReviewState.REJECTED],
            "reviewer_id": reviewer_id,
            "review_note": note,
            "reviewed_at": datetime.now(timezone.utc),
        },
        deep=True,
    )
    if audit is not None:
        audit.emit(
            "HUMAN_REVIEW",
            AuditEventType.HUMAN_REVIEW_RECORDED,
            entity_refs={
                "document_id": record.draft.document_id if record.draft else "UNKNOWN"
            },
            input_data=record,
            output_data=rejected,
            payload={"decision": "REJECT", "reviewer_id": reviewer_id},
        )
    return rejected


def activate_approved_draft(
    record: ReviewRecord,
    *,
    store: InMemoryProductRuleStore | None = None,
) -> tuple[ReviewRecord, ActiveRuleVersion]:
    if record.state != DraftReviewState.APPROVED or record.draft is None:
        raise ValueError("Only APPROVED drafts can become ACTIVE")
    reviewer_id = record.reviewer_id
    if reviewer_id is None:
        raise ValueError("Approved draft has no reviewer_id")
    current_hash = canonical_hash(record.draft)
    if record.draft_hash is not None and current_hash != record.draft_hash:
        raise ValueError("Approved draft changed after review; activation blocked")
    draft_hash = record.draft_hash or current_hash
    version = ActiveRuleVersion(
        version_id=f"RULEVER-{draft_hash[:16]}",
        draft=record.draft,
        activated_at=datetime.now(timezone.utc),
        reviewer_id=reviewer_id,
        draft_hash=draft_hash,
    )
    active = record.model_copy(
        update={
            "state": DraftReviewState.ACTIVE,
            "state_history": [*record.state_history, DraftReviewState.ACTIVE],
        },
        deep=True,
    )
    if store is not None:
        store.append_active(version)
    return active, version
