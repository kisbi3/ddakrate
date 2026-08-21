from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.extraction import salary_envelope_extraction_draft
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.fixtures.kakao_26_week import (
    kakao_26_week_context,
    kakao_26_week_product,
    kakao_user_from_statuses,
)
from eligibility.fx import (
    FxPlanningService,
    FxThresholdPolicy,
    MockFxQuoteProvider,
    PlannedMonetaryAmount,
)
from eligibility.goal import GoalFactory
from eligibility.ingestion import SemanticValidator, product_definition_from_draft
from eligibility.schema.enums import (
    EvaluationStatus,
    EvaluationTrustMode,
    FactSemanticType,
    FactSourceType,
    FxThresholdClassification,
    ScheduledOccurrenceStatus,
    TermUnit,
)
from eligibility.schema.product import MonthlyContributionPlan
from eligibility.schema.user_fact import DataCoverage, UserFact


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "v0.3.1"
REPORT_DIR = ROOT / "reports"


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _salary_self_report_scenario() -> dict[str, Any]:
    product = salary_envelope_6m_product()
    base_context = salary_envelope_context()
    context = base_context.model_copy(update={"as_of": base_context.maturity_date})
    store = salary_envelope_user()
    reports: list[UserFact] = []
    for index, event_date in enumerate(
        [
            date(2026, 8, 20),
            date(2026, 9, 20),
            date(2026, 10, 20),
            date(2026, 11, 20),
            date(2026, 12, 20),
            date(2027, 1, 20),
        ],
        start=1,
    ):
        reports.append(
            UserFact(
                fact_id=f"ADV-SALARY-SELF-{index}",
                user_id=store.user_id,
                fact_type="SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED",
                value=True,
                valid_from=event_date,
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                collected_at=datetime(2027, 1, 21, tzinfo=timezone.utc),
            )
        )
    coverage = DataCoverage(
        coverage_id="ADV-SALARY-COVERAGE",
        user_id=store.user_id,
        fact_domain="SHINHAN_SALARY_CLUB_ENVELOPE_EVENT",
        institution="SHINHAN_BANK",
        covered_from=context.subscription_date,
        covered_to=context.as_of,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
    )
    store = store.model_copy(
        update={
            "facts": [*store.facts, *reports],
            "data_coverages": [coverage],
        },
        deep=True,
    )
    strict = FinancialEligibilityEngine().evaluate_product(
        product,
        store,
        context,
        trust_mode=EvaluationTrustMode.STRICT,
    )
    provisional = FinancialEligibilityEngine().evaluate_product(
        product,
        store,
        context,
        trust_mode=EvaluationTrustMode.MVP_PROVISIONAL,
    )
    strict_rule = strict.preferential_rule_results[0]
    provisional_rule = provisional.preferential_rule_results[0]

    assert strict_rule.status != EvaluationStatus.SATISFIED
    assert strict_rule.reason_code == "FACT_AUTHORITY_INSUFFICIENT"
    assert strict.rates.confirmed_rate == product.base_rate
    # v0.3.2+ deliberately stopped treating the legacy global
    # MVP_PROVISIONAL flag as authority to upgrade self-reported financial
    # evidence.  Keep this historical adversarial script runnable against the
    # current baseline by asserting that the compatibility parameter does not
    # bypass per-Fact evidence semantics.
    assert provisional_rule.status != EvaluationStatus.SATISFIED
    assert provisional_rule.reason_code == "FACT_AUTHORITY_INSUFFICIENT"
    assert provisional.rates.confirmed_rate == product.base_rate
    assert provisional.is_provisional is False

    return {
        "strict": {
            "status": strict_rule.status,
            "reason_code": strict_rule.reason_code,
            "progress": strict_rule.progress,
            "confirmed_rate": strict.rates.confirmed_rate,
            "is_provisional": strict.is_provisional,
            "rule_verification_level": strict_rule.verification_level,
            "evaluation_verification_level": strict.verification_level,
        },
        "mvp_provisional_control": {
            "status": provisional_rule.status,
            "progress": provisional_rule.progress,
            "confirmed_rate": provisional.rates.confirmed_rate,
            "is_provisional": provisional.is_provisional,
            "rule_verification_level": provisional_rule.verification_level,
            "evaluation_verification_level": provisional.verification_level,
            "provisional_rule_ids": provisional.provisional_rule_ids,
        },
    }


