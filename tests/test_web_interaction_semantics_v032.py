from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from eligibility.application import (
    QuestionGenerator,
    UserAnswerMapper,
    UserAnswerSubmission,
    submit_user_answer,
)
from eligibility.engine.evaluator import FinancialEligibilityEngine, RuleEvaluator
from eligibility.fixtures.future_goals import (
    salary_envelope_6m_product,
    salary_envelope_context,
    salary_envelope_user,
)
from eligibility.schema.enums import (
    ComparisonOperator,
    ContextDateField,
    EntityType,
    EvaluationStatus,
    EvaluationTrustMode,
    FactSemanticType,
    FactSourceType,
    ResolutionStrategy,
    RulePurpose,
    VerificationLevel,
)
from eligibility.schema.evaluation import EvaluationContext, MissingFactRequest
from eligibility.schema.product import PreferentialRateRule, ProductDefinition, Reward
from eligibility.schema.rule import (
    AndRule,
    CoverageRequirement,
    DateExpression,
    ExistenceAssertionFallback,
    FactComparisonRule,
    MissingFactSpec,
    NotExistsRule,
    OrRule,
    RateImpact,
    TimeWindow,
)
from eligibility.schema.user_fact import DataCoverage, UserFact, UserFactStore


USER_ID = "WEB-U001"


def _context() -> EvaluationContext:
    return EvaluationContext(
        as_of=date(2026, 8, 19),
        subscription_date=date(2026, 8, 20),
        maturity_date=date(2027, 8, 20),
    )


def _eligibility_fact() -> UserFact:
    return UserFact(
        fact_id="WEB-ELIGIBLE",
        user_id=USER_ID,
        fact_type="ELIGIBLE",
        value=True,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
        semantic_type=FactSemanticType.OBSERVED_FACT,
    )


def _absence_product() -> ProductDefinition:
    subscription = DateExpression.context(ContextDateField.SUBSCRIPTION_DATE)
    branch = NotExistsRule(
        rule_id="RATE_FIRST_TRANSACTION",
        name="직전 1년 신한 예적금 미보유",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        entity=EntityType.ACCOUNT_HOLDING_INTERVAL,
        filters={
            "institution": "SHINHAN_BANK",
            "product_type": ["TIME_DEPOSIT", "INSTALLMENT_SAVINGS", "HOUSING_SUBSCRIPTION"],
        },
        overlaps=TimeWindow(
            start=DateExpression.add_years(subscription, -1),
            end=DateExpression.add_days(subscription, -1),
        ),
        coverage=CoverageRequirement(
            fact_domain="SHINHAN_RELEVANT_HOLDING_HISTORY",
            institution="SHINHAN_BANK",
        ),
        self_report_fallback=ExistenceAssertionFallback(
            fact_type="SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y",
            missing_fact=MissingFactSpec(
                resolution_strategy=ResolutionStrategy.ASK_USER,
                question="최근 1년간 신한은행 정기예금·정기적금·주택청약을 보유한 적이 있나요?",
                expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                impact=RateImpact(rate_pp=Decimal("1.0")),
                reward_id="REWARD_FIRST_TRANSACTION",
                grounding_terms=["최근 1년", "신한은행"],
            ),
        ),
        true_reason_code="NO_RELEVANT_HOLDING_IN_LOOKBACK",
        false_reason_code="PRIOR_HOLDING_OVERLAPS_LOOKBACK",
    )
    return ProductDefinition(
        product_id="SHINHAN-SELF-REPORT-ABSENCE-DEMO",
        institution_id="SHINHAN_BANK",
        name="Self-report absence demo",
        product_type="INSTALLMENT_SAVINGS",
        contract_months=12,
        base_rate=Decimal("2.0"),
        advertised_max_rate=Decimal("3.0"),
        preferential_rate_cap=Decimal("1.0"),
        eligibility_rule=FactComparisonRule(
            rule_id="ELIG-WEB",
            name="가입 가능",
            purpose=RulePurpose.ELIGIBILITY,
            fact_type="ELIGIBLE",
            operator=ComparisonOperator.EQ,
            expected=True,
        ),
        preferential_rules=[PreferentialRateRule(rule=branch, reward=Reward(value=Decimal("1.0")))],
    )


