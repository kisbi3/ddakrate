from __future__ import annotations

from datetime import date
from decimal import Decimal

from eligibility.application_service import ApplicationService
from eligibility.schema.application_input import (
    Capability,
    HardConstraint,
    NumericPreference,
    Preference,
)
from eligibility.schema.enums import (
    CapabilityState,
    ComparisonOperator,
    HardConstraintValue,
    IntentConflictType,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
    SaleStatus,
    SearchSessionStatus,
    RulePurpose,
)
from eligibility.schema.rule import FactComparisonRule
from eligibility.search.intent import IntentConflictValidator, IntentParser
from eligibility.search.retrieval import CandidateRetriever

from tests.v04_helpers import AS_OF, base_store, make_intent, make_product


def test_parse_hard_preference_capability_numeric_input():
    utterance = (
        "1년 적금 중 월 30만원을 넣을 수 없는 상품은 제외하고, "
        "영업점 필수 상품은 제외해 줘. 카드 새로 만드는 건 싫고 "
        "급여계좌 변경은 가능해."
    )

    intent = IntentParser().parse(utterance, user_id="V04-USER")

    assert intent.product_types == ["INSTALLMENT_SAVINGS"]
    assert intent.contribution_plan is not None
    assert intent.contribution_plan.desired_periodic_amount == Decimal("300000")
    assert intent.contribution_plan.maximum_affordable_periodic_amount is None
    assert any(item.field == "SUBSCRIPTION_CHANNEL" for item in intent.hard_constraints)
    amount = next(
        item for item in intent.numeric_preferences if item.field == "MONTHLY_CONTRIBUTION"
    )
    assert amount.strictness == PreferenceStrictness.HARD
    assert amount.direction == NumericPreferenceDirection.AT_LEAST
    assert any(
        item.field == "NEW_CARD_REQUIRED"
        and item.preference == PreferenceValue.PREFER_ABSENT
        for item in intent.preferences
    )
    states = {item.capability_id: item.state for item in intent.capabilities}
    assert states["NEW_CARD_ISSUANCE"] == CapabilityState.CANNOT
    assert states["CHANGE_SALARY_ACCOUNT"] == CapabilityState.CAN


def test_cross_category_hard_soft_conflict():
    intent = make_intent(
        hard_constraints=[
            HardConstraint(
                field="FIRST_TRANSACTION_BENEFIT",
                constraint=HardConstraintValue.REQUIRE,
            )
        ],
        preferences=[
            Preference(
                field="FIRST_TRANSACTION_BENEFIT",
                preference=PreferenceValue.PREFER_ABSENT,
            )
        ],
    )

    conflicts = IntentConflictValidator().validate(intent)

    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == IntentConflictType.HARD_SOFT_CONTRADICTION


def test_conflict_produces_allowed_resolution_options():
    intent = make_intent(
        hard_constraints=[
            HardConstraint(
                field="NEW_CARD_REQUIRED",
                constraint=HardConstraintValue.REQUIRE,
            )
        ],
        capabilities=[
            Capability(
                capability_id="NEW_CARD_ISSUANCE",
                state=CapabilityState.CANNOT,
            )
        ],
    )

    conflicts = IntentConflictValidator().validate(intent)

    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == IntentConflictType.HARD_CAPABILITY_CONTRADICTION
    assert conflicts[0].resolution_options == [
        "KEEP_REQUIRE_SET_CAN",
        "REMOVE_REQUIRE_KEEP_CANNOT",
    ]
    clarification = IntentConflictValidator.clarification(conflicts)
    assert set(clarification.allowed_resolutions) == set(conflicts[0].resolution_options)


def test_clarification_updates_intent_version():
    product = make_product("CLARIFICATION-PRODUCT")
    intent = make_intent(
        hard_constraints=[
            HardConstraint(
                field="FIRST_TRANSACTION_BENEFIT",
                constraint=HardConstraintValue.REQUIRE,
            )
        ],
        preferences=[
            Preference(
                field="FIRST_TRANSACTION_BENEFIT",
                preference=PreferenceValue.PREFER_ABSENT,
            )
        ],
    )
    service = ApplicationService([product], user_fact_stores={intent.user_id: base_store()})

    session = service.create_search_session(user_id=intent.user_id, intent=intent, as_of=AS_OF)
    conflict = service.get_intent_conflicts(session.search_session_id)[0]
    resolved = service.resolve_intent_conflict(
        session.search_session_id,
        conflict_id=conflict.conflict_id,
        resolution="KEEP_HARD_REMOVE_PREFERENCE",
    )

    assert session.status == SearchSessionStatus.CLARIFICATION_REQUIRED
    assert resolved.intent_version == 2
    assert resolved.status in {
        SearchSessionStatus.RANKING_READY,
        SearchSessionStatus.QUESTIONING,
    }
    current = service.get_search_intent(session.search_session_id)
    assert current.preferences == []
    assert current.hard_constraints[0].constraint == HardConstraintValue.REQUIRE


def test_non_conflicting_soft_preferences_do_not_trigger_question():
    intent = make_intent(
        preferences=[
            Preference(field="NEW_CARD_REQUIRED", preference=PreferenceValue.PREFER_ABSENT),
            Preference(field="FIRST_TRANSACTION_BENEFIT", preference=PreferenceValue.PREFER_PRESENT),
        ]
    )

    assert IntentConflictValidator().validate(intent) == []


