from __future__ import annotations

from eligibility.catalog.loader import load_default_product_catalog
from eligibility.engine.rate_engine import RateEngine
from eligibility.schema.enums import EvaluationStatus, VerificationLevel
from eligibility.schema.evaluation import RuleEvaluation


def test_conditional_rate_never_exceeds_the_published_maximum() -> None:
    """Every product family must treat the published maximum as a hard ceiling."""

    for product in load_default_product_catalog():
        if product.base_rate is None or product.advertised_max_rate is None:
            continue
        rule_results = [
            RuleEvaluation(
                rule_id=rule.rule.rule_id,
                rule_name=rule.rule.name,
                status=EvaluationStatus.UNKNOWN,
                reason_code="TEST_UNKNOWN_CONDITION",
                verification_level=VerificationLevel.UNKNOWN,
                evidence_levels=[],
            )
            for rule in product.preferential_rules
        ]
        summary = RateEngine.calculate(product, rule_results, [])

        assert summary.user_specific_conditional_upper_rate is None or (
            summary.user_specific_conditional_upper_rate
            <= product.advertised_max_rate
        ), product.product_id
