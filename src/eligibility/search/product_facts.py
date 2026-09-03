"""Typed, family-neutral facts for deterministic product retrieval."""

from __future__ import annotations

from typing import Any

from eligibility.schema.product import ProductDefinition
from eligibility.schema.search import ProductSearchFacts


_CUSTOMER_SCOPE_ALIASES = {
    "PERSONAL": "INDIVIDUAL",
    "CORPORATE": "CORPORATION",
    "BUSINESS": "SOLE_PROPRIETOR",
    "PERSONAL_BUSINESS_OWNER": "SOLE_PROPRIETOR",
}


def _scope(value: Any) -> str:
    normalized = str(value).strip().upper()
    return _CUSTOMER_SCOPE_ALIASES.get(normalized, normalized)


def _classification_values(rows: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for row in rows:
        tag = row.get("tag")
        value = row.get("value")
        if tag is not None:
            values.add(str(tag).strip().upper())
        if value is not None:
            values.add(str(value).strip().upper())
        if tag is not None and value is not None:
            values.add(f"{str(tag).strip().upper()}:{str(value).strip().upper()}")
    return sorted(item for item in values if item)


def _structured_fee_waiver(fee_policy: dict[str, Any]) -> bool | None:
    if not fee_policy:
        return None
    benefit_keys = {
        "waiver_policy",
        "waiver_policies",
        "waiver_policy_ref",
        "transfer_fee_benefits",
        "atm_benefits",
        "benefits",
    }
    if any(fee_policy.get(key) for key in benefit_keys):
        return True
    # Free-form disclosure text is deliberately not converted into a hard
    # filter. A structured fee policy without a benefit declaration is known
    # fee information, but not proof that a waiver exists.
    if any(key in fee_policy for key in {"management_fee", "management_fee_rates", "entries"}):
        return False
    return None


def project_product_search_facts(product: ProductDefinition) -> ProductSearchFacts:
    metadata = product.metadata
    normalized = product.normalized
    eligibility = normalized.eligibility_policy if normalized is not None else {}
    term_policy = normalized.term_policy if normalized is not None else {}
    cash_flow = normalized.cash_flow_policy if normalized is not None else {}
    return_policy = normalized.return_policy if normalized is not None else {}
    protection = normalized.protection_policy if normalized is not None else {}
    fee_policy = normalized.fee_policy if normalized is not None else {}

    scopes = {
        _scope(value)
        for value in eligibility.get("allowed_customer_types", [])
        if value is not None
    }
    scopes.update(
        _scope(entry.get("customer_scope"))
        for entry in return_policy.get("rate_entries", [])
        if entry.get("customer_scope") is not None
    )
    channels = (
        sorted({item.value for item in metadata.allowed_channels})
        if metadata is not None
        else []
    )
    contribution = metadata.contribution_policy if metadata is not None else None
    raw_classifications = (
        normalized.raw_product.get("classifications", []) if normalized is not None else []
    )
    interest_method = None
    if metadata is not None and metadata.interest_payment_method.value != "UNKNOWN":
        interest_method = metadata.interest_payment_method.value

    return ProductSearchFacts(
        product_id=product.product_id,
        product_family=product.product_type,
        product_subtype=(
            normalized.product_subtype
            if normalized is not None
            else metadata.product_subtype if metadata is not None else None
        ),
        institution_sector=(metadata.institution_sector if metadata is not None else None),
        sale_status=(
            normalized.sale_status
            if normalized is not None
            else metadata.sale_status.value if metadata is not None else None
        ),
        open_ended=(term_policy.get("kind") == "OPEN_ENDED" if term_policy else None),
        customer_scopes=sorted(scopes),
        subscription_channels=channels,
        funding_type=(
            cash_flow.get("funding_type")
            or (contribution.funding_type if contribution is not None else None)
        ),
        contribution_frequency=(
            contribution.contribution_frequency.value
            if contribution is not None and contribution.contribution_frequency is not None
            else cash_flow.get("contribution_frequency")
        ),
        return_kind=return_policy.get("return_kind"),
        protection_status=protection.get("coverage_status"),
        reinvestment_mode=(return_policy.get("reinvestment_policy") or {}).get("mode"),
        balance_tier_method=return_policy.get("balance_tier_method"),
        fee_waiver_available=_structured_fee_waiver(fee_policy),
        interest_payment_method=interest_method,
        classifications=_classification_values(raw_classifications),
    )