def test_ended_product_is_removed():
    product = make_product("ENDED", sale_status=SaleStatus.ENDED)
    retained, decisions = CandidateRetriever().retrieve([product], make_intent(), as_of=AS_OF)

    assert retained == []
    assert decisions[0].reason_code == "SALE_STATUS_CLOSED"


def test_unknown_sale_status_not_removed_as_certain_failure():
    product = make_product("UNKNOWN-SALE", sale_status=SaleStatus.UNKNOWN)
    retained, decisions = CandidateRetriever().retrieve([product], make_intent(), as_of=AS_OF)

    assert [item.product_id for item in retained] == [product.product_id]
    assert decisions[0].retained is True


def test_hard_term_violation_is_removed():
    product = make_product("TERM-12M", term_value=12)
    intent = make_intent(
        numeric_preferences=[
            NumericPreference(
                field="TERM_MONTHS",
                value=Decimal("6"),
                direction=NumericPreferenceDirection.AT_MOST,
                strictness=PreferenceStrictness.HARD,
            )
        ]
    )

    retained, decisions = CandidateRetriever().retrieve([product], intent, as_of=AS_OF)

    assert retained == []
    assert decisions[0].reason_code == "HARD_TERM_VIOLATION"


def test_capability_cannot_does_not_remove_product():
    product = make_product(
        "CARD-PRODUCT",
        reward_pp="1.0",
        bonus_fact_type="NEW_CARD_ISSUANCE_POSSIBLE",
        rule_name="신규카드 우대",
    )
    intent = make_intent(
        capabilities=[
            Capability(
                capability_id="NEW_CARD_ISSUANCE",
                state=CapabilityState.CANNOT,
            )
        ]
    )

    retained, decisions = CandidateRetriever().retrieve([product], intent, as_of=AS_OF)

    assert [item.product_id for item in retained] == [product.product_id]
    assert decisions[0].reason_code == "RETAINED_NO_CERTAIN_HARD_FAILURE"


def test_unknown_eligibility_remains_candidate():
    product = make_product("UNKNOWN-ELIG")
    unknown_eligibility = FactComparisonRule(
        rule_id="UNKNOWN-ELIGIBILITY",
        name="기관 확인이 필요한 가입자격",
        purpose=RulePurpose.ELIGIBILITY,
        fact_type="INSTITUTION_ELIGIBILITY",
        operator=ComparisonOperator.EQ,
        expected=True,
    )
    product = product.model_copy(
        update={"eligibility_rule": unknown_eligibility},
        deep=True,
    )

    retained, decisions = CandidateRetriever().retrieve([product], make_intent(), as_of=AS_OF)

    assert [item.product_id for item in retained] == [product.product_id]
    assert decisions[0].retained is True


def test_hard_contribution_capacity_violation_is_removed():
    product = make_product("LOW-LIMIT", periodic_max="200000")
    intent = make_intent(
        numeric_preferences=[
            NumericPreference(
                field="MONTHLY_CONTRIBUTION",
                value=Decimal("300000"),
                direction=NumericPreferenceDirection.AT_LEAST,
                strictness=PreferenceStrictness.HARD,
                currency="KRW",
            )
        ]
    )

    retained, decisions = CandidateRetriever().retrieve([product], intent, as_of=AS_OF)

    assert retained == []
    assert decisions[0].reason_code == "HARD_CONTRIBUTION_VIOLATION"


def test_llm_clarification_cannot_change_resolution_options():
    from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
    from eligibility.search.intent import IntentConflictClarifier

    intent = make_intent(
        hard_constraints=[
            HardConstraint(
                field="FIRST_TRANSACTION_BENEFIT",
                constraint=HardConstraintValue.REQUIRE,
            )
        ],
        preferences=[
            Preference(
                field="FIRST_TRANSACTION_BENEFIT",
                preference=PreferenceValue.PREFER_ABSENT,
            )
        ],
    )
    conflicts = IntentConflictValidator().validate(intent)
    fallback = IntentConflictValidator.clarification(conflicts)
    gateway = LLMGateway(
        MockLLMAdapter(
            {
                LLMPurpose.INTENT_CLARIFICATION: {
                    "question": "첫거래 조건을 아무 방식으로나 정할까요?",
                    "conflict_ids": fallback.conflict_ids,
                    "allowed_resolutions": [*fallback.allowed_resolutions, "LLM_DECIDES"],
                }
            }
        )
    )

    clarification = IntentConflictClarifier(gateway).generate(conflicts)

    assert clarification.question == fallback.question
    assert clarification.allowed_resolutions == fallback.allowed_resolutions


def test_hard_capability_resolution_updates_original_capability_id():
    intent = make_intent(
        hard_constraints=[
            HardConstraint(
                field="NEW_CARD_REQUIRED",
                constraint=HardConstraintValue.REQUIRE,
            )
        ],
        capabilities=[
            Capability(
                capability_id="NEW_CARD_ISSUANCE",
                state=CapabilityState.CANNOT,
            )
        ],
    )
    validator = IntentConflictValidator()
    conflict = validator.validate(intent)[0]

    resolved = validator.apply_resolution(intent, conflict, "KEEP_REQUIRE_SET_CAN")

    assert resolved.hard_constraints[0].field == "NEW_CARD_REQUIRED"
    assert resolved.capabilities == [
        Capability(
            capability_id="NEW_CARD_ISSUANCE",
            state=CapabilityState.CAN,
        )
    ]
