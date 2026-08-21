from __future__ import annotations

from eligibility.fixtures.future_goals import salary_envelope_6m_product


def test_salary_club_action_path_preserves_official_branch_and_capability_detail():
    product = salary_envelope_6m_product()
    rule = product.preferential_rules[0].rule
    assert rule.future_achievement is not None
    paths = rule.future_achievement.action_paths

    assert len(paths) == 1
    path = paths[0]
    assert path.action_path_id == "SHINHAN_SALARY_CLUB_ENVELOPE_PATH"
    assert path.rule_id == rule.rule_id
    assert path.source_rule_node_id == "RATE_SALARY_ENVELOPE_6M"
    assert set(path.required_capabilities) == {
        "JOIN_BANK_SERVICE",
        "ACCEPT_MARKETING_CONSENT",
        "MANAGE_QUALIFYING_INCOME_CREDIT",
        "MAINTAIN_RECURRING_CONDITION",
    }
    assert path.required_service_refs == ["SHINHAN_SALARY_CLUB"]
    assert path.required_fact_types == [
        "SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED"
    ]
    assert path.required_duration is not None
    assert path.required_duration.value == 6
    assert path.required_duration.unit.value == "MONTH"
    assert path.provenance and path.provenance[0].page == 2

    serialized = path.model_dump_json().upper()
    assert "SELF_TRANSFER" not in serialized
    assert "자가이체" not in path.model_dump_json()
