from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eligibility.schema.enums import (
    ContributionFrequency,
    ContributionMode,
    InterestPaymentMethod,
    RewardType,
    RewardUnit,
    SaleStatus,
    SubscriptionChannel,
    TermUnit,
)
from eligibility.schema.rule import RuleNode, SourceReference


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Reward(StrictModel):
    type: RewardType = RewardType.INTEREST_RATE
    value: Decimal = Field(ge=0)
    unit: RewardUnit = RewardUnit.PERCENTAGE_POINT


class PreferentialRateRule(StrictModel):
    rule: RuleNode
    reward: Reward


class ContractTerm(StrictModel):
    value: int = Field(gt=0)
    unit: TermUnit


class ContributionPolicy(StrictModel):
    initial_amount_min: Decimal | None = Field(default=None, ge=0)
    initial_amount_max: Decimal | None = Field(default=None, ge=0)
    initial_amount_options: list[Decimal] = Field(default_factory=list)
    periodic_amount_min: Decimal | None = Field(default=None, ge=0)
    periodic_amount_max: Decimal | None = Field(default=None, ge=0)
    contribution_frequency: ContributionFrequency | None = None
    contribution_mode: ContributionMode | None = None
    increment_amount: Decimal | None = Field(default=None, gt=0)
    increment_amount_options: list[Decimal] = Field(default_factory=list)
    total_principal_limit: Decimal | None = Field(default=None, gt=0)
    currency: str = "KRW"

    @model_validator(mode="after")
    def validate_amounts(self) -> "ContributionPolicy":
        if (
            self.initial_amount_min is not None
            and self.initial_amount_max is not None
            and self.initial_amount_min > self.initial_amount_max
        ):
            raise ValueError("initial_amount_min must not exceed initial_amount_max")
        if (
            self.periodic_amount_min is not None
            and self.periodic_amount_max is not None
            and self.periodic_amount_min > self.periodic_amount_max
        ):
            raise ValueError("periodic_amount_min must not exceed periodic_amount_max")
        if self.initial_amount_options:
            if len(set(self.initial_amount_options)) != len(self.initial_amount_options):
                raise ValueError("initial_amount_options must be unique")
            if any(amount < 0 for amount in self.initial_amount_options):
                raise ValueError("initial_amount_options must be non-negative")
        if self.increment_amount_options:
            if len(set(self.increment_amount_options)) != len(self.increment_amount_options):
                raise ValueError("increment_amount_options must be unique")
            if any(amount <= 0 for amount in self.increment_amount_options):
                raise ValueError("increment_amount_options must be positive")
        if (
            self.contribution_mode == ContributionMode.INCREMENTAL
            and self.increment_amount is None
            and not self.increment_amount_options
        ):
            raise ValueError(
                "INCREMENTAL contribution mode requires increment_amount or increment_amount_options"
            )
        return self


class ProductFeature(StrictModel):
    """Typed product/application feature used by cheap filtering and preferences.

    ``feature_id`` is an explicit semantic identifier (for example
    ``NEW_CARD_REQUIRED``).  It must never be inferred from Korean/English
    substrings in rule names.  ``required_for_subscription`` distinguishes a
    mandatory product-level requirement from a merely optional preferential
    action path.
    """

    feature_id: str
    present: bool = True
    required_for_subscription: bool = False
    required_capabilities: list[str] = Field(default_factory=list)
    source_reference: SourceReference | None = None


class ProductMetadata(StrictModel):
    institution_id: str
    product_id: str
    product_name: str
    product_type: str
    sale_status: SaleStatus = SaleStatus.UNKNOWN

    min_term: ContractTerm | None = None
    max_term: ContractTerm | None = None
    available_terms: list[ContractTerm] = Field(default_factory=list)

    contribution_policy: ContributionPolicy | None = None

    base_rate: Decimal = Field(ge=0)
    advertised_max_rate: Decimal = Field(ge=0)
    preferential_rate_cap: Decimal = Field(ge=0)

    allowed_channels: list[SubscriptionChannel] = Field(default_factory=list)
    features: list[ProductFeature] = Field(default_factory=list)
    target_customer_summary: str | None = None
    one_account_per_person: bool | None = None
    sale_start: date | None = None
    sale_end: date | None = None
    quantity_limit: int | None = Field(default=None, gt=0)

    early_termination_policy: str | None = None
    partial_withdrawal_policy: str | None = None
    interest_payment_method: InterestPaymentMethod = InterestPaymentMethod.UNKNOWN
    tax_treatment: dict[str, Any] = Field(default_factory=dict)

    effective_from: date | None = None
    effective_to: date | None = None
    source_reference: SourceReference | None = None

    @model_validator(mode="after")
    def validate_metadata(self) -> "ProductMetadata":
        if self.sale_start and self.sale_end and self.sale_start > self.sale_end:
            raise ValueError("sale_start must not be after sale_end")
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("effective_from must not be after effective_to")
        theoretical = self.base_rate + self.preferential_rate_cap
        if self.advertised_max_rate > theoretical:
            raise ValueError(
                "advertised_max_rate cannot exceed base_rate + preferential_rate_cap"
            )
        if self.min_term and self.max_term:
            if self.min_term.unit == self.max_term.unit and self.min_term.value > self.max_term.value:
                raise ValueError("min_term must not exceed max_term")
        return self


