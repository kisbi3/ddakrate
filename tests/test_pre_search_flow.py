from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from eligibility.application_service import ApplicationService
from eligibility.adapters.rest import RestApplicationAdapter
from eligibility.catalog import load_default_product_catalog
from eligibility.schema.application_input import HardConstraint
from eligibility.schema.conversation import (
    ConversationAction,
    ConversationOperation,
    ConversationPlan,
    PreSearchAnswerPlan,
)
from eligibility.schema.enums import (
    ContributionFrequency,
    EvaluationStatus,
    HardConstraintValue,
    PreSearchAnswerStatus,
    RankingObjective,
    TermUnit,
)
from eligibility.schema.search import ContributionPlan, ContributionPlanPatch, ProductSearchIntent
from eligibility.schema.search import IntentPatch
from eligibility.search.intent import IntentParser
from eligibility.search.pre_search import (
    APPLICATION_CAPACITY,
    BIRTH_DATE,
    COMMON_BENEFIT_WILLINGNESS,
    CONTRIBUTION_AND_TERM,
    DeterministicPreSearchQuestionPlanner,
    INSTITUTION_SCOPE,
    INSTITUTION_PRODUCT_HOLDING_HISTORY,
    PRODUCT_TYPE,
    PROTECTION_AND_CMA_SCOPE,
    QUESTION_ORDER,
    SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY,
    YOUTH_POLICY_ACCOUNT_HOLDING,
    pre_search_answer_examples,
)


class _PreSearchInterpreter:
    def __init__(self, answers: dict[str, PreSearchAnswerPlan]) -> None:
        self.answers = answers
        self.keys_seen: list[str] = []

    def interpret_pre_search(self, message, *, context):
        key = context["ACTIVE_QUESTION"]["pre_search_key"]
        self.keys_seen.append(key)
        return self.answers[key]


class _PreSearchThenFilterInterpreter:
    def interpret_pre_search(self, message, *, context):
        return PreSearchAnswerPlan(resolution="NOT_AN_ANSWER")

    def interpret(self, message, *, context):
        return ConversationPlan(
            actions=[
                ConversationAction(
                    operation=ConversationOperation.UPDATE_SEARCH_INTENT,
                    intent_patch=IntentPatch(
                        upsert_hard_constraints=[
                            HardConstraint(
                                field="institution_type",
                                constraint=HardConstraintValue.EXCLUDE,
                                expected="SAVINGS_BANK",
                            )
                        ]
                    ),
                )
            ]
        )


class _InitialIntentGateway:
    """Test double for the sole natural-language intent boundary."""

    def generate_structured(self, _purpose, prompt, _schema, **_kwargs):
        product_types = ["TIME_DEPOSIT"] if "정기예금" in prompt else ["INSTALLMENT_SAVINGS"]
        application_capacity = (
            "CORPORATION"
            if "법인 명의" in prompt
            else "INDIVIDUAL" if "개인 명의" in prompt else None
        )
        amount = 10_000_000 if "1천만원" in prompt else 300_000 if "30만원" in prompt else None
        term_value = 6 if "6개월" in prompt else 1 if "1년" in prompt else None
        contribution = (
            ContributionPlanPatch(
                desired_periodic_amount=amount,
                preferred_start_amount=(amount if product_types == ["TIME_DEPOSIT"] else None),
                frequency=(
                    ContributionFrequency.FLEXIBLE
                    if product_types == ["TIME_DEPOSIT"]
                    else ContributionFrequency.MONTHLY
                ),
                selected_term_value=term_value,
                selected_term_unit=(TermUnit.MONTH if term_value == 6 else TermUnit.YEAR),
            )
            if amount is not None and term_value is not None
            else None
        )
        return SimpleNamespace(
            data=IntentPatch(
                upsert_product_types=product_types,
                contribution_plan_patch=contribution,
                application_capacity_patch=application_capacity,
            )
        )


def _service(interpreter: _PreSearchInterpreter) -> ApplicationService:
    return ApplicationService(
        load_default_product_catalog(),
        intent_parser=IntentParser(_InitialIntentGateway()),
        conversation_orchestrator=interpreter,
        pre_search_enabled=True,
    )


def test_realizable_rate_is_the_schema_default() -> None:
    intent = ProductSearchIntent(search_intent_id="I", user_id="U")
    assert intent.ranking_objective == RankingObjective.MAX_REALIZABLE_RATE


def test_contribution_plan_does_not_implicitly_change_rate_ranking() -> None:
    intent = ProductSearchIntent(
        search_intent_id="I-RATE-ORDER",
        user_id="U-RATE-ORDER",
        ranking_objective=RankingObjective.MAX_REALIZABLE_RATE,
    )

    updated = IntentParser.apply_patch(
        intent,
        IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                desired_periodic_amount=300_000,
                frequency=ContributionFrequency.MONTHLY,
                selected_term_value=6,
                selected_term_unit=TermUnit.MONTH,
            )
        ),
    )

    assert updated.ranking_objective == RankingObjective.MAX_REALIZABLE_RATE
    assert updated.contribution_plan is not None
    assert updated.contribution_plan.desired_periodic_amount == 300_000


def test_explicit_interest_ranking_patch_still_overrides_rate_ranking() -> None:
    intent = ProductSearchIntent(
        search_intent_id="I-INTEREST-ORDER",
        user_id="U-INTEREST-ORDER",
        ranking_objective=RankingObjective.MAX_REALIZABLE_RATE,
    )

    updated = IntentParser.apply_patch(
        intent,
        IntentPatch(
            contribution_plan_patch=ContributionPlanPatch(
                desired_periodic_amount=300_000,
                frequency=ContributionFrequency.MONTHLY,
            ),
            ranking_objective_patch=RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST,
        ),
    )

    assert updated.ranking_objective == RankingObjective.MAX_ESTIMATED_PRE_TAX_INTEREST


