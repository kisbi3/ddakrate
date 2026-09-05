from __future__ import annotations

from datetime import date

from eligibility.catalog.normalized_loader import (
    _build_rules,
    load_normalized_product_catalog,
)
from eligibility.engine.evaluator import RuleEvaluator
from eligibility.schema.enums import (
    FactSemanticType,
    FactSourceType,
    ResolutionStrategy,
    EvaluationStatus,
)
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.user_fact import UserFact, UserFactStore


def _product(policy: dict) -> dict:
    return {
        "product_code": "TEST-RELATIONSHIP",
        "version": 1,
        "institution_id": "TEST-BANK",
        "name": "관계조건 상품",
        "eligibility_policy": policy,
        "standard_conditions": [],
        "custom_bindings": [],
        "return_policy": {},
    }


def _evaluate(rule, store: UserFactStore | None = None):
    return RuleEvaluator(
        store or UserFactStore(user_id="USER-1"),
        EvaluationContext(
            as_of=date(2026, 9, 5),
            subscription_date=date(2026, 9, 5),
            maturity_date=date(2027, 9, 5),
        ),
    ).evaluate(rule)


def _fact_types(rule) -> set[str]:
    payload = rule.model_dump(mode="python")
    result: set[str] = set()
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("fact_type"):
                result.add(str(node["fact_type"]))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return result


def test_relationship_only_restricted_policy_never_falls_back_to_pass_rule():
    rule, _, _ = _build_rules(
        _product(
            {
                "mode": "RESTRICTED",
                "relationship_requirements": [
                    {
                        "kind": "PRODUCT_OR_SERVICE_RELATIONSHIP",
                        "source_text": "연계 서비스를 보유한 고객만 가입 가능",
                    }
                ],
            }
        ),
        {},
        {},
        {},
    )

    assert rule.fact_type == "NORMALIZED_ELIGIBILITY_UNKNOWN::TEST-RELATIONSHIP"
    assert rule.missing_fact is not None
    assert rule.missing_fact.resolution_strategy == ResolutionStrategy.QUERY_INSTITUTION
    assert rule.missing_fact.required_source == "OFFICIAL_PRODUCT_SOURCE"
    assert _evaluate(rule).status == EvaluationStatus.UNKNOWN


def test_uncompiled_relationship_remains_unknown_with_other_typed_requirements():
    rule, _, _ = _build_rules(
        _product(
            {
                "mode": "RESTRICTED",
                "allowed_customer_types": ["INDIVIDUAL"],
                "relationship_requirements": [
                    {
                        "kind": "PRODUCT_OR_ACCOUNT_RELATIONSHIP",
                        "source_text": "당행 입출금계좌를 보유한 고객만 가입 가능",
                    }
                ],
            }
        ),
        {},
        {},
        {},
    )

    assert rule.type == "AND"
    unknown = next(
        child
        for child in rule.children
        if getattr(child, "fact_type", "").startswith("NORMALIZED_ELIGIBILITY_UNKNOWN::")
    )
    assert unknown.missing_fact is not None
    assert _evaluate(rule).status == EvaluationStatus.UNKNOWN

    # A conversational self-report cannot turn an official-only gap into a
    # satisfied eligibility result. An institution-verified fact remains an
    # available future resolution path.
    self_report = UserFact(
        fact_id="FACT-1",
        user_id="USER-1",
        fact_type=unknown.fact_type,
        value=True,
        source_type=FactSourceType.USER_DECLARED,
        semantic_type=FactSemanticType.SELF_REPORTED_FACT,
    )
    assert _evaluate(rule, UserFactStore(user_id="USER-1", facts=[self_report])).status == (
        EvaluationStatus.UNKNOWN
    )


def test_relationship_field_is_not_reinterpreted_from_source_wording():
    rule, _, _ = _build_rules(
        _product(
            {
                "mode": "RESTRICTED",
                "relationship_requirements": [
                    {
                        "kind": "PRODUCT_OR_SERVICE_RELATIONSHIP",
                        "source_text": "예금잔액증명서 발급 당일 잔액 변동이 불가합니다.",
                    }
                ],
            }
        ),
        {},
        {},
        {},
    )
    assert rule.fact_type == "NORMALIZED_ELIGIBILITY_UNKNOWN::TEST-RELATIONSHIP"
    assert _evaluate(rule).status == EvaluationStatus.UNKNOWN


def test_explicit_operating_notice_does_not_become_an_eligibility_gate():
    rule, _, _ = _build_rules(
        _product(
            {
                "mode": "RESTRICTED",
                "relationship_requirements": [
                    {
                        "kind": "PRODUCT_OR_SERVICE_RELATIONSHIP",
                        "requirement_role": "OPERATING_NOTICE",
                        "source_text": "예금잔액증명서 발급 당일 잔액 변동이 불가합니다.",
                    }
                ],
            }
        ),
        {},
        {},
        {},
    )
    assert rule.fact_type == "NORMALIZED_BASE_ELIGIBILITY::TEST-RELATIONSHIP"


def test_uncompiled_eligibility_is_guarded_even_without_restricted_mode():
    rule, _, _ = _build_rules(
        _product(
            {
                "mode": "UNRESTRICTED_OR_SOURCE_LIMITED",
                "account_limit": {"scope": "PER_CUSTOMER", "max_accounts": 1},
            }
        ),
        {},
        {},
        {},
    )
    assert rule.fact_type == "NORMALIZED_ELIGIBILITY_UNKNOWN::TEST-RELATIONSHIP"
    assert _evaluate(rule).status == EvaluationStatus.UNKNOWN


def test_empty_relationship_container_does_not_create_a_guard():
    rule, _, _ = _build_rules(
        _product({"mode": "RESTRICTED", "relationship_requirements": []}),
        {},
        {},
        {},
    )
    assert rule.fact_type == "NORMALIZED_BASE_ELIGIBILITY::TEST-RELATIONSHIP"


def test_empty_uncompiled_container_does_not_create_a_guard():
    rule, _, _ = _build_rules(
        _product({"mode": "RESTRICTED", "excluded_customer_types": []}),
        {},
        {},
        {},
    )
    assert rule.fact_type == "NORMALIZED_BASE_ELIGIBILITY::TEST-RELATIONSHIP"


def test_every_published_relationship_requirement_has_a_non_pass_guard():
    affected = []
    missing_guards = []
    for product in load_normalized_product_catalog():
        policy = product.normalized.eligibility_policy if product.normalized else {}
        requirements = policy.get("relationship_requirements") or []
        if not any(
            row.get("requirement_role", "SUBSCRIPTION_ELIGIBILITY")
            == "SUBSCRIPTION_ELIGIBILITY"
            for row in requirements
            if isinstance(row, dict)
        ):
            continue
        affected.append(product.product_id)
        fact_types = _fact_types(product.eligibility_rule)
        if not any(
            fact_type.startswith("NORMALIZED_ELIGIBILITY_UNKNOWN::")
            or fact_type.startswith("NORMALIZED_DATA_GAP::eligibility_policy::")
            for fact_type in fact_types
        ):
            missing_guards.append(product.product_id)

    assert affected, "published catalog should exercise the relationship invariant"
    assert missing_guards == []


def test_every_published_relationship_requirement_has_an_explicit_role():
    rows = 0
    for product in load_normalized_product_catalog():
        policy = product.normalized.eligibility_policy if product.normalized else {}
        for row in policy.get("relationship_requirements") or []:
            rows += 1
            assert row.get("requirement_role") in {
                "SUBSCRIPTION_ELIGIBILITY",
                "OPERATING_NOTICE",
            }
    assert rows > 0
