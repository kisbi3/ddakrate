from __future__ import annotations

from datetime import date

from eligibility.engine.temporal import business_day_offset, resolve_date_expression
from eligibility.schema.enums import ContextDateField
from eligibility.schema.rule import DateExpression


def test_business_day_offset_skips_weekend_and_configured_holiday(basic_context):
    context = basic_context.model_copy(
        update={"business_holidays": {date(2026, 1, 5)}}, deep=True
    )
    expression = DateExpression.business_day_offset(
        DateExpression.literal(date(2026, 1, 2)), 1
    )

    assert resolve_date_expression(expression, context) == date(2026, 1, 6)


def test_context_date_expression_resolves_subscription_date(basic_context):
    expression = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)

    assert resolve_date_expression(expression, basic_context) == date(2026, 1, 2)