def test_savings_contribution_question_follows_product_type() -> None:
    intent = ProductSearchIntent(
        search_intent_id="I-SAVINGS-ORDER",
        user_id="U-SAVINGS-ORDER",
        product_types=["INSTALLMENT_SAVINGS"],
    )
    profile = DeterministicPreSearchQuestionPlanner.initialize_profile(intent)

    question = DeterministicPreSearchQuestionPlanner().select_next(
        profile,
        intent,
        [],
    )

    assert question is not None
    assert question.pre_search_key == CONTRIBUTION_AND_TERM


def test_liquidity_is_defined_by_selected_product_family_not_a_pre_search_question() -> None:
    assert "LIQUIDITY_NEED" not in QUESTION_ORDER
    profile = DeterministicPreSearchQuestionPlanner.initialize_profile(
        ProductSearchIntent(search_intent_id="I-LIQUIDITY", user_id="U")
    )
    assert "LIQUIDITY_NEED" not in profile

def test_initial_utterance_skips_product_type_and_combined_amount_term_question() -> None:
    service = _service(_PreSearchInterpreter({}))
    session = service.create_search_session(
        user_id="U",
        utterance="월 30만원씩 1년 적금을 찾고 있어요",
    )

    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.question_kind == "PRE_SEARCH_PROFILE"
    assert question.pre_search_key == APPLICATION_CAPACITY
    assert session.answered_question_ids == [
        "PRESEARCH-PRODUCT_TYPE",
        "PRESEARCH-CONTRIBUTION_AND_TERM",
    ]


def test_application_capacity_question_combines_foreigner_notice() -> None:
    question = DeterministicPreSearchQuestionPlanner._question(
        APPLICATION_CAPACITY,
        ProductSearchIntent(search_intent_id="I-CAPACITY", user_id="U"),
    )

    assert not question.startswith("이번에는")
    assert "외국인이면 말해주세요" in question
    assert "대한민국 국민으로 적용" not in question
    assert pre_search_answer_examples(APPLICATION_CAPACITY)[:2] == [
        "개인 명의로 가입할 상품을 찾고 있어요.",
        "개인사업자 명의로 가입하려고 해요.",
    ]


def test_initial_product_type_examples_do_not_assume_an_amount() -> None:
    examples = pre_search_answer_examples(PRODUCT_TYPE)

    assert examples == [
        "정기적으로 돈을 모으고 싶어요.",
        "목돈을 일정 기간 맡기고 싶어요.",
        "필요할 때 넣고 뺄 수 있는 계좌를 찾고 있어요.",
    ]


def test_partial_liquidity_contribution_examples_ask_only_for_the_period() -> None:
    examples = pre_search_answer_examples(
        CONTRIBUTION_AND_TERM,
        ["PARKING_ACCOUNT", "CMA"],
        contribution_plan=ContributionPlan(
            desired_periodic_amount=1_000_000,
        ),
    )

    assert examples == [
        "1년 기준으로 비교해 주세요.",
        "6개월 정도로 비교해 주세요.",
        "기간은 아직 정하지 않았어요.",
    ]
    assert not any("원" in example for example in examples)


def test_known_initial_amount_makes_combined_question_ask_only_for_term() -> None:
    intent = ProductSearchIntent(
        search_intent_id="I-PARTIAL-LIQUID",
        user_id="U",
        product_types=["PARKING_ACCOUNT", "CMA"],
        contribution_plan=ContributionPlan(
            desired_periodic_amount=5_000_000,
            frequency=ContributionFrequency.FLEXIBLE,
        ),
    )

    question = DeterministicPreSearchQuestionPlanner._question(
        CONTRIBUTION_AND_TERM,
        intent,
    )

    assert "말씀하신 잔액" in question
    assert "기간" in question
    assert "어느 정도 잔액" not in question


def test_term_any_patches_the_existing_canonical_contribution_plan() -> None:
    intent = ProductSearchIntent(
        search_intent_id="I-TERM-ANY",
        user_id="U",
        product_types=["PARKING_ACCOUNT", "CMA"],
        contribution_plan=ContributionPlan(
            desired_periodic_amount=2_000_000,
            frequency=ContributionFrequency.FLEXIBLE,
        ),
    )
    patch, _ = ApplicationService._pre_search_patch(
        CONTRIBUTION_AND_TERM,
        PreSearchAnswerPlan(
            resolution="ANSWER",
            term_strictness="ANY",
        ),
        product_types=set(intent.product_types),
    )

    updated = IntentParser.apply_patch(intent, patch, source_utterance="기간은 상관없어요")

    assert updated.contribution_plan is not None
    assert updated.contribution_plan.desired_periodic_amount == 2_000_000
    assert updated.contribution_plan.term_strictness == "ANY"
    assert updated.contribution_plan.selected_term_value is None
    assert updated.contribution_plan.selected_term_unit is None
    assert all(item.field != "TERM_MONTHS" for item in updated.numeric_preferences)
    profile = DeterministicPreSearchQuestionPlanner.initialize_profile(updated)
    assert profile[CONTRIBUTION_AND_TERM].answer_status == PreSearchAnswerStatus.ANSWERED


def test_capacity_answer_defaults_to_korean_when_foreigner_is_not_mentioned() -> None:
    _, value = ApplicationService._pre_search_patch(
        APPLICATION_CAPACITY,
        PreSearchAnswerPlan(
            resolution="ANSWER",
            application_capacity="INDIVIDUAL",
        ),
    )

    assert value["foreign_national"] is False


