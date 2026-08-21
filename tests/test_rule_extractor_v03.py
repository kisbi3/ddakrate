from __future__ import annotations

import pytest

from eligibility.audit import AuditEventType, AuditSession, InMemoryAuditSink
from eligibility.fixtures.extraction import (
    salary_envelope_document_sections_payload,
    salary_envelope_extraction_draft,
)
from eligibility.ingestion import (
    DocumentSection,
    DraftReviewState,
    InMemoryProductRuleStore,
    RuleExtractor,
    SemanticValidator,
    activate_approved_draft,
    approve_rule_draft,
    compare_drafts,
    product_definition_from_draft,
)
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter


def _extractor(response):
    sink = InMemoryAuditSink()
    audit = AuditSession(sink, request_id="REQ-EXTRACT", trace_id="TRACE-EXTRACT")
    gateway = LLMGateway(
        MockLLMAdapter({LLMPurpose.RULE_EXTRACTION: response}),
        audit=audit,
    )
    return RuleExtractor(gateway, audit=audit), sink, audit


def _sections():
    return [
        DocumentSection.model_validate(item)
        for item in salary_envelope_document_sections_payload()
    ]


def test_rule_extractor_returns_knowledge_draft():
    golden = salary_envelope_extraction_draft()
    extractor, sink, _ = _extractor(golden.model_dump(mode="json"))

    record = extractor.extract(_sections())

    assert record.state == DraftReviewState.REVIEW_REQUIRED
    assert record.draft is not None
    assert record.draft.document_id == "DOC-SHINHAN-YOUTH-20260722"
    assert record.schema_report.valid
    assert record.semantic_report is not None
    assert record.semantic_report.valid
    event_types = [event.event_type for event in sink.events]
    assert AuditEventType.DOCUMENT_PARSED in event_types
    assert AuditEventType.SCHEMA_VALIDATION_COMPLETED in event_types
    assert AuditEventType.SEMANTIC_VALIDATION_COMPLETED in event_types


def test_service_reference_is_not_dsl_operator():
    golden = salary_envelope_extraction_draft()
    extractor, _, _ = _extractor(golden.model_dump(mode="json"))

    record = extractor.extract(_sections())

    assert record.draft is not None
    assert record.draft.service_references[0].service_name == "급여클럽"
    assert record.draft.service_references[0].concept_name == "월급봉투"
    rate_ast = next(
        rule.ast
        for rule in record.draft.rules
        if rule.rule_id == "RATE_SALARY_ENVELOPE_6M"
    )
    assert rate_ast["type"] == "COUNT_DISTINCT_PERIODS"
    assert "HAS_SHINHAN" not in str(rate_ast)


def test_required_facts_extracted():
    golden = salary_envelope_extraction_draft()
    extractor, _, _ = _extractor(golden.model_dump(mode="json"))

    record = extractor.extract(_sections())

    assert record.draft is not None
    fact_types = {fact.fact_type for fact in record.draft.required_facts}
    assert "SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED" in fact_types
    assert "WILL_TRACK_SHINHAN_SALARY_ENVELOPE_6M" in fact_types


def test_provenance_preserved():
    golden = salary_envelope_extraction_draft()
    extractor, _, _ = _extractor(golden.model_dump(mode="json"))

    record = extractor.extract(_sections())

    assert record.draft is not None
    rate = next(
        rule
        for rule in record.draft.rules
        if rule.rule_id == "RATE_SALARY_ENVELOPE_6M"
    )
    assert rate.provenance[0].page == 2
    assert rate.provenance[0].section == "[1] 주거래 우대"
    assert "월급봉투" in (rate.provenance[0].source_text or "")


def test_schema_failure_blocks_activation():
    invalid = {
        "document_id": "DOC-BAD",
        "rules": [{"unexpected": "shape"}],
    }
    extractor, _, _ = _extractor(invalid)

    record = extractor.extract(_sections())

    assert record.state == DraftReviewState.INVALID_DRAFT
    assert record.draft is None
    assert not record.schema_report.valid
    with pytest.raises(ValueError):
        approve_rule_draft(record, reviewer_id="reviewer")


