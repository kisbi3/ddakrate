from __future__ import annotations

from eligibility.ingestion.knowledge_draft import DraftProvenance, ProductKnowledgeDraft, RuleDraft
from eligibility.schema.enums import EvaluationPhase, RulePurpose, TermUnit
from eligibility.schema.product import (
    ContractTerm,
    PreferentialRateRule,
    ProductDefinition,
    Reward,
)
from eligibility.schema.rule import AndRule, RULE_NODE_ADAPTER, RuleNode, SourceReference


def _source_from_draft(provenance: DraftProvenance) -> SourceReference:
    return SourceReference(
        document=provenance.document_name,
        document_id=provenance.document_id,
        page=provenance.page,
        section=provenance.section,
        source_url=provenance.source_url,
        source_text=provenance.source_text,
    )


def _materialize_source(rule_draft: RuleDraft, ast_source: SourceReference | None) -> SourceReference:
    if not rule_draft.provenance:
        if ast_source is None:
            raise ValueError(f"Rule {rule_draft.rule_id} has no provenance to materialize")
        return ast_source
    keys = {
        (
            item.document_id,
            item.document_name,
            item.page,
            item.section,
            item.source_url,
            item.source_text,
        )
        for item in rule_draft.provenance
    }
    if len(keys) != 1:
        raise ValueError(
            f"Rule {rule_draft.rule_id} has conflicting draft provenance; activation blocked"
        )
    draft_source = _source_from_draft(rule_draft.provenance[0])
    if ast_source is None:
        return draft_source

    comparisons = {
        "document": (ast_source.document, draft_source.document),
        "document_id": (ast_source.document_id, draft_source.document_id),
        "page": (ast_source.page, draft_source.page),
        "section": (ast_source.section, draft_source.section),
        "source_url": (ast_source.source_url, draft_source.source_url),
        "source_text": (ast_source.source_text, draft_source.source_text),
    }
    conflicts = [
        field
        for field, (ast_value, draft_value) in comparisons.items()
        if ast_value is not None
        and draft_value is not None
        and ast_value != draft_value
    ]
    if conflicts:
        raise ValueError(
            f"Rule {rule_draft.rule_id} AST/draft provenance conflict: {conflicts}"
        )
    # Fill only missing fields; never silently replace validated AST evidence.
    return ast_source.model_copy(
        update={
            "document_id": ast_source.document_id or draft_source.document_id,
            "page": ast_source.page if ast_source.page is not None else draft_source.page,
            "section": ast_source.section or draft_source.section,
            "source_url": ast_source.source_url or draft_source.source_url,
            "source_text": ast_source.source_text or draft_source.source_text,
        },
        deep=True,
    )


def product_definition_from_draft(draft: ProductKnowledgeDraft) -> ProductDefinition:
    metadata = draft.product_metadata
    required_metadata = {
        "product_id": metadata.product_id,
        "institution_id": metadata.institution_id,
        "name": metadata.name,
        "product_type": metadata.product_type,
        "contract_value": metadata.contract_value,
        "contract_unit": metadata.contract_unit,
        "base_rate": metadata.base_rate,
        "advertised_max_rate": metadata.advertised_max_rate,
        "preferential_rate_cap": metadata.preferential_rate_cap,
    }
    missing = [key for key, value in required_metadata.items() if value is None]
    if missing:
        raise ValueError(f"Draft cannot be activated as a ProductDefinition; missing {missing}")

    eligibility_nodes: list[RuleNode] = []
    preferential: list[PreferentialRateRule] = []
    guards: list[RuleNode] = []
    for rule_draft in draft.rules:
        node = RULE_NODE_ADAPTER.validate_python(rule_draft.ast)
        if node.rule_id != rule_draft.rule_id:
            raise ValueError(
                f"RuleDraft/AST rule_id mismatch: {rule_draft.rule_id} != {node.rule_id}"
            )
        if node.name != rule_draft.name:
            raise ValueError(
                f"RuleDraft/AST name mismatch: {rule_draft.name!r} != {node.name!r}"
            )
        source = _materialize_source(rule_draft, node.source)
        node = node.model_copy(
            update={
                "purpose": rule_draft.purpose,
                "evaluation_phase": rule_draft.evaluation_phase,
                "source": source,
            },
            deep=True,
        )
        if rule_draft.purpose == RulePurpose.ELIGIBILITY:
            eligibility_nodes.append(node)
        elif rule_draft.purpose == RulePurpose.PREFERENTIAL_RATE:
            if rule_draft.reward is None:
                raise ValueError(f"Preferential rule {rule_draft.rule_id} has no reward")
            preferential.append(
                PreferentialRateRule(
                    rule=node,
                    reward=Reward(
                        type=rule_draft.reward.type,
                        value=rule_draft.reward.value,
                        unit=rule_draft.reward.unit,
                    ),
                )
            )
        elif rule_draft.purpose == RulePurpose.GLOBAL_GUARD:
            guards.append(node)

    if not eligibility_nodes:
        raise ValueError("Draft has no eligibility rule")
    if len(eligibility_nodes) == 1:
        eligibility = eligibility_nodes[0]
    else:
        eligibility = AndRule(
            rule_id="ELIGIBILITY",
            name="Extracted eligibility rules",
            purpose=RulePurpose.ELIGIBILITY,
            evaluation_phase=EvaluationPhase.PRE_SUBSCRIPTION,
            children=eligibility_nodes,
        )

    assert metadata.product_id is not None
    assert metadata.institution_id is not None
    assert metadata.name is not None
    assert metadata.product_type is not None
    assert metadata.contract_value is not None
    assert metadata.contract_unit is not None
    assert metadata.base_rate is not None
    assert metadata.advertised_max_rate is not None
    assert metadata.preferential_rate_cap is not None
    contract_months = (
        metadata.contract_value if metadata.contract_unit == TermUnit.MONTH else None
    )
    return ProductDefinition(
        product_id=metadata.product_id,
        institution_id=metadata.institution_id,
        name=metadata.name,
        product_type=metadata.product_type,
        contract_months=contract_months,
        contract_term=ContractTerm(
            value=metadata.contract_value,
            unit=metadata.contract_unit,
        ),
        base_rate=metadata.base_rate,
        advertised_max_rate=metadata.advertised_max_rate,
        preferential_rate_cap=metadata.preferential_rate_cap,
        eligibility_rule=eligibility,
        preferential_rules=preferential,
        global_guards=guards,
    )