def test_application_capacity_answer_materializes_shared_identity_facts() -> None:
    interpreter = _PreSearchInterpreter(
        {
            APPLICATION_CAPACITY: PreSearchAnswerPlan(
                resolution="ANSWER",
                application_capacity="INDIVIDUAL",
                foreign_national=False,
            )
        }
    )
    service = _service(interpreter)
    session = service.create_search_session(
        user_id="U-SHARED-IDENTITY",
        utterance="1천만원을 6개월 동안 정기예금에 맡기고 싶어요",
    )

    service.handle_user_message(
        session.search_session_id,
        message="개인 명의이고 외국인은 아니에요.",
    )
    facts = {
        fact.fact_type: fact.value
        for fact in service._runtime(session.search_session_id).fact_store.active_facts
    }

    assert facts["APPLICATION_CAPACITY"] == "INDIVIDUAL"
    assert facts["KOREAN_NATIONAL"] is True
    assert facts["REAL_NAME_SUBSCRIPTION_POSSIBLE"] is True


def test_birth_date_answer_materializes_age_at_subscription_date() -> None:
    interpreter = _PreSearchInterpreter(
        {
            BIRTH_DATE: PreSearchAnswerPlan(
                resolution="ANSWER",
                birth_date=date(2000, 8, 26),
            )
        }
    )
    service = _service(interpreter)
    intent = ProductSearchIntent(
        search_intent_id="I-BIRTH-DATE",
        user_id="U-BIRTH-DATE",
        product_types=["INSTALLMENT_SAVINGS"],
        application_capacity="INDIVIDUAL",
        contribution_plan=ContributionPlan(
            desired_periodic_amount=300_000,
            selected_term_value=12,
            selected_term_unit=TermUnit.MONTH,
        ),
    )
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        subscription_date=date(2026, 8, 25),
    )

    service.handle_user_message(session.search_session_id, message="2000년 8월 26일")
    facts = {
        fact.fact_type: fact.value
        for fact in service._runtime(session.search_session_id).fact_store.active_facts
    }

    assert facts["AGE_YEARS"] == 25


def test_youth_policy_account_question_is_global_for_installment_savings() -> None:
    intent = ProductSearchIntent(
        search_intent_id="I-YOUTH-POLICY",
        user_id="U-YOUTH-POLICY",
        product_types=["INSTALLMENT_SAVINGS"],
        application_capacity="INDIVIDUAL",
    )
    products = [
        product
        for product in load_default_product_catalog()
        if "청년미래적금" in product.name
    ]
    assert products
    profile = DeterministicPreSearchQuestionPlanner.initialize_profile(intent)
    profile[BIRTH_DATE] = profile[BIRTH_DATE].model_copy(
        update={
            "answer_status": PreSearchAnswerStatus.ANSWERED,
            "value": {"age_years": 29},
        },
        deep=True,
    )

    assert DeterministicPreSearchQuestionPlanner._is_applicable(
        YOUTH_POLICY_ACCOUNT_HOLDING,
        products,
        intent=intent,
        profile=profile,
    )
    assert "청년도약계좌" in DeterministicPreSearchQuestionPlanner._question(
        YOUTH_POLICY_ACCOUNT_HOLDING,
        intent,
    )

    profile[BIRTH_DATE] = profile[BIRTH_DATE].model_copy(
        update={"value": {"age_years": 40}},
        deep=True,
    )
    assert DeterministicPreSearchQuestionPlanner._is_applicable(
        YOUTH_POLICY_ACCOUNT_HOLDING,
        products,
        intent=intent,
        profile=profile,
    )
    assert DeterministicPreSearchQuestionPlanner._is_applicable(
        YOUTH_POLICY_ACCOUNT_HOLDING,
        [],
        intent=intent,
        profile=profile,
    )


def test_soldier_question_is_asked_only_when_a_soldier_candidate_remains() -> None:
    intent = ProductSearchIntent(
        search_intent_id="I-SOLDIER-GLOBAL",
        user_id="U-SOLDIER-GLOBAL",
        product_types=["INSTALLMENT_SAVINGS"],
        application_capacity="INDIVIDUAL",
    )
    profile = DeterministicPreSearchQuestionPlanner.initialize_profile(intent)

    assert not DeterministicPreSearchQuestionPlanner._is_applicable(
        SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY,
        [],
        intent=intent,
        profile=profile,
    )
    products = [
        product
        for product in load_default_product_catalog()
        if "장병내일준비" in product.name
    ]
    assert products
    assert DeterministicPreSearchQuestionPlanner._is_applicable(
        SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY,
        products,
        intent=intent,
        profile=profile,
    )
    question = DeterministicPreSearchQuestionPlanner._question(
        SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY,
        intent,
    )
    for label in (
        "현역병",
        "상근예비역",
        "의무경찰",
        "대체복무요원",
        "사회복무요원",
    ):
        assert label in question
    assert "가입 불가" in question


