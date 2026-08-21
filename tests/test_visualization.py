from __future__ import annotations

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.kakao_26_week import kakao_26_week_product
from eligibility.fixtures.shinhan_youth_first import (
    golden_context,
    shinhan_youth_first_product,
)
from eligibility.fixtures.user_001 import user_001
from eligibility.visualization import (
    product_rule_to_mermaid,
    trace_to_mermaid,
    user_facts_to_mermaid,
)


def _shinhan_evaluation():
    return FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(),
        user_001(),
        golden_context(),
    )


def test_trace_to_mermaid_contains_product():
    source = trace_to_mermaid(_shinhan_evaluation())

    assert source.startswith("flowchart TD\n")
    assert "청년 처음적금" in source
    assert "advertised=6.05%" in source


def test_trace_to_mermaid_contains_rule_status():
    source = trace_to_mermaid(_shinhan_evaluation())

    assert "RATE_FIRST_TRANSACTION_BRANCH" in source
    assert "UNSATISFIABLE" in source
    assert "PRIOR_HOLDING_OVERLAPS_LOOKBACK" in source


def test_trace_to_mermaid_contains_or_branches():
    source = trace_to_mermaid(_shinhan_evaluation())

    assert "RATE_FIRST_OR_EVENT | OR" in source
    assert "첫거래 branch" in source
    assert "이벤트 branch" in source


def test_user_fact_map_contains_provenance():
    source = user_facts_to_mermaid(user_001(), include_provenance=True)

    assert source.startswith("flowchart LR\n")
    assert "source=MYDATA_VERIFIED" in source
    assert "2025-11-15~2026-04-12" in source
    assert "virtual-mydata/account-history/closed" in source


def test_visualizers_are_deterministic():
    evaluation = _shinhan_evaluation()
    store = user_001()

    assert trace_to_mermaid(evaluation) == trace_to_mermaid(evaluation)
    assert user_facts_to_mermaid(store) == user_facts_to_mermaid(store)


def test_trace_max_depth_omits_deeper_children_without_changing_status():
    source = trace_to_mermaid(_shinhan_evaluation(), max_depth=0)

    assert "deeper trace omitted" in source
    assert "RATE_FIRST_OR_EVENT" in source
    assert "UNKNOWN" in source


def test_product_rule_mermaid_contains_count_consecutive():
    source = product_rule_to_mermaid(kakao_26_week_product(), include_source=True)

    assert "26주적금" in source
    assert "COUNT_CONSECUTIVE" in source
    assert "카카오뱅크 26주적금 공식 상품페이지" in source


def test_mermaid_labels_escape_quotes_and_backticks():
    from eligibility.visualization.common import mermaid_escape

    escaped = mermaid_escape('상품"] --> X[`unsafe`]')

    assert '&quot;' in escaped
    assert '&#96;' in escaped
    assert '"' not in escaped
    assert '`' not in escaped
