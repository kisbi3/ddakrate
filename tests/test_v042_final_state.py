from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from eligibility.application_service import ApplicationService
from eligibility.adapters import MCPToolAdapter, RestApplicationAdapter
from eligibility.audit import AuditEventType
from eligibility.fixtures.hana_run import hana_run_product
from eligibility.fixtures.ibk_parent_benefit import ibk_parent_benefit_product
from eligibility.fixtures.kakao_26_week import (
    USER_ID as KAKAO_USER_ID,
    kakao_26_week_product,
    kakao_future_intent,
    kakao_pre_subscription_user,
)
from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
from eligibility.schema.application_input import Capability, NumericPreference, Preference
from eligibility.schema.enums import (
    CapabilityState,
    ComparisonOperator,
    ContributionFrequency,
    FactRecordStatus,
    FactSemanticType,
    FactSourceType,
    NumericPreferenceDirection,
    PreferenceStrictness,
    PreferenceValue,
    RankingObjective,
    ResolutionStrategy,
    RulePurpose,
    TermUnit,
)
from eligibility.schema.search import ContributionPlan, IntentPatch, ProductSearchIntent
from eligibility.schema.product import ProductDefinition
from eligibility.schema.rule import FactComparisonRule, MissingFactSpec, SourceReference
from eligibility.schema.user_fact import UserFact
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.intent import IntentParser
from eligibility.search.questions import RankingAwareQuestionPlanner
from eligibility.search.retrieval import CandidateRetriever

from tests.v04_helpers import AS_OF, SUBSCRIPTION_DATE, USER_ID, base_store, make_intent, make_product


def _kakao_intent(*, objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST, top_k=2):
    return ProductSearchIntent(
        search_intent_id=f"INTENT-V042-{objective.value}",
        user_id=KAKAO_USER_ID,
        product_types=["INSTALLMENT_SAVINGS"],
        ranking_objective=objective,
        contribution_plan=ContributionPlan(
            desired_periodic_amount=Decimal("300000"),
            maximum_affordable_periodic_amount=Decimal("1000000"),
            frequency=ContributionFrequency.MONTHLY,
            selected_term_value=26,
            selected_term_unit=TermUnit.WEEK,
        ),
        requested_top_k=top_k,
        source_utterances=["월 30만원 정도 생각하고 있어"],
    )


def _clone_kakao(product_id: str, name: str):
    original = kakao_26_week_product()
    metadata = original.metadata.model_copy(
        update={"product_id": product_id, "product_name": name}, deep=True
    )
    return original.model_copy(
        update={"product_id": product_id, "name": name, "metadata": metadata},
        deep=True,
    )


def _answer_two_product_choices(service: ApplicationService, session_id: str):
    seen: list[tuple[str, str, Decimal]] = []
    desired = {}
    for _ in range(2):
        question = service.get_next_question(session_id)
        assert question is not None and question.question_kind == "RANKING_INPUT"
        assert question.ranking_input is not None
        product_id = question.ranking_input.product_id
        value = Decimal("10000") if not seen else Decimal("5000")
        desired[product_id] = value
        seen.append((question.question_id, product_id, value))
        service.submit_user_answer(
            session_id,
            question_id=question.question_id,
            answer=str(value),
        )
    return seen, desired


