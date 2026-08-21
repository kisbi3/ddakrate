from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import salary_envelope_6m_product
from eligibility.fixtures.shinhan_youth_first import (
    golden_context,
    shinhan_youth_first_product,
)
from eligibility.fixtures.user_001 import user_001, with_supersol_answer
from eligibility.schema.application_input import Capability, QuickInputProfile
from eligibility.schema.enums import (
    CapabilityState,
    ComparisonOperator,
    ContextDateField,
    EntityType,
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    ResolutionStrategy,
    RulePurpose,
)
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.product import PreferentialRateRule, ProductDefinition, Reward
from eligibility.schema.rule import (
    CoverageRequirement,
    DateExpression,
    ExistenceAssertionFallback,
    FactComparisonRule,
    MissingFactSpec,
    NotExistsRule,
    TimeWindow,
)
from eligibility.schema.user_fact import DataCoverage, UserFact, UserFactStore


OUT = Path(__file__).resolve().parents[1] / "examples" / "v0.3.2"
USER = "ADV-WEB-U001"


def context() -> EvaluationContext:
    return EvaluationContext(
        as_of=date(2026, 8, 19),
        subscription_date=date(2026, 8, 20),
        maturity_date=date(2027, 8, 20),
    )


def absence_product() -> ProductDefinition:
    subscription = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)
    absence = NotExistsRule(
        rule_id="ADV-FIRST-TRANSACTION",
        name="직전 1년 관련상품 미보유",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        entity=EntityType.ACCOUNT_HOLDING_INTERVAL,
        filters={
            "institution": "SHINHAN_BANK",
            "product_type": ["TIME_DEPOSIT", "INSTALLMENT_SAVINGS", "HOUSING_SUBSCRIPTION"],
        },
        overlaps=TimeWindow(
            start=DateExpression.add_years(subscription, -1),
            end=DateExpression.add_days(subscription, -1),
        ),
        coverage=CoverageRequirement(
            fact_domain="SHINHAN_RELEVANT_HOLDING_HISTORY",
            institution="SHINHAN_BANK",
        ),
        self_report_fallback=ExistenceAssertionFallback(
            fact_type="SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y",
            missing_fact=MissingFactSpec(
                resolution_strategy=ResolutionStrategy.ASK_USER,
                expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            ),
        ),
    )
    return ProductDefinition(
        product_id="ADV-ABSENCE",
        institution_id="SHINHAN_BANK",
        name="Adversarial absence",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=12,
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("3.0"),
        preferential_rate_cap=Decimal("1.0"),
        eligibility_rule=FactComparisonRule(
            rule_id="ADV-ELIG",
            name="가입 가능",
            purpose=RulePurpose.ELIGIBILITY,
            fact_type="ELIGIBLE",
            operator=ComparisonOperator.EQ,
            expected=True,
        ),
        preferential_rules=[
            PreferentialRateRule(rule=absence, reward=Reward(value=Decimal("1.0")))
        ],
    )


