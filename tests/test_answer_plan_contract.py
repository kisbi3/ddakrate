from __future__ import annotations

import pytest
from pydantic import ValidationError

from eligibility.application_service import ApplicationService
from eligibility.schema.conversation import AnswerPlan
from eligibility.schema.enums import UserConditionStatus

from tests.v04_helpers import AS_OF, USER_ID, base_store, make_intent, make_product


class _AnswerPlanInterpreter:
    def __init__(self, factory):
        self.factory = factory

    def interpret_answer_plan(self, message, *, context):
        return self.factory(message, context)


def _named_institution(product, name: str):
    assert product.metadata is not None
    return product.model_copy(
        update={
            "metadata": product.metadata.model_copy(
                update={"institution_name": name},
                deep=True,
            )
        },
        deep=True,
    )


def _service(factory):
    bonus = _named_institution(
        make_product(
            "SH-BONUS",
            institution_id="SH_BANK",
            name="수협 우대적금",
            base_rate="2",
            reward_pp="2",
            bonus_fact_type="AUTO_TRANSFER",
        ),
        "수협은행",
    )
    fixed = _named_institution(
        make_product(
            "OTHER-FIXED",
            institution_id="OTHER_BANK",
            name="다른은행 적금",
            base_rate="3",
        ),
        "다른은행",
    )
    service = ApplicationService(
        [bonus, fixed],
        user_fact_stores={USER_ID: base_store()},
        conversation_orchestrator=_AnswerPlanInterpreter(factory),
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=2),
        as_of=AS_OF,
    )
    assert service.get_next_question(session.search_session_id) is not None
    return service, session


def test_answer_plan_schema_rejects_verified_and_duplicate_updates():
    with pytest.raises(ValidationError, match="cannot create VERIFIED"):
        AnswerPlan.model_validate(
            {
                "active_question_answer": {
                    "question_id": "Q-1",
                    "state": "VERIFIED",
                    "value": True,
                }
            }
        )
    with pytest.raises(ValidationError, match="Duplicate AnswerPlan update"):
        AnswerPlan.model_validate(
            {
                "additional_updates": [
                    {
                        "kind": "INSTITUTION_EXCLUSION",
                        "institution_id": "BANK",
                        "excluded": True,
                    },
                    {
                        "kind": "INSTITUTION_EXCLUSION",
                        "institution_id": "BANK",
                        "excluded": False,
                    },
                ]
            }
        )


def test_answer_and_institution_exclusion_commit_with_one_pipeline(monkeypatch):
    def plan(_message, context):
        return {
            "active_question_answer": {
                "question_id": context["ACTIVE_QUESTION"]["question_id"],
                "state": "DECLARED_FEASIBLE",
                "value": True,
            },
            "additional_updates": [
                {
                    "kind": "INSTITUTION_EXCLUSION",
                    "institution_id": "SH_BANK",
                    "excluded": True,
                }
            ],
        }

    service, session = _service(plan)
    calls = 0
    original = service._retrieve

    def counted(runtime):
        nonlocal calls
        calls += 1
        return original(runtime)

    monkeypatch.setattr(service, "_retrieve", counted)
    service.handle_user_message(
        session.search_session_id,
        message="수협은행은 싫고 자동이체는 할 수 있어요",
    )

    runtime = service._runtime(session.search_session_id)
    assert calls == 1
    assert runtime.intent.excluded_institution_ids == ["SH_BANK"]
    assert runtime.condition_states[
        "ACTION-AUTO_TRANSFER"
    ].status == UserConditionStatus.DECLARED_FEASIBLE


def test_false_value_mislabeled_feasible_is_normalized_to_declined():
    def plan(_message, context):
        return {
            "active_question_answer": {
                "question_id": context["ACTIVE_QUESTION"]["question_id"],
                "state": "DECLARED_FEASIBLE",
                "value": False,
            }
        }

    service, session = _service(plan)
    service.handle_user_message(
        session.search_session_id,
        message="그 조건에는 해당하지 않아요",
    )

    runtime = service._runtime(session.search_session_id)
    assert runtime.condition_states[
        "ACTION-AUTO_TRANSFER"
    ].status == UserConditionStatus.DECLINED
    assert runtime.evaluations["SH-BONUS"].realizable_rate == 2