def _goal_current_month_scenario() -> dict[str, Any]:
    product = salary_envelope_6m_product()
    base_context = salary_envelope_context()
    context = base_context.model_copy(update={"as_of": base_context.subscription_date})
    store = salary_envelope_user()
    event = UserFact(
        fact_id="ADV-SALARY-MONTH-1",
        user_id=store.user_id,
        fact_type="SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED",
        value=True,
        valid_from=context.subscription_date,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_EVENT,
        collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    coverage = DataCoverage(
        coverage_id="ADV-SALARY-CURRENT-COVERAGE",
        user_id=store.user_id,
        fact_domain="SHINHAN_SALARY_CLUB_ENVELOPE_EVENT",
        institution="SHINHAN_BANK",
        covered_from=context.subscription_date,
        covered_to=context.subscription_date,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
    )
    store = store.model_copy(
        update={
            "facts": [
                *store.facts,
                future_intent_fact(SALARY_INTENT_FACT_TYPE, True),
                event,
            ],
            "data_coverages": [coverage],
        },
        deep=True,
    )
    evaluation = FinancialEligibilityEngine().evaluate_product(product, store, context)
    rule = evaluation.preferential_rule_results[0]
    goal = GoalFactory().create_from_evaluation(
        product=product,
        evaluation=evaluation,
        context=context,
        subscription_id="ADV-SUB-SALARY-CURRENT",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]

    assert rule.progress is not None and rule.progress.current == 1
    assert rule.evidence["remaining_opportunities"] == 11
    assert goal.current_progress == 1
    assert goal.remaining_opportunities == 11
    assert goal.qualified_opportunity_keys == ["2026-08"]

    return {
        "rule_status": rule.status,
        "current_progress": goal.current_progress,
        "future_remaining_opportunities": goal.remaining_opportunities,
        "qualified_opportunity_keys": goal.qualified_opportunity_keys,
        "buffer": goal.buffer,
    }


def _rule_identity_scenario() -> dict[str, Any]:
    draft = salary_envelope_extraction_draft()
    rate = draft.rules[1]
    bad_rate = rate.model_copy(
        update={"ast": {**rate.ast, "rule_id": "ADV-DIFFERENT-AST-ID"}},
        deep=True,
    )
    bad = draft.model_copy(update={"rules": [draft.rules[0], bad_rate]}, deep=True)
    report = SemanticValidator().validate(bad)
    activation_blocked = False
    activation_error = None
    try:
        product_definition_from_draft(bad)
    except ValueError as exc:
        activation_blocked = True
        activation_error = str(exc)

    assert report.valid is False
    assert any(issue.code == "RULE_IDENTITY_MISMATCH" for issue in report.errors)
    assert activation_blocked is True

    return {
        "validation_valid": report.valid,
        "error_codes": [issue.code for issue in report.errors],
        "activation_blocked": activation_blocked,
        "activation_error": activation_error,
    }


def _evaluation_identity_scenario() -> dict[str, Any]:
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
    low_interest = low.interest_estimates["realizable"].pre_tax_interest
    high_interest = high.interest_estimates["realizable"].pre_tax_interest

    assert low_interest != high_interest
    assert low.evaluation_id != high.evaluation_id

    return {
        "monthly_100000": {
            "evaluation_id": low.evaluation_id,
            "realizable_pre_tax_interest": low_interest,
        },
        "monthly_500000": {
            "evaluation_id": high.evaluation_id,
            "realizable_pre_tax_interest": high_interest,
        },
        "identity_collision": low.evaluation_id == high.evaluation_id,
    }


def _kakao_streak_scenario() -> dict[str, Any]:
    statuses = [ScheduledOccurrenceStatus.FAILED] * 26
    statuses[0] = ScheduledOccurrenceStatus.SUCCESS
    statuses[1] = ScheduledOccurrenceStatus.FAILED
    for index in range(2, 9):
        statuses[index] = ScheduledOccurrenceStatus.SUCCESS
    evaluation = FinancialEligibilityEngine().evaluate_product(
        kakao_26_week_product(),
        kakao_user_from_statuses(statuses),
        kakao_26_week_context(),
    )
    seven, twenty_six = evaluation.preferential_rule_results

    assert seven.status == EvaluationStatus.SATISFIED
    assert seven.progress is not None and seven.progress.current == 7
    assert twenty_six.status != EvaluationStatus.SATISFIED
    assert evaluation.rates.confirmed_rate == Decimal("3.0")

    return {
        "seven_week": {
            "status": seven.status,
            "progress": seven.progress,
            "streak_start_sequence": seven.evidence["streak_start_sequence"],
            "streak_end_sequence": seven.evidence["streak_end_sequence"],
            "reason_code": seven.reason_code,
        },
        "twenty_six_week": {
            "status": twenty_six.status,
            "progress": twenty_six.progress,
            "reason_code": twenty_six.reason_code,
        },
        "confirmed_rate": evaluation.rates.confirmed_rate,
    }


def _fx_near_threshold_scenario() -> dict[str, Any]:
    service = FxPlanningService(
        MockFxQuoteProvider({("KRW", "USD"): Decimal("0.000723")}),
        threshold_policy=FxThresholdPolicy(
            near_threshold_margin_ratio=Decimal("0.03")
        ),
    )
    estimate = service.estimate_threshold(
        PlannedMonetaryAmount(
            amount=Decimal("14000000"),
            currency="KRW",
            period=TermUnit.YEAR,
            source=FactSourceType.USER_DECLARED,
            semantic_type=FactSemanticType.FUTURE_INTENT,
        ),
        target_currency="USD",
        target_threshold=Decimal("10000"),
    )

    assert estimate.classification == FxThresholdClassification.NEAR_THRESHOLD
    assert estimate.warning == "FX_VOLATILITY_WARNING"
    assert estimate.authoritative_for_final_reward is False

    return estimate.model_dump(mode="json")


def main() -> None:
    EXAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = {
        "suite": "Financial Eligibility Engine v0.3.1 adversarial hardening",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenarios": {
            "strict_self_report_authority": _salary_self_report_scenario(),
            "goal_current_month_no_double_count": _goal_current_month_scenario(),
            "rule_draft_ast_identity": _rule_identity_scenario(),
            "contribution_plan_evaluation_identity": _evaluation_identity_scenario(),
            "kakao_late_seven_week_streak": _kakao_streak_scenario(),
            "fx_near_threshold": _fx_near_threshold_scenario(),
        },
        "all_assertions_passed": True,
    }
    json_path = EXAMPLE_DIR / "adversarial-results.json"
    json_path.write_text(
        json.dumps(_jsonable(results), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    scenarios = results["scenarios"]
    lines = [
        "Financial Eligibility Engine v0.3.1 — Adversarial Results",
        "",
        "1. STRICT self-report authority",
        f"   status={scenarios['strict_self_report_authority']['strict']['status']}",
        f"   reason={scenarios['strict_self_report_authority']['strict']['reason_code']}",
        "   verified SATISFIED prohibited=PASS",
        "",
        "2. Goal current-period opportunity",
        f"   progress={scenarios['goal_current_month_no_double_count']['current_progress']}",
        f"   future_remaining_opportunities={scenarios['goal_current_month_no_double_count']['future_remaining_opportunities']}",
        "   current month double count absent=PASS",
        "",
        "3. RuleDraft / AST identity",
        f"   validation_valid={scenarios['rule_draft_ast_identity']['validation_valid']}",
        f"   activation_blocked={scenarios['rule_draft_ast_identity']['activation_blocked']}",
        "",
        "4. ContributionPlan evaluation identity",
        f"   low={scenarios['contribution_plan_evaluation_identity']['monthly_100000']['evaluation_id']}",
        f"   high={scenarios['contribution_plan_evaluation_identity']['monthly_500000']['evaluation_id']}",
        f"   identity_collision={scenarios['contribution_plan_evaluation_identity']['identity_collision']}",
        "",
        "5. Kakao 7-week streak after early failure",
        f"   status={scenarios['kakao_late_seven_week_streak']['seven_week']['status']}",
        f"   sequence={scenarios['kakao_late_seven_week_streak']['seven_week']['streak_start_sequence']}..{scenarios['kakao_late_seven_week_streak']['seven_week']['streak_end_sequence']}",
        "",
        "6. FX near threshold",
        f"   classification={scenarios['fx_near_threshold']['classification']}",
        f"   warning={scenarios['fx_near_threshold']['warning']}",
        "",
        "ALL ASSERTIONS PASSED",
    ]
    text_path = REPORT_DIR / "adversarial-results-v0.3.1.txt"
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json_path)
    print(text_path)
    print("ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
