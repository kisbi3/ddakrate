from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Iterable

from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.schema.application_input import Capability, HardConstraint, NumericPreference, Preference
from eligibility.schema.enums import (
    CapabilityState,
    ComparisonOperator,
    ContributionFrequency,
    ContributionMode,
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    HardConstraintValue,
    PreferenceStrictness,
    PreferenceValue,
    RankingObjective,
    ResolutionStrategy,
    RulePurpose,
    SaleStatus,
    SubscriptionChannel,
    TermUnit,
)
from eligibility.schema.product import (
    ContractTerm,
    ContributionPolicy,
    PreferentialRateRule,
    ProductDefinition,
    ProductMetadata,
    Reward,
)
from eligibility.schema.rule import (
    ActionDefinition,
    FactComparisonRule,
    MissingFactSpec,
    OrRule,
    RateImpact,
    SourceReference,
)
from eligibility.schema.search import ContributionPlan, IntentPatch, ProductSearchIntent
from eligibility.schema.user_fact import UserFact, UserFactStore
from eligibility.search.intent import IntentParser


USER_ID = "V04-USER"
AS_OF = date(2026, 8, 19)
SUBSCRIPTION_DATE = date(2026, 8, 20)


def verified_fact(
    fact_id: str,
    fact_type: str,
    value: object,
    *,
    user_id: str = USER_ID,
    semantic_type: FactSemanticType = FactSemanticType.OBSERVED_FACT,
    source_type: FactSourceType = FactSourceType.INSTITUTION_VERIFIED,
) -> UserFact:
    return UserFact(
        fact_id=fact_id,
        user_id=user_id,
        fact_type=fact_type,
        value=value,
        valid_from=AS_OF,
        source_type=source_type,
        semantic_type=semantic_type,
        collected_at=datetime(2026, 8, 19, 9, tzinfo=timezone.utc),
    )


def base_store(*facts: UserFact, user_id: str = USER_ID) -> UserFactStore:
    return UserFactStore(
        user_id=user_id,
        facts=[verified_fact("ELIGIBLE", "ELIGIBLE", True, user_id=user_id), *facts],
    )


def source(product_id: str, section: str = "synthetic") -> SourceReference:
    return SourceReference(
        document="v0.4 synthetic product fixture",
        document_id=f"DOC-{product_id}",
        version_date=date(2026, 8, 20),
        page=1,
        section=section,
        source_text=section,
    )