def test_product_specific_contribution_choice_does_not_mutate_global_plan():
    product = kakao_26_week_product()
    service = ApplicationService(
        [product], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.ranking_input is not None
    service.submit_user_answer(
        session.search_session_id, question_id=question.question_id, answer="10000"
    )

    intent = service.get_search_intent(session.search_session_id)
    assert intent.contribution_plan is not None
    assert intent.contribution_plan.desired_periodic_amount == Decimal("300000")
    assert intent.contribution_plan.frequency == ContributionFrequency.MONTHLY
    assert intent.contribution_plan.preferred_start_amount is None
    choices = service.get_product_contribution_choices(session.search_session_id)
    assert len(choices) == 1
    assert choices[0].product_id == product.product_id
    assert choices[0].field == "preferred_start_amount"
    assert choices[0].value == Decimal("10000")


def test_two_products_can_have_different_start_amounts():
    a = _clone_kakao("KAKAO-LIKE-A", "점증식 A")
    b = _clone_kakao("KAKAO-LIKE-B", "점증식 B")
    service = ApplicationService(
        [a, b], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(top_k=2),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    seen, desired = _answer_two_product_choices(service, session.search_session_id)
    assert seen[0][0] != seen[1][0]
    choices = {
        (item.product_id, item.field): item.value
        for item in service.get_product_contribution_choices(session.search_session_id)
    }
    assert choices[(seen[0][1], "preferred_start_amount")] == desired[seen[0][1]]
    assert choices[(seen[1][1], "preferred_start_amount")] == desired[seen[1][1]]
    assert set(choices.values()) == {Decimal("10000"), Decimal("5000")}


def test_product_specific_choice_used_only_for_matching_product():
    a = _clone_kakao("KAKAO-ISO-A", "점증식 격리 A")
    b = _clone_kakao("KAKAO-ISO-B", "점증식 격리 B")
    service = ApplicationService(
        [a, b], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(top_k=2),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    _seen, desired = _answer_two_product_choices(service, session.search_session_id)
    evaluations = service.evaluate_candidates(session.search_session_id)
    for product_id, start in desired.items():
        assert evaluations[product_id].contribution_projection.initial_amount == start
    principals = {
        product_id: evaluations[product_id].estimated_total_principal
        for product_id in desired
    }
    high = next(pid for pid, amount in desired.items() if amount == Decimal("10000"))
    low = next(pid for pid, amount in desired.items() if amount == Decimal("5000"))
    assert principals[high] == principals[low] * 2


def test_product_choice_survives_reranking():
    a = _clone_kakao("KAKAO-RERANK-A", "점증식 재순위 A")
    b = _clone_kakao("KAKAO-RERANK-B", "점증식 재순위 B")
    service = ApplicationService(
        [a, b], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(top_k=2),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    _answer_two_product_choices(service, session.search_session_id)
    before = service.get_product_contribution_choices(session.search_session_id)
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            upsert_preferences=[
                Preference(
                    field="INCREMENTAL_CONTRIBUTION",
                    preference=PreferenceValue.PREFER_PRESENT,
                )
            ]
        ),
    )
    after = service.get_product_contribution_choices(session.search_session_id)
    assert [(x.product_id, x.field, x.value) for x in after] == [
        (x.product_id, x.field, x.value) for x in before
    ]
    evaluations = service.evaluate_candidates(session.search_session_id)
    assert all(item.ranking_comparability.value == "COMPARABLE" for item in evaluations.values())


def test_answered_question_identity_is_product_scoped():
    a = _clone_kakao("KAKAO-QID-A", "점증식 질문 A")
    b = _clone_kakao("KAKAO-QID-B", "점증식 질문 B")
    service = ApplicationService(
        [a, b], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(top_k=2),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    first = service.get_next_question(session.search_session_id)
    assert first is not None and first.ranking_input is not None
    service.submit_user_answer(session.search_session_id, question_id=first.question_id, answer="10000")
    second = service.get_next_question(session.search_session_id)
    assert second is not None and second.ranking_input is not None
    assert first.ranking_input.product_id != second.ranking_input.product_id
    assert first.ranking_input.required_field == second.ranking_input.required_field
    assert first.question_id != second.question_id


def test_real_product_fixtures_have_search_features():
    fixtures = [
        shinhan_youth_first_product(),
        kakao_26_week_product(),
        ibk_parent_benefit_product(),
        hana_run_product(),
    ]
    assert all(product.metadata is not None and product.metadata.features for product in fixtures)
    assert {f.feature_id for f in fixtures[0].metadata.features} >= {
        "FIRST_TRANSACTION_BENEFIT", "CARD_BENEFIT", "APP_SERVICE_BENEFIT"
    }
    assert {f.feature_id for f in fixtures[1].metadata.features} >= {
        "INCREMENTAL_CONTRIBUTION", "CONSECUTIVE_AUTO_TRANSFER_BENEFIT"
    }


def test_search_feature_wiring_matches_real_fixture_semantics():
    shinhan = shinhan_youth_first_product()
    shinhan_rule_ids = {item.rule.rule_id for item in shinhan.preferential_rules}
    shinhan_features = {item.feature_id for item in shinhan.metadata.features}
    assert "FIRST_TRANSACTION_BENEFIT" in shinhan_features
    assert "RATE_FIRST_OR_EVENT" in shinhan_rule_ids
    assert "CARD_BENEFIT" in shinhan_features
    assert "RATE_CARD" in shinhan_rule_ids

    kakao = kakao_26_week_product()
    kakao_rule_ids = {item.rule.rule_id for item in kakao.preferential_rules}
    kakao_features = {item.feature_id for item in kakao.metadata.features}
    assert "INCREMENTAL_CONTRIBUTION" in kakao_features
    assert {"K26_RATE_7_CONSECUTIVE", "K26_RATE_26_CONSECUTIVE"} <= kakao_rule_ids


def test_first_transaction_preference_affects_real_fixture():
    product = shinhan_youth_first_product()
    intent = make_intent(
        user_id="U001",
        selected_term_value=None,
        selected_term_unit=None,
        preferences=[
            Preference(
                field="FIRST_TRANSACTION_BENEFIT",
                preference=PreferenceValue.PREFER_PRESENT,
            )
        ],
    )
    from eligibility.fixtures.user_001 import user_001
    candidate = MultiProductEvaluator().evaluate(
        [product], user_001(), intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )[product.product_id]
    assert candidate.preference_score == 1


def test_first_transaction_preference_affects_shinhan_ranking():
    shinhan = shinhan_youth_first_product()
    clone_metadata = shinhan.metadata.model_copy(
        update={
            "product_id": "AAA-SHINHAN-CONTROL",
            "product_name": "청년 처음적금 control",
            "features": [
                feature
                for feature in shinhan.metadata.features
                if feature.feature_id != "FIRST_TRANSACTION_BENEFIT"
            ],
        },
        deep=True,
    )
    control = shinhan.model_copy(
        update={
            "product_id": "AAA-SHINHAN-CONTROL",
            "name": "청년 처음적금 control",
            "metadata": clone_metadata,
        },
        deep=True,
    )
    intent = make_intent(
        user_id="U001",
        selected_term_value=None,
        selected_term_unit=None,
        top_k=2,
        preferences=[
            Preference(
                field="FIRST_TRANSACTION_BENEFIT",
                preference=PreferenceValue.PREFER_PRESENT,
            )
        ],
    )
    from eligibility.fixtures.user_001 import user_001
    evaluations = MultiProductEvaluator().evaluate(
        [control, shinhan], user_001(), intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )
    from eligibility.search.ranking import RankingService
    ranking, _ = RankingService().rank(
        evaluations,
        {control.product_id: control, shinhan.product_id: shinhan},
        objective=intent.ranking_objective,
        top_k=2,
    )
    assert evaluations[shinhan.product_id].preference_score == 1
    assert evaluations[control.product_id].preference_score == 0
    assert ranking.items[0].product_id == shinhan.product_id


def test_card_benefit_not_equal_new_card_required_real_fixture():
    product = shinhan_youth_first_product()
    assert CandidateRetriever._feature_present(product, "CARD_BENEFIT") is True
    assert CandidateRetriever._feature_present(product, "NEW_CARD_REQUIRED") is None


def test_incremental_feature_wired_to_kakao():
    product = kakao_26_week_product()
    assert CandidateRetriever._feature_present(product, "INCREMENTAL_CONTRIBUTION") is True


def test_preferential_feature_does_not_hard_filter_for_new_card_capability():
    product = shinhan_youth_first_product()
    intent = make_intent(
        capabilities=[
            Capability(capability_id="NEW_CARD_ISSUANCE", state=CapabilityState.CANNOT)
        ]
    )
    retained, decisions = CandidateRetriever().retrieve([product], intent, as_of=AS_OF)
    assert retained == [product]
    assert decisions[0].retained is True


def _salary_product():
    return make_product(
        "SALARY-REVISION",
        base_rate="2.0",
        reward_pp="1.0",
        bonus_fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
    )


def _active_user_declarations(service: ApplicationService, session_id: str, fact_type: str):
    runtime = service._runtime(session_id)
    return [
        fact for fact in runtime.fact_store.facts
        if fact.fact_type == fact_type
        and fact.source_type == FactSourceType.USER_DECLARED
        and fact.record_status == FactRecordStatus.ACTIVE
        and fact.semantic_type in {FactSemanticType.FUTURE_INTENT, FactSemanticType.SELF_REPORTED_FACT}
    ]


def test_intent_update_supersedes_prior_user_declared_fact():
    product = _salary_product()
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = service.get_next_question(session.search_session_id)
    assert question is not None and question.request is not None
    service.submit_user_answer(session.search_session_id, question_id=question.question_id, answer=True)
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            upsert_capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT)
            ]
        ),
    )
    active = _active_user_declarations(
        service, session.search_session_id, "SALARY_ACCOUNT_CHANGE_POSSIBLE"
    )
    assert len(active) == 1 and active[0].value is False
    assert any(
        event.event_type == AuditEventType.USER_DECLARED_FACT_SUPERSEDED_BY_INTENT_UPDATE
        for event in service.get_evaluation_trace(session.search_session_id)
    )