def test_hallucinated_variable_is_rejected_without_partial_state_change():
    def plan(_message, context):
        return {
            "active_question_answer": {
                "question_id": context["ACTIVE_QUESTION"]["question_id"],
                "state": "DECLARED_FEASIBLE",
                "value": True,
            },
            "additional_updates": [
                {
                    "kind": "USER_CONDITION",
                    "variable_id": "HALLUCINATED-CONDITION",
                    "state": "DECLINED",
                }
            ],
        }

    service, session = _service(plan)
    runtime = service._runtime(session.search_session_id)
    before = service._snapshot_runtime(runtime)

    with pytest.raises(ValueError, match="Unknown or non-pending variable_id"):
        service.handle_user_message(
            session.search_session_id,
            message="할 수 있어요",
        )

    assert runtime.fact_store == before["fact_store"]
    assert runtime.condition_states == before.get("condition_states", {})
    assert runtime.active_question == before["active_question"]


def test_unmentioned_institution_is_rejected():
    def plan(_message, context):
        return {
            "active_question_answer": {
                "question_id": context["ACTIVE_QUESTION"]["question_id"],
                "state": "ACKNOWLEDGED_UNKNOWN",
            },
            "additional_updates": [
                {
                    "kind": "INSTITUTION_EXCLUSION",
                    "institution_id": "SH_BANK",
                    "excluded": True,
                }
            ],
        }

    service, session = _service(plan)
    with pytest.raises(ValueError, match="not grounded"):
        service.handle_user_message(
            session.search_session_id,
            message="그 조건은 잘 모르겠어요",
        )

    runtime = service._runtime(session.search_session_id)
    assert runtime.intent.excluded_institution_ids == []
    assert runtime.condition_states == {}


def test_one_card_amount_answers_all_bound_thresholds():
    products = []
    for product_id, threshold in (("CARD-20", 20), ("CARD-30", 30), ("CARD-50", 50)):
        product = make_product(
            product_id,
            base_rate="2",
            reward_pp="1",
            bonus_fact_type=f"CARD_SPEND_{threshold}",
        )
        preferential = product.preferential_rules[0]
        rule = preferential.rule
        product = product.model_copy(
            update={
                "preferential_rules": [
                    preferential.model_copy(
                        update={
                            "rule": rule.model_copy(
                                update={
                                    "missing_fact": rule.missing_fact.model_copy(
                                        update={
                                            "action_id": "ACTION-CARD_MONTHLY_SPEND_LIMIT",
                                            "question": f"월 카드 실적 {threshold}만원 이상 가능하신가요?",
                                        },
                                        deep=True,
                                    )
                                },
                                deep=True,
                            )
                        },
                        deep=True,
                    )
                ]
            },
            deep=True,
        )
        products.append(product)

    def plan(_message, context):
        return {
            "active_question_answer": {
                "question_id": context["ACTIVE_QUESTION"]["question_id"],
                "state": "DECLARED_FEASIBLE",
                "value": {
                    "amount": 300000,
                    "currency": "KRW",
                    "period": "MONTH",
                },
            }
        }

    service = ApplicationService(
        products,
        user_fact_stores={USER_ID: base_store()},
        conversation_orchestrator=_AnswerPlanInterpreter(plan),
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=3),
        as_of=AS_OF,
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.answer_mode == "OPTIONS"
    assert question.question_spec is not None
    assert question.question_spec.options == [0, 100000, 300000, 500000, 1000000]

    service.handle_user_message(
        session.search_session_id,
        message="한 달에 30만원까지 가능해요",
    )
    runtime = service._runtime(session.search_session_id)

    assert runtime.evaluations["CARD-20"].realizable_rate == 3
    assert runtime.evaluations["CARD-30"].realizable_rate == 3
    assert runtime.evaluations["CARD-50"].realizable_rate == 2
    state = runtime.condition_states["ACTION-CARD_MONTHLY_SPEND_LIMIT"]
    assert state.value == 300000
    assert state.interpreter_version == "ANSWER_PLAN_V1"


def test_boolean_card_willingness_is_not_treated_as_a_threshold_answer():
    def plan(_message, context):
        return {
            "active_question_answer": {
                "question_id": context["ACTIVE_QUESTION"]["question_id"],
                "state": "DECLARED_FEASIBLE",
                "value": True,
            }
        }

    product = make_product(
        "CARD-VAGUE",
        base_rate="2",
        reward_pp="1",
        bonus_fact_type="CARD_MONTHLY_SPEND_LIMIT",
    )
    service = ApplicationService(
        [product],
        user_fact_stores={USER_ID: base_store()},
        conversation_orchestrator=_AnswerPlanInterpreter(plan),
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=1),
        as_of=AS_OF,
    )

    service.handle_user_message(
        session.search_session_id,
        message="카드는 가능해요",
    )
    runtime = service._runtime(session.search_session_id)
    assert runtime.condition_states[
        "ACTION-CARD_MONTHLY_SPEND_LIMIT"
    ].status == "WILLING_UNSPECIFIED"
    assert runtime.evaluations["CARD-VAGUE"].realizable_rate == 2
