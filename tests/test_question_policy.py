from __future__ import annotations

from eligibility.application_service import ApplicationService
from eligibility.question_policy import normalize_user_question_request
from eligibility.schema.conversation import FlexibleConversationTurnPlan
from eligibility.schema.enums import FactSemanticType
from eligibility.schema.enums import ResolutionStrategy
from eligibility.schema.evaluation import MissingFactRequest
from eligibility.search.questions import RankingAwareQuestionPlanner

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, base_store, make_intent, make_product


def _evaluate(products, intent):
    from eligibility.search.evaluation import MultiProductEvaluator

    return MultiProductEvaluator().evaluate(
        products,
        base_store(),
        intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )


def test_question_frontier_keeps_every_top3_challenger() -> None:
    products = [
        make_product(
            f"FRONTIER-{index:02d}",
            base_rate=str(2 + index / 100),
            reward_pp="0.5",
            bonus_fact_type=f"BONUS-{index:02d}",
        )
        for index in range(25)
    ]
    intent = make_intent(top_k=20)
    evaluations = _evaluate(products, intent)
    planner = RankingAwareQuestionPlanner()

    frontier = planner.frontier_product_ids(evaluations, intent)
    questions = planner.score_candidates(evaluations, intent)

    # Every product's optimistic upper can cross the current third-place
    # realizable cutoff, so the frontier must not silently truncate at ten.
    assert len(frontier) == len(products)
    assert "FRONTIER-00" in frontier
    assert {
        product_id
        for question in questions
        for product_id in question.affected_product_ids
    } <= set(frontier)


def test_product_that_cannot_enter_top_three_does_not_extend_questions() -> None:
    fixed = [
        make_product(f"TOP-{index}", base_rate=str(rate))
        for index, rate in enumerate((5, 4, 3), start=1)
    ]
    fourth = make_product(
        "FOURTH",
        base_rate="1",
        reward_pp="1",
        bonus_fact_type="FOURTH_BONUS",
    )
    intent = make_intent(top_k=5)

    assert RankingAwareQuestionPlanner().select_next(
        _evaluate([*fixed, fourth], intent),
        intent,
    ) is None


def test_challenger_that_can_enter_top_three_can_still_be_questioned() -> None:
    fixed = [
        make_product(f"TOP-{index}", base_rate=str(rate))
        for index, rate in enumerate((5, 4, 3), start=1)
    ]
    challenger = make_product(
        "CHALLENGER",
        base_rate="2.5",
        reward_pp="1",
        bonus_fact_type="CHALLENGER_BONUS",
    )
    intent = make_intent(top_k=5)

    question = RankingAwareQuestionPlanner().select_next(
        _evaluate([*fixed, challenger], intent),
        intent,
    )

    assert question is not None
    assert question.request is not None
    assert question.request.fact_type == "CHALLENGER_BONUS"


def test_routine_identification_question_is_not_used_for_ranking() -> None:
    product = make_product(
        "ROUTINE-ID",
        reward_pp="1",
        bonus_fact_type="IDENTIFICATION_DOCUMENT_POSSESSION",
        bonus_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
    )
    intent = make_intent(top_k=1)

    assert RankingAwareQuestionPlanner().select_next(
        _evaluate([product], intent), intent
    ) is None


def test_opaque_after_opening_payment_action_is_not_invented() -> None:
    product = make_product(
        "PAYMENT-ACTION",
        reward_pp="1",
        bonus_fact_type=(
            "PAYMENT_QUALIFYING_PROVIDER_PAYMENT_ACCOUNT_REGISTERED_AT_MONTH_END"
        ),
        bonus_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
    )
    intent = make_intent(top_k=1)
    service = ApplicationService([product], user_fact_stores={intent.user_id: base_store()})
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )

    # The fact identifier proves that this is a future action, but does not say
    # which provider/card/payment must be registered. The planner must retain
    # the uncertainty instead of making the customer guess from a generic
    # "결제계좌를 등록" sentence.
    assert service.get_next_question(session.search_session_id) is None
    candidate = service.evaluate_candidates(session.search_session_id)[product.product_id]
    assert candidate.realizable_rate == product.base_rate
    assert candidate.user_specific_conditional_upper_rate == product.base_rate + 1


def test_future_action_keeps_its_concrete_grounded_question() -> None:
    request = MissingFactRequest(
        fact_type="WILL_KAKAO_FREE_AUTO_TRANSFER_MONTH",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        requested_by_rule_id="RULE-1",
        question="전체 계약월수 1/2 이상 자동이체 성공을 목표로 관리할까요?",
        grounding_terms=["전체 계약월수 1/2 이상 자동이체 성공"],
    )

    normalized = normalize_user_question_request(request)

    assert normalized.question == request.question
    assert normalized.expected_semantic_type == FactSemanticType.FUTURE_INTENT