def test_intent_revision_history_remains_auditable():
    product = _salary_product()
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(
            top_k=1,
            capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN)
            ],
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            upsert_capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT)
            ]
        ),
    )
    runtime = service._runtime(session.search_session_id)
    declarations = [
        fact
        for fact in runtime.fact_store.facts
        if fact.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE"
        and fact.source_type == FactSourceType.USER_DECLARED
    ]
    assert len(declarations) == 2
    assert {(fact.value, fact.record_status) for fact in declarations} == {
        (True, FactRecordStatus.SUPERSEDED),
        (False, FactRecordStatus.ACTIVE),
    }
    events = service.get_evaluation_trace(session.search_session_id)
    supersession = [
        event
        for event in events
        if event.event_type == AuditEventType.USER_DECLARED_FACT_SUPERSEDED_BY_INTENT_UPDATE
    ]
    assert supersession
    assert supersession[-1].entity_refs["prior_fact_id"] in {fact.fact_id for fact in declarations}
    assert supersession[-1].entity_refs["new_fact_id"] in {fact.fact_id for fact in declarations}


def test_true_to_false_leaves_single_active_declaration():
    product = _salary_product()
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(
            top_k=1,
            capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN)
            ],
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            upsert_capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT)
            ]
        ),
    )
    active = _active_user_declarations(service, session.search_session_id, "SALARY_ACCOUNT_CHANGE_POSSIBLE")
    assert [(item.value, item.version) for item in active] == [(False, 2)]


