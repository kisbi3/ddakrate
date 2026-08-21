from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from eligibility.application_service import ApplicationService
from eligibility.engine.interest_engine import InterestEngine
from eligibility.schema.enums import (
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    ResolutionStrategy,
)
from eligibility.schema.product import MonthlyContributionPlan
from eligibility.schema.user_fact import UserFact
from eligibility.search.evaluation import MultiProductEvaluator
from eligibility.search.questions import RankingAwareQuestionPlanner

from tests.v04_helpers import (
    AS_OF,
    SUBSCRIPTION_DATE,
    base_store,
    make_intent,
    make_or_short_circuit_product,
    make_product,
    verified_fact,
)


def _evaluate(products, store=None, intent=None):
    return MultiProductEvaluator().evaluate(
        products,
        store or base_store(),
        intent or make_intent(),
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )


def test_unsatisfiable_reward_excluded_from_upper_bound():
    product = make_product(
        "UNSAT-UPPER",
        base_rate="2.0",
        reward_pp="1.0",
        bonus_fact_type="UNSAT_ACTION",
    )
    store = base_store(
        UserFact(
            fact_id="UNSAT-ACTION-FALSE",
            user_id="V04-USER",
            fact_type="UNSAT_ACTION",
            value=False,
            valid_from=AS_OF,
            source_type=FactSourceType.USER_DECLARED,
            semantic_type=FactSemanticType.FUTURE_INTENT,
            collected_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
        )
    )

    candidate = _evaluate([product], store)[product.product_id]

    assert candidate.product_evaluation.preferential_rule_results[0].status == EvaluationStatus.UNSATISFIABLE
    assert candidate.realizable_rate == Decimal("2.0")
    assert candidate.user_specific_conditional_upper_rate == Decimal("2.0")


def test_unknown_reward_included_only_in_upper_bound():
    product = make_product(
        "UNKNOWN-UPPER",
        base_rate="2.0",
        reward_pp="1.0",
        bonus_fact_type="UNKNOWN_ACTION",
    )

    candidate = _evaluate([product])[product.product_id]

    assert candidate.product_evaluation.preferential_rule_results[0].status == EvaluationStatus.UNKNOWN
    assert candidate.confirmed_rate == Decimal("2.0")
    assert candidate.realizable_rate == Decimal("2.0")
    assert candidate.user_specific_conditional_upper_rate == Decimal("3.0")


def test_upper_bound_not_exposed_as_realizable():
    product = make_product("UPPER-NOT-REALIZABLE", base_rate="3", reward_pp="2")

    candidate = _evaluate([product])[product.product_id]

    assert candidate.realizable_rate == Decimal("3")
    assert candidate.user_specific_conditional_upper_rate == Decimal("5")
    assert candidate.realizable_after_tax_interest < candidate.conditional_upper_after_tax_interest


def test_upper_interest_uses_contribution_plan():
    product = make_product(
        "PLAN-UPPER",
        base_rate="2",
        reward_pp="1",
        periodic_max="500000",
    )
    intent = make_intent(desired_amount="100000", maximum_amount="500000")

    candidate = _evaluate([product], intent=intent)[product.product_id]
    expected = InterestEngine.estimate_installment_savings(
        MonthlyContributionPlan(
            monthly_amount=Decimal("100000"),
            months=12,
            tax_rate=Decimal("0.154"),
        ),
        Decimal("3"),
    )
    maxed = InterestEngine.estimate_installment_savings(
        MonthlyContributionPlan(
            monthly_amount=Decimal("500000"),
            months=12,
            tax_rate=Decimal("0.154"),
        ),
        Decimal("3"),
    )

    assert candidate.estimated_total_principal == Decimal("1200000")
    assert candidate.conditional_upper_after_tax_interest == expected.after_tax_interest
    assert candidate.conditional_upper_after_tax_interest < maxed.after_tax_interest


def test_candidate_frontier_includes_possible_top5_entry():
    products = [
        make_product(f"FRONT-{i}", base_rate=str(rate))
        for i, rate in enumerate((5.0, 4.8, 4.6, 4.4, 4.2), start=1)
    ]
    challenger = make_product(
        "FRONT-6",
        base_rate="3.0",
        reward_pp="2.0",
        bonus_fact_type="CHALLENGER_FACT",
    )
    products.append(challenger)
    intent = make_intent(top_k=5)
    evaluations = _evaluate(products, intent=intent)

    questions = RankingAwareQuestionPlanner().score_candidates(evaluations, intent)

    assert any(
        item.fact_type == "CHALLENGER_FACT"
        and "FRONT-6" in item.affected_product_ids
        for item in questions
    )


def test_question_selected_by_top5_ranking_impact():
    high = make_product(
        "QUESTION-HIGH",
        base_rate="3",
        reward_pp="2",
        bonus_fact_type="HIGH_IMPACT_FACT",
    )
    low = make_product(
        "QUESTION-LOW",
        base_rate="3",
        reward_pp="0.1",
        bonus_fact_type="LOW_IMPACT_FACT",
    )
    intent = make_intent(top_k=2)
    evaluations = _evaluate([high, low], intent=intent)

    question = RankingAwareQuestionPlanner().select_next(evaluations, intent)

    assert question is not None
    assert question.request.fact_type == "HIGH_IMPACT_FACT"


