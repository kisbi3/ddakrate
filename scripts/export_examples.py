from __future__ import annotations

import json
from pathlib import Path

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.hana_run import (
    hana_health_asset_management_service,
    hana_run_context,
    hana_run_product,
    hana_run_user,
)
from eligibility.fixtures.ibk_parent_benefit import (
    ibk_family_aggregation_service,
    ibk_parent_benefit_context,
    ibk_parent_benefit_product,
    ibk_parent_benefit_user,
)
from eligibility.fixtures.kakao_26_week import (
    kakao_26_week_context,
    kakao_26_week_product,
    kakao_user_all_success,
    kakao_user_with_failure,
)
from eligibility.fixtures.shinhan_youth_first import (
    golden_context,
    golden_contribution_plan,
    shinhan_youth_first_product,
)
from eligibility.fixtures.user_001 import user_001
from eligibility.schema.enums import FactSourceType
from eligibility.visualization import (
    product_rule_to_mermaid,
    trace_to_mermaid,
    user_facts_to_mermaid,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "v0.2"
ENGINE = FinancialEligibilityEngine()


def evaluate(product, store, context, plan=None):
    return ENGINE.evaluate_product(product, store, context, plan)


def write_model(filename: str, model) -> None:
    (OUTPUT / filename).write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")


def write_text(filename: str, text: str) -> None:
    (OUTPUT / filename).write_text(text, encoding="utf-8")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)

    shinhan = evaluate(
        shinhan_youth_first_product(),
        user_001(),
        golden_context(),
        golden_contribution_plan(),
    )
    kakao_success = evaluate(
        kakao_26_week_product(), kakao_user_all_success(), kakao_26_week_context()
    )
    kakao_failure = evaluate(
        kakao_26_week_product(), kakao_user_with_failure(), kakao_26_week_context()
    )
    kakao_manual = evaluate(
        kakao_26_week_product(),
        kakao_user_with_failure(manual_recovery=True),
        kakao_26_week_context(),
    )
    ibk_verified = evaluate(
        ibk_parent_benefit_product(),
        ibk_parent_benefit_user(),
        ibk_parent_benefit_context(),
    )
    ibk_unverified = evaluate(
        ibk_parent_benefit_product(),
        ibk_parent_benefit_user(
            relationship_source=FactSourceType.USER_DECLARED
        ),
        ibk_parent_benefit_context(),
    )
    hana = evaluate(hana_run_product(), hana_run_user(), hana_run_context())

    for filename, result in {
        "shinhan_initial.json": shinhan,
        "kakao_all_success.json": kakao_success,
        "kakao_failure.json": kakao_failure,
        "kakao_manual_recovery.json": kakao_manual,
        "ibk_verified_relationship.json": ibk_verified,
        "ibk_unverified_relationship.json": ibk_unverified,
        "hana_523km.json": hana,
    }.items():
        write_model(filename, result)

    write_model("hana_health_service.json", hana_health_asset_management_service())
    write_model("ibk_family_service.json", ibk_family_aggregation_service())

    shinhan_trace = trace_to_mermaid(
        shinhan, include_evidence=True, include_provenance=True
    )
    shinhan_facts = user_facts_to_mermaid(user_001(), include_provenance=True)
    kakao_rules = product_rule_to_mermaid(
        kakao_26_week_product(), include_source=True
    )
    write_text("shinhan_trace.mmd", shinhan_trace)
    write_text("user_001_fact_map.mmd", shinhan_facts)
    write_text("kakao_rule_graph.mmd", kakao_rules)

    summary = {
        "schema_version": "0.2",
        "shinhan": _summary(shinhan),
        "kakao_all_success": _summary(kakao_success),
        "kakao_failure": _summary(kakao_failure),
        "kakao_manual_recovery": _summary(kakao_manual),
        "ibk_verified_relationship": _summary(ibk_verified),
        "ibk_unverified_relationship": _summary(ibk_unverified),
        "hana_523km_focused_slice": _summary(hana),
    }
    write_text(
        "summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )

    demo = (
        "# Financial Eligibility Engine v0.2 Mermaid Demo\n\n"
        "## Evaluation Trace — 신한 청년 처음적금\n\n"
        "```mermaid\n"
        f"{shinhan_trace}```\n\n"
        "## User Fact Map — U001\n\n"
        "```mermaid\n"
        f"{shinhan_facts}```\n\n"
        "## Product Rule Graph — 카카오뱅크 26주적금\n\n"
        "```mermaid\n"
        f"{kakao_rules}```\n"
    )
    write_text("mermaid_demo.md", demo)


def _summary(result) -> dict:
    return {
        "product": result.product_name,
        "eligibility": result.eligibility_status.value,
        "rule_statuses": {
            item.rule_id: item.status.value
            for item in result.preferential_rule_results
        },
        "advertised_max_rate": str(result.rates.advertised_max_rate),
        "confirmed_rate": str(result.rates.confirmed_rate),
        "realizable_rate": str(result.rates.realizable_rate),
        "user_specific_conditional_upper_rate": str(
            result.rates.user_specific_conditional_upper_rate
        ),
    }


if __name__ == "__main__":
    main()