def _self_report_store(had_holding: bool) -> UserFactStore:
    return UserFactStore(
        user_id=USER_ID,
        facts=[
            _eligibility_fact(),
            UserFact(
                fact_id=f"WEB-HISTORY-{had_holding}",
                user_id=USER_ID,
                fact_type="SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y",
                value=had_holding,
                valid_from=date(2026, 8, 19),
                source_type=FactSourceType.USER_DECLARED,
                semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            ),
        ],
    )


def _mydata_absence_store() -> UserFactStore:
    return UserFactStore(
        user_id=USER_ID,
        facts=[_eligibility_fact()],
        data_coverages=[
            DataCoverage(
                coverage_id="WEB-COV-HISTORY",
                user_id=USER_ID,
                fact_domain="SHINHAN_RELEVANT_HOLDING_HISTORY",
                institution="SHINHAN_BANK",
                covered_from=date(2025, 8, 20),
                covered_to=date(2026, 8, 19),
                source_type=FactSourceType.MYDATA_VERIFIED,
            )
        ],
    )


def test_user_answer_mapper_historical_yes_no_are_self_reported_facts():
    request = MissingFactRequest(
        missing_fact_id="MFR-HISTORY-001",
        fact_type="SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        requested_by_rule_id="RATE_FIRST_TRANSACTION",
        expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
    )
    mapper = UserAnswerMapper()
    for answer in (True, False):
        fact = mapper.map(
            request,
            UserAnswerSubmission(
                request_reference="MFR-HISTORY-001",
                answer=answer,
                answered_at=datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
            ),
            user_id=USER_ID,
        )
        assert fact.value is answer
        assert fact.semantic_type == FactSemanticType.SELF_REPORTED_FACT
        assert fact.source_type == FactSourceType.USER_DECLARED


def test_user_answer_mapper_future_yes_no_are_future_intent():
    request = MissingFactRequest(
        missing_fact_id="MFR-INTENT-001",
        fact_type="WILL_JOIN_SUPERSOL",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        requested_by_rule_id="RATE_SUPERSOL",
        expected_semantic_type=FactSemanticType.FUTURE_INTENT,
    )
    mapper = UserAnswerMapper()
    for answer in (True, False):
        fact = mapper.map(
            request,
            UserAnswerSubmission(
                request_reference="MFR-INTENT-001",
                answer=answer,
                answered_at=datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
            ),
            user_id=USER_ID,
        )
        assert fact.value is answer
        assert fact.semantic_type == FactSemanticType.FUTURE_INTENT
        assert fact.source_type == FactSourceType.USER_DECLARED


def test_missing_fact_web_answer_store_and_re_evaluation_self_reported_absence():
    product = _absence_product()
    initial_store = UserFactStore(user_id=USER_ID, facts=[_eligibility_fact()])
    first = FinancialEligibilityEngine().evaluate_product(product, initial_store, _context())
    rule = first.preferential_rule_results[0]
    assert rule.status == EvaluationStatus.UNKNOWN
    assert len(rule.missing_facts) == 1
    request = rule.missing_facts[0]
    assert request.expected_semantic_type == FactSemanticType.SELF_REPORTED_FACT

    ref = UserAnswerMapper.request_reference(request)
    updated_store, fact = submit_user_answer(
        initial_store,
        request,
        UserAnswerSubmission(
            request_reference=ref,
            answer=False,  # no matching holding existed
            answered_at=datetime(2026, 8, 19, 13, tzinfo=timezone.utc),
        ),
    )
    second = FinancialEligibilityEngine().evaluate_product(product, updated_store, _context())
    rule = second.preferential_rule_results[0]
    assert fact.semantic_type == FactSemanticType.SELF_REPORTED_FACT
    assert rule.status == EvaluationStatus.SATISFIED
    assert rule.verification_level == VerificationLevel.SELF_REPORTED
    assert rule.evidence["authoritative_coverage_fabricated"] is False
    assert updated_store.data_coverages == []
    assert second.rates.confirmed_rate == Decimal("2.0")
    assert second.rates.realizable_rate == Decimal("3.0")
    assert second.self_reported_rule_ids == ["RATE_FIRST_TRANSACTION"]