def make_product(
    product_id: str,
    *,
    institution_id: str = "TEST_BANK",
    name: str | None = None,
    product_type: str = "INSTALLMENT_SAVINGS",
    base_rate: str | Decimal = "2.0",
    reward_pp: str | Decimal = "0",
    preferential_cap: str | Decimal | None = None,
    bonus_fact_type: str | None = None,
    bonus_question_strategy: ResolutionStrategy = ResolutionStrategy.ASK_USER,
    bonus_semantic_type: FactSemanticType = FactSemanticType.FUTURE_INTENT,
    on_true_status: EvaluationStatus = EvaluationStatus.ACHIEVABLE,
    action_burden: int | None = None,
    term_value: int = 12,
    term_unit: TermUnit = TermUnit.MONTH,
    sale_status: SaleStatus = SaleStatus.ON_SALE,
    periodic_min: str | Decimal = "1000",
    periodic_max: str | Decimal = "300000",
    frequency: ContributionFrequency = ContributionFrequency.MONTHLY,
    contribution_mode: ContributionMode | None = ContributionMode.FLEXIBLE,
    allowed_channels: list[SubscriptionChannel] | None = None,
    rule_name: str | None = None,
) -> ProductDefinition:
    base = Decimal(str(base_rate))
    reward = Decimal(str(reward_pp))
    cap = Decimal(str(preferential_cap)) if preferential_cap is not None else reward
    term = ContractTerm(value=term_value, unit=term_unit)
    eligibility = FactComparisonRule(
        rule_id=f"ELIG-{product_id}",
        name="가입 가능",
        purpose=RulePurpose.ELIGIBILITY,
        fact_type="ELIGIBLE",
        operator=ComparisonOperator.EQ,
        expected=True,
        source=source(product_id, "eligibility"),
    )
    preferential_rules: list[PreferentialRateRule] = []
    if reward > 0:
        fact_type = bonus_fact_type or f"BONUS_{product_id}"
        missing = MissingFactSpec(
            resolution_strategy=bonus_question_strategy,
            impact=RateImpact(rate_pp=reward),
            required_source=(
                "USER" if bonus_question_strategy == ResolutionStrategy.ASK_USER else "INSTITUTION"
            ),
            question=f"{rule_name or product_id} 조건을 수행할 수 있나요? +{reward}%p",
            expected_semantic_type=(
                bonus_semantic_type
                if bonus_question_strategy == ResolutionStrategy.ASK_USER
                else None
            ),
            action_id=f"ACTION-{fact_type}",
            reward_id=f"REWARD-{product_id}",
            grounding_terms=[rule_name or "우대조건"],
        )
        rule = FactComparisonRule(
            rule_id=f"RATE-{product_id}",
            name=rule_name or f"{product_id} 우대",
            purpose=RulePurpose.PREFERENTIAL_RATE,
            fact_type=fact_type,
            operator=ComparisonOperator.EQ,
            expected=True,
            on_true_status=on_true_status,
            on_false_status=EvaluationStatus.UNSATISFIABLE,
            missing_fact=missing,
            required_semantic_type=bonus_semantic_type,
            action=(
                ActionDefinition(
                    action_id=f"ACTION-{fact_type}",
                    description=f"{rule_name or product_id} 조건 관리",
                    burden_score=action_burden,
                )
                if action_burden is not None
                else None
            ),
            source=source(product_id, rule_name or "preferential"),
        )
        preferential_rules.append(
            PreferentialRateRule(rule=rule, reward=Reward(value=reward))
        )

    metadata = ProductMetadata(
        institution_id=institution_id,
        product_id=product_id,
        product_name=name or product_id,
        product_type=product_type,
        sale_status=sale_status,
        min_term=term,
        max_term=term,
        available_terms=[term],
        contribution_policy=ContributionPolicy(
            periodic_amount_min=Decimal(str(periodic_min)),
            periodic_amount_max=Decimal(str(periodic_max)),
            contribution_frequency=frequency,
            contribution_mode=contribution_mode,
        ),
        base_rate=base,
        advertised_max_rate=base + cap,
        preferential_rate_cap=cap,
        allowed_channels=allowed_channels or [SubscriptionChannel.MOBILE],
        effective_from=date(2026, 8, 20),
        source_reference=source(product_id, "metadata"),
    )
    return ProductDefinition(
        product_id=product_id,
        institution_id=institution_id,
        name=name or product_id,
        product_type=product_type,
        contract_term=term,
        base_rate=base,
        advertised_max_rate=base + cap,
        preferential_rate_cap=cap,
        eligibility_rule=eligibility,
        preferential_rules=preferential_rules,
        metadata=metadata,
    )


def make_or_short_circuit_product(product_id: str = "OR-SHORT") -> ProductDefinition:
    product = make_product(product_id, base_rate="2", reward_pp="0")
    true_branch = FactComparisonRule(
        rule_id=f"{product_id}-TRUE",
        name="이미 충족된 경로",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        fact_type="OR_TRUE",
        operator=ComparisonOperator.EQ,
        expected=True,
        source=source(product_id, "true branch"),
    )
    unknown_branch = FactComparisonRule(
        rule_id=f"{product_id}-UNKNOWN",
        name="불필요한 대체 경로",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        fact_type="IRRELEVANT_OR_FACT",
        operator=ComparisonOperator.EQ,
        expected=True,
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.ASK_USER,
            impact=RateImpact(rate_pp=Decimal("1")),
            question="불필요한 대체 경로를 수행할 수 있나요? +1.0%p",
            expected_semantic_type=FactSemanticType.FUTURE_INTENT,
            grounding_terms=["대체 경로"],
        ),
        source=source(product_id, "unknown branch"),
    )
    or_rule = OrRule(
        rule_id=f"RATE-{product_id}",
        name="한 경로만 충족하면 되는 우대",
        purpose=RulePurpose.PREFERENTIAL_RATE,
        children=[true_branch, unknown_branch],
        source=source(product_id, "or"),
    )
    cap = Decimal("1")
    metadata = product.metadata.model_copy(
        update={
            "advertised_max_rate": product.base_rate + cap,
            "preferential_rate_cap": cap,
        },
        deep=True,
    )
    return product.model_copy(
        update={
            "advertised_max_rate": product.base_rate + cap,
            "preferential_rate_cap": cap,
            "preferential_rules": [
                PreferentialRateRule(rule=or_rule, reward=Reward(value=cap))
            ],
            "metadata": metadata,
        },
        deep=True,
    )


