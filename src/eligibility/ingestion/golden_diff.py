from __future__ import annotations

import json
from pydantic import BaseModel, ConfigDict, Field

from eligibility.ingestion.knowledge_draft import ProductKnowledgeDraft


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


class GoldenDiffMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_recall: float = Field(ge=0.0, le=1.0)
    hallucinated_rule_count: int = Field(ge=0)
    logical_structure_mismatch: int = Field(ge=0)
    temporal_mismatch: int = Field(ge=0)
    aggregation_mismatch: int = Field(ge=0)
    reward_mismatch: int = Field(ge=0)
    service_reference_recall: float = Field(ge=0.0, le=1.0)
    required_fact_recall: float = Field(ge=0.0, le=1.0)
    provenance_match: float = Field(ge=0.0, le=1.0)
    unresolved_item_recall: float = Field(ge=0.0, le=1.0)


def compare_drafts(
    actual: ProductKnowledgeDraft,
    golden: ProductKnowledgeDraft,
) -> GoldenDiffMetrics:
    actual_rules = {rule.rule_id: rule for rule in actual.rules}
    golden_rules = {rule.rule_id: rule for rule in golden.rules}
    matched_ids = set(actual_rules) & set(golden_rules)

    logical = temporal = aggregation = reward = 0
    provenance_matches = 0
    for rule_id in matched_ids:
        left = actual_rules[rule_id]
        right = golden_rules[rule_id]
        if _canonical(left.ast) != _canonical(right.ast):
            logical += 1
        if _extract_keys(left.ast, {"window", "effective_at", "reference_date"}) != _extract_keys(
            right.ast, {"window", "effective_at", "reference_date"}
        ):
            temporal += 1
        if _extract_types(left.ast, {"COUNT_DISTINCT_PERIODS", "COUNT_DISTINCT_MONTHS", "COUNT_CONSECUTIVE"}) != _extract_types(
            right.ast, {"COUNT_DISTINCT_PERIODS", "COUNT_DISTINCT_MONTHS", "COUNT_CONSECUTIVE"}
        ):
            aggregation += 1
        if _canonical(left.reward.model_dump(mode="json") if left.reward else None) != _canonical(
            right.reward.model_dump(mode="json") if right.reward else None
        ):
            reward += 1
        if _canonical([item.model_dump(mode="json") for item in left.provenance]) == _canonical(
            [item.model_dump(mode="json") for item in right.provenance]
        ):
            provenance_matches += 1

    actual_services = {(item.institution_id, item.service_name, item.concept_name) for item in actual.service_references}
    golden_services = {(item.institution_id, item.service_name, item.concept_name) for item in golden.service_references}
    actual_facts = {item.fact_type for item in actual.required_facts}
    golden_facts = {item.fact_type for item in golden.required_facts}
    actual_unresolved = {(item.issue_type, item.description) for item in actual.unresolved_items}
    golden_unresolved = {(item.issue_type, item.description) for item in golden.unresolved_items}

    return GoldenDiffMetrics(
        rule_recall=_ratio(len(matched_ids), len(golden_rules)),
        hallucinated_rule_count=len(set(actual_rules) - set(golden_rules)),
        logical_structure_mismatch=logical,
        temporal_mismatch=temporal,
        aggregation_mismatch=aggregation,
        reward_mismatch=reward,
        service_reference_recall=_ratio(len(actual_services & golden_services), len(golden_services)),
        required_fact_recall=_ratio(len(actual_facts & golden_facts), len(golden_facts)),
        provenance_match=_ratio(provenance_matches, len(matched_ids)),
        unresolved_item_recall=_ratio(
            len(actual_unresolved & golden_unresolved), len(golden_unresolved)
        ),
    )


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _extract_keys(value: object, keys: set[str]) -> list[object]:
    found: list[object] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                found.append(item)
            found.extend(_extract_keys(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_extract_keys(item, keys))
    return found


def _extract_types(value: object, types: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        current = value.get("type")
        if current in types:
            found.append(str(current))
        for item in value.values():
            found.extend(_extract_types(item, types))
    elif isinstance(value, list):
        for item in value:
            found.extend(_extract_types(item, types))
    return found