def test_false_to_true_leaves_single_active_declaration():
    product = _salary_product()
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(
            top_k=1,
            capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT)
            ],
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            upsert_capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN)
            ]
        ),
    )
    active = _active_user_declarations(service, session.search_session_id, "SALARY_ACCOUNT_CHANGE_POSSIBLE")
    assert [(item.value, item.version) for item in active] == [(True, 2)]


def test_authoritative_fact_not_superseded():
    product = _salary_product()
    authoritative = UserFact(
        fact_id="AUTH-SALARY",
        user_id=USER_ID,
        fact_type="SALARY_ACCOUNT_CHANGE_POSSIBLE",
        value=True,
        valid_from=AS_OF,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_FACT,
        collected_at=datetime(2026, 8, 19, 8, tzinfo=timezone.utc),
    )
    service = ApplicationService(
        [product], user_fact_stores={USER_ID: base_store(authoritative)}
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(
            top_k=1,
            capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT)
            ],
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    runtime = service._runtime(session.search_session_id)
    auth = next(item for item in runtime.fact_store.facts if item.fact_id == "AUTH-SALARY")
    assert auth.record_status == FactRecordStatus.ACTIVE
    assert auth.source_type == FactSourceType.INSTITUTION_VERIFIED


def test_revision_re_evaluates_products():
    product = _salary_product()
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(
            top_k=1,
            capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CAN)
            ],
        ),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    before = service.evaluate_candidates(session.search_session_id)[product.product_id]
    service.update_search_intent(
        session.search_session_id,
        patch=IntentPatch(
            upsert_capabilities=[
                Capability(capability_id="CHANGE_SALARY_ACCOUNT", state=CapabilityState.CANNOT)
            ]
        ),
    )
    after = service.evaluate_candidates(session.search_session_id)[product.product_id]
    assert before.realizable_rate == Decimal("3.0")
    assert after.realizable_rate == Decimal("2.0")


def test_max_rate_objective_skips_interest_only_contribution_question():
    product = kakao_26_week_product()
    store = kakao_pre_subscription_user()
    store = store.with_fact(kakao_future_intent(True, target=7))
    store = store.with_fact(kakao_future_intent(True, target=26))
    intent = _kakao_intent(objective=RankingObjective.MAX_REALIZABLE_RATE, top_k=1)
    evaluations = MultiProductEvaluator().evaluate(
        [product], store, intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )
    questions = RankingAwareQuestionPlanner().score_candidates(evaluations, intent)
    assert all(item.question_kind != "RANKING_INPUT" for item in questions)