class ProductDefinition(StrictModel):
    product_id: str
    institution_id: str
    name: str
    product_type: str
    # Legacy core fields remain for v0.3 fixtures. Application metadata is an
    # optional, identity-checked envelope rather than an alternative truth.
    contract_months: int | None = Field(default=None, gt=0)
    contract_term: ContractTerm | None = None
    base_rate: Decimal = Field(ge=0)
    advertised_max_rate: Decimal = Field(ge=0)
    preferential_rate_cap: Decimal = Field(ge=0)
    eligibility_rule: RuleNode
    preferential_rules: list[PreferentialRateRule] = Field(default_factory=list)
    global_guards: list[RuleNode] = Field(default_factory=list)
    metadata: ProductMetadata | None = None

    @model_validator(mode="after")
    def validate_product(self) -> "ProductDefinition":
        if self.contract_months is None and self.contract_term is None:
            raise ValueError("Either contract_months or contract_term is required")
        if (
            self.contract_months is not None
            and self.contract_term is not None
            and self.contract_term.unit == TermUnit.MONTH
            and self.contract_term.value != self.contract_months
        ):
            raise ValueError("contract_months and contract_term disagree")

        theoretical = self.base_rate + self.preferential_rate_cap
        if self.advertised_max_rate > theoretical:
            raise ValueError(
                "advertised_max_rate cannot exceed base_rate + preferential_rate_cap"
            )
        if self.metadata is not None:
            comparisons = {
                "product_id": (self.product_id, self.metadata.product_id),
                "institution_id": (self.institution_id, self.metadata.institution_id),
                "name": (self.name, self.metadata.product_name),
                "product_type": (self.product_type, self.metadata.product_type),
                "base_rate": (self.base_rate, self.metadata.base_rate),
                "advertised_max_rate": (
                    self.advertised_max_rate,
                    self.metadata.advertised_max_rate,
                ),
                "preferential_rate_cap": (
                    self.preferential_rate_cap,
                    self.metadata.preferential_rate_cap,
                ),
            }
            mismatches = [
                key for key, (core_value, metadata_value) in comparisons.items()
                if core_value != metadata_value
            ]
            if mismatches:
                raise ValueError(
                    "ProductDefinition core fields and metadata disagree: "
                    f"{mismatches}"
                )
            self._validate_fixed_term_consistency()

        self._validate_action_path_lineage()
        return self

    def _core_term(self) -> ContractTerm | None:
        if self.contract_term is not None:
            return self.contract_term
        if self.contract_months is not None:
            return ContractTerm(value=self.contract_months, unit=TermUnit.MONTH)
        return None

    def _validate_fixed_term_consistency(self) -> None:
        assert self.metadata is not None
        core = self._core_term()
        if core is None:
            return
        metadata = self.metadata
        # A min=max pair is an explicit fixed term. Unknown/range metadata does
        # not block the product, as required by the application handoff contract.
        if (
            metadata.min_term is not None
            and metadata.max_term is not None
            and metadata.min_term == metadata.max_term
            and metadata.min_term != core
        ):
            raise ValueError(
                "ProductDefinition fixed term and ProductMetadata fixed term disagree"
            )
        if metadata.available_terms and core not in metadata.available_terms:
            # Only reject when the metadata claims an exact finite option set.
            raise ValueError(
                "ProductDefinition contract term is absent from ProductMetadata.available_terms"
            )

    def _validate_action_path_lineage(self) -> None:
        for preferential in self.preferential_rules:
            owning_rule = preferential.rule
            node_ids = {node.rule_id for node in _walk_rule_nodes(owning_rule)}
            for node in _walk_rule_nodes(owning_rule):
                future = getattr(node, "future_achievement", None)
                if future is None:
                    continue
                for path in future.action_paths:
                    if path.rule_id != owning_rule.rule_id:
                        raise ValueError(
                            "ActionPath.rule_id must equal the owning PreferentialRule rule_id"
                        )
                    if (
                        path.source_rule_node_id is not None
                        and path.source_rule_node_id not in node_ids
                    ):
                        raise ValueError(
                            "ActionPath.source_rule_node_id must reference a node in the owning Rule AST"
                        )
        return None


def _walk_rule_nodes(root: RuleNode):
    """Yield the executable AST nodes of one preferential rule."""

    yield root
    children = getattr(root, "children", None)
    if children:
        for child in children:
            yield from _walk_rule_nodes(child)
    child = getattr(root, "child", None)
    if child is not None:
        yield from _walk_rule_nodes(child)


class MonthlyContributionPlan(StrictModel):
    monthly_amount: Decimal = Field(gt=0)
    months: int = Field(gt=0)
    tax_rate: Decimal = Field(default=Decimal("0.154"), ge=0, lt=1)


class ContributionCashflow(StrictModel):
    amount: Decimal = Field(gt=0)
    days_held: int = Field(ge=0)


class ContributionSchedulePlan(StrictModel):
    """Generic deterministic cashflow plan for daily/weekly/incremental products."""

    cashflows: list[ContributionCashflow]
    tax_rate: Decimal = Field(default=Decimal("0.154"), ge=0, lt=1)
    schedule_label: str = "GENERIC_CASHFLOW_SCHEDULE"

    @model_validator(mode="after")
    def require_cashflows(self) -> "ContributionSchedulePlan":
        if not self.cashflows:
            raise ValueError("ContributionSchedulePlan requires at least one cashflow")
        return self