def test_opaque_generic_card_question_uses_family_template() -> None:
    request = MissingFactRequest(
        fact_type="CARD_MONTHLY_SPEND_LIMIT",
        action_id="ACTION-CARD_MONTHLY_SPEND_LIMIT",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        requested_by_rule_id="RULE-CARD",
        question="가입 후 우대에 필요한 조건을 수행하고 유지할 수 있나요?",
        grounding_terms=["우대조건"],
    )

    normalized = normalize_user_question_request(request)

    assert normalized.question == "카드 이용실적은 한 달에 최대 얼마까지 가능하신가요?"
    assert "필요한 조건을 수행" not in normalized.question


def test_opaque_payment_account_question_is_not_invented() -> None:
    request = MissingFactRequest(
        fact_type="PAYMENT_ACCOUNT_REGISTRATION_POSSIBLE",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        requested_by_rule_id="RULE-PAYMENT-ACCOUNT",
        question="가입 후 우대에 필요한 조건을 수행하고 유지할 수 있나요?",
        grounding_terms=["우대조건"],
    )

    normalized = normalize_user_question_request(request)

    assert normalized.expected_semantic_type == FactSemanticType.FUTURE_INTENT
    assert normalized.question is None


def test_source_jargon_questions_are_rewritten_as_user_decisions() -> None:
    cases = [
        (
            "SUBSCRIPTION_CHANNEL_DIGITAL",
            "인터넷/모바일뱅킹에서 가입시 비대면우대이자율(연0.05%p) 추가 적용 조건에 해당하는지 알려주세요.",
            "인터넷뱅킹이나 모바일뱅킹으로 가입할 예정인가요?",
        ),
        (
            "QUALIFYING_HOME_LOAN_HELD_THROUGH_SAVINGS_TERMINATION",
            "당행 주택담보대출 또는 전세자금대출 이용 시 조건에 해당하는지 알려주세요.",
            "해당 은행의 주택담보대출이나 전세자금대출을 이미 이용 중이거나, 적금 가입 기간 중 이용해 만기까지 유지할 계획이 있나요?",
        ),
        (
            "KB_CROSS_TRANSACTION_REQUIREMENT_POSSIBLE",
            "교차거래 우대이율 : 연 조건에 해당하는지 알려주세요.",
            "가입 3개월이 지난 달에 급여이체 실적을 만들거나, KB국민카드를 30만원 이상 사용할 수 있나요?",
        ),
    ]
    for fact_type, source, expected in cases:
        normalized = normalize_user_question_request(
            MissingFactRequest(
                fact_type=fact_type,
                resolution_strategy=ResolutionStrategy.ASK_USER,
                requested_by_rule_id=f"RULE-{fact_type}",
                question=source,
                grounding_terms=[source],
            )
        )
        assert normalized.question == expected


def test_unknown_opaque_future_action_remains_unasked_for_review() -> None:
    product = make_product(
        "OPAQUE-ACTION",
        reward_pp="1",
        bonus_fact_type="LIVING_EXPENSE_UNKNOWN_ACTION",
    )
    rule = product.preferential_rules[0].rule
    product = product.model_copy(
        update={
            "preferential_rules": [
                product.preferential_rules[0].model_copy(
                    update={
                        "rule": rule.model_copy(
                            update={
                                "missing_fact": rule.missing_fact.model_copy(
                                    update={
                                        "question": "가입 후 우대에 필요한 조건을 수행하고 유지할 수 있나요?",
                                        "grounding_terms": ["우대조건"],
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

    assert RankingAwareQuestionPlanner().select_next(
        _evaluate([product], make_intent(top_k=1)),
        make_intent(top_k=1),
    ) is None


def test_ungrounded_external_future_outcome_does_not_become_generic_question() -> None:
    product = make_product(
        "UNGROUNDED-FUTURE",
        reward_pp="1",
        bonus_fact_type="TEST_MARKETING_CONSENT",
        bonus_question_strategy=ResolutionStrategy.QUERY_INSTITUTION,
    )
    # Reproduce normalized catalog rows whose institution outcome has no
    # customer-facing action/question metadata.
    rule = product.preferential_rules[0].rule
    product = product.model_copy(
        update={
            "preferential_rules": [
                product.preferential_rules[0].model_copy(
                    update={
                        "rule": rule.model_copy(
                            update={
                                "missing_fact": rule.missing_fact.model_copy(
                                    update={
                                        "question": None,
                                        "action_id": None,
                                        "grounding_terms": [],
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

    assert RankingAwareQuestionPlanner().select_next(
        _evaluate([product], make_intent(top_k=1)),
        make_intent(top_k=1),
    ) is None


def test_first_customer_history_question_is_not_confused_with_account_holding() -> None:
    product = make_product(
        "FIRST-CUSTOMER",
        institution_id="SBI",
        reward_pp="1",
        bonus_fact_type="RELATIONSHIP_SBI_FIRST_DEPOSIT_OR_SAVINGS_CUSTOMER_AT_OPENING",
        bonus_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
        rule_name="신규고객",
    )
    intent = make_intent(top_k=1)
    service = ApplicationService([product], user_fact_stores={intent.user_id: base_store()})
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )

    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert "현재 계좌 보유 여부와 별개로" in question.question
    assert "입출금·예금·적금" in question.question
