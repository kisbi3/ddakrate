from __future__ import annotations

from eligibility.engine.evaluator import RuleEvaluator
from eligibility.schema.enums import ComparisonOperator, EvaluationStatus
from eligibility.schema.rule import AndRule, FactComparisonRule, NotRule, OrRule
from eligibility.schema.user_fact import UserFactStore


def _fact_rule(rule_id: str, fact_type: str, *, on_true=EvaluationStatus.SATISFIED):
    return FactComparisonRule(
        rule_id=rule_id,
        name=rule_id,
        fact_type=fact_type,
        operator=ComparisonOperator.EQ,
        expected=True,
        on_true_status=on_true,
    )


def test_and_is_satisfied_when_all_children_are_satisfied(basic_context, fact_factory):
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[fact_factory("A", "A", True), fact_factory("B", "B", True)],
    )
    rule = AndRule(rule_id="AND", name="all", children=[_fact_rule("A", "A"), _fact_rule("B", "B")])

    result = RuleEvaluator(store, basic_context).evaluate(rule)

    assert result.status == EvaluationStatus.SATISFIED
    assert [child.status for child in result.children] == [
        EvaluationStatus.SATISFIED,
        EvaluationStatus.SATISFIED,
    ]


def test_and_short_circuits_on_unsatisfiable_child(basic_context, fact_factory):
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[fact_factory("A", "A", False)],
    )
    rule = AndRule(
        rule_id="AND",
        name="all",
        children=[_fact_rule("A", "A"), _fact_rule("MISSING", "MISSING")],
    )

    result = RuleEvaluator(store, basic_context).evaluate(rule)

    assert result.status == EvaluationStatus.UNSATISFIABLE
    assert len(result.children) == 1
    assert result.missing_facts == []


def test_or_propagates_unknown_when_all_known_branches_are_false(basic_context, fact_factory):
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[fact_factory("FALSE", "FALSE", False)],
    )
    rule = OrRule(
        rule_id="OR",
        name="any",
        children=[_fact_rule("FALSE", "FALSE"), _fact_rule("UNKNOWN", "UNKNOWN")],
    )

    result = RuleEvaluator(store, basic_context).evaluate(rule)

    assert result.status == EvaluationStatus.UNKNOWN


def test_or_achievable_branch_dominates_unknown_alternative(basic_context, fact_factory):
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[fact_factory("ACTION", "ACTION", True)],
    )
    rule = OrRule(
        rule_id="OR",
        name="any",
        children=[
            _fact_rule("ACTION", "ACTION", on_true=EvaluationStatus.ACHIEVABLE),
            _fact_rule("UNKNOWN", "UNKNOWN"),
        ],
    )

    result = RuleEvaluator(store, basic_context).evaluate(rule)

    assert result.status == EvaluationStatus.ACHIEVABLE
    assert result.missing_facts == []


def test_and_propagates_achievable_when_no_child_is_unknown_or_false(basic_context, fact_factory):
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[fact_factory("NOW", "NOW", True), fact_factory("ACTION", "ACTION", True)],
    )
    rule = AndRule(
        rule_id="AND",
        name="all",
        children=[
            _fact_rule("NOW", "NOW"),
            _fact_rule("ACTION", "ACTION", on_true=EvaluationStatus.ACHIEVABLE),
        ],
    )

    result = RuleEvaluator(store, basic_context).evaluate(rule)

    assert result.status == EvaluationStatus.ACHIEVABLE


def test_not_inverts_satisfied_and_unsatisfiable(basic_context, fact_factory):
    true_store = UserFactStore(
        user_id="TEST_USER", facts=[fact_factory("F", "F", True)]
    )
    false_store = UserFactStore(
        user_id="TEST_USER", facts=[fact_factory("F", "F", False)]
    )
    rule = NotRule(rule_id="NOT", name="not", child=_fact_rule("F", "F"))

    assert RuleEvaluator(true_store, basic_context).evaluate(rule).status == EvaluationStatus.UNSATISFIABLE
    assert RuleEvaluator(false_store, basic_context).evaluate(rule).status == EvaluationStatus.SATISFIED
