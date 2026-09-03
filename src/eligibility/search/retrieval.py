from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

from eligibility.schema.application_input import HardConstraint, NumericPreference
from eligibility.schema.enums import (
    HardConstraintValue,
    NumericPreferenceDirection,
    PreferenceStrictness,
    SaleStatus,
    SubscriptionChannel,
    TermUnit,
)
from eligibility.schema.product import ContractTerm, ProductDefinition
from eligibility.schema.search import CandidateFilterDecision, ProductSearchIntent
from eligibility.search.contribution import terms_equivalent
from eligibility.search.product_facts import project_product_search_facts


_CLOSED_STATUSES = {SaleStatus.SOLD_OUT, SaleStatus.ENDED, SaleStatus.SUSPENDED}


class CandidateRetriever:
    """Cheap, metadata-first filtering that removes only certain failures."""

    def retrieve(
        self,
        products: Iterable[ProductDefinition],
        intent: ProductSearchIntent,
        *,
        as_of: date,
    ) -> tuple[list[ProductDefinition], list[CandidateFilterDecision]]:
        retained: list[ProductDefinition] = []
        decisions: list[CandidateFilterDecision] = []
        for product in products:
            decision = self._decision(product, intent, as_of=as_of)
            decisions.append(decision)
            if decision.retained:
                retained.append(product)
        return retained, decisions

    def _decision(
        self,
        product: ProductDefinition,
        intent: ProductSearchIntent,
        *,
        as_of: date,
    ) -> CandidateFilterDecision:
        metadata = product.metadata
        search_facts = project_product_search_facts(product)
        if metadata is not None:
            if metadata.sale_status in _CLOSED_STATUSES:
                return self._remove(product, "SALE_STATUS_CLOSED", sale_status=metadata.sale_status.value)
            if metadata.sale_end is not None and metadata.sale_end < as_of:
                return self._remove(product, "SALE_END_PASSED", sale_end=metadata.sale_end.isoformat())

        if intent.product_types and product.product_type not in set(intent.product_types):
            return self._remove(
                product,
                "PRODUCT_TYPE_MISMATCH",
                actual=product.product_type,
                requested=intent.product_types,
            )

        if product.institution_id in set(intent.excluded_institution_ids):
            return self._remove(
                product,
                "EXCLUDED_INSTITUTION",
                institution_id=product.institution_id,
            )

        # Customer capacity is a cross-family catalog fact, not an LLM
        # eligibility judgment.  Exclude only when the product explicitly
        # declares scopes and the requested capacity is absent; unknown scope
        # remains a candidate for later verification.
        if (
            intent.application_capacity is not None
            and search_facts.customer_scopes
            and intent.application_capacity not in search_facts.customer_scopes
        ):
            return self._remove(
                product,
                "HARD_CUSTOMER_SCOPE_VIOLATION",
                expected=intent.application_capacity,
                actual=search_facts.customer_scopes,
            )

        contribution_plan = intent.contribution_plan
        if (
            contribution_plan is not None
            and contribution_plan.selected_term_value is not None
            and contribution_plan.selected_term_unit is not None
            # MAXIMUM/MINIMUM are ranges, not requests for a product that
            # offers the boundary term itself.  Their hard numeric constraint
            # below decides whether any available term lies inside the range.
            and contribution_plan.term_strictness not in {"MAXIMUM", "MINIMUM"}
            and not self._supports_requested_term(
                product,
                ContractTerm(
                    value=contribution_plan.selected_term_value,
                    unit=contribution_plan.selected_term_unit,
                ),
            )
        ):
            return self._remove(
                product,
                "REQUESTED_TERM_NOT_AVAILABLE",
                requested_value=contribution_plan.selected_term_value,
                requested_unit=contribution_plan.selected_term_unit.value,
            )

        if (
            contribution_plan is not None
            and contribution_plan.selected_term_value is not None
            and contribution_plan.selected_term_unit is not None
            and contribution_plan.term_strictness in {"MAXIMUM", "MINIMUM"}
        ):
            bounds = self._term_bounds_months(product)
            if bounds is not None:
                boundary = self._term_to_months(
                    ContractTerm(
                        value=contribution_plan.selected_term_value,
                        unit=contribution_plan.selected_term_unit,
                    )
                )
                minimum, maximum = bounds
                violates_range = (
                    contribution_plan.term_strictness == "MAXIMUM"
                    and minimum > boundary
                ) or (
                    contribution_plan.term_strictness == "MINIMUM"
                    and maximum < boundary
                )
                if violates_range:
                    return self._remove(
                        product,
                        "HARD_TERM_VIOLATION",
                        minimum_months=str(minimum),
                        maximum_months=str(maximum),
                        boundary_months=str(boundary),
                        term_strictness=contribution_plan.term_strictness,
                    )

        for hard in intent.hard_constraints:
            outcome = self._hard_constraint(product, hard, search_facts=search_facts)
            if outcome is not None:
                reason, evidence = outcome
                return self._remove(product, reason, **evidence)

        for numeric in intent.numeric_preferences:
            if numeric.strictness != PreferenceStrictness.HARD:
                continue
            outcome = self._numeric_constraint(product, numeric)
            if outcome is not None:
                reason, evidence = outcome
                return self._remove(product, reason, **evidence)

        return CandidateFilterDecision(
            product_id=product.product_id,
            retained=True,
            reason_code="RETAINED_NO_CERTAIN_HARD_FAILURE",
            evidence={
                "sale_status": metadata.sale_status.value if metadata else "UNKNOWN",
                "metadata_present": metadata is not None,
            },
        )

    @staticmethod
    def _remove(product: ProductDefinition, reason: str, **evidence) -> CandidateFilterDecision:
        return CandidateFilterDecision(
            product_id=product.product_id,
            retained=False,
            reason_code=reason,
            evidence=evidence,
        )

    def _hard_constraint(
        self,
        product: ProductDefinition,
        hard: HardConstraint,
        *,
        search_facts=None,
    ) -> tuple[str, dict] | None:
        field = hard.field.upper()
        metadata = product.metadata
        facts = search_facts or project_product_search_facts(product)

        if field == "INSTITUTION_SECTOR":
            if metadata is None:
                return None
            expected_sectors = {
                item.strip().upper()
                for item in str(hard.expected).split("|")
                if item.strip()
            }
            matches = metadata.institution_sector in expected_sectors
            if self._violates(matches, hard.constraint):
                return "HARD_INSTITUTION_SECTOR_VIOLATION", {
                    "expected": sorted(expected_sectors),
                    "actual": metadata.institution_sector,
                }
            return None

        if field == "PRODUCT_TYPE":
            matches = product.product_type == str(hard.expected)
            if self._violates(matches, hard.constraint):
                return "HARD_PRODUCT_TYPE_VIOLATION", {"expected": hard.expected}
            return None

        scalar_fact_fields = {
            "PRODUCT_SUBTYPE": "product_subtype",
            "RETURN_KIND": "return_kind",
            "PROTECTION_STATUS": "protection_status",
            "REINVESTMENT_MODE": "reinvestment_mode",
            "BALANCE_TIER_METHOD": "balance_tier_method",
            "FUNDING_TYPE": "funding_type",
            "CONTRIBUTION_FREQUENCY": "contribution_frequency",
            "INTEREST_PAYMENT_METHOD": "interest_payment_method",
        }
        if field in scalar_fact_fields:
            actual = getattr(facts, scalar_fact_fields[field])
            if actual is None:
                return None
            expected = {
                item.strip().upper()
                for item in str(hard.expected).split("|")
                if item.strip()
            }
            matches = str(actual).upper() in expected
            if self._violates(matches, hard.constraint):
                return "HARD_PRODUCT_FACT_VIOLATION", {
                    "field": field,
                    "expected": sorted(expected),
                    "actual": actual,
                }
            return None

        if field in {"CUSTOMER_SCOPE", "APPLICATION_CAPACITY"}:
            if not facts.customer_scopes:
                return None
            expected = str(hard.expected).strip().upper()
            matches = expected in set(facts.customer_scopes)
            if self._violates(matches, hard.constraint):
                return "HARD_CUSTOMER_SCOPE_VIOLATION", {
                    "expected": expected,
                    "actual": facts.customer_scopes,
                }
            return None

        if field in {"DEPOSIT_PROTECTION", "DEPOSIT_PROTECTED"}:
            if facts.protection_status is None:
                return None
            expected = self._as_bool(hard.expected)
            if expected is None:
                return None
            protected = facts.protection_status == "PROTECTED"
            matches = protected == expected
            if self._violates(matches, hard.constraint):
                return "HARD_PROTECTION_VIOLATION", {
                    "expected": expected,
                    "actual": facts.protection_status,
                }
            return None

        if field == "FEE_WAIVER_AVAILABLE":
            if facts.fee_waiver_available is None:
                return None
            expected = self._as_bool(hard.expected)
            if expected is None:
                return None
            matches = facts.fee_waiver_available == expected
            if self._violates(matches, hard.constraint):
                return "HARD_FEE_WAIVER_VIOLATION", {
                    "expected": expected,
                    "actual": facts.fee_waiver_available,
                }
            return None

        if field == "OPEN_ENDED":
            if facts.open_ended is None:
                return None
            expected = self._as_bool(hard.expected)
            if expected is None:
                return None
            matches = facts.open_ended == expected
            if self._violates(matches, hard.constraint):
                return "HARD_OPEN_ENDED_VIOLATION", {
                    "expected": expected,
                    "actual": facts.open_ended,
                }
            return None

        if field in {"CLASSIFICATION", "PRODUCT_TRAIT"}:
            if not facts.classifications:
                return None
            expected = str(hard.expected).strip().upper()
            matches = expected in set(facts.classifications)
            if self._violates(matches, hard.constraint):
                return "HARD_CLASSIFICATION_VIOLATION", {
                    "expected": expected,
                    "actual": facts.classifications,
                }
            return None

        if field in {"SUBSCRIPTION_CHANNEL", "CHANNEL"}:
            if metadata is None or not metadata.allowed_channels:
                return None
            try:
                channel = SubscriptionChannel(str(hard.expected).upper())
            except ValueError:
                return None
            present = channel in metadata.allowed_channels
            if self._violates(present, hard.constraint):
                return "HARD_CHANNEL_VIOLATION", {
                    "channel": channel.value,
                    "allowed_channels": [item.value for item in metadata.allowed_channels],
                }
            return None

        if field in {"REQUIRES_BRANCH", "BRANCH_VISIT_REQUIRED"}:
            if metadata is None or not metadata.allowed_channels:
                return None
            branch_only = metadata.allowed_channels == [SubscriptionChannel.BRANCH]
            expected = bool(hard.expected)
            matches = branch_only == expected
            if self._violates(matches, hard.constraint):
                return "HARD_BRANCH_REQUIREMENT_VIOLATION", {"branch_only": branch_only}
            return None

        if field in {"TERM_MONTHS", "MAX_TERM_MONTHS", "MIN_TERM_MONTHS"}:
            try:
                expected_months = int(hard.expected)
            except (TypeError, ValueError):
                return None
            bounds = self._term_bounds_months(product)
            if bounds is None:
                return None
            minimum, maximum = bounds
            if field == "TERM_MONTHS":
                present = minimum <= expected_months <= maximum
            elif field == "MAX_TERM_MONTHS":
                present = minimum <= expected_months
            else:
                present = maximum >= expected_months
            if self._violates(present, hard.constraint):
                return "HARD_TERM_VIOLATION", {
                    "minimum_months": str(minimum),
                    "maximum_months": str(maximum),
                    "expected_months": expected_months,
                }
            return None

        if field in {"MONTHLY_CONTRIBUTION", "PERIODIC_AMOUNT", "MAX_PERIODIC_AMOUNT"}:
            if metadata is None or metadata.contribution_policy is None:
                return None
            policy = metadata.contribution_policy
            if policy.contribution_frequency is not None and policy.contribution_frequency.value != "MONTHLY":
                return None
            try:
                amount = Decimal(str(hard.expected))
            except Exception:
                return None
            can_accept = (
                (policy.periodic_amount_min is None or policy.periodic_amount_min <= amount)
                and (policy.periodic_amount_max is None or policy.periodic_amount_max >= amount)
            )
            if self._violates(can_accept, hard.constraint):
                return "HARD_CONTRIBUTION_VIOLATION", {
                    "amount": str(amount),
                    "minimum": str(policy.periodic_amount_min),
                    "maximum": str(policy.periodic_amount_max),
                }
            return None

        # Product feature filtering is authoritative only when the product carries
        # typed metadata (or a mandatory eligibility ActionPath with an exact
        # capability id).  Text found in rule names/descriptions is never a hard
        # filtering signal.
        feature_present = self._feature_present(product, field)
        if feature_present is None:
            return None
        if self._violates(feature_present, hard.constraint):
            return "HARD_FEATURE_VIOLATION", {
                "field": field,
                "feature_present": feature_present,
                "evidence_type": "TYPED_PRODUCT_FEATURE",
            }
        return None

    def _numeric_constraint(
        self,
        product: ProductDefinition,
        numeric: NumericPreference,
    ) -> tuple[str, dict] | None:
        field = numeric.field.upper()
        if field == "TERM_MONTHS":
            bounds = self._term_bounds_months(product)
            if bounds is None:
                return None
            minimum, maximum = bounds
            if numeric.direction == NumericPreferenceDirection.AT_MOST and minimum > numeric.value:
                return "HARD_TERM_VIOLATION", {"minimum_months": str(minimum), "limit": str(numeric.value)}
            if numeric.direction == NumericPreferenceDirection.AT_LEAST and maximum < numeric.value:
                return "HARD_TERM_VIOLATION", {"maximum_months": str(maximum), "limit": str(numeric.value)}
            if numeric.direction == NumericPreferenceDirection.AROUND and not (minimum <= numeric.value <= maximum):
                return "HARD_TERM_VIOLATION", {"range": [str(minimum), str(maximum)], "target": str(numeric.value)}
            return None

        if field in {"MONTHLY_CONTRIBUTION", "PERIODIC_AMOUNT"}:
            metadata = product.metadata
            if metadata is None or metadata.contribution_policy is None:
                return None
            policy = metadata.contribution_policy
            if policy.contribution_frequency is not None and policy.contribution_frequency.value != "MONTHLY":
                return None
            minimum = policy.periodic_amount_min
            maximum = policy.periodic_amount_max
            if numeric.direction == NumericPreferenceDirection.AT_LEAST:
                if maximum is not None and maximum < numeric.value:
                    return "HARD_CONTRIBUTION_VIOLATION", {"maximum": str(maximum), "required": str(numeric.value)}
            elif numeric.direction == NumericPreferenceDirection.AT_MOST:
                if minimum is not None and minimum > numeric.value:
                    return "HARD_CONTRIBUTION_VIOLATION", {"minimum": str(minimum), "limit": str(numeric.value)}
            elif minimum is not None and maximum is not None and not (minimum <= numeric.value <= maximum):
                return "HARD_CONTRIBUTION_VIOLATION", {"range": [str(minimum), str(maximum)], "target": str(numeric.value)}
        return None

    @staticmethod
    def _violates(present_or_matches: bool, constraint: HardConstraintValue) -> bool:
        if constraint == HardConstraintValue.REQUIRE:
            return not present_or_matches
        return present_or_matches

    @staticmethod
    def _as_bool(value) -> bool | None:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().upper()
        if normalized in {"TRUE", "YES", "Y", "1", "REQUIRED", "PROTECTED"}:
            return True
        if normalized in {"FALSE", "NO", "N", "0", "NOT_PROTECTED"}:
            return False
        return None

    @staticmethod
    def _term_to_months(term: ContractTerm) -> Decimal:
        if term.unit == TermUnit.MONTH:
            return Decimal(term.value)
        if term.unit == TermUnit.YEAR:
            return Decimal(term.value * 12)
        if term.unit == TermUnit.WEEK:
            return Decimal(term.value) * Decimal("7") / Decimal("30.4375")
        return Decimal(term.value) / Decimal("30.4375")

    @classmethod
    def _supports_requested_term(
        cls,
        product: ProductDefinition,
        requested: ContractTerm,
    ) -> bool:
        metadata = product.metadata
        if (
            product.normalized is not None
            and product.normalized.term_policy.get("kind") == "OPEN_ENDED"
        ):
            return True
        requested_months = cls._term_to_months(requested)
        if metadata is not None:
            if metadata.available_terms:
                return (
                    any(
                        terms_equivalent(available, requested)
                        for available in metadata.available_terms
                    )
                    or min(
                        cls._term_to_months(available)
                        for available in metadata.available_terms
                    )
                    <= requested_months
                )
            if metadata.min_term is not None and metadata.max_term is not None:
                # A shorter fixed product can still be a useful alternative to
                # a preferred horizon. A product whose *minimum* term already
                # exceeds the requested horizon must not be projected at its
                # longer representative/max term.
                return cls._term_to_months(metadata.min_term) <= requested_months
        if product.contract_term is not None:
            return cls._term_to_months(product.contract_term) <= requested_months
        if product.contract_months is not None:
            return Decimal(product.contract_months) <= requested_months
        return True

    def _term_bounds_months(self, product: ProductDefinition) -> tuple[Decimal, Decimal] | None:
        if (
            product.normalized is not None
            and product.normalized.term_policy.get("kind") == "OPEN_ENDED"
        ):
            return None
        metadata = product.metadata
        if metadata is not None:
            if metadata.available_terms:
                values = [self._term_to_months(item) for item in metadata.available_terms]
                return min(values), max(values)
            if metadata.min_term is not None and metadata.max_term is not None:
                return self._term_to_months(metadata.min_term), self._term_to_months(metadata.max_term)
        if product.contract_term is not None:
            value = self._term_to_months(product.contract_term)
            return value, value
        if product.contract_months is not None:
            value = Decimal(product.contract_months)
            return value, value
        return None

    @staticmethod
    def _feature_present(product: ProductDefinition, field: str) -> bool | None:
        """Return typed feature presence, or None when the product is silent.

        `NEW_CARD_REQUIRED` is deliberately stricter than "has a card bonus": it
        is true only for an explicit mandatory product feature or a mandatory
        eligibility ActionPath requiring NEW_CARD_ISSUANCE. Preferential ActionPaths
        are not product-level requirements and therefore never hard-filter here.
        """

        feature_id = field.upper()
        metadata = product.metadata
        if metadata is not None:
            for feature in metadata.features:
                if feature.feature_id.upper() != feature_id:
                    continue
                if feature_id == "NEW_CARD_REQUIRED":
                    return bool(feature.present and feature.required_for_subscription)
                return bool(feature.present)

        capability_by_feature = {
            "NEW_CARD_REQUIRED": "NEW_CARD_ISSUANCE",
            "BRANCH_VISIT_REQUIRED": "BRANCH_VISIT",
        }
        capability_id = capability_by_feature.get(feature_id)
        if capability_id is not None:
            if CandidateRetriever._eligibility_requires_capability(
                product.eligibility_rule, capability_id
            ):
                return True
        return None

    @staticmethod
    def _eligibility_requires_capability(rule, capability_id: str) -> bool:
        future = getattr(rule, "future_achievement", None)
        if future is not None:
            for path in future.action_paths:
                if capability_id in path.required_capabilities:
                    return True
        children = getattr(rule, "children", None) or []
        if any(
            CandidateRetriever._eligibility_requires_capability(child, capability_id)
            for child in children
        ):
            return True
        child = getattr(rule, "child", None)
        return (
            child is not None
            and CandidateRetriever._eligibility_requires_capability(child, capability_id)
        )