def test_unknown_soldier_eligibility_becomes_false_shared_fact() -> None:
    interpreter = _PreSearchInterpreter(
        {
            SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY: PreSearchAnswerPlan(
                resolution="ACKNOWLEDGED_UNKNOWN"
            )
        }
    )
    service = _service(interpreter)
    intent = ProductSearchIntent(
        search_intent_id="I-SOLDIER-UNKNOWN",
        user_id="U-SOLDIER-UNKNOWN",
        product_types=["INSTALLMENT_SAVINGS"],
        application_capacity="INDIVIDUAL",
        contribution_plan=ContributionPlan(
            desired_periodic_amount=300_000,
            selected_term_value=12,
            selected_term_unit=TermUnit.MONTH,
        ),
    )
    session = service.create_search_session(user_id=intent.user_id, intent=intent)
    runtime = service._runtime(session.search_session_id)
    for key in (BIRTH_DATE, YOUTH_POLICY_ACCOUNT_HOLDING):
        runtime.pre_search_profile[key] = runtime.pre_search_profile[key].model_copy(
            update={"answer_status": PreSearchAnswerStatus.NOT_APPLICABLE},
            deep=True,
        )
    service._select_question(runtime)

    active = service.get_next_question(session.search_session_id)
    assert active is not None
    assert active.pre_search_key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY
    service.handle_user_message(session.search_session_id, message="잘 모르겠어요")

    entry = runtime.pre_search_profile[SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY]
    assert entry.answer_status == PreSearchAnswerStatus.ANSWERED
    assert entry.value["soldier_tomorrow_savings_eligible"] is False
    facts = [
        fact
        for fact in runtime.fact_store.active_facts
        if fact.fact_type == "SOLDIER_TOMORROW_SAVINGS_ELIGIBLE"
    ]
    assert len(facts) == 1
    assert facts[0].value is False
    soldier = next(
        product
        for product in runtime.candidate_products.values()
        if "장병내일준비" in product.name
    )
    evaluated = service.evaluator.evaluate(
        [soldier],
        runtime.fact_store,
        runtime.intent,
        as_of=runtime.as_of,
        subscription_date=runtime.subscription_date,
    )[soldier.product_id]
    assert evaluated.eligibility_status == EvaluationStatus.UNSATISFIABLE
    # The answer must also refresh the session's displayed ranking immediately;
    # it must not wait for unrelated onboarding questions to finish.
    assert soldier.product_id not in runtime.ranking.ordered_product_ids


def test_contribution_answer_refreshes_interest_before_onboarding_finishes() -> None:
    interpreter = _PreSearchInterpreter(
        {
            CONTRIBUTION_AND_TERM: PreSearchAnswerPlan(
                resolution="ANSWER",
                desired_amount_krw=300_000,
                contribution_frequency="MONTHLY",
                term_value=1,
                term_unit="YEAR",
                term_strictness="PREFERRED",
            )
        }
    )
    service = _service(interpreter)
    intent = ProductSearchIntent(
        search_intent_id="I-CONTRIBUTION-REFRESH",
        user_id="U-CONTRIBUTION-REFRESH",
        product_types=["INSTALLMENT_SAVINGS"],
        application_capacity="INDIVIDUAL",
    )
    session = service.create_search_session(user_id=intent.user_id, intent=intent)
    runtime = service._runtime(session.search_session_id)
    for key in (
        BIRTH_DATE,
        YOUTH_POLICY_ACCOUNT_HOLDING,
        SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY,
        INSTITUTION_SCOPE,
    ):
        runtime.pre_search_profile[key] = runtime.pre_search_profile[key].model_copy(
            update={"answer_status": PreSearchAnswerStatus.NOT_APPLICABLE},
            deep=True,
        )
    service._select_question(runtime)

    active = service.get_next_question(session.search_session_id)
    assert active is not None
    assert active.pre_search_key == CONTRIBUTION_AND_TERM
    service.handle_user_message(session.search_session_id, message="매달 30만원씩 1년 넣을게요")

    product = next(
        item
        for item in runtime.candidate_products.values()
        if "JB 슈퍼씨드" in item.name
    )
    candidate = runtime.evaluations[product.product_id]
    assert candidate.contribution_projection.planned_periodic_amount == 300_000
    assert candidate.realizable_pre_tax_interest is not None


def test_youth_policy_account_answer_materializes_one_shared_fact() -> None:
    interpreter = _PreSearchInterpreter(
        {
            BIRTH_DATE: PreSearchAnswerPlan(
                resolution="ANSWER",
                birth_date=date(2000, 1, 1),
            ),
            YOUTH_POLICY_ACCOUNT_HOLDING: PreSearchAnswerPlan(
                resolution="ANSWER",
                youth_policy_account_held=False,
            ),
        }
    )
    service = _service(interpreter)
    intent = ProductSearchIntent(
        search_intent_id="I-YOUTH-POLICY-FACT",
        user_id="U-YOUTH-POLICY-FACT",
        product_types=["INSTALLMENT_SAVINGS"],
        application_capacity="INDIVIDUAL",
        contribution_plan=ContributionPlan(
            desired_periodic_amount=300_000,
            selected_term_value=12,
            selected_term_unit=TermUnit.MONTH,
        ),
    )
    session = service.create_search_session(user_id=intent.user_id, intent=intent)

    service.handle_user_message(session.search_session_id, message="2000년 1월 1일")
    active = service.get_next_question(session.search_session_id)
    assert active is not None
    assert active.pre_search_key == YOUTH_POLICY_ACCOUNT_HOLDING

    service.handle_user_message(session.search_session_id, message="둘 다 없어요")
    facts = [
        fact
        for fact in service._runtime(session.search_session_id).fact_store.active_facts
        if fact.fact_type == "YOUTH_FUTURE_OR_LEAP_ACCOUNT_HELD"
    ]
    assert len(facts) == 1
    assert facts[0].value is False


