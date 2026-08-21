from eligibility.application import submit_user_fact
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_6m_product,
    salary_envelope_user,
)
from eligibility.visualization import product_rule_to_mermaid, user_facts_to_mermaid


def test_future_intent_rule_visualization_handles_intent_only_spec():
    rendered = product_rule_to_mermaid(salary_envelope_6m_product())

    assert "phase=POST_SUBSCRIPTION" in rendered
    assert "WILL_TRACK_SHINHAN_SALARY_ENVELOPE_6M" in rendered
    assert "ACCUMULATIVE" in rendered


def test_user_fact_visualization_distinguishes_future_intent():
    store = submit_user_fact(
        salary_envelope_user(),
        future_intent_fact(SALARY_INTENT_FACT_TYPE, True),
    )

    rendered = user_facts_to_mermaid(store)

    assert "semantic=FUTURE_INTENT" in rendered
    assert "source=USER_DECLARED" in rendered