def test_contribution_question_still_asked_if_needed_for_eligibility():
    base = make_product("ELIG-CONTRIB", base_rate="4.0")
    eligibility = FactComparisonRule(
        rule_id="ELIG-CONTRIB-CHOICE",
        name="가입금액 조건 확인",
        purpose=RulePurpose.ELIGIBILITY,
        fact_type="PRODUCT_CONTRIBUTION_ELIGIBILITY_CONFIRMED",
        operator=ComparisonOperator.EQ,
        expected=True,
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.ASK_USER,
            question="이 상품의 가입금액 조건을 선택할 수 있나요?",
            expected_semantic_type=FactSemanticType.FUTURE_INTENT,
            grounding_terms=["가입금액 조건"],
        ),
        source=SourceReference(
            document="v0.4.2 synthetic eligibility fixture",
            document_id="DOC-ELIG-CONTRIB",
            version_date=SUBSCRIPTION_DATE,
            page=1,
            section="가입금액",
            source_text="가입금액 조건",
        ),
    )
    product: ProductDefinition = base.model_copy(update={"eligibility_rule": eligibility}, deep=True)
    intent = make_intent(
        top_k=1,
        objective=RankingObjective.MAX_REALIZABLE_RATE,
    )
    evaluations = MultiProductEvaluator().evaluate(
        [product], base_store(), intent, as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE
    )
    question = RankingAwareQuestionPlanner().select_next(evaluations, intent)
    assert question is not None
    assert question.question_kind == "FINANCIAL_FACT"
    assert question.request is not None
    assert question.request.fact_type == "PRODUCT_CONTRIBUTION_ELIGIBILITY_CONFIRMED"


def test_after_tax_objective_requests_missing_product_contribution():
    product = kakao_26_week_product()
    intent = _kakao_intent(objective=RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST, top_k=1)
    evaluations = MultiProductEvaluator().evaluate(
        [product], kakao_pre_subscription_user(), intent,
        as_of=AS_OF, subscription_date=SUBSCRIPTION_DATE,
    )
    question = RankingAwareQuestionPlanner().select_next(evaluations, intent)
    assert question is not None
    assert question.question_kind == "RANKING_INPUT"
    assert question.ranking_input is not None
    assert question.ranking_input.required_field == "preferred_start_amount"


def _range_intent():
    return make_intent(
        numeric_preferences=[
            NumericPreference(
                field="MONTHLY_CONTRIBUTION",
                value=Decimal("200000"),
                direction=NumericPreferenceDirection.AT_LEAST,
                strictness=PreferenceStrictness.HARD,
                currency="KRW",
            ),
            NumericPreference(
                field="MONTHLY_CONTRIBUTION",
                value=Decimal("300000"),
                direction=NumericPreferenceDirection.AT_MOST,
                strictness=PreferenceStrictness.HARD,
                currency="KRW",
            ),
        ]
    )


def _range_map(intent):
    return {(item.field, item.direction): item.value for item in intent.numeric_preferences}


def test_numeric_range_survives_patch():
    current = _range_intent()
    updated = IntentParser.apply_patch(current, IntentPatch())
    assert _range_map(updated) == _range_map(current)


def test_lower_bound_update_preserves_upper_bound():
    current = _range_intent()
    updated = IntentParser.apply_patch(
        current,
        IntentPatch(
            upsert_numeric_preferences=[
                NumericPreference(
                    field="MONTHLY_CONTRIBUTION",
                    value=Decimal("220000"),
                    direction=NumericPreferenceDirection.AT_LEAST,
                    strictness=PreferenceStrictness.HARD,
                    currency="KRW",
                )
            ]
        ),
    )
    values = _range_map(updated)
    assert values[("MONTHLY_CONTRIBUTION", NumericPreferenceDirection.AT_LEAST)] == Decimal("220000")
    assert values[("MONTHLY_CONTRIBUTION", NumericPreferenceDirection.AT_MOST)] == Decimal("300000")


def test_upper_bound_update_preserves_lower_bound():
    current = _range_intent()
    updated = IntentParser.apply_patch(
        current,
        IntentPatch(
            upsert_numeric_preferences=[
                NumericPreference(
                    field="MONTHLY_CONTRIBUTION",
                    value=Decimal("280000"),
                    direction=NumericPreferenceDirection.AT_MOST,
                    strictness=PreferenceStrictness.HARD,
                    currency="KRW",
                )
            ]
        ),
    )
    values = _range_map(updated)
    assert values[("MONTHLY_CONTRIBUTION", NumericPreferenceDirection.AT_LEAST)] == Decimal("200000")
    assert values[("MONTHLY_CONTRIBUTION", NumericPreferenceDirection.AT_MOST)] == Decimal("280000")