def test_self_reported_presence_deterministically_makes_absence_rule_unsatisfiable():
    result = FinancialEligibilityEngine().evaluate_product(
        _absence_product(), _self_report_store(True), _context()
    )
    rule = result.preferential_rule_results[0]
    assert rule.status == EvaluationStatus.UNSATISFIABLE
    assert rule.verification_level == VerificationLevel.SELF_REPORTED
    assert result.rates.confirmed_rate == Decimal("2.0")
    assert result.rates.realizable_rate == Decimal("2.0")


def test_mydata_complete_absence_is_verified_and_enters_confirmed_rate():
    result = FinancialEligibilityEngine().evaluate_product(
        _absence_product(), _mydata_absence_store(), _context()
    )
    rule = result.preferential_rule_results[0]
    assert rule.status == EvaluationStatus.SATISFIED
    assert rule.verification_level == VerificationLevel.MYDATA_VERIFIED
    assert result.rates.confirmed_rate == Decimal("3.0")
    assert result.rates.realizable_rate == Decimal("3.0")
    assert result.rates.evidence_breakdown.verified_reward_pp == Decimal("1.0")


def test_legacy_global_trust_mode_no_longer_changes_fact_semantics_or_identity():
    product = _absence_product()
    store = _self_report_store(False)
    strict = FinancialEligibilityEngine().evaluate_product(
        product, store, _context(), trust_mode=EvaluationTrustMode.STRICT
    )
    provisional = FinancialEligibilityEngine().evaluate_product(
        product, store, _context(), trust_mode=EvaluationTrustMode.MVP_PROVISIONAL
    )
    assert strict.evaluation_id == provisional.evaluation_id
    assert strict.preferential_rule_results[0].status == EvaluationStatus.SATISFIED
    assert provisional.preferential_rule_results[0].status == EvaluationStatus.SATISFIED
    assert strict.rates == provisional.rates
    assert not hasattr(strict, "trust_mode")


def test_future_intent_web_answer_re_evaluates_to_achievable_not_satisfied():
    product = salary_envelope_6m_product()
    store = salary_envelope_user()
    first = FinancialEligibilityEngine().evaluate_product(
        product, store, salary_envelope_context()
    )
    rule = first.preferential_rule_results[0]
    assert rule.status == EvaluationStatus.UNKNOWN
    request = rule.missing_facts[0]
    assert request.expected_semantic_type == FactSemanticType.FUTURE_INTENT

    updated, fact = submit_user_answer(
        store,
        request,
        UserAnswerSubmission(
            request_reference=UserAnswerMapper.request_reference(request),
            answer=True,
            answered_at=datetime(2026, 8, 19, 14, tzinfo=timezone.utc),
        ),
    )
    result = FinancialEligibilityEngine().evaluate_product(
        product, updated, salary_envelope_context()
    )
    rule = result.preferential_rule_results[0]
    assert fact.semantic_type == FactSemanticType.FUTURE_INTENT
    assert rule.status == EvaluationStatus.ACHIEVABLE
    assert rule.verification_level == VerificationLevel.USER_INTENT
    assert result.rates.confirmed_rate == Decimal("3.05")
    assert result.rates.realizable_rate == Decimal("4.05")
    assert result.user_intent_rule_ids == ["RATE_SALARY_ENVELOPE_6M"]


def test_question_fallback_distinguishes_historical_fact_and_future_intent():
    historical = MissingFactRequest(
        fact_type="PAST_HOLDING",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        requested_by_rule_id="R-HISTORY",
        expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
    )
    future = MissingFactRequest(
        fact_type="WILL_CHANGE_ACCOUNT",
        resolution_strategy=ResolutionStrategy.ASK_USER,
        requested_by_rule_id="R-FUTURE",
        expected_semantic_type=FactSemanticType.FUTURE_INTENT,
    )
    historical_q = QuestionGenerator().generate(historical)
    future_q = QuestionGenerator().generate(future)
    assert "과거 또는 현재" in historical_q
    assert "기관 검증 정보가 아닙니다" in historical_q
    assert "앞으로" in future_q
    assert "목표" in future_q
    assert "과거 또는 현재" not in future_q


