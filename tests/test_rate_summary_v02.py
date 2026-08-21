from __future__ import annotations

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.shinhan_youth_first import golden_context, shinhan_youth_first_product
from eligibility.fixtures.user_001 import user_001


def test_rate_summary_serializes_user_specific_conditional_upper_name():
    result = FinancialEligibilityEngine().evaluate_product(
        shinhan_youth_first_product(),
        user_001(),
        golden_context(),
    )
    dumped = result.rates.model_dump(mode="json")

    assert "user_specific_conditional_upper_rate" in dumped
    assert "conditional_upper_rate" not in dumped
    assert result.rates.conditional_upper_rate == result.rates.user_specific_conditional_upper_rate