def test_llm_institution_aliases_are_canonicalized_for_retrieval() -> None:
    patch = IntentPatch(
        upsert_hard_constraints=[
            HardConstraint(
                field="institution_scope",
                constraint=HardConstraintValue.REQUIRE,
                expected="PRIMARY_FINANCIAL_INSTITUTION",
            ),
            HardConstraint(
                field="institution_type",
                constraint=HardConstraintValue.EXCLUDE,
                expected="SAVINGS_BANK",
            ),
        ]
    )

    normalized = ApplicationService._canonicalize_institution_constraint_patch(patch)

    assert [item.field for item in normalized.upsert_hard_constraints] == [
        "INSTITUTION_SECTOR",
        "INSTITUTION_SECTOR",
    ]
    assert [item.expected for item in normalized.upsert_hard_constraints] == [
        "BANK",
        "SAVINGS_BANK",
    ]


def test_institution_filter_request_is_rerouted_during_pre_search() -> None:
    catalog = load_default_product_catalog()
    products = [
        next(
            product
            for product in catalog
            if product.product_type == "TIME_DEPOSIT"
            and product.metadata.institution_sector == sector
        )
        for sector in ("BANK", "SAVINGS_BANK")
    ]
    service = ApplicationService(
        products,
        conversation_orchestrator=_PreSearchThenFilterInterpreter(),
        pre_search_enabled=True,
    )
    intent = ProductSearchIntent(
        search_intent_id="I-PRESEARCH-FILTER",
        user_id="U-PRESEARCH-FILTER",
        product_types=["TIME_DEPOSIT"],
        application_capacity="INDIVIDUAL",
        contribution_plan=ContributionPlan(
            preferred_start_amount=10_000_000,
            selected_term_value=12,
            selected_term_unit=TermUnit.MONTH,
        ),
    )
    session = service.create_search_session(user_id=intent.user_id, intent=intent)
    assert service.get_next_question(session.search_session_id).pre_search_key == BIRTH_DATE

    result = service.handle_user_message(
        session.search_session_id,
        message="저축은행은 싫어요.",
    )
    runtime = service._runtime(session.search_session_id)

    assert result.operations_executed == [ConversationOperation.UPDATE_SEARCH_INTENT]
    assert {
        product.metadata.institution_sector
        for product in runtime.candidate_products.values()
    } == {"BANK"}
    assert result.next_question is not None
    assert result.next_question.pre_search_key == BIRTH_DATE


def test_bank_only_pre_search_answer_filters_savings_banks() -> None:
    interpreter = _PreSearchInterpreter(
        {
            BIRTH_DATE: PreSearchAnswerPlan(
                resolution="ANSWER", birth_date=date(1990, 1, 1)
            ),
            INSTITUTION_SCOPE: PreSearchAnswerPlan(
                resolution="ANSWER",
                institution_scope="FIRST_SECTOR_ONLY",
            )
        }
    )
    service = _service(interpreter)
    intent = ProductSearchIntent(
        search_intent_id="I-BANK-ONLY",
        user_id="U-BANK-ONLY",
        product_types=["TIME_DEPOSIT"],
        application_capacity="INDIVIDUAL",
        contribution_plan=ContributionPlan(
            desired_periodic_amount=10_000_000,
            selected_term_value=6,
            selected_term_unit=TermUnit.MONTH,
        ),
    )
    session = service.create_search_session(user_id=intent.user_id, intent=intent)
    assert service.get_next_question(session.search_session_id).pre_search_key == BIRTH_DATE

    service.handle_user_message(session.search_session_id, message="1990년 1월 1일")
    assert service.get_next_question(session.search_session_id).pre_search_key == INSTITUTION_SCOPE

    service.handle_user_message(
        session.search_session_id,
        message="은행 상품만 보고 싶어요.",
    )
    runtime = service._runtime(session.search_session_id)

    assert runtime.candidate_products
    assert {
        product.metadata.institution_sector
        for product in runtime.candidate_products.values()
    } == {"BANK"}


def test_institution_question_defines_bank_as_first_sector() -> None:
    intent = ProductSearchIntent(
        search_intent_id="I-BANK-WORDING",
        user_id="U-BANK-WORDING",
        product_types=["INSTALLMENT_SAVINGS"],
    )

    question = DeterministicPreSearchQuestionPlanner._question(
        INSTITUTION_SCOPE, intent
    )
    examples = pre_search_answer_examples(
        INSTITUTION_SCOPE, intent.product_types
    )

    assert "은행(1금융권)" in question
    assert any("은행(1금융권)" in example for example in examples)


def test_bank_and_securities_scope_is_not_treated_as_any() -> None:
    patch, value = ApplicationService._pre_search_patch(
        INSTITUTION_SCOPE,
        PreSearchAnswerPlan(
            resolution="ANSWER",
            institution_scope="BANKS_AND_SECURITIES",
        ),
    )

    assert value["institution_scope"] == "BANKS_AND_SECURITIES"
    assert patch.upsert_hard_constraints[0].expected == "BANK|SECURITIES"


def test_explicit_application_capacity_is_global_and_skips_the_question() -> None:
    service = _service(_PreSearchInterpreter({}))
    session = service.create_search_session(
        user_id="U-CORPORATION",
        utterance="법인 명의로 1천만원을 1년 정기예금에 맡기고 싶어요",
    )

    intent = service.get_search_intent(session.search_session_id)
    question = service.get_next_question(session.search_session_id)
    runtime = service._runtime(session.search_session_id)

    assert intent.application_capacity == "CORPORATION"
    assert "PRESEARCH-APPLICATION_CAPACITY" in session.answered_question_ids
    assert question is not None
    assert question.pre_search_key != APPLICATION_CAPACITY
    facts = [
        fact
        for fact in runtime.fact_store.active_facts
        if fact.fact_type == "APPLICATION_CAPACITY"
    ]
    assert len(facts) == 1
    assert facts[0].value == "CORPORATION"