def test_same_field_same_direction_replaces_old_value():
    current = _range_intent()
    updated = IntentParser.apply_patch(
        current,
        IntentPatch(
            upsert_numeric_preferences=[
                NumericPreference(
                    field="MONTHLY_CONTRIBUTION",
                    value=Decimal("250000"),
                    direction=NumericPreferenceDirection.AT_LEAST,
                    strictness=PreferenceStrictness.HARD,
                    currency="KRW",
                )
            ]
        ),
    )
    lowers = [
        item for item in updated.numeric_preferences
        if item.field == "MONTHLY_CONTRIBUTION"
        and item.direction == NumericPreferenceDirection.AT_LEAST
    ]
    assert len(lowers) == 1 and lowers[0].value == Decimal("250000")


def test_ui_reason_comes_from_structured_reason_code():
    product = make_product("STRUCTURED-REASON", base_rate="4.0")
    service = ApplicationService([product], user_fact_stores={USER_ID: base_store()})
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    detail = service.get_product_recommendation_detail(
        session.search_session_id, product.product_id, include_explanation=False
    )
    codes = {entry.code for entry in detail.recommendation_reason.positive_entries}
    assert "HIGHEST_AFTER_TAX_INTEREST" in codes
    assert detail.recommendation_reason.positives == [
        entry.text for entry in detail.recommendation_reason.positive_entries
    ]


def test_llm_explanation_not_used_as_financial_source():
    class HallucinatingExplainer:
        def explain(self, detail):
            return "브랜드 신뢰도가 높고 금리는 99%라서 1위입니다."

    from eligibility.search.recommendation import RecommendationService

    product = make_product("PROSE-NOT-SOURCE", base_rate="4.0")
    recommendation_service = RecommendationService(explainer=HallucinatingExplainer())
    service = ApplicationService(
        [product],
        user_fact_stores={USER_ID: base_store()},
        recommendation_service=recommendation_service,
    )
    session = service.create_search_session(
        user_id=USER_ID,
        intent=make_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    detail = service.get_product_recommendation_detail(
        session.search_session_id, product.product_id, include_explanation=True
    )
    assert "99%" in detail.explanation
    assert detail.realizable_rate == Decimal("4.0")
    result = service.get_top_recommendations(session.search_session_id)
    assert result.top_products[0].realizable_rate == Decimal("4.0")
    assert result.top_products[0].product_id == product.product_id


def test_rest_product_specific_choice_isolated_from_global_plan():
    product = kakao_26_week_product()
    service = ApplicationService(
        [product], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    rest = RestApplicationAdapter(service)
    q = rest.handle("GET", f"/search-sessions/{session.search_session_id}/questions/next")
    assert q.status_code == 200 and q.body["question_kind"] == "RANKING_INPUT"
    response = rest.handle(
        "POST",
        f"/search-sessions/{session.search_session_id}/answers",
        {"question_id": q.body["question_id"], "answer": "10000"},
    )
    assert response.status_code == 200
    assert response.body["product_contribution_choices"][0]["product_id"] == product.product_id
    assert response.body["product_contribution_choices"][0]["value"] == "10000"
    intent = service.get_search_intent(session.search_session_id)
    assert intent.contribution_plan.desired_periodic_amount == Decimal("300000")
    recommendations = rest.handle(
        "GET", f"/search-sessions/{session.search_session_id}/recommendations"
    )
    assert recommendations.status_code == 200
    assert recommendations.body["top_products"][0]["product_id"] == product.product_id


def test_mcp_product_specific_choice_uses_shared_application_service():
    product = kakao_26_week_product()
    service = ApplicationService(
        [product], user_fact_stores={KAKAO_USER_ID: kakao_pre_subscription_user()}
    )
    session = service.create_search_session(
        user_id=KAKAO_USER_ID,
        intent=_kakao_intent(top_k=1),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    mcp = MCPToolAdapter(service)
    q = mcp.get_next_question(search_session_id=session.search_session_id)
    state = mcp.submit_user_fact(
        search_session_id=session.search_session_id,
        question_id=q["question_id"],
        answer="5000",
    )
    assert state["product_contribution_choices"][0]["value"] == "5000"
    assert service.get_product_contribution_choices(session.search_session_id)[0].value == Decimal("5000")
    intent = service.get_search_intent(session.search_session_id)
    assert intent.contribution_plan.desired_periodic_amount == Decimal("300000")