def test_human_review_boundary_blocks_automatic_activation():
    golden = salary_envelope_extraction_draft()
    extractor, _, audit = _extractor(golden.model_dump(mode="json"))
    record = extractor.extract(_sections())

    with pytest.raises(ValueError):
        activate_approved_draft(record)

    approved = approve_rule_draft(
        record, reviewer_id="HUMAN-001", note="AST and provenance checked", audit=audit
    )
    store = InMemoryProductRuleStore()
    active, version = activate_approved_draft(approved, store=store)

    assert approved.state == DraftReviewState.APPROVED
    assert active.state == DraftReviewState.ACTIVE
    assert version.reviewer_id == "HUMAN-001"
    assert store.versions == (version,)


def test_approved_draft_builds_product_definition():
    draft = salary_envelope_extraction_draft()

    product = product_definition_from_draft(draft)

    assert product.product_id == "SHINHAN_SALARY_ENVELOPE_6M_GOLDEN"
    assert product.preferential_rules[0].rule.type == "COUNT_DISTINCT_PERIODS"
    assert product.preferential_rules[0].reward.value == 1


def test_service_specific_operator_is_semantic_error():
    draft = salary_envelope_extraction_draft()
    rate = draft.rules[1].model_copy(
        update={
            "ast": {
                "type": "HAS_SHINHAN_SALARY_ENVELOPE",
                "rule_id": "RATE_SALARY_ENVELOPE_6M",
                "name": "bad institution-specific operator",
            }
        },
        deep=True,
    )
    bad = draft.model_copy(update={"rules": [draft.rules[0], rate]}, deep=True)

    report = SemanticValidator().validate(bad)

    assert not report.valid
    assert any(
        issue.code == "SERVICE_SPECIFIC_OPERATOR_USAGE" for issue in report.errors
    )


def test_missing_required_fact_is_semantic_error():
    draft = salary_envelope_extraction_draft()
    bad = draft.model_copy(
        update={
            "required_facts": [
                fact
                for fact in draft.required_facts
                if fact.fact_type != "SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED"
            ]
        },
        deep=True,
    )

    report = SemanticValidator().validate(bad)

    assert not report.valid
    assert any(issue.code == "MISSING_FACT_REFERENCE" for issue in report.errors)


def test_golden_diff_metrics_are_computable():
    golden = salary_envelope_extraction_draft()

    metrics = compare_drafts(golden, golden)

    assert metrics.rule_recall == 1.0
    assert metrics.hallucinated_rule_count == 0
    assert metrics.logical_structure_mismatch == 0
    assert metrics.service_reference_recall == 1.0
    assert metrics.required_fact_recall == 1.0
    assert metrics.provenance_match == 1.0


def test_extraction_metadata_is_gateway_generated_not_llm_trusted():
    golden = salary_envelope_extraction_draft()
    spoofed = golden.model_copy(
        update={
            "extraction_metadata": golden.extraction_metadata.model_copy(
                update={
                    "provider": "SPOOFED_PROVIDER",
                    "model": "spoofed-model",
                    "document_hash": "spoofed-hash",
                }
            )
        },
        deep=True,
    )
    extractor, _, _ = _extractor(spoofed.model_dump(mode="json"))

    record = extractor.extract(_sections())

    assert record.draft is not None
    metadata = record.draft.extraction_metadata
    assert metadata.provider == "MOCK"
    assert metadata.model == "mock-model"
    assert metadata.document_hash != "spoofed-hash"
    assert metadata.prompt_template_id == "financial-rule-extraction"


def test_draft_hash_integrity_blocks_post_validation_mutation():
    golden = salary_envelope_extraction_draft()
    extractor, _, _ = _extractor(golden.model_dump(mode="json"))
    record = extractor.extract(_sections())
    assert record.draft is not None

    record.draft.rules[0].name = "MUTATED AFTER VALIDATION"

    with pytest.raises(ValueError, match="changed after validation"):
        approve_rule_draft(record, reviewer_id="reviewer")