def test_pre_search_explanation_uses_generated_message_and_retains_question() -> None:
    generated = (
        "은행만 볼지 저축은행까지 함께 비교할지를 묻고 있어요. "
        "제외하고 싶은 금융기관 유형이 있는지 알려주세요."
    )
    service = _service(
        _PreSearchInterpreter(
            {
                BIRTH_DATE: PreSearchAnswerPlan(
                    resolution="ANSWER", birth_date=date(1990, 1, 1)
                ),
                YOUTH_POLICY_ACCOUNT_HOLDING: PreSearchAnswerPlan(
                    resolution="ANSWER", youth_policy_account_held=False
                ),
                SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY: PreSearchAnswerPlan(
                    resolution="ANSWER",
                    soldier_tomorrow_savings_eligible=False,
                ),
                "INSTITUTION_SCOPE": PreSearchAnswerPlan(
                    resolution="EXPLAIN",
                    rationale="사용자가 금융기관 범위의 의미를 물었습니다.",
                    assistant_message=generated,
                )
            }
        )
    )
    session = service.create_search_session(
        user_id="EXPLAIN-USER",
        utterance="개인 명의로 월 30만원씩 1년 적금을 찾고 있어요",
    )
    active = service.get_next_question(session.search_session_id)
    assert active is not None
    assert active.pre_search_key == BIRTH_DATE
    service.handle_user_message(session.search_session_id, message="1990년 1월 1일")
    active = service.get_next_question(session.search_session_id)
    assert active is not None
    assert active.pre_search_key == YOUTH_POLICY_ACCOUNT_HOLDING
    service.handle_user_message(session.search_session_id, message="둘 다 없어요")
    active = service.get_next_question(session.search_session_id)
    assert active is not None
    assert active.pre_search_key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY
    service.handle_user_message(session.search_session_id, message="해당하지 않아요")
    active = service.get_next_question(session.search_session_id)
    assert active is not None
    assert active.pre_search_key == INSTITUTION_SCOPE

    result = service.handle_user_message(
        session.search_session_id,
        message="저축은행까지 비교한다는 건 무슨 뜻인가요?",
    )

    assert result.assistant_message == generated
    assert result.next_question is not None
    assert result.next_question.question_id == active.question_id
    assert result.session.active_question_id == active.question_id


def test_lump_sum_or_balance_amount_also_completes_combined_amount_term_profile() -> None:
    intent = ProductSearchIntent(
        search_intent_id="LUMP-SUM-INTENT",
        user_id="LUMP-SUM-USER",
        product_types=["TIME_DEPOSIT"],
        contribution_plan=ContributionPlan(
            preferred_start_amount=10_000_000,
            selected_term_value=6,
            selected_term_unit=TermUnit.MONTH,
        ),
    )
    profile = DeterministicPreSearchQuestionPlanner.initialize_profile(intent)

    assert profile["CONTRIBUTION_AND_TERM"].answer_status == PreSearchAnswerStatus.ANSWERED
    assert profile["CONTRIBUTION_AND_TERM"].value["balance_or_lump_sum_krw"] == 10_000_000


def test_product_type_change_preserves_institution_scope_and_closes_inapplicable_cma_scope() -> None:
    app_service = ApplicationService(
        load_default_product_catalog(),
        pre_search_enabled=True,
    )
    intent = ProductSearchIntent(
        search_intent_id="SCOPE-CHANGE-INTENT",
        user_id="SCOPE-CHANGE-USER",
        product_types=["PARKING_ACCOUNT"],
        contribution_plan=ContributionPlan(
            preferred_start_amount=20_000_000,
            selected_term_value=12,
            selected_term_unit=TermUnit.MONTH,
        ),
    )
    session = app_service.create_search_session(user_id=intent.user_id, intent=intent)
    runtime = app_service._runtime(session.search_session_id)
    for key, entry in list(runtime.pre_search_profile.items()):
        runtime.pre_search_profile[key] = entry.model_copy(
            update={"answer_status": PreSearchAnswerStatus.ANSWERED, "value": {"done": True}},
            deep=True,
        )

    app_service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            upsert_product_types=["CMA"],
            remove_product_types=["PARKING_ACCOUNT"],
        ),
    )

    runtime = app_service._runtime(session.search_session_id)
    assert runtime.pre_search_profile[INSTITUTION_SCOPE].answer_status == PreSearchAnswerStatus.ANSWERED
    assert (
        runtime.pre_search_profile[PROTECTION_AND_CMA_SCOPE].answer_status
        == PreSearchAnswerStatus.NOT_APPLICABLE
    )
    assert runtime.pre_search_profile[CONTRIBUTION_AND_TERM].answer_status == PreSearchAnswerStatus.ANSWERED
    assert runtime.pre_search_profile[COMMON_BENEFIT_WILLINGNESS].answer_status == PreSearchAnswerStatus.ANSWERED
    assert runtime.active_question is None or runtime.active_question.pre_search_key != INSTITUTION_SCOPE


def test_product_type_patch_records_the_current_user_utterance_as_profile_source() -> None:
    app_service = ApplicationService(
        load_default_product_catalog(),
        pre_search_enabled=True,
    )
    intent = ProductSearchIntent(
        search_intent_id="SCOPE-SOURCE-INTENT",
        user_id="SCOPE-SOURCE-USER",
        product_types=["PARKING_ACCOUNT", "CMA"],
    )
    session = app_service.create_search_session(user_id=intent.user_id, intent=intent)
    utterance = "CMA는 빼고 파킹통장만 보여 주세요."

    app_service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            remove_product_types=["CMA"],
        ),
        utterance=utterance,
    )

    runtime = app_service._runtime(session.search_session_id)
    product_type = runtime.pre_search_profile["PRODUCT_TYPE"]
    assert product_type.value == {"product_types": ["PARKING_ACCOUNT"]}
    assert product_type.source_text == utterance
    assert app_service.get_search_intent(
        session.search_session_id
    ).source_utterances[-1] == utterance


