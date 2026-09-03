from __future__ import annotations

import pytest

from eligibility.application_service import ApplicationService
from eligibility.conversation import ConversationOrchestrator
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.product import ProductFeature

from tests.v04_helpers import AS_OF, USER_ID, base_store, make_intent, make_product


def _feature_product(product_id: str, *, lottery: bool):
    product = make_product(
        product_id,
        institution_id="WOORI" if lottery else "TEST_BANK",
        name="우리 두근두근 행운적금" if lottery else "일반 정기적금",
    )
    if not lottery:
        return product
    assert product.metadata is not None
    return product.model_copy(
        update={
            "metadata": product.metadata.model_copy(
                update={
                    "features": [
                        ProductFeature(feature_id="LOTTERY_BASED_BENEFIT")
                    ]
                },
                deep=True,
            )
        },
        deep=True,
    )


def _service(*plans):
    adapter = MockLLMAdapter(
        {LLMPurpose.CONVERSATION_ORCHESTRATION: list(plans)}
    )
    products = [
        _feature_product("LOTTERY", lottery=True),
        _feature_product("ORDINARY", lottery=False),
    ]
    service = ApplicationService(
        products,
        user_fact_stores={USER_ID: base_store()},
        conversation_orchestrator=ConversationOrchestrator(LLMGateway(adapter)),
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=2),
        as_of=AS_OF,
    )
    return service, session, adapter


def test_feature_exclusion_is_global_and_exact_decision_revert_restores_it():
    service, session, adapter = _service(
        {
            "actions": [
                {
                    "operation": "SET_PRODUCT_FEATURE_POLICY",
                    "feature_id": "LOTTERY_BASED_BENEFIT",
                    "feature_policy": "EXCLUDE",
                }
            ],
            "assistant_message": "추첨형 혜택 상품을 제외했어요.",
        },
        {
            "actions": [
                {
                    "operation": "REVERT_DECISIONS",
                    "decision_ids": ["DEC-00001"],
                }
            ],
            "assistant_message": "추첨형 제외 결정을 취소했어요.",
        },
    )

    first = service.handle_user_message(
        session.search_session_id,
        message="확률적인 거 싫어",
    )
    assert first.assistant_message == "추첨형 혜택 상품을 제외했어요."
    assert service.get_search_status(session.search_session_id).candidate_product_ids == [
        "ORDINARY"
    ]
    runtime = service._runtime(session.search_session_id)
    assert runtime.decision_ledger.active_entries[0].decision_id == "DEC-00001"

    second = service.handle_user_message(
        session.search_session_id,
        message="확률형 제외한 거 취소",
    )
    assert second.assistant_message == "추첨형 제외 결정을 취소했어요."
    assert set(service.get_search_status(session.search_session_id).candidate_product_ids) == {
        "LOTTERY",
        "ORDINARY",
    }
    assert len(adapter.call_history) == 2


def test_removed_question_presentation_field_is_rejected_by_strict_contract():
    service, session, _adapter = _service(
        {
            "actions": [
                {
                    "operation": "SET_PRODUCT_FEATURE_POLICY",
                    "feature_id": "LOTTERY_BASED_BENEFIT",
                    "feature_policy": "EXCLUDE",
                }
            ],
            "assistant_message": "성공했다고 말하면 안 되는 문장",
            "question_id_to_present": "PRESEARCH-NOT-ALLOWED",
        }
    )

    with pytest.raises(Exception, match="question_id_to_present"):
        service.handle_user_message(
            session.search_session_id,
            message="추첨형은 빼고 존재하지 않는 질문을 보여줘",
        )

    runtime = service._runtime(session.search_session_id)
    assert runtime.feature_policies == {}
    assert runtime.decision_ledger.entries == ()
    assert set(runtime.session.candidate_product_ids) == {"LOTTERY", "ORDINARY"}


def test_institution_sector_change_has_a_single_llm_origin():
    service, session, _adapter = _service(
        {
            "actions": [
                {
                    "operation": "UPDATE_SEARCH_INTENT",
                    "intent_patch": {
                        "upsert_hard_constraints": [
                            {
                                "field": "INSTITUTION_SECTOR",
                                "constraint": "EXCLUDE",
                                    "expected": "SAVINGS_BANK",
                            }
                        ]
                    },
                    "assistant_message": "저축은행을 제외했어요.",
                }
            ],
        }
    )

    service.handle_user_message(
        session.search_session_id,
        message="저축은행 귀찮아 죽겠네 안해",
    )

    constraints = service._runtime(session.search_session_id).intent.hard_constraints
    assert [(item.field, item.constraint.value, item.expected) for item in constraints] == [
        ("INSTITUTION_SECTOR", "EXCLUDE", "SAVINGS_BANK")
    ]
    assert service.get_search_intent(session.search_session_id).source_utterances.count(
        "저축은행 귀찮아 죽겠네 안해"
    ) == 1


def test_multiple_mutations_execute_the_deterministic_pipeline_once(monkeypatch):
    service, session, _adapter = _service(
        {
            "actions": [
                {
                    "operation": "SET_PRODUCT_FEATURE_POLICY",
                    "feature_id": "LOTTERY_BASED_BENEFIT",
                    "feature_policy": "PREFER_ABSENT",
                },
                {
                    "operation": "SET_PRODUCT_EXCLUSION",
                    "product_id": "LOTTERY",
                    "excluded": True,
                },
            ],
            "assistant_message": "두 조건을 함께 반영했어요.",
        }
    )
    calls = 0
    original = service._retrieve

    def counted(runtime):
        nonlocal calls
        calls += 1
        return original(runtime)

    monkeypatch.setattr(service, "_retrieve", counted)
    service.handle_user_message(
        session.search_session_id,
        message="추첨형은 덜 선호하고 LOTTERY 상품은 빼줘",
    )
    assert calls == 1


def test_named_product_evidence_is_projected_without_exposing_hidden_catalog():
    service, session, _adapter = _service(
        {
            "actions": [{"operation": "NO_OP"}],
            "assistant_message": "해당 상품을 기준으로 볼게요.",
        }
    )
    runtime = service._runtime(session.search_session_id)
    full_context = service._conversation_context(runtime)
    projected = ConversationOrchestrator._project_context(
        "우리 두근두근 행운적금 같은 건 싫어",
        full_context,
    )

    assert "_PRODUCT_EVIDENCE_INDEX" not in projected
    assert [
        item["product_id"]
        for item in projected["REFERENCED_PRODUCT_EVIDENCE"]
    ] == ["LOTTERY"]
    assert projected["REFERENCED_PRODUCT_EVIDENCE"][0][
        "preferential_application_mode"
    ] is None
    assert projected["REFERENCED_PRODUCT_EVIDENCE"][0]["typed_features"][0][
        "feature_id"
    ] == "LOTTERY_BASED_BENEFIT"