def test_shared_fact_updates_multiple_products():
    products = [
        make_product(
            "SHARED-A",
            base_rate="2.0",
            reward_pp="1.0",
            bonus_fact_type="SHARED_CAPABILITY",
        ),
        make_product(
            "SHARED-B",
            base_rate="2.5",
            reward_pp="0.5",
            bonus_fact_type="SHARED_CAPABILITY",
        ),
    ]
    intent = make_intent(top_k=2)
    service = ApplicationService(products, user_fact_stores={intent.user_id: base_store()})
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    question = service.get_next_question(session.search_session_id)

    assert question is not None
    assert set(question.affected_product_ids) == {"SHARED-A", "SHARED-B"}
    service.submit_user_answer(
        session.search_session_id,
        question_id=question.question_id,
        answer=True,
        answered_at=datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
    )
    evaluations = service.evaluate_candidates(session.search_session_id)

    assert evaluations["SHARED-A"].realizable_rate == Decimal("3.0")
    assert evaluations["SHARED-B"].realizable_rate == Decimal("3.0")


def test_or_short_circuit_suppresses_irrelevant_question():
    product = make_or_short_circuit_product()
    store = base_store(verified_fact("OR-TRUE", "OR_TRUE", True))
    intent = make_intent(top_k=1)
    evaluations = _evaluate([product], store=store, intent=intent)

    candidate = evaluations[product.product_id]
    assert candidate.product_evaluation.preferential_rule_results[0].status == EvaluationStatus.SATISFIED
    assert all(
        request.fact_type != "IRRELEVANT_OR_FACT"
        for request in candidate.product_evaluation.missing_facts
    )
    assert RankingAwareQuestionPlanner().select_next(evaluations, intent) is None


def test_no_fixed_question_budget_in_mvp():
    products = [
        make_product(
            f"BUDGET-{index}",
            base_rate=str(2 + index / 10),
            reward_pp="0.2",
            bonus_fact_type=f"BUDGET_FACT_{index}",
        )
        for index in range(1, 7)
    ]
    intent = make_intent(top_k=6)
    service = ApplicationService(products, user_fact_stores={intent.user_id: base_store()})
    session = service.create_search_session(
        user_id=intent.user_id,
        intent=intent,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )

    answered = 0
    while True:
        question = service.get_next_question(session.search_session_id)
        if question is None:
            break
        service.submit_user_answer(
            session.search_session_id,
            question_id=question.question_id,
            answer=False,
        )
        answered += 1
        assert answered <= 10

    assert answered == 6
    assert len(service.get_search_status(session.search_session_id).answered_question_ids) == 6


def test_irrelevant_lower_candidate_question_not_asked():
    top = [
        make_product(f"SAFE-{i}", base_rate=str(rate))
        for i, rate in enumerate((5.0, 4.8, 4.6, 4.4, 4.2), start=1)
    ]
    low = make_product(
        "IRRELEVANT-LOW",
        base_rate="1.0",
        reward_pp="0.1",
        bonus_fact_type="LOW_CANDIDATE_FACT",
    )
    intent = make_intent(top_k=5)
    evaluations = _evaluate([*top, low], intent=intent)

    questions = RankingAwareQuestionPlanner().score_candidates(evaluations, intent)

    assert all(item.fact_type != "LOW_CANDIDATE_FACT" for item in questions)


def test_conversational_exploration_can_include_plausible_lower_candidate():
    top = [
        make_product(f"EXPLORE-SAFE-{i}", base_rate=str(rate))
        for i, rate in enumerate((5.0, 4.8, 4.6, 4.4, 4.2), start=1)
    ]
    challenger = make_product(
        "EXPLORE-CHALLENGER",
        base_rate="1.0",
        reward_pp="0.1",
        bonus_fact_type="EXPLORATION_FACT",
    )
    intent = make_intent(top_k=5)
    evaluations = _evaluate([*top, challenger], intent=intent)

    questions = RankingAwareQuestionPlanner(
        exploration_depth_multiplier=2
    ).score_candidates(evaluations, intent)

    assert any(item.fact_type == "EXPLORATION_FACT" for item in questions)


def test_external_top_product_fact_becomes_self_reported_review_question():
    product = make_product(
        "EXTERNAL-ONLY",
        base_rate="2",
        reward_pp="1",
        bonus_fact_type="BANK_COUPON_VALID",
        bonus_question_strategy=ResolutionStrategy.QUERY_INSTITUTION,
    )
    intent = make_intent(top_k=1)
    evaluations = _evaluate([product], intent=intent)

    candidate = evaluations[product.product_id]
    assert candidate.product_evaluation.missing_facts[0].resolution_strategy == ResolutionStrategy.QUERY_INSTITUTION
    question = RankingAwareQuestionPlanner().select_next(evaluations, intent)
    assert question is not None and question.request is not None
    assert question.request.resolution_strategy == ResolutionStrategy.QUERY_INSTITUTION
    assert question.request.expected_semantic_type == FactSemanticType.SELF_REPORTED_FACT