def test_ranking_question_is_removed_and_rate_sort_remains_the_default() -> None:
    service = _service(_PreSearchInterpreter({}))
    session = service.create_search_session(
        user_id="U",
        utterance="개인 명의로 월 30만원씩 1년 적금을 찾고 있어요",
    )

    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.pre_search_key == BIRTH_DATE
    assert service.get_search_intent(
        session.search_session_id
    ).ranking_objective == RankingObjective.MAX_REALIZABLE_RATE
    state = service.get_mutable_search_state(session.search_session_id)
    assert "RANKING_OBJECTIVE" not in {
        item["question_key"] for item in state["pre_search_profile"]
    }


def test_acknowledged_unknown_closes_question_without_becoming_unwilling() -> None:
    interpreter = _PreSearchInterpreter(
            {
                BIRTH_DATE: PreSearchAnswerPlan(
                    resolution="ANSWER", birth_date=date(1990, 1, 1)
                ),
                YOUTH_POLICY_ACCOUNT_HOLDING: PreSearchAnswerPlan(
                    resolution="ANSWER", youth_policy_account_held=False
                ),
                SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY: PreSearchAnswerPlan(
                    resolution="ANSWER",
                    soldier_tomorrow_savings_eligible=False,
                ),
                "INSTITUTION_SCOPE": PreSearchAnswerPlan(
                    resolution="ACKNOWLEDGED_UNKNOWN"
                ),
        }
    )
    service = _service(interpreter)
    session = service.create_search_session(
        user_id="U",
        utterance="개인 명의로 월 30만원씩 1년 적금을 찾고 있어요",
    )
    service.handle_user_message(session.search_session_id, message="1990년 1월 1일")
    service.handle_user_message(session.search_session_id, message="둘 다 없어요")
    service.handle_user_message(session.search_session_id, message="해당하지 않아요")
    result = service.handle_user_message(
        session.search_session_id,
        message="잘 모르겠어요",
    )
    state = service.get_mutable_search_state(session.search_session_id)
    institution = next(
        item for item in state["pre_search_profile"]
        if item["question_key"] == "INSTITUTION_SCOPE"
    )

    assert institution["answer_status"] == "ACKNOWLEDGED_UNKNOWN"
    assert institution["value"] == {}
    assert result.next_question is not None
    assert result.next_question.pre_search_key == "COMMON_BENEFIT_WILLINGNESS"


def test_explicit_parking_and_cma_scope_skips_the_redundant_protection_question() -> None:
    service = _service(
        _PreSearchInterpreter(
            {
                BIRTH_DATE: PreSearchAnswerPlan(
                    resolution="ANSWER", birth_date=date(1990, 1, 1)
                )
            }
        )
    )
    session = service.create_search_session(
        user_id="U",
        intent=ProductSearchIntent(
            search_intent_id="I-LIQUID",
            user_id="U",
            product_types=["PARKING_ACCOUNT", "CMA"],
            application_capacity="INDIVIDUAL",
            contribution_plan=ContributionPlan(
                desired_periodic_amount=10_000_000,
                frequency=ContributionFrequency.FLEXIBLE,
                selected_term_value=3,
                selected_term_unit=TermUnit.MONTH,
            ),
        ),
    )

    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.pre_search_key == BIRTH_DATE

    service.handle_user_message(session.search_session_id, message="1990년 1월 1일")
    question = service.get_next_question(session.search_session_id)
    assert question is not None
    assert question.pre_search_key == INSTITUTION_SCOPE
    assert service._runtime(session.search_session_id).pre_search_profile[
        PROTECTION_AND_CMA_SCOPE
    ].value == {"liquid_product_scope": "INCLUDE_CMA"}

def test_institution_scope_names_securities_only_when_cma_is_in_scope() -> None:
    liquid_intent = ProductSearchIntent(
        search_intent_id="I-LIQUID-SCOPE",
        user_id="U",
        product_types=["PARKING_ACCOUNT", "CMA"],
    )
    parking_intent = ProductSearchIntent(
        search_intent_id="I-PARKING-SCOPE",
        user_id="U",
        product_types=["PARKING_ACCOUNT"],
    )

    liquid_question = DeterministicPreSearchQuestionPlanner._question(
        INSTITUTION_SCOPE, liquid_intent
    )
    parking_question = DeterministicPreSearchQuestionPlanner._question(
        INSTITUTION_SCOPE, parking_intent
    )

    assert "증권사의 CMA" in liquid_question
    assert "제외하고 싶은 금융기관 유형" in liquid_question
    assert "증권사" not in parking_question
    assert "저축은행의 파킹통장" in parking_question


def test_protection_scope_answer_filters_liquid_product_types() -> None:
    patch, value = ApplicationService._pre_search_patch(
        PROTECTION_AND_CMA_SCOPE,
        PreSearchAnswerPlan(
            resolution="ANSWER",
            liquid_product_scope="PARKING_ONLY",
        ),
    )

    assert patch.upsert_product_types == ["PARKING_ACCOUNT"]
    assert patch.remove_product_types == ["CMA"]
    assert value["liquid_product_scope"] == "PARKING_ONLY"


