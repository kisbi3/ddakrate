from __future__ import annotations

from decimal import Decimal

import pytest

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.extraction import salary_envelope_extraction_draft
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.ingestion import (
    DraftProvenance,
    SemanticValidator,
    product_definition_from_draft,
)
from eligibility.schema.product import MonthlyContributionPlan


def test_rule_draft_id_mismatch_is_validation_failure_and_activation_blocked():
    draft = salary_envelope_extraction_draft()
    rate = draft.rules[1]
    bad_ast = {**rate.ast, "rule_id": "DIFFERENT-AST-ID"}
    bad_rate = rate.model_copy(update={"ast": bad_ast}, deep=True)
    bad = draft.model_copy(update={"rules": [draft.rules[0], bad_rate]}, deep=True)

    report = SemanticValidator().validate(bad)

    assert report.valid is False
    assert any(issue.code == "RULE_IDENTITY_MISMATCH" for issue in report.errors)
    with pytest.raises(ValueError, match="rule_id mismatch"):
        product_definition_from_draft(bad)


def test_rule_draft_name_mismatch_is_an_explicit_error_policy():
    draft = salary_envelope_extraction_draft()
    rate = draft.rules[1]
    bad_ast = {**rate.ast, "name": "다른 실행 Rule 이름"}
    bad_rate = rate.model_copy(update={"ast": bad_ast}, deep=True)
    bad = draft.model_copy(update={"rules": [draft.rules[0], bad_rate]}, deep=True)

    report = SemanticValidator().validate(bad)

    assert report.valid is False
    assert any(issue.code == "RULE_NAME_IDENTITY_MISMATCH" for issue in report.errors)


def test_draft_provenance_materializes_into_active_rule_and_evaluation_trace():
    draft = salary_envelope_extraction_draft()
    rate = draft.rules[1]
    ast_without_source = {**rate.ast, "source": None}
    rate_without_source = rate.model_copy(update={"ast": ast_without_source}, deep=True)
    materialized_draft = draft.model_copy(
        update={"rules": [draft.rules[0], rate_without_source]}, deep=True
    )

    product = product_definition_from_draft(materialized_draft)
    executable_source = product.preferential_rules[0].rule.source

    assert executable_source is not None
    assert executable_source.document_id == "DOC-SHINHAN-YOUTH-20260722"
    assert executable_source.page == 2
    assert executable_source.section == "[1] 주거래 우대"
    assert "월급봉투" in (executable_source.source_text or "")

    store = salary_envelope_user().with_fact(
        future_intent_fact(SALARY_INTENT_FACT_TYPE, True)
    )
    evaluation = FinancialEligibilityEngine().evaluate_product(
        product, store, salary_envelope_context()
    )
    product_documents = [
        item
        for item in evaluation.preferential_rule_results[0].source_provenance
        if item.source_type == "PRODUCT_DOCUMENT"
    ]
    assert product_documents
    assert product_documents[0].details["document_id"] == "DOC-SHINHAN-YOUTH-20260722"
    assert product_documents[0].details["page"] == 2
    assert "월급봉투" in product_documents[0].details["source_text"]


def test_contribution_plan_changes_evaluation_identity_and_interest():
    product = product_definition_from_draft(salary_envelope_extraction_draft())
    store = salary_envelope_user().with_fact(
        future_intent_fact(SALARY_INTENT_FACT_TYPE, True)
    )
    engine = FinancialEligibilityEngine()

    low = engine.evaluate_product(
        product,
        store,
        salary_envelope_context(),
        MonthlyContributionPlan(monthly_amount=Decimal("100000"), months=12),
    )
    high = engine.evaluate_product(
        product,
        store,
        salary_envelope_context(),
        MonthlyContributionPlan(monthly_amount=Decimal("500000"), months=12),
    )

    assert low.evaluation_id != high.evaluation_id
    assert (
        low.interest_estimates["realizable"].pre_tax_interest
        != high.interest_estimates["realizable"].pre_tax_interest
    )


def test_conflicting_rule_draft_provenance_is_validation_error_and_blocks_activation():
    draft = salary_envelope_extraction_draft()
    rate = draft.rules[1]
    conflicting = DraftProvenance(
        document_id="DOC-SHINHAN-YOUTH-20260722",
        document_name="신한은행 청년 처음적금 상품설명서",
        page=3,
        section="[2] 서로 다른 근거",
        source_url="https://example.invalid/shinhan-youth-first.pdf",
        source_text="충돌하는 근거 위치",
    )
    bad_rate = rate.model_copy(
        update={"provenance": [*rate.provenance, conflicting]}, deep=True
    )
    bad = draft.model_copy(update={"rules": [draft.rules[0], bad_rate]}, deep=True)

    report = SemanticValidator().validate(bad)

    assert report.valid is False
    assert any(issue.code == "CONFLICTING_RULE_PROVENANCE" for issue in report.errors)
    with pytest.raises(ValueError, match="conflicting draft provenance"):
        product_definition_from_draft(bad)


def test_ast_and_draft_provenance_conflict_is_never_silently_overwritten():
    draft = salary_envelope_extraction_draft()
    rate = draft.rules[1]
    ast_source = {**rate.ast["source"], "page": 99, "source_text": "AST conflict"}
    bad_rate = rate.model_copy(
        update={"ast": {**rate.ast, "source": ast_source}}, deep=True
    )
    bad = draft.model_copy(update={"rules": [draft.rules[0], bad_rate]}, deep=True)

    report = SemanticValidator().validate(bad)

    assert report.valid is False
    assert any(issue.code == "RULE_PROVENANCE_CONFLICT" for issue in report.errors)
    with pytest.raises(ValueError, match="AST/draft provenance conflict"):
        product_definition_from_draft(bad)
