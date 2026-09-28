"""Regression checks for preserving evidence without widening conditions."""

from decimal import Decimal

import pytest

from eligibility.search.semantic import SourceClause, coerce_expression


def clause(text):
    return SourceClause("clause", "rule", "hash", text, {}, False, Decimal("0.2"))


def predicate(text, **updates):
    return {
        "op": "PREDICATE", "source_quote": text, "kind": "CARD_PAYMENT",
        "metric": "AMOUNT", "comparator": "GTE", "expected": 5000,
        "expected_unit": "CHEON_WON", **updates,
    }


@pytest.mark.parametrize("text,kind", [
    ("마케팅동의 및 모바일메세지 수신동의", "CONSENT"),
    ("가입일 기준 직전 1년간 당행 예·적금 미보유 고객", "ABSENCE_HISTORY"),
])
def test_boolean_predicate_does_not_require_numeric_evidence(text, kind):
    result = coerce_expression(predicate(
        text, kind=kind, metric="BOOLEAN", comparator="EQ", expected=True,
        expected_unit=None,
    ), clause(text))
    assert result.op == "PREDICATE"
    assert result.expected is True


@pytest.mark.parametrize("amount", ["5,000", "5000"])
def test_grouped_amount_keeps_value_and_unit(amount):
    text = f"가입기간 12개월, 카드 결제금액 {amount}천원 이상"
    result = coerce_expression(predicate(text), clause(text))
    assert result.op == "PREDICATE"
    assert result.expected == 5000
    assert result.expected_unit == "CHEON_WON"


@pytest.mark.parametrize("source_amount,expected", [
    ("5,000", 5), ("5,000", 0), ("50,00", 5000),
    ("5.000", 5000), ("5.5", 5), ("5000", 500), ("5,000", 6000),
])
def test_numeric_grounding_does_not_accept_fragments_or_invented_amounts(source_amount, expected):
    text = f"카드 결제금액 {source_amount}천원 이상"
    result = coerce_expression(predicate(text, expected=expected), clause(text))
    assert result.op == "UNKNOWN"


@pytest.mark.parametrize("qualifier", [
    {"period": {"basis": "INVENTED_BASIS"}},
    {"period": {"start_quote": "존재하지 않는 집계 시작일"}},
    {"period": {"end_quote": "존재하지 않는 집계 종료일"}},
    {"period": {"term_ratio": "1/2"}},
    {"period": {"length": 6, "unit": "MONTH"}},
    {"period": "가입일부터"},
    {"scope": {"institution": "INVENTED_BANK"}},
    {"scope": "당행"},
])
def test_invalid_qualifier_defers_only_affected_leaf(qualifier):
    text = "가입기간 12개월, 카드 결제금액 5,000천원 이상, 마케팅동의"
    valid = predicate(text, kind="CONSENT", metric="BOOLEAN", comparator="EQ",
                      expected=True, expected_unit=None)
    result = coerce_expression({
        "op": "ALL", "source_quote": text,
        "children": [predicate(text, **qualifier), valid],
    }, clause(text))
    assert result.op == "ALL"
    assert [child.op for child in result.children] == ["UNKNOWN", "PREDICATE"]


def test_valid_scope_and_period_are_preserved():
    text = "당행 카드 결제금액 5,000천원 이상, 가입월부터 만기일 전전월말일까지"
    raw = predicate(text, scope={"institution": "THIS_INSTITUTION"}, period={
        "basis": "SUBSCRIPTION_MONTH", "start_quote": "가입월",
        "end_quote": "만기일 전전월말일",
    })
    result = coerce_expression(raw, clause(text))
    assert result.op == "PREDICATE"
    assert result.scope.institution == "THIS_INSTITUTION"
    assert result.period.start_quote == "가입월"
    assert result.period.end_quote == "만기일 전전월말일"


def catalog_clause(text, *, rule_id="RULE-PREF-02", runtime_id=None):
    runtime_id = runtime_id or f"PROD:CANONICAL:{rule_id}"
    return SourceClause(
        "clause", rule_id, "hash", text, {"rule_id": rule_id}, False, Decimal("0.2"),
        existing_runtime_rule_id=runtime_id,
    )


def test_ungrounded_official_flag_does_not_discard_amount_or_window():
    text = "당행 카드 결제금액 5,000천원 이상, 가입월부터 만기일 전전월말일까지"
    result = coerce_expression(predicate(text, expected_literal="5,000천원", official_confirmation_required=True,
                                         official_confirmation_basis="SOURCE_TEXT",
                                         scope={"institution": "THIS_INSTITUTION"},
                                         period={"start_quote": "가입월", "end_quote": "만기일 전전월말일"}),
                               catalog_clause(text))
    assert result.op == "PREDICATE"
    assert result.expected == 5000
    assert result.expected_literal == "5,000천원"
    assert result.period.start_quote == "가입월"
    assert result.official_confirmation_required is None


def test_source_backed_official_flag_is_kept():
    text = "증빙서류 제출 후 승인 완료된 경우 우대"
    result = coerce_expression(predicate(
        text, kind="CONSENT", metric="BOOLEAN", comparator="EQ", expected=True,
        expected_unit=None, official_confirmation_required=True,
        official_confirmation_basis="SOURCE_TEXT",
    ), catalog_clause(text))
    assert result.op == "PREDICATE"
    assert result.official_confirmation_required is True
    assert result.official_confirmation_basis == "SOURCE_TEXT"


@pytest.mark.parametrize("rule_id", [
    "RULE-PREF-02",
    "PROD:CANONICAL:RULE-PREF-02",
    "OTHER:CANONICAL:RULE-PREF-02",
])
def test_rule_id_alias_does_not_discard_grounded_leaf(rule_id):
    text = "카드 결제금액 5,000천원 이상"
    result = coerce_expression(predicate(text, existing_rule_id=rule_id), catalog_clause(text))
    assert result.op == "PREDICATE"
    assert result.expected == 5000
    assert result.existing_rule_id in {"RULE-PREF-02", "PROD:CANONICAL:RULE-PREF-02"}


def test_unrelated_rule_id_is_dropped_without_losing_the_leaf():
    text = "카드 결제금액 5,000천원 이상"
    result = coerce_expression(predicate(text, existing_rule_id="UNRELATED-RULE"), catalog_clause(text))
    assert result.op == "PREDICATE"
    assert result.existing_rule_id is None
    assert result.expected == 5000


def test_expected_literal_outside_leaf_quote_is_still_rejected():
    text = "당행신용(체크)카드 결제금액이 아래의 조건을 충족하는 경우\n결제금액 : 5,000천원"
    result = coerce_expression(predicate(
        "당행신용(체크)카드 결제금액", expected=None, expected_literal="5,000천원",
        expected_unit="CHEON_WON",
    ), catalog_clause(text))
    assert result.op == "UNKNOWN"
