"""Deterministic filtering for user-selected product feature policies."""
from __future__ import annotations

from typing import Iterable

from eligibility.schema.conversation import ProductFeaturePolicy
from eligibility.schema.product import ProductDefinition

FeaturePolicy = ProductFeaturePolicy

_RANDOM_PROMOTION_EVENT_TYPES = {
    "PROMOTION_RANDOM_DRAW",
    "OFFICIAL_RANDOM_ALLOCATION",
    "OFFICIAL_RANDOM_PAIRING",
}


def feature_present(product: ProductDefinition, feature_id: str) -> bool:
    """Return true only for an explicitly typed, present feature."""
    if feature_id == "RANDOM_PROMOTION_RATE":
        policy = product.normalized.return_policy if product.normalized else {}
        return any(
            str(row.get("event_type") or "").upper()
            in _RANDOM_PROMOTION_EVENT_TYPES
            for row in (policy.get("preferential_policy") or {}).get("event_definitions") or []
        )
    return any(item.feature_id == feature_id and item.present for item in (product.metadata.features if product.metadata else []))


def random_promotion_rule_ids(product: ProductDefinition) -> set[str]:
    """Return rule IDs whose reward depends on an official random outcome."""

    if not feature_present(product, "RANDOM_PROMOTION_RATE"):
        return set()
    policy = product.normalized.return_policy if product.normalized else {}
    rules = (policy.get("preferential_policy") or {}).get("rules") or []
    return {
        str(rule["rule_id"])
        for rule in rules
        if rule.get("rule_id")
        and any(
            marker in str(rule.get("condition") or {}).upper()
            for marker in ("PROMOTION.", "OFFICIAL_RANDOM_RESULT", "RANDOM_")
        )
    }


def apply_feature_policy(
    products: Iterable[ProductDefinition],
    feature_id: str,
    policy: FeaturePolicy | str,
) -> list[ProductDefinition]:
    """Apply a global feature policy; preference keeps all products stable."""
    policy = FeaturePolicy(policy)
    items = list(products)
    if policy is FeaturePolicy.EXCLUDE:
        return [p for p in items if not feature_present(p, feature_id)]
    return items