def eligibility_fact() -> UserFact:
    return UserFact(
        fact_id="ADV-ELIG-FACT",
        user_id=USER,
        fact_type="ELIGIBLE",
        value=True,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_FACT,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    engine = FinancialEligibilityEngine()
    product = absence_product()

    # Case 1: no authoritative coverage, user explicitly reports no past holding.
    self_store = UserFactStore(
        user_id=USER,
        facts=[
            eligibility_fact(),
            UserFact(
                fact_id="ADV-SELF-NO-HOLDING",
                user_id=USER,
                fact_type="SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y",
                value=False,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            ),
        ],
    )
    self_result = engine.evaluate_product(product, self_store, context())
    self_rule = self_result.preferential_rule_results[0]
    assert self_rule.status == EvaluationStatus.SATISFIED
    assert self_rule.verification_level.value == "SELF_REPORTED"
    assert self_result.rates.confirmed_rate == Decimal("2.0")
    assert self_result.rates.realizable_rate == Decimal("3.0")
    assert not self_store.data_coverages

    # Case 2: same absence established by MyData coverage.
    verified_store = UserFactStore(
        user_id=USER,
        facts=[eligibility_fact()],
        data_coverages=[
            DataCoverage(
                coverage_id="ADV-MYDATA-COVERAGE",
                user_id=USER,
                fact_domain="SHINHAN_RELEVANT_HOLDING_HISTORY",
                institution="SHINHAN_BANK",
                covered_from=date(2025, 8, 20),
                covered_to=date(2026, 8, 19),
                source_type=FactSourceType.MYDATA_VERIFIED,
            )
        ],
    )
    verified_result = engine.evaluate_product(product, verified_store, context())
    verified_rule = verified_result.preferential_rule_results[0]
    assert verified_rule.status == EvaluationStatus.SATISFIED
    assert verified_rule.verification_level.value == "MYDATA_VERIFIED"
    assert verified_result.rates.confirmed_rate == Decimal("3.0")

    # Case 3: forward-looking SuperSOL answer never becomes SATISFIED performance.
    shinhan = engine.evaluate_product(
        shinhan_youth_first_product(),
        with_supersol_answer(user_001(), True),
        golden_context(),
    )
    supersol = next(
        r for r in shinhan.preferential_rule_results if r.rule_id == "RATE_SUPERSOL"
    )
    assert supersol.status == EvaluationStatus.ACHIEVABLE
    assert supersol.verification_level.value == "USER_INTENT"

    # Case 4: contradictory capability input is invalid.
    capability_error = None
    try:
        QuickInputProfile(
            capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN),
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT),
            ]
        )
    except ValidationError as exc:
        capability_error = str(exc).splitlines()[0]
    assert capability_error is not None

    # Case 5: 12-month ProductDefinition cannot claim fixed 6-month metadata.
    term_error = None
    payload = shinhan_youth_first_product().model_dump(mode="python")
    payload["metadata"]["min_term"] = {"value": 6, "unit": "MONTH"}
    payload["metadata"]["max_term"] = {"value": 6, "unit": "MONTH"}
    payload["metadata"]["available_terms"] = [{"value": 6, "unit": "MONTH"}]
    try:
        ProductDefinition.model_validate(payload)
    except ValidationError as exc:
        term_error = str(exc).splitlines()[0]
    assert term_error is not None

    # Case 6: ActionPath cannot point at another owning rule.
    lineage_error = None
    action_payload = salary_envelope_6m_product().model_dump(mode="python")
    action_payload["preferential_rules"][0]["rule"]["future_achievement"]["action_paths"][0][
        "rule_id"
    ] = "OTHER-RULE"
    try:
        ProductDefinition.model_validate(action_payload)
    except ValidationError as exc:
        lineage_error = str(exc).splitlines()[0]
    assert lineage_error is not None

    results = {
        "case_1_self_reported_absence": {
            "status": self_rule.status.value,
            "verification": self_rule.verification_level.value,
            "confirmed_rate": str(self_result.rates.confirmed_rate),
            "realizable_rate": str(self_result.rates.realizable_rate),
            "data_coverage_count": len(self_store.data_coverages),
        },
        "case_2_mydata_verified_absence": {
            "status": verified_rule.status.value,
            "verification": verified_rule.verification_level.value,
            "confirmed_rate": str(verified_result.rates.confirmed_rate),
            "realizable_rate": str(verified_result.rates.realizable_rate),
        },
        "case_3_supersol_future_intent": {
            "status": supersol.status.value,
            "verification": supersol.verification_level.value,
            "is_satisfied": supersol.status == EvaluationStatus.SATISFIED,
        },
        "case_4_capability_conflict": {"validation_blocked": True, "error": capability_error},
        "case_5_fixed_term_mismatch": {"validation_blocked": True, "error": term_error},
        "case_6_action_path_owner_mismatch": {"validation_blocked": True, "error": lineage_error},
    }
    path = OUT / "adversarial-results-v0.3.2.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nWROTE {path}")


if __name__ == "__main__":
    main()