def test_or_short_circuit_does_not_ask_irrelevant_second_branch():
    store = UserFactStore(
        user_id=USER_ID,
        facts=[
            UserFact(
                fact_id="OR-YES",
                user_id=USER_ID,
                fact_type="BRANCH_A",
                value=True,
                source_type=FactSourceType.INSTITUTION_VERIFIED,
            )
        ],
    )
    rule = OrRule(
        rule_id="OR-SHORT",
        name="OR short circuit",
        children=[
            FactComparisonRule(
                rule_id="OR-A",
                name="A",
                fact_type="BRANCH_A",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
            FactComparisonRule(
                rule_id="OR-B",
                name="B",
                fact_type="MISSING_B",
                operator=ComparisonOperator.EQ,
                expected=True,
                missing_fact=MissingFactSpec(
                    resolution_strategy=ResolutionStrategy.ASK_USER,
                    expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                ),
            ),
        ],
    )
    result = RuleEvaluator(store, _context()).evaluate(rule)
    assert result.status == EvaluationStatus.SATISFIED
    assert result.missing_facts == []
    assert [child.rule_id for child in result.children] == ["OR-A"]


def test_and_short_circuit_does_not_ask_when_outcome_already_unsatisfiable():
    store = UserFactStore(
        user_id=USER_ID,
        facts=[
            UserFact(
                fact_id="AND-NO",
                user_id=USER_ID,
                fact_type="HARD_BLOCK",
                value=False,
                source_type=FactSourceType.INSTITUTION_VERIFIED,
            )
        ],
    )
    rule = AndRule(
        rule_id="AND-SHORT",
        name="AND short circuit",
        children=[
            FactComparisonRule(
                rule_id="AND-A",
                name="A",
                fact_type="HARD_BLOCK",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
            FactComparisonRule(
                rule_id="AND-B",
                name="Future B",
                fact_type="WILL_DO_B",
                operator=ComparisonOperator.EQ,
                expected=True,
                required_semantic_type=FactSemanticType.FUTURE_INTENT,
                missing_fact=MissingFactSpec(
                    resolution_strategy=ResolutionStrategy.ASK_USER,
                    expected_semantic_type=FactSemanticType.FUTURE_INTENT,
                ),
            ),
        ],
    )
    result = RuleEvaluator(store, _context()).evaluate(rule)
    assert result.status == EvaluationStatus.UNSATISFIABLE
    assert result.missing_facts == []
    assert [child.rule_id for child in result.children] == ["AND-A"]


def test_rate_evidence_breakdown_separates_verified_self_report_future_and_unknown():
    from eligibility.engine.rate_engine import RateEngine
    from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
    from eligibility.schema.evaluation import RuleEvaluation

    product = shinhan_youth_first_product()
    statuses = [
        RuleEvaluation(
            rule_id="RATE_SALARY",
            rule_name="주거래 우대",
            status=EvaluationStatus.SATISFIED,
            reason_code="VERIFIED",
            verification_level=VerificationLevel.INSTITUTION_VERIFIED,
            evidence_levels=[VerificationLevel.INSTITUTION_VERIFIED],
        ),
        RuleEvaluation(
            rule_id="RATE_CARD",
            rule_name="신한카드 결제 우대",
            status=EvaluationStatus.SATISFIED,
            reason_code="SELF",
            verification_level=VerificationLevel.SELF_REPORTED,
            evidence_levels=[VerificationLevel.SELF_REPORTED],
            is_provisional=True,
        ),
        RuleEvaluation(
            rule_id="RATE_SUPERSOL",
            rule_name="신한 슈퍼SOL 우대",
            status=EvaluationStatus.ACHIEVABLE,
            reason_code="INTENT",
            verification_level=VerificationLevel.USER_INTENT,
            evidence_levels=[VerificationLevel.USER_INTENT],
        ),
        RuleEvaluation(
            rule_id="RATE_FIRST_OR_EVENT",
            rule_name="첫거래 또는 이벤트 우대",
            status=EvaluationStatus.UNKNOWN,
            reason_code="UNKNOWN",
        ),
    ]
    summary = RateEngine.calculate(product, statuses, [])
    assert summary.confirmed_rate == Decimal("4.05")
    assert summary.realizable_rate == Decimal("5.05")
    assert summary.user_specific_conditional_upper_rate == Decimal("6.05")
    assert summary.evidence_breakdown.verified_reward_pp == Decimal("1.0")
    assert summary.evidence_breakdown.self_reported_reward_pp == Decimal("0.5")
    assert summary.evidence_breakdown.future_action_reward_pp == Decimal("0.5")
    assert summary.evidence_breakdown.unknown_conditional_reward_pp == Decimal("1.0")
