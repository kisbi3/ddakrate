from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eligibility.application import (
    GeneratedQuestion,
    QuestionGenerator,
    UserAnswerMapper,
)
from eligibility.application_service import ApplicationService
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.future_goals import (
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.fixtures.kakao_26_week import (
    kakao_26_week_context,
    kakao_26_week_product,
    kakao_user_from_statuses,
)
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.schema.application_input import HardConstraint, Preference
from eligibility.schema.enums import (
    EvaluationStatus,
    HardConstraintValue,
    PreferenceValue,
    ScheduledOccurrenceStatus,
)
from eligibility.search.intent import IntentConflictValidator

from tests.v04_helpers import (
    AS_OF,
    SUBSCRIPTION_DATE,
    USER_ID,
    base_store,
    make_intent,
    make_product,
)


OUT = ROOT / "examples" / "v0.4"


def _salary_request():
    result = FinancialEligibilityEngine().evaluate_product(
        salary_envelope_6m_product(),
        salary_envelope_user(),
        salary_envelope_context(),
    )
    return result.missing_facts[0]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {}

    # 1. Question grounding rejects rate/unit rebinding.
    request = _salary_request()
    malicious = GeneratedQuestion(
        question="월급봉투 조건을 6개월 달성하면 +6.0%p 우대를 받을까요?",
        fact_type=request.fact_type,
        rule_id=request.requested_by_rule_id,
        action_id=request.action_id,
        reward_id=request.reward_id,
    )
    grounded = QuestionGenerator(
        LLMGateway(MockLLMAdapter({LLMPurpose.QUESTION_GENERATION: malicious}))
    ).generate(request)
    assert grounded == request.question
    assert "+6.0%p" not in grounded
    results["question_grounding"] = {
        "malicious_output_rejected": True,
        "fallback": grounded,
    }

    # 2. Kakao late 7-week streak remains valid; a manual fill is irrelevant.
    statuses = [
        ScheduledOccurrenceStatus.SUCCESS,
        ScheduledOccurrenceStatus.FAILED,
        *([ScheduledOccurrenceStatus.SUCCESS] * 7),
        *([ScheduledOccurrenceStatus.FAILED] * 17),
    ]
    kakao = FinancialEligibilityEngine().evaluate_product(
        kakao_26_week_product(),
        kakao_user_from_statuses(statuses),
        kakao_26_week_context(),
    )
    seven = next(
        item
        for item in kakao.preferential_rule_results
        if item.rule_id == "K26_RATE_7_CONSECUTIVE"
    )
    twenty_six = next(
        item
        for item in kakao.preferential_rule_results
        if item.rule_id == "K26_RATE_26_CONSECUTIVE"
    )
    assert seven.status == EvaluationStatus.SATISFIED
    assert twenty_six.status == EvaluationStatus.UNSATISFIABLE
    results["kakao_late_streak"] = {
        "week_3_to_9_qualifies": True,
        "seven_week_status": seven.status.value,
        "twenty_six_week_status": twenty_six.status.value,
    }

    # 3. Deterministic cross-category conflict.
    conflict_intent = make_intent(
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
    conflicts = IntentConflictValidator().validate(conflict_intent)
    assert len(conflicts) == 1
    results["intent_conflict"] = {
        "conflict_type": conflicts[0].conflict_type.value,
        "resolution_options": conflicts[0].resolution_options,
    }

    # 4. Synthetic 6-candidate Top 5 and the 5th/6th optimistic boundary.
    top_products = [
        make_product(f"ADV-TOP-{index}", base_rate=str(rate))
        for index, rate in enumerate((5.0, 4.8, 4.6, 4.4, 4.2), start=1)
    ]
    challenger = make_product(
        "ADV-TOP-6",
        base_rate="3.0",
        reward_pp="2.0",
        bonus_fact_type="ADV_CHALLENGER_FACT",
    )
    service = ApplicationService(
        [*top_products, challenger],
        user_fact_stores={USER_ID: base_store()},
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=5),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert "ADV-TOP-6" in question.affected_product_ids
    before = service.get_top_recommendations(session.search_session_id)
    assert len(before.top_products) == 5
    results["top5_frontier"] = {
        "top_k": len(before.top_products),
        "question_fact_type": question.request.fact_type,
        "affected_products": question.affected_product_ids,
    }

    # 5. Shared Fact updates multiple candidates, then answer revision False→True.
    shared_products = [
        make_product(
            "ADV-SHARED-A",
            base_rate="2.0",
            reward_pp="1.0",
            bonus_fact_type="ADV_SHARED_CAPABILITY",
        ),
        make_product(
            "ADV-SHARED-B",
            base_rate="2.5",
            reward_pp="0.5",
            bonus_fact_type="ADV_SHARED_CAPABILITY",
        ),
    ]
    shared_service = ApplicationService(
        shared_products,
        user_fact_stores={USER_ID: base_store()},
    )
    shared_session = shared_service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=2),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    shared_question = shared_service.get_next_question(shared_session.search_session_id)
    assert shared_question is not None
    reference = UserAnswerMapper.request_reference(shared_question.request)
    shared_service.submit_user_answer(
        shared_session.search_session_id,
        question_id=shared_question.question_id,
        answer=False,
        answered_at=datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
    )
    shared_service.revise_user_answer(
        shared_session.search_session_id,
        request_reference=reference,
        new_value=True,
        answered_at=datetime(2026, 8, 19, 13, tzinfo=timezone.utc),
    )
    shared_eval = shared_service.evaluate_candidates(shared_session.search_session_id)
    assert shared_eval["ADV-SHARED-A"].realizable_rate == Decimal("3.0")
    assert shared_eval["ADV-SHARED-B"].realizable_rate == Decimal("3.0")
    history = shared_service.get_answer_history(shared_session.search_session_id)
    assert len(history) == 2
    results["shared_fact_revision"] = {
        "affected_products": shared_question.affected_product_ids,
        "rates_after_revision": {
            key: str(value.realizable_rate) for key, value in shared_eval.items()
        },
        "answer_statuses": [item.status.value for item in history],
    }

    # 6. Contribution-plan update changes final ranking.
    contribution_products = [
        make_product(
            "ADV-LARGE-CAPACITY",
            base_rate="3.0",
            periodic_max="300000",
        ),
        make_product(
            "ADV-HIGH-RATE-SMALL",
            base_rate="4.0",
            periodic_max="100000",
        ),
    ]
    contribution_service = ApplicationService(
        contribution_products,
        user_fact_stores={USER_ID: base_store()},
    )
    contribution_session = contribution_service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(
            desired_amount="300000",
            maximum_amount="500000",
            top_k=2,
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    initial_top = contribution_service.get_top_recommendations(
        contribution_session.search_session_id
    ).top_products[0].product_id
    contribution_service.update_search_intent(
        contribution_session.search_session_id,
        utterance="월 10만원 기준으로 다시 보여줘.",
    )
    updated_result = contribution_service.get_top_recommendations(
        contribution_session.search_session_id
    )
    updated_top = updated_result.top_products[0].product_id
    assert initial_top == "ADV-LARGE-CAPACITY"
    assert updated_top == "ADV-HIGH-RATE-SMALL"
    results["contribution_rerank"] = {
        "before": initial_top,
        "after": updated_top,
        "planned_contribution": updated_result.top_products[0].planned_contribution_summary,
    }

    # 7. UNKNOWN upper never enters final realizable ranking.
    upper_products = [
        make_product("ADV-REALIZABLE", base_rate="5.0"),
        make_product(
            "ADV-UNKNOWN-UPPER",
            base_rate="2.0",
            reward_pp="10.0",
            bonus_fact_type="ADV_UNKNOWN_BIG_REWARD",
        ),
    ]
    upper_service = ApplicationService(
        upper_products,
        user_fact_stores={USER_ID: base_store()},
    )
    upper_session = upper_service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=2),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    upper_recommendation = upper_service.get_top_recommendations(
        upper_session.search_session_id
    )
    upper_eval = upper_service.evaluate_candidates(upper_session.search_session_id)
    assert upper_recommendation.top_products[0].product_id == "ADV-REALIZABLE"
    results["realizable_only_ranking"] = {
        "rank_1": upper_recommendation.top_products[0].product_id,
        "unknown_candidate_realizable_rate": str(
            upper_eval["ADV-UNKNOWN-UPPER"].realizable_rate
        ),
        "unknown_candidate_upper_rate": str(
            upper_eval["ADV-UNKNOWN-UPPER"].user_specific_conditional_upper_rate
        ),
    }

    # 8. List, detail, cap and audit chain are materialized.
    list_item = updated_result.top_products[0]
    detail = contribution_service.get_product_recommendation_detail(
        contribution_session.search_session_id,
        list_item.product_id,
        include_explanation=True,
    )
    trace = contribution_service.get_evaluation_trace(
        contribution_session.search_session_id
    )
    assert list_item.estimated_after_tax_interest is not None
    assert detail.rate_cap_adjustment.cap_reduction_pp >= 0
    assert any(event.event_type.value == "RECOMMENDATION_CREATED" for event in trace)
    assert any(event.event_type.value == "EXPLANATION_GENERATED" for event in trace)
    results["list_detail_audit"] = {
        "list": list_item.model_dump(mode="json"),
        "detail_summary": {
            "product_id": detail.product_id,
            "rank": detail.rank,
            "confirmed_rate": str(detail.confirmed_rate),
            "realizable_rate": str(detail.realizable_rate),
            "advertised_max_rate": str(detail.advertised_max_rate),
            "rate_breakdown_count": len(detail.rate_breakdown),
            "cap_adjustment": detail.rate_cap_adjustment.model_dump(mode="json"),
        },
        "audit_event_count": len(trace),
        "audit_event_types": sorted({event.event_type.value for event in trace}),
    }

    output = OUT / "adversarial-results-v0.4.json"
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nWROTE {output}")


if __name__ == "__main__":
    main()
