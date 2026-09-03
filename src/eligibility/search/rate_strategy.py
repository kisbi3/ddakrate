"""Product-family rate scenario selection and common result construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from eligibility.catalog.normalized_loader import product_with_scenario_rate
from eligibility.schema.enums import RankingComparability
from eligibility.schema.evaluation import ProductEvaluation
from eligibility.schema.product import ContractTerm, ProductDefinition
from eligibility.schema.search import RateEvaluationResult


_CALCULATION_MODE = {
    "INSTALLMENT_SAVINGS": "INSTALLMENT_CASHFLOW",
    "TIME_DEPOSIT": "LUMP_SUM_TERM",
    "PARKING_ACCOUNT": "ON_DEMAND_BALANCE",
}


@dataclass(frozen=True)
class PreparedRateScenario:
    product: ProductDefinition
    original_product_id: str
    product_family: str
    calculation_mode: str
    return_kind: str | None
    term: ContractTerm
    principal: Decimal | None
    customer_scope: str | None
    as_of: date


class ProductFamilyRateStrategy:
    """Select the applicable base-rate scenario without flattening families."""

    def prepare(
        self,
        product: ProductDefinition,
        *,
        term: ContractTerm,
        principal: Decimal | None,
        customer_scope: str | None,
        as_of: date,
    ) -> PreparedRateScenario:
        return_kind = (
            product.normalized.return_policy.get("return_kind")
            if product.normalized is not None
            else None
        )
        if product.product_type == "CMA":
            calculation_mode = (
                "CMA_PERFORMANCE_LINKED"
                if return_kind == "PERFORMANCE_LINKED"
                else "CMA_POSTED_YIELD"
            )
        else:
            calculation_mode = _CALCULATION_MODE.get(
                product.product_type, "GENERIC_RATE"
            )
        scenario_product = product_with_scenario_rate(
            product,
            term,
            balance=principal,
            customer_scope=customer_scope,
            as_of=as_of,
        )
        return PreparedRateScenario(
            product=scenario_product,
            original_product_id=product.product_id,
            product_family=product.product_type,
            calculation_mode=calculation_mode,
            return_kind=return_kind,
            term=term,
            principal=principal,
            customer_scope=customer_scope,
            as_of=as_of,
        )

    @staticmethod
    def result(
        prepared: PreparedRateScenario,
        evaluation: ProductEvaluation,
        *,
        ranking_comparability: RankingComparability,
        missing_fact_ids: list[str],
    ) -> RateEvaluationResult:
        rate_entries = (
            prepared.product.normalized.return_policy.get("rate_entries", [])
            if prepared.product.normalized is not None
            else []
        )
        as_of_values: list[date] = []
        for entry in rate_entries:
            entry_scope = entry.get("customer_scope")
            if (
                prepared.customer_scope is not None
                and entry_scope is not None
                and str(entry_scope).strip().upper()
                != prepared.customer_scope.strip().upper()
            ):
                continue
            raw = entry.get("as_of")
            if not raw:
                continue
            try:
                parsed = date.fromisoformat(str(raw))
            except ValueError:
                continue
            if parsed <= prepared.as_of:
                as_of_values.append(parsed)
        applied_condition_ids = [
            item.rule_id
            for item in evaluation.rates.applied_rewards
            if item.included_in_confirmed or item.included_in_realizable
        ]
        comparison_basis = (
            "PERFORMANCE_LINKED_RETURN"
            if prepared.return_kind == "PERFORMANCE_LINKED"
            else "ANNUAL_PERCENT_RATE"
            if evaluation.rates.calculation_status == "CALCULATED"
            else "UNKNOWN"
        )
        return RateEvaluationResult(
            product_id=prepared.original_product_id,
            product_family=prepared.product_family,
            calculation_mode=prepared.calculation_mode,
            return_kind=prepared.return_kind,
            calculation_status=evaluation.rates.calculation_status,
            calculation_reason=evaluation.rates.calculation_reason,
            eligibility_status=evaluation.eligibility_status,
            confirmed_rate=evaluation.rates.confirmed_rate,
            realizable_rate=evaluation.rates.realizable_rate,
            user_specific_conditional_upper_rate=(
                evaluation.rates.user_specific_conditional_upper_rate
            ),
            advertised_max_rate=evaluation.rates.advertised_max_rate,
            rate_as_of=max(as_of_values) if as_of_values else None,
            selected_term_value=prepared.term.value,
            selected_term_unit=prepared.term.unit,
            scenario_principal=prepared.principal,
            customer_scope=prepared.customer_scope,
            comparison_basis=comparison_basis,
            rate_comparable=(
                evaluation.rates.calculation_status == "CALCULATED"
                and evaluation.rates.realizable_rate is not None
            ),
            interest_comparable=(
                ranking_comparability == RankingComparability.COMPARABLE
            ),
            ranking_comparability=ranking_comparability,
            applied_condition_ids=applied_condition_ids,
            missing_fact_ids=sorted(set(missing_fact_ids)),
        )