def test_common_benefit_question_is_always_applicable() -> None:
    assert DeterministicPreSearchQuestionPlanner._is_applicable(
        COMMON_BENEFIT_WILLINGNESS,
        [],
    )


def test_initial_question_answer_is_not_repeated_when_general_parser_misses_it() -> None:
    interpreter = _PreSearchInterpreter(
        {
            "PRODUCT_TYPE": PreSearchAnswerPlan(
                resolution="ANSWER",
                product_types=["PARKING_ACCOUNT", "CMA"],
            )
        }
    )
    service = _service(interpreter)
    rest = RestApplicationAdapter(service)

    created = rest.handle(
        "POST",
        "/search-sessions",
        {
            "user_id": "U-INITIAL",
            "natural_language_query": "수시로 찾고 넣고 하고 싶어요",
            "initial_pre_search_question_id": "PRESEARCH-PRODUCT_TYPE",
            "as_of": "2026-08-24",
            "subscription_date": "2026-08-24",
        },
    )

    assert created.status_code == 201
    assert created.body["active_question_id"] == "PRESEARCH-APPLICATION_CAPACITY"
    assert interpreter.keys_seen == ["PRODUCT_TYPE"]
    intent = service.get_search_intent(created.body["search_session_id"])
    assert intent.product_types == ["PARKING_ACCOUNT", "CMA"]


def test_one_non_applicable_common_action_does_not_close_the_other_actions() -> None:
    patch, value = ApplicationService._pre_search_patch(
        COMMON_BENEFIT_WILLINGNESS,
        PreSearchAnswerPlan(
            resolution="ANSWER",
            first_transaction_willingness="WILLING",
            salary_transfer_willingness="NOT_APPLICABLE",
        ),
    )

    assert value["action_preferences"] == {
        "FIRST_TRANSACTION_BENEFIT": "WILLING",
        "SALARY_BENEFIT": "NOT_APPLICABLE",
    }
    assert {item.field for item in patch.upsert_preferences} == {"SALARY_BENEFIT"}
    assert patch.remove_preference_keys == ["FIRST_TRANSACTION_BENEFIT"]
    assert patch.upsert_capabilities[0].capability_id == "CHANGE_SALARY_ACCOUNT"
    assert patch.upsert_capabilities[0].state.value == "CANNOT"


def test_pre_search_continues_into_product_specific_verification() -> None:
    answers = {
        "APPLICATION_CAPACITY": PreSearchAnswerPlan(
            resolution="ANSWER", application_capacity="INDIVIDUAL"
        ),
        BIRTH_DATE: PreSearchAnswerPlan(
            resolution="ANSWER", birth_date=date(1990, 1, 1)
        ),
        YOUTH_POLICY_ACCOUNT_HOLDING: PreSearchAnswerPlan(
            resolution="ANSWER", youth_policy_account_held=False
        ),
        SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY: PreSearchAnswerPlan(
            resolution="ANSWER",
            soldier_tomorrow_savings_eligible=False,
        ),
        "INSTITUTION_SCOPE": PreSearchAnswerPlan(
            resolution="ANSWER", institution_scope="ANY"
        ),
        "COMMON_BENEFIT_WILLINGNESS": PreSearchAnswerPlan(
            resolution="ANSWER",
            first_transaction_willingness="WILLING",
            salary_transfer_willingness="CONDITIONAL",
            card_willingness="UNWILLING",
        ),
        INSTITUTION_PRODUCT_HOLDING_HISTORY: PreSearchAnswerPlan(
            resolution="ANSWER",
            prior_product_holding_institutions=["신한은행", "OO저축은행"],
        ),
    }
    interpreter = _PreSearchInterpreter(answers)
    service = _service(interpreter)
    session = service.create_search_session(
        user_id="U",
        utterance="월 30만원씩 1년 적금을 찾고 있어요",
    )

    result = None
    while (
        (question := service.get_next_question(session.search_session_id)) is not None
        and question.question_kind == "PRE_SEARCH_PROFILE"
    ):
        result = service.handle_user_message(
            session.search_session_id,
            message="자연어 답변",
        )

    assert result is not None
    if result.next_question is not None:
        assert result.next_question.question_kind != "PRE_SEARCH_PROFILE"
        assert result.next_question.question_stage != "PRE_SEARCH"
    else:
        assert result.recommendations is not None
    state = service.get_mutable_search_state(session.search_session_id)
    assert state["search_progress"]["completed_question_count"] == 9
    assert state["search_progress"]["estimated_total_question_count"] >= 9
    common = next(
        item for item in state["pre_search_profile"]
        if item["question_key"] == "COMMON_BENEFIT_WILLINGNESS"
    )
    assert common["value"]["action_preferences"] == {
        "FIRST_TRANSACTION_BENEFIT": "WILLING",
        "SALARY_BENEFIT": "CONDITIONAL",
        "CARD_BENEFIT": "UNWILLING",
    }
    intent = state["intent"]
    absent = {
        item["field"] for item in intent["preferences"]
        if item["preference"] == "PREFER_ABSENT"
    }
    assert "CARD_BENEFIT" in absent
    assert "FIRST_TRANSACTION_BENEFIT" not in absent


def test_institution_product_holding_history_question_is_last() -> None:
    assert QUESTION_ORDER[-1] == INSTITUTION_PRODUCT_HOLDING_HISTORY
    question = DeterministicPreSearchQuestionPlanner._question(
        INSTITUTION_PRODUCT_HOLDING_HISTORY,
        ProductSearchIntent(search_intent_id="I", user_id="U"),
    )
    assert "은행이나 저축은행" in question
    assert "신한은행" not in question
