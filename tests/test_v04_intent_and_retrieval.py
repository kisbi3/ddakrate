from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

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
    ContributionFrequency,
    HardConstraintValue,
    IntentConflictType,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
    SaleStatus,
    SearchSessionStatus,
    RulePurpose,
    RankingObjective,
    TermUnit,
)
from eligibility.schema.rule import FactComparisonRule
from eligibility.schema.search import ContributionPlan, IntentPatch
from eligibility.search.intent import (
    IntentConflictValidator,
    IntentParser,
)
from eligibility.search.retrieval import CandidateRetriever

from tests.v04_helpers import AS_OF, base_store, make_intent, make_product


def test_natural_language_intent_requires_an_llm_gateway():
    with pytest.raises(RuntimeError, match="requires an LLM gateway"):
        IntentParser().parse("1년 동안 월 30만원씩 적금하고 싶어요", user_id="NO-GATEWAY")


def test_institution_exclusion_patch_is_scoped_to_the_institution():
    retained, decisions = CandidateRetriever().retrieve(
        [
            make_product("SUHYUP", institution_id="BK_SH"),
            make_product("OTHER", institution_id="OTHER_BANK"),
        ],
        IntentParser.apply_patch(
            make_intent(),
            IntentPatch(upsert_excluded_institution_ids=["BK_SH"]),
        ),
        as_of=AS_OF,
    )

    assert [product.product_id for product in retained] == ["OTHER"]
    decision = next(item for item in decisions if item.product_id == "SUHYUP")
    assert decision.reason_code == "EXCLUDED_INSTITUTION"


def test_institution_exclusion_can_be_removed_without_erasing_other_intent():
    current = IntentParser.apply_patch(
        make_intent(),
        IntentPatch(upsert_excluded_institution_ids=["BK_SH", "TEST_BANK"]),
    )

    updated = IntentParser.apply_patch(
        current,
        IntentPatch(remove_excluded_institution_ids=["BK_SH"]),
    )

    assert updated.excluded_institution_ids == ["TEST_BANK"]


class _IntentPatchGateway:
    def __init__(self):
        self.calls = []

    def generate_structured(self, *_args, **_kwargs):
        self.calls.append((_args, _kwargs))
        prompt = _args[1]
        return SimpleNamespace(
            data=IntentPatch(
                upsert_product_types=["INSTALLMENT_SAVINGS"],
                requested_top_k_patch=3 if "상위 3개" in prompt else None,
            )
        )


class _FailingIntentGateway:
    def generate_structured(self, *_args, **_kwargs):
        raise RuntimeError("intent LLM unavailable")


class _LiquidAmountOnlyGateway:
    def generate_structured(self, *_args, **_kwargs):
        return SimpleNamespace(
            data=IntentPatch(
                upsert_product_types=["PARKING_ACCOUNT", "CMA"],
                upsert_numeric_preferences=[
                    NumericPreference(
                        field="amount",
                        value=Decimal("5000000"),
                        direction=NumericPreferenceDirection.AROUND,
                        strictness=PreferenceStrictness.SOFT,
                        currency="KRW",
                    )
                ],
            )
        )


def test_llm_intent_is_the_sole_owner_of_requested_top_k():
    gateway = _IntentPatchGateway()
    parser = IntentParser(gateway)

    default = parser.parse("금리가 높은 적금을 추천해줘", user_id="WEB-USER")
    explicit = parser.parse("상위 3개 적금을 추천해줘", user_id="WEB-USER")

    assert default.requested_top_k == 5
    assert explicit.requested_top_k == 3
    assert default.capabilities == []
    assert default.hard_constraints == []
    assert gateway.calls[0][0][2] is IntentPatch
    assert "구조화 추출기" in gateway.calls[0][1]["system_prompt"]
    assert "CURRENT_SEARCH_INTENT" in gateway.calls[0][0][1]
    assert "ProductSearchIntentDraft" not in gateway.calls[0][0][1]


def test_initial_llm_numeric_amount_is_bridged_into_cashflow_plan() -> None:
    intent = IntentParser(_LiquidAmountOnlyGateway()).parse(
        "500만원 정도 수시입출금",
        user_id="LIQUID-AMOUNT-USER",
    )

    assert intent.product_types == ["PARKING_ACCOUNT", "CMA"]
    assert intent.contribution_plan is not None
    assert intent.contribution_plan.desired_periodic_amount == Decimal("5000000")
    assert intent.contribution_plan.frequency == ContributionFrequency.FLEXIBLE


def test_configured_intent_llm_failure_is_not_hidden_by_deterministic_fallback():
    with pytest.raises(RuntimeError, match="intent LLM unavailable"):
        IntentParser(_FailingIntentGateway()).parse(
            "파킹통장만 금리순으로 보여줘",
            user_id="LLM-FAILURE-USER",
        )


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


def test_multi_sector_hard_scope_keeps_bank_and_securities_only():
    def with_sector(product_id: str, sector: str):
        product = make_product(product_id)
        return product.model_copy(
            update={
                "metadata": product.metadata.model_copy(
                    update={"institution_sector": sector},
                    deep=True,
                )
            },
            deep=True,
        )

    products = [
        with_sector("BANK-PRODUCT", "BANK"),
        with_sector("SAVINGS-PRODUCT", "SAVINGS_BANK"),
        with_sector("SECURITIES-PRODUCT", "SECURITIES"),
    ]
    intent = make_intent(
        hard_constraints=[
            HardConstraint(
                field="INSTITUTION_SECTOR",
                constraint=HardConstraintValue.REQUIRE,
                expected="BANK|SECURITIES",
            )
        ]
    )

    retained, _ = CandidateRetriever().retrieve(products, intent, as_of=AS_OF)

    assert {item.product_id for item in retained} == {
        "BANK-PRODUCT",
        "SECURITIES-PRODUCT",
    }


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


def test_maximum_one_year_keeps_six_month_product_and_removes_longer_product():
    six_month = make_product("TERM-6M", term_value=6)
    eighteen_month = make_product("TERM-18M", term_value=18)
    intent = make_intent(selected_term_value=None, selected_term_unit=None).model_copy(
        update={
            "contribution_plan": ContributionPlan(
                selected_term_value=1,
                selected_term_unit=TermUnit.YEAR,
                term_strictness="MAXIMUM",
            )
        },
        deep=True,
    )

    retained, decisions = CandidateRetriever().retrieve(
        [six_month, eighteen_month], intent, as_of=AS_OF
    )

    assert [product.product_id for product in retained] == ["TERM-6M"]
    assert decisions[0].reason_code == "RETAINED_NO_CERTAIN_HARD_FAILURE"
    assert decisions[1].reason_code == "HARD_TERM_VIOLATION"


def test_product_whose_minimum_term_exceeds_preferred_horizon_is_removed():
    product = make_product("TERM-60M", term_value=60)

    retained, decisions = CandidateRetriever().retrieve(
        [product],
        make_intent(selected_term_value=12),
        as_of=AS_OF,
    )

    assert retained == []
    assert decisions[0].reason_code == "REQUESTED_TERM_NOT_AVAILABLE"


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
