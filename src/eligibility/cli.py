from __future__ import annotations

import argparse
import json
from typing import Sequence

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.hana_run import (
    hana_run_context,
    hana_run_product,
    hana_run_user,
)
from eligibility.fixtures.ibk_parent_benefit import (
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
from eligibility.fixtures.user_001 import (
    user_001,
    with_event_coupon_answer,
    with_supersol_answer,
)
from eligibility.schema.enums import FactSourceType
from eligibility.llm import (
    LLMGateway,
    LLMSettings,
    resolve_openai_responses_model_profile,
)
from eligibility.schema.rule import RULE_NODE_ADAPTER
from eligibility.visualization import (
    product_rule_to_mermaid,
    trace_to_mermaid,
    user_facts_to_mermaid,
)


def _parse_tristate(value: str) -> bool | None:
    mapping = {"yes": True, "no": False, "unknown": None}
    return mapping[value]


def _print_evaluation(product, store, context, contribution_plan=None) -> int:
    result = FinancialEligibilityEngine().evaluate_product(
        product,
        store,
        context,
        contribution_plan,
    )
    print(result.model_dump_json(indent=2))
    return 0


def _golden(args: argparse.Namespace) -> int:
    store = user_001()
    supersol = _parse_tristate(args.supersol)
    event_coupon = _parse_tristate(args.event_coupon)
    if supersol is not None:
        store = with_supersol_answer(store, supersol)
    if event_coupon is not None:
        store = with_event_coupon_answer(store, event_coupon)
    return _print_evaluation(
        shinhan_youth_first_product(),
        store,
        golden_context(),
        golden_contribution_plan(),
    )


def _kakao(args: argparse.Namespace) -> int:
    if args.scenario == "all-success":
        store = kakao_user_all_success()
    else:
        store = kakao_user_with_failure(
            manual_recovery=args.scenario == "manual-recovery"
        )
    return _print_evaluation(
        kakao_26_week_product(), store, kakao_26_week_context()
    )


def _ibk(args: argparse.Namespace) -> int:
    source = (
        FactSourceType.INSTITUTION_VERIFIED
        if args.relationship == "verified"
        else FactSourceType.USER_DECLARED
    )
    return _print_evaluation(
        ibk_parent_benefit_product(),
        ibk_parent_benefit_user(relationship_source=source),
        ibk_parent_benefit_context(),
    )


def _hana(args: argparse.Namespace) -> int:
    return _print_evaluation(
        hana_run_product(),
        hana_run_user(args.distance_km),
        hana_run_context(),
    )
def _llm_health(_: argparse.Namespace) -> int:
    settings = LLMSettings.from_env()
    status = LLMGateway.from_settings(settings).health_check()
    print(status.model_dump_json(indent=2))
    return 0 if status.healthy else 1


def _llm_config(_: argparse.Namespace) -> int:
    settings = LLMSettings.from_env()
    summary: dict[str, object] = {
        "provider": settings.provider,
        "base_url": settings.base_url,
        "model": settings.model,
        "api_family": (
            "RESPONSES"
            if settings.provider == "OPENAI"
            else "CHAT_COMPLETIONS"
            if settings.provider == "OPENAI_COMPATIBLE"
            else "MOCK"
        ),
        "credential_configured": bool(settings.api_key),
        "timeout_seconds": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }
    if settings.provider == "OPENAI":
        profile = resolve_openai_responses_model_profile(settings.model)
        summary["model_profile"] = profile.name
        summary["known_models"] = list(profile.known_models)
        summary["request_controls"] = {
            "temperature": (
                settings.temperature if profile.supports_temperature else "OMITTED"
            ),
            "reasoning_effort": settings.reasoning_effort,
            "supported_reasoning_efforts": (
                list(profile.supported_reasoning_efforts)
                if profile.supported_reasoning_efforts is not None
                else "PROVIDER_VALIDATED"
            ),
            "structured_outputs": (
                profile.supports_structured_outputs
                if profile.supports_structured_outputs is not None
                else "PROVIDER_VALIDATED"
            ),
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _schema(_: argparse.Namespace) -> int:
    print(json.dumps(RULE_NODE_ADAPTER.json_schema(), ensure_ascii=False, indent=2))
    return 0


def _visualize(args: argparse.Namespace) -> int:
    product, store, context = _fixture_bundle(args.fixture)
    if args.kind == "facts":
        print(user_facts_to_mermaid(store, include_provenance=args.provenance), end="")
        return 0
    if args.kind == "rules":
        print(
            product_rule_to_mermaid(
                product,
                include_source=args.provenance,
                max_depth=args.max_depth,
            ),
            end="",
        )
        return 0
    result = FinancialEligibilityEngine().evaluate_product(product, store, context)
    print(
        trace_to_mermaid(
            result,
            include_evidence=not args.no_evidence,
            include_provenance=args.provenance,
            max_depth=args.max_depth,
        ),
        end="",
    )
    return 0


def _fixture_bundle(name: str):
    if name == "shinhan":
        return shinhan_youth_first_product(), user_001(), golden_context()
    if name == "kakao":
        return kakao_26_week_product(), kakao_user_with_failure(), kakao_26_week_context()
    if name == "ibk":
        return (
            ibk_parent_benefit_product(),
            ibk_parent_benefit_user(),
            ibk_parent_benefit_context(),
        )
    if name == "hana":
        return hana_run_product(), hana_run_user(), hana_run_context()
    raise ValueError(name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eligibility-demo",
        description="Financial Eligibility Engine deterministic v0.3.2 demo",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    golden = subparsers.add_parser(
        "golden",
        help="Evaluate U001 against 신한은행 청년 처음적금",
    )
    golden.add_argument(
        "--supersol",
        choices=["unknown", "yes", "no"],
        default="unknown",
    )
    golden.add_argument(
        "--event-coupon",
        choices=["unknown", "yes", "no"],
        default="unknown",
    )
    golden.set_defaults(handler=_golden)

    kakao = subparsers.add_parser("kakao", help="Evaluate 카카오뱅크 26주적금")
    kakao.add_argument(
        "--scenario",
        choices=["all-success", "failure", "manual-recovery"],
        default="failure",
    )
    kakao.set_defaults(handler=_kakao)

    ibk = subparsers.add_parser("ibk", help="Evaluate IBK부모급여우대적금")
    ibk.add_argument(
        "--relationship",
        choices=["verified", "user-declared"],
        default="verified",
    )
    ibk.set_defaults(handler=_ibk)

    hana = subparsers.add_parser("hana", help="Evaluate 하나은행 달려라 하나 적금")
    hana.add_argument("--distance-km", type=int, default=523)
    hana.set_defaults(handler=_hana)

    visualize = subparsers.add_parser(
        "visualize", help="Render trace, facts, or Rule AST as Mermaid"
    )
    visualize.add_argument(
        "--kind", choices=["trace", "facts", "rules"], default="trace"
    )
    visualize.add_argument(
        "--fixture", choices=["shinhan", "kakao", "ibk", "hana"], default="shinhan"
    )
    visualize.add_argument("--max-depth", type=int, default=None)
    visualize.add_argument("--provenance", action="store_true")
    visualize.add_argument("--no-evidence", action="store_true")
    visualize.set_defaults(handler=_visualize)

    schema = subparsers.add_parser("schema", help="Print Rule AST v0.3.2 JSON Schema")
    schema.set_defaults(handler=_schema)

    health = subparsers.add_parser(
        "llm-health",
        help="Check the configured Mock or OpenAI-compatible LLM endpoint",
    )
    health.set_defaults(handler=_llm_health)

    config = subparsers.add_parser(
        "llm-config",
        help="Print the resolved LLM model profile without exposing credentials",
    )
    config.set_defaults(handler=_llm_config)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