def make_intent(
    *,
    user_id: str = USER_ID,
    objective: RankingObjective = RankingObjective.MAX_ESTIMATED_AFTER_TAX_INTEREST,
    desired_amount: str | Decimal | None = "300000",
    maximum_amount: str | Decimal | None = "500000",
    top_k: int = 5,
    hard_constraints: Iterable[HardConstraint] = (),
    preferences: Iterable[Preference] = (),
    capabilities: Iterable[Capability] = (),
    numeric_preferences: Iterable[NumericPreference] = (),
    selected_term_value: int | None = 12,
    selected_term_unit: TermUnit | None = TermUnit.MONTH,
    frequency: ContributionFrequency | None = ContributionFrequency.MONTHLY,
) -> ProductSearchIntent:
    plan = None
    if desired_amount is not None or maximum_amount is not None or selected_term_value is not None:
        plan = ContributionPlan(
            desired_periodic_amount=(
                Decimal(str(desired_amount)) if desired_amount is not None else None
            ),
            maximum_affordable_periodic_amount=(
                Decimal(str(maximum_amount)) if maximum_amount is not None else None
            ),
            frequency=frequency,
            selected_term_value=selected_term_value,
            selected_term_unit=selected_term_unit,
        )
    return ProductSearchIntent(
        search_intent_id="INTENT-V04-TEST",
        user_id=user_id,
        ranking_objective=objective,
        hard_constraints=list(hard_constraints),
        preferences=list(preferences),
        capabilities=list(capabilities),
        numeric_preferences=list(numeric_preferences),
        contribution_plan=plan,
        requested_top_k=top_k,
        source_utterances=["synthetic v0.4 test"],
    )


def evaluate_product(
    product: ProductDefinition,
    store: UserFactStore | None = None,
) -> object:
    from eligibility.search.contribution import build_evaluation_context, ContributionPlanner

    intent = make_intent(user_id=(store.user_id if store else USER_ID))
    context = build_evaluation_context(
        product,
        intent.contribution_plan,
        as_of=AS_OF,
        subscription_date=SUBSCRIPTION_DATE,
    )
    planned = ContributionPlanner().build(product, intent.contribution_plan)
    return FinancialEligibilityEngine().evaluate_product(
        product,
        store or base_store(),
        context,
        contribution_plan=planned.core_plan,
    )


class ScriptedIntentPatchGateway:
    """Stub LLM gateway for ``IntentParser`` that returns caller-supplied patches.

    Real natural-language understanding is deliberately unavailable without a
    configured LLM gateway (see ``IntentParser.parse``/``update``). Tests that
    only care about the deterministic behavior *after* an intent change (
    re-ranking, exclusion, adapter parity, etc.) can use this stub to stand in
    for the LLM: queue up the ``IntentPatch`` each expected utterance should
    produce, and each call to ``generate_structured`` pops the next one.

    This does not simulate natural-language understanding itself -- it lets a
    scripted patch flow through the real ``IntentParser``/``ApplicationService``
    code path so the deterministic assertions downstream still exercise real
    code.
    """

    def __init__(self, patches: Iterable[IntentPatch] = (), *, default: IntentPatch | None = None):
        self._queue: list[IntentPatch] = list(patches)
        self._default = default
        self.calls: list[tuple[tuple, dict]] = []

    def queue(self, patch: IntentPatch) -> None:
        self._queue.append(patch)

    def generate_structured(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if not self._queue and self._default is not None:
            return SimpleNamespace(data=self._default)
        if not self._queue:
            raise AssertionError(
                "ScriptedIntentPatchGateway received a generate_structured call "
                "with no queued IntentPatch left"
            )
        return SimpleNamespace(data=self._queue.pop(0))


def intent_from_patch(
    patch: IntentPatch,
    *,
    user_id: str = USER_ID,
    base: ProductSearchIntent | None = None,
) -> ProductSearchIntent:
    """Deterministically build a ``ProductSearchIntent`` from a patch.

    Equivalent to what ``IntentParser.parse``/``update`` would produce given an
    LLM that returned exactly ``patch``, without requiring a gateway. Useful
    wherever a test's intent change can be expressed as a structured call
    (``create_search_session(intent=...)`` / ``update_search_intent(patch=...)``)
    instead of a natural-language utterance.
    """

    baseline = base or ProductSearchIntent(
        search_intent_id="INTENT-FROM-PATCH",
        user_id=user_id,
    )
    return IntentParser.apply_patch(baseline, patch)
