from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from eligibility.audit import canonical_hash
from eligibility.schema.enums import ContributionFrequency, ContributionMode, TermUnit
from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.product import (
    ContractTerm,
    ContributionCashflow,
    ContributionSchedulePlan,
    MonthlyContributionPlan,
    ProductDefinition,
)
from eligibility.schema.search import (
    ContributionPlan,
    ProductContributionChoice,
    ContributionProjection,
    ContributionOptionFeasibility,
    ContributionFeasibilityClarification,
    MissingRankingInput,
    PlannedCashflow,
)


CoreContributionPlan = MonthlyContributionPlan | ContributionSchedulePlan


@dataclass(frozen=True)
class PlannedContributionResult:
    projection: ContributionProjection
    core_plan: CoreContributionPlan | None
    missing_ranking_inputs: tuple[MissingRankingInput, ...] = ()
    not_comparable_reason: str | None = None


@dataclass(frozen=True)
class AffordabilityCheck:
    feasible: bool
    max_bucket_amount: Decimal | None
    affordability_limit: Decimal | None
    affordability_frequency: ContributionFrequency | None
    violation_bucket: str | None = None
    reason_code: str = "AFFORDABILITY_NOT_CONSTRAINED"


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def term_end(start: date, term: ContractTerm) -> date:
    if term.unit == TermUnit.MONTH:
        return add_months(start, term.value)
    if term.unit == TermUnit.YEAR:
        return add_months(start, term.value * 12)
    if term.unit == TermUnit.WEEK:
        return start + timedelta(weeks=term.value)
    return start + timedelta(days=term.value)


def _product_core_term(product: ProductDefinition) -> ContractTerm:
    if product.contract_term is not None:
        return product.contract_term
    assert product.contract_months is not None
    return ContractTerm(value=product.contract_months, unit=TermUnit.MONTH)


def _term_days(term: ContractTerm) -> Decimal:
    if term.unit == TermUnit.DAY:
        return Decimal(term.value)
    if term.unit == TermUnit.WEEK:
        return Decimal(term.value * 7)
    if term.unit == TermUnit.MONTH:
        return Decimal(term.value) * Decimal("30.4375")
    return Decimal(term.value) * Decimal("365")


def terms_equivalent(left: ContractTerm, right: ContractTerm, *, tolerance_days: Decimal = Decimal("2")) -> bool:
    return abs(_term_days(left) - _term_days(right)) <= tolerance_days


def resolve_term(product: ProductDefinition, plan: ContributionPlan | None) -> ContractTerm:
    """Resolve the product's actual selectable contract term.

    ``ContributionPlan.selected_term`` is a user preference, not authority to
    rewrite a fixed product term.  It is used only when ProductMetadata proves
    that the requested term is one of the product's selectable terms (or lies
    inside an explicit range).
    """

    core = _product_core_term(product)
    if plan is None or plan.selected_term_value is None:
        return core

    assert plan.selected_term_unit is not None
    requested = ContractTerm(value=plan.selected_term_value, unit=plan.selected_term_unit)
    metadata = product.metadata
    if metadata is None:
        return core
    if metadata.available_terms:
        for available in metadata.available_terms:
            if terms_equivalent(available, requested):
                return available
        return core
    if metadata.min_term is not None and metadata.max_term is not None:
        requested_days = _term_days(requested)
        if _term_days(metadata.min_term) <= requested_days <= _term_days(metadata.max_term):
            return requested
    return core


def build_evaluation_context(
    product: ProductDefinition,
    plan: ContributionPlan | None,
    *,
    as_of: date,
    subscription_date: date,
) -> EvaluationContext:
    term = resolve_term(product, plan)
    return EvaluationContext(
        as_of=as_of,
        subscription_date=subscription_date,
        maturity_date=term_end(subscription_date, term),
    )


def _term_count(term: ContractTerm, frequency: ContributionFrequency) -> int:
    if frequency == ContributionFrequency.MONTHLY:
        if term.unit == TermUnit.MONTH:
            return term.value
        if term.unit == TermUnit.YEAR:
            return term.value * 12
        if term.unit == TermUnit.WEEK:
            return max(1, round(term.value * 7 / 30.4375))
        return max(1, round(term.value / 30.4375))
    if frequency == ContributionFrequency.WEEKLY:
        if term.unit == TermUnit.WEEK:
            return term.value
        if term.unit == TermUnit.MONTH:
            return max(1, round(term.value * 30.4375 / 7))
        if term.unit == TermUnit.YEAR:
            return term.value * 52
        return max(1, round(term.value / 7))
    if frequency == ContributionFrequency.DAILY:
        if term.unit == TermUnit.DAY:
            return term.value
        if term.unit == TermUnit.WEEK:
            return term.value * 7
        if term.unit == TermUnit.MONTH:
            return max(1, round(term.value * 30.4375))
        return term.value * 365
    # FLEXIBLE is projected with the plan's own frequency when known; monthly is
    # the least surprising fallback for a monthly user plan.
    return _term_count(term, ContributionFrequency.MONTHLY)


def term_summary(term: ContractTerm) -> str:
    labels = {
        TermUnit.DAY: "일",
        TermUnit.WEEK: "주",
        TermUnit.MONTH: "개월",
        TermUnit.YEAR: "년",
    }
    return f"{term.value}{labels[term.unit]}"


def _money(value: Decimal | None) -> str:
    if value is None:
        return "확인 필요"
    return f"{int(value):,}원"


def apply_product_contribution_choices(
    product: ProductDefinition,
    global_plan: ContributionPlan | None,
    choices: list[ProductContributionChoice] | tuple[ProductContributionChoice, ...] = (),
) -> ContributionPlan | None:
    """Overlay product-scoped cashflow answers without mutating global intent state.

    The global plan continues to carry affordability / general periodic preference.
    Product-specific fields are applied only to the matching product.  When a
    product-specific periodic amount is supplied, its product contribution
    frequency becomes authoritative for that product so a monthly global amount
    is never reinterpreted as a weekly/daily amount.
    """

    relevant = [choice for choice in choices if choice.product_id == product.product_id]
    if not relevant:
        return global_plan

    payload = global_plan.model_dump(mode="python") if global_plan is not None else {}
    policy = product.metadata.contribution_policy if product.metadata is not None else None
    product_frequency = policy.contribution_frequency if policy is not None else None
    frequency_scoped = False

    for choice in sorted(relevant, key=lambda item: (item.answered_at, item.choice_id)):
        field = choice.field
        value = choice.value
        if field == "selected_term":
            if not isinstance(value, dict) or "value" not in value or "unit" not in value:
                raise ValueError("selected_term product choice must contain value and unit")
            payload["selected_term_value"] = int(value["value"])
            payload["selected_term_unit"] = str(value["unit"])
            continue
        if field == "desired_daily_amount":
            payload["desired_periodic_amount"] = Decimal(str(value))
            payload["frequency"] = ContributionFrequency.DAILY
            frequency_scoped = True
            continue
        if field == "desired_weekly_amount":
            payload["desired_periodic_amount"] = Decimal(str(value))
            payload["frequency"] = ContributionFrequency.WEEKLY
            frequency_scoped = True
            continue
        if field == "desired_monthly_amount":
            payload["desired_periodic_amount"] = Decimal(str(value))
            payload["frequency"] = ContributionFrequency.MONTHLY
            frequency_scoped = True
            continue
        if field in {"desired_periodic_amount", "preferred_start_amount", "incremental_amount"}:
            payload[field] = Decimal(str(value))
            frequency_scoped = True
            continue
        if field in ContributionPlan.model_fields:
            payload[field] = value
            continue
        raise ValueError(f"Unsupported product contribution choice field: {field}")

    if frequency_scoped and product_frequency is not None:
        payload["frequency"] = product_frequency

    return ContributionPlan.model_validate(payload)


class ContributionFeasibilityEvaluator:
    """Evaluate product cashflows against the user's global affordability cadence.

    The user's maximum is interpreted only in its declared global frequency.  A
    MONTHLY maximum is therefore compared with calendar-month cashflow totals,
    never with a weekly/daily per-event amount.
    """

    def check_projection(
        self,
        projection: ContributionProjection,
        global_plan: ContributionPlan | None,
    ) -> AffordabilityCheck:
        if (
            global_plan is None
            or global_plan.maximum_affordable_periodic_amount is None
            or global_plan.frequency is None
        ):
            return AffordabilityCheck(
                feasible=True,
                max_bucket_amount=None,
                affordability_limit=(
                    global_plan.maximum_affordable_periodic_amount
                    if global_plan is not None
                    else None
                ),
                affordability_frequency=(
                    global_plan.frequency if global_plan is not None else None
                ),
                reason_code="AFFORDABILITY_NOT_CONSTRAINED",
            )
        if not projection.cashflows:
            return AffordabilityCheck(
                feasible=False,
                max_bucket_amount=None,
                affordability_limit=global_plan.maximum_affordable_periodic_amount,
                affordability_frequency=global_plan.frequency,
                reason_code="CASHFLOW_UNAVAILABLE_FOR_AFFORDABILITY",
            )
        if any(item.contribution_date is None for item in projection.cashflows):
            return AffordabilityCheck(
                feasible=False,
                max_bucket_amount=None,
                affordability_limit=global_plan.maximum_affordable_periodic_amount,
                affordability_frequency=global_plan.frequency,
                reason_code="DATED_CASHFLOW_REQUIRED_FOR_AFFORDABILITY",
            )

        totals: dict[str, Decimal] = {}
        for item in projection.cashflows:
            assert item.contribution_date is not None
            bucket = self._bucket_key(
                item.contribution_date,
                global_plan.frequency,
                sequence_no=item.sequence_no,
            )
            totals[bucket] = totals.get(bucket, Decimal("0")) + item.amount

        max_bucket, max_amount = max(
            totals.items(),
            key=lambda pair: (pair[1], pair[0]),
        )
        limit = global_plan.maximum_affordable_periodic_amount
        violating = sorted(
            ((bucket, amount) for bucket, amount in totals.items() if amount > limit),
            key=lambda pair: (pair[0], pair[1]),
        )
        if violating:
            return AffordabilityCheck(
                feasible=False,
                max_bucket_amount=max_amount,
                affordability_limit=limit,
                affordability_frequency=global_plan.frequency,
                violation_bucket=violating[0][0],
                reason_code="GLOBAL_AFFORDABILITY_EXCEEDED",
            )
        return AffordabilityCheck(
            feasible=True,
            max_bucket_amount=max_amount,
            affordability_limit=limit,
            affordability_frequency=global_plan.frequency,
            violation_bucket=None,
            reason_code="GLOBAL_AFFORDABILITY_SATISFIED",
        )

    def analyze_ranking_input(
        self,
        product: ProductDefinition,
        global_plan: ContributionPlan | None,
        ranking_input: MissingRankingInput,
        *,
        planner: "ContributionPlanner",
        subscription_date: date,
    ) -> tuple[
        MissingRankingInput | None,
        list[ContributionOptionFeasibility],
        ContributionFeasibilityClarification | None,
    ]:
        """Prune deterministic infeasible options before the question is exposed.

        If selecting an option still leaves some *other* cashflow input unresolved,
        feasibility cannot yet be established and the original input is preserved.
        """

        if (
            not ranking_input.allowed_options
            or global_plan is None
            or global_plan.maximum_affordable_periodic_amount is None
            or global_plan.frequency is None
        ):
            return ranking_input, [], None

        results: list[ContributionOptionFeasibility] = []
        for option in ranking_input.allowed_options:
            choice = ProductContributionChoice(
                choice_id=f"TENTATIVE-{canonical_hash({'product': product.product_id, 'field': ranking_input.required_field, 'option': option})[:16]}",
                product_id=product.product_id,
                field=ranking_input.required_field,
                value=option,
                source_question_id=f"TENTATIVE-{ranking_input.input_id}",
                answered_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
            )
            try:
                effective_plan = apply_product_contribution_choices(
                    product,
                    global_plan,
                    [choice],
                )
                planned = planner.build(
                    product,
                    effective_plan,
                    subscription_date=subscription_date,
                )
            except (TypeError, ValueError):
                results.append(
                    ContributionOptionFeasibility(
                        product_id=product.product_id,
                        field=ranking_input.required_field,
                        option_value=option,
                        status="INFEASIBLE",
                        affordability_limit=global_plan.maximum_affordable_periodic_amount,
                        affordability_frequency=global_plan.frequency,
                        reason_code="PRODUCT_CONTRIBUTION_OPTION_INVALID",
                    )
                )
                continue

            unresolved_other = [
                item
                for item in planned.missing_ranking_inputs
                if item.required_field != ranking_input.required_field
            ]
            if planned.core_plan is None and unresolved_other:
                # Another answer is still needed before a real schedule exists;
                # do not invent an affordability result for this option.
                return ranking_input, [], None
            if planned.core_plan is None:
                results.append(
                    ContributionOptionFeasibility(
                        product_id=product.product_id,
                        field=ranking_input.required_field,
                        option_value=option,
                        status="INFEASIBLE",
                        affordability_limit=global_plan.maximum_affordable_periodic_amount,
                        affordability_frequency=global_plan.frequency,
                        reason_code=(
                            planned.not_comparable_reason
                            or "PRODUCT_CONTRIBUTION_OPTION_INVALID"
                        ),
                    )
                )
                continue

            check = self.check_projection(planned.projection, global_plan)
            results.append(
                ContributionOptionFeasibility(
                    product_id=product.product_id,
                    field=ranking_input.required_field,
                    option_value=option,
                    status="FEASIBLE" if check.feasible else "INFEASIBLE",
                    max_bucket_amount=check.max_bucket_amount,
                    affordability_limit=check.affordability_limit,
                    affordability_frequency=check.affordability_frequency,
                    violation_bucket=check.violation_bucket,
                    reason_code=check.reason_code,
                )
            )

        feasible_options = [
            item.option_value for item in results if item.status == "FEASIBLE"
        ]
        if feasible_options:
            filtered = ranking_input.model_copy(
                update={
                    "allowed_options": feasible_options,
                    "reason": (
                        ranking_input.reason
                        + " 사용자의 전역 납입 가능 한도를 초과하는 선택지는 제외했습니다."
                    ),
                    "question": self._feasible_option_question(
                        product,
                        ranking_input.required_field,
                        feasible_options,
                        global_plan,
                    ),
                },
                deep=True,
            )
            return filtered, results, None

        max_amounts = [
            item.max_bucket_amount
            for item in results
            if item.max_bucket_amount is not None
        ]
        minimum_required = min(max_amounts) if max_amounts else None
        identity = {
            "product_id": product.product_id,
            "field": ranking_input.required_field,
            "affordability": str(global_plan.maximum_affordable_periodic_amount),
            "frequency": global_plan.frequency.value,
        }
        clarification = ContributionFeasibilityClarification(
            clarification_id=f"CONTRIBUTION-FEASIBILITY-{canonical_hash(identity)[:16]}",
            product_id=product.product_id,
            current_affordability_amount=global_plan.maximum_affordable_periodic_amount,
            field=ranking_input.required_field,
            current_affordability_frequency=global_plan.frequency,
            feasible_options=[],
            minimum_required_affordability=minimum_required,
            question=self._all_infeasible_question(
                product,
                global_plan,
                minimum_required,
            ),
        )
        return None, results, clarification

    @staticmethod
    def _bucket_key(
        contribution_date: date,
        frequency: ContributionFrequency,
        *,
        sequence_no: int,
    ) -> str:
        if frequency == ContributionFrequency.DAILY:
            return contribution_date.isoformat()
        if frequency == ContributionFrequency.WEEKLY:
            iso = contribution_date.isocalendar()
            return f"{iso.year}-W{iso.week:02d}"
        if frequency == ContributionFrequency.MONTHLY:
            return f"{contribution_date.year:04d}-{contribution_date.month:02d}"
        return f"EVENT-{sequence_no:04d}"

    @staticmethod
    def _frequency_label(frequency: ContributionFrequency) -> str:
        return {
            ContributionFrequency.DAILY: "일",
            ContributionFrequency.WEEKLY: "주",
            ContributionFrequency.MONTHLY: "월",
            ContributionFrequency.FLEXIBLE: "회차",
        }[frequency]

    def _feasible_option_question(
        self,
        product: ProductDefinition,
        field: str,
        feasible_options: list,
        global_plan: ContributionPlan,
    ) -> str:
        limit = global_plan.maximum_affordable_periodic_amount
        assert limit is not None and global_plan.frequency is not None
        frequency_label = self._frequency_label(global_plan.frequency)
        rendered = ", ".join(self._render_option(item) for item in feasible_options)
        field_label = {
            "preferred_start_amount": "시작금액",
            "incremental_amount": "증액금액",
            "selected_term": "가입기간",
        }.get(field, "납입 선택")
        if len(feasible_options) == 1:
            return (
                f"현재 {frequency_label} 최대 {_money(limit)} 납입 가능 조건으로는 "
                f"{product.name}의 {field_label} {rendered} 선택만 가능합니다. 이 조건으로 진행할까요?"
            )
        return (
            f"{frequency_label} 최대 {_money(limit)} 납입 가능 조건을 기준으로 보면 "
            f"{product.name}에서는 {field_label} {rendered} 선택이 가능합니다. 얼마로 진행할까요?"
        )

    def _all_infeasible_question(
        self,
        product: ProductDefinition,
        global_plan: ContributionPlan,
        minimum_required: Decimal | None,
    ) -> str:
        assert global_plan.maximum_affordable_periodic_amount is not None
        assert global_plan.frequency is not None
        frequency_label = self._frequency_label(global_plan.frequency)
        minimum = (
            f" 가장 낮은 선택지를 유지하려면 {frequency_label} 최대 약 {_money(minimum_required)}까지 가능해야 합니다."
            if minimum_required is not None
            else ""
        )
        return (
            f"현재 설정한 {frequency_label} 최대 {_money(global_plan.maximum_affordable_periodic_amount)}으로는 "
            f"{product.name}의 납입 스케줄을 유지하기 어렵습니다.{minimum} "
            "납입 가능액을 조정해서 다시 볼까요, 아니면 이 상품은 제외할까요?"
        )

    @staticmethod
    def _render_option(option) -> str:
        try:
            return _money(Decimal(str(option)))
        except Exception:
            if isinstance(option, dict):
                return f"{option.get('value')} {option.get('unit')}"
            return str(option)


class ContributionPlanner:
    """Map a user plan to the product's real contribution pattern without maxing it out."""

    def build(
        self,
        product: ProductDefinition,
        plan: ContributionPlan | None,
        *,
        subscription_date: date | None = None,
    ) -> PlannedContributionResult:
        term = resolve_term(product, plan)
        metadata = product.metadata
        policy = metadata.contribution_policy if metadata is not None else None
        if policy is None:
            projection = ContributionProjection(
                term_summary=term_summary(term),
                contribution_summary="납입 방식 확인 필요",
                maximum_deposit_summary="상품 한도 확인 필요",
                planned_contribution_summary="납입계획 미계산",
                assumptions=["ProductMetadata.contribution_policy is unavailable."],
            )
            return PlannedContributionResult(
                projection=projection,
                core_plan=None,
                not_comparable_reason="PRODUCT_CONTRIBUTION_POLICY_UNAVAILABLE",
            )

        if (
            metadata is not None
            and len(metadata.available_terms) > 1
            and (plan is None or plan.selected_term_value is None)
        ):
            ranking_input = self._ranking_input(
                product,
                required_field="selected_term",
                allowed_options=[
                    {"value": item.value, "unit": item.unit.value}
                    for item in metadata.available_terms
                ],
                reason="상품의 선택 가능한 기간 중 실제 가입 기간이 필요합니다.",
                question=(
                    f"{product.name}은 여러 가입 기간을 선택할 수 있습니다. "
                    "어느 기간으로 가입할까요?"
                ),
            )
            projection = ContributionProjection(
                frequency=policy.contribution_frequency,
                maximum_periodic_amount=policy.periodic_amount_max,
                term_summary="가입 기간 선택 필요",
                contribution_summary=self._policy_summary(policy),
                maximum_deposit_summary=self._maximum_summary(policy),
                planned_contribution_summary="가입 기간 입력 후 납입계획 계산 가능",
                assumptions=["Selectable term was not guessed for interest ranking."],
            )
            return PlannedContributionResult(
                projection=projection,
                core_plan=None,
                missing_ranking_inputs=(ranking_input,),
            )

        frequency = policy.contribution_frequency or (
            plan.frequency if plan is not None else None
        )
        maximum = policy.periodic_amount_max
        maximum_summary = self._maximum_summary(policy)
        contribution_summary = self._policy_summary(policy)

        if plan is None or plan.desired_periodic_amount is None:
            if (
                policy.contribution_mode == ContributionMode.INCREMENTAL
                and frequency in {ContributionFrequency.WEEKLY, ContributionFrequency.DAILY}
                and policy.initial_amount_options
                and (plan is None or plan.preferred_start_amount is None)
            ):
                ranking_input = self._ranking_input(
                    product,
                    required_field="preferred_start_amount",
                    allowed_options=[str(item) for item in policy.initial_amount_options],
                    reason="점증식 상품의 실제 cashflow는 시작금액에 따라 달라집니다.",
                    question=self._start_amount_question(product, policy.initial_amount_options),
                )
                planned_summary = "시작금액 입력 필요"
            else:
                ranking_input = self._ranking_input(
                    product,
                    required_field="desired_periodic_amount",
                    allowed_options=[],
                    reason="예상 세후이자를 계산하려면 실제 납입금액이 필요합니다.",
                    question=self._periodic_amount_question(product, frequency),
                )
                planned_summary = "사용자 납입계획 미입력"
            projection = ContributionProjection(
                frequency=frequency,
                maximum_periodic_amount=maximum,
                term_summary=term_summary(term),
                contribution_summary=contribution_summary,
                maximum_deposit_summary=maximum_summary,
                planned_contribution_summary=planned_summary,
                assumptions=["Product maximum was not substituted for a missing user plan."],
            )
            return PlannedContributionResult(
                projection=projection,
                core_plan=None,
                missing_ranking_inputs=(ranking_input,),
            )

        desired = plan.desired_periodic_amount
        # A monthly amount must not silently become a daily or weekly amount.
        # Product-specific frequencies require an explicitly matching plan or,
        # for incremental products, an explicit preferred_start_amount.
        if (
            frequency in {ContributionFrequency.WEEKLY, ContributionFrequency.DAILY}
            and plan.frequency is not None
            and plan.frequency != frequency
            and plan.preferred_start_amount is None
        ):
            if (
                policy.contribution_mode == ContributionMode.INCREMENTAL
                and policy.initial_amount_options
            ):
                ranking_input = self._ranking_input(
                    product,
                    required_field="preferred_start_amount",
                    allowed_options=[str(item) for item in policy.initial_amount_options],
                    reason=(
                        "월 납입 희망액을 점증식 주간 상품의 시작금액으로 재해석할 수 없습니다."
                    ),
                    question=self._start_amount_question(product, policy.initial_amount_options),
                )
            else:
                ranking_input = self._ranking_input(
                    product,
                    required_field="desired_periodic_amount",
                    allowed_options=[],
                    reason="상품 납입주기에 맞는 실제 납입금액이 필요합니다.",
                    question=self._periodic_amount_question(product, frequency),
                )
            projection = ContributionProjection(
                frequency=frequency,
                maximum_periodic_amount=maximum,
                term_summary=term_summary(term),
                contribution_summary=contribution_summary,
                maximum_deposit_summary=maximum_summary,
                planned_contribution_summary=f"{frequency.value} 기준 납입금액 입력 필요",
                assumptions=[
                    "A contribution amount from another frequency was not reinterpreted."
                ],
            )
            return PlannedContributionResult(
                projection=projection,
                core_plan=None,
                missing_ranking_inputs=(ranking_input,),
            )

        if plan.preferred_start_amount is not None and frequency in {
            ContributionFrequency.WEEKLY,
            ContributionFrequency.DAILY,
        }:
            desired = plan.preferred_start_amount
        # Global affordability is a cross-frequency schedule constraint.  It must
        # never be reinterpreted as this product's per-event limit.  Product-level
        # min/max is applied here; global affordability is checked later against
        # the actual dated cashflow.
        planned = desired
        if maximum is not None:
            planned = min(planned, maximum)
        if policy.periodic_amount_min is not None and planned < policy.periodic_amount_min:
            projection = ContributionProjection(
                frequency=frequency,
                planned_periodic_amount=planned,
                maximum_periodic_amount=maximum,
                term_summary=term_summary(term),
                contribution_summary=contribution_summary,
                maximum_deposit_summary=maximum_summary,
                planned_contribution_summary=(
                    f"희망 {_money(planned)}은 상품 최소 {_money(policy.periodic_amount_min)} 미만"
                ),
                assumptions=["No amount was silently raised to the product minimum."],
            )
            ranking_input = self._ranking_input(
                product,
                required_field="desired_periodic_amount",
                allowed_options=[],
                reason="희망 납입액이 상품 최소 납입액보다 작아 계획을 확정할 수 없습니다.",
                question=self._periodic_amount_question(product, frequency),
            )
            return PlannedContributionResult(
                projection=projection,
                core_plan=None,
                missing_ranking_inputs=(ranking_input,),
            )

        if frequency == ContributionFrequency.MONTHLY or (
            frequency == ContributionFrequency.FLEXIBLE
            and (plan.frequency in {None, ContributionFrequency.MONTHLY})
        ):
            months = _term_count(term, ContributionFrequency.MONTHLY)
            core = MonthlyContributionPlan(
                monthly_amount=planned,
                months=months,
                tax_rate=plan.tax_rate,
            )
            total = planned * months
            maturity = term_end(subscription_date, term) if subscription_date is not None else None
            cashflows = []
            for index in range(1, months + 1):
                contribution_at = (
                    add_months(subscription_date, index - 1)
                    if subscription_date is not None
                    else None
                )
                days_held = (
                    max(0, (maturity - contribution_at).days)
                    if maturity is not None and contribution_at is not None
                    else max(0, round((months - index + 1) * 365 / 12))
                )
                cashflows.append(
                    PlannedCashflow(
                        sequence_no=index,
                        amount=planned,
                        days_held=days_held,
                        contribution_date=contribution_at,
                    )
                )
            projection = ContributionProjection(
                frequency=ContributionFrequency.MONTHLY,
                contribution_count=months,
                planned_periodic_amount=planned,
                maximum_periodic_amount=maximum,
                estimated_total_principal=total,
                term_summary=term_summary(term),
                contribution_summary=contribution_summary,
                maximum_deposit_summary=maximum_summary,
                planned_contribution_summary=f"월 {_money(planned)} 납입 계획",
                cashflows=cashflows,
                assumptions=["Monthly simple installment approximation."],
            )
            return PlannedContributionResult(projection=projection, core_plan=core)

        if frequency == ContributionFrequency.WEEKLY:
            return self._weekly(product, plan, term, planned, maximum_summary, contribution_summary, subscription_date)

        if frequency == ContributionFrequency.DAILY:
            return self._daily(plan, term, planned, maximum, maximum_summary, contribution_summary, subscription_date)

        projection = ContributionProjection(
            frequency=frequency,
            planned_periodic_amount=planned,
            maximum_periodic_amount=maximum,
            term_summary=term_summary(term),
            contribution_summary=contribution_summary,
            maximum_deposit_summary=maximum_summary,
            planned_contribution_summary="상품 납입주기와 사용자 계획 매핑 확인 필요",
            assumptions=["Unsupported contribution frequency was not coerced to monthly."],
        )
        return PlannedContributionResult(
            projection=projection,
            core_plan=None,
            not_comparable_reason="UNSUPPORTED_CONTRIBUTION_FREQUENCY_MAPPING",
        )

    def _weekly(
        self,
        product: ProductDefinition,
        plan: ContributionPlan,
        term: ContractTerm,
        planned: Decimal,
        maximum_summary: str,
        contribution_summary: str,
        subscription_date: date | None,
    ) -> PlannedContributionResult:
        policy = product.metadata.contribution_policy  # type: ignore[union-attr]
        assert policy is not None
        count = _term_count(term, ContributionFrequency.WEEKLY)
        if policy.contribution_mode == ContributionMode.INCREMENTAL:
            start = plan.preferred_start_amount
            if start is None and plan.frequency == ContributionFrequency.WEEKLY:
                start = planned
            if start is None:
                projection = ContributionProjection(
                    frequency=ContributionFrequency.WEEKLY,
                    contribution_count=count,
                    maximum_periodic_amount=policy.periodic_amount_max,
                    term_summary=term_summary(term),
                    contribution_summary=contribution_summary,
                    maximum_deposit_summary=maximum_summary,
                    planned_contribution_summary="시작금액 입력 필요",
                    assumptions=["Incremental products require an explicit preferred_start_amount."],
                )
                return PlannedContributionResult(
                    projection=projection,
                    core_plan=None,
                    missing_ranking_inputs=(
                        self._ranking_input(
                            product,
                            required_field="preferred_start_amount",
                            allowed_options=[str(item) for item in policy.initial_amount_options],
                            reason="점증식 상품의 실제 cashflow를 만들 시작금액이 필요합니다.",
                            question=self._start_amount_question(
                                product, policy.initial_amount_options
                            ),
                        ),
                    ),
                )
            if policy.initial_amount_options and start not in policy.initial_amount_options:
                projection = ContributionProjection(
                    frequency=ContributionFrequency.WEEKLY,
                    contribution_count=count,
                    initial_amount=start,
                    term_summary=term_summary(term),
                    contribution_summary=contribution_summary,
                    maximum_deposit_summary=maximum_summary,
                    planned_contribution_summary="허용되지 않은 시작금액",
                    assumptions=["Initial amount was not rounded to a permitted option."],
                )
                return PlannedContributionResult(
                    projection=projection,
                    core_plan=None,
                    missing_ranking_inputs=(
                        self._ranking_input(
                            product,
                            required_field="preferred_start_amount",
                            allowed_options=[str(item) for item in policy.initial_amount_options],
                            reason="선택한 시작금액이 상품의 허용 옵션이 아닙니다.",
                            question=self._start_amount_question(
                                product, policy.initial_amount_options
                            ),
                        ),
                    ),
                )
            increment = plan.incremental_amount or policy.increment_amount
            if increment is None and start in policy.increment_amount_options:
                increment = start
            if increment is None:
                projection = ContributionProjection(
                    frequency=ContributionFrequency.WEEKLY,
                    contribution_count=count,
                    initial_amount=start,
                    term_summary=term_summary(term),
                    contribution_summary=contribution_summary,
                    maximum_deposit_summary=maximum_summary,
                    planned_contribution_summary="증액금액 입력 필요",
                )
                return PlannedContributionResult(
                    projection=projection,
                    core_plan=None,
                    missing_ranking_inputs=(
                        self._ranking_input(
                            product,
                            required_field="incremental_amount",
                            allowed_options=[str(item) for item in policy.increment_amount_options],
                            reason="점증식 상품의 회차별 증액금액이 필요합니다.",
                            question=f"{product.name}의 회차별 증액금액을 얼마로 할까요?",
                        ),
                    ),
                )
            amounts = [start + increment * (index - 1) for index in range(1, count + 1)]
        else:
            start = planned
            increment = None
            amounts = [planned] * count

        schedule_violation = self._schedule_policy_violation(policy, amounts)
        if schedule_violation is not None:
            projection = ContributionProjection(
                frequency=ContributionFrequency.WEEKLY,
                contribution_count=count,
                initial_amount=start,
                increment_amount=increment,
                term_summary=term_summary(term),
                contribution_summary=contribution_summary,
                maximum_deposit_summary=maximum_summary,
                planned_contribution_summary="상품 납입한도 위반",
                assumptions=[schedule_violation],
            )
            return PlannedContributionResult(
                projection=projection,
                core_plan=None,
                not_comparable_reason=schedule_violation,
            )

        maturity = term_end(subscription_date, term) if subscription_date is not None else None
        dated_rows: list[tuple[date | None, Decimal, int]] = []
        for index, amount in enumerate(amounts, start=1):
            contribution_at = (
                subscription_date + timedelta(weeks=index - 1)
                if subscription_date is not None
                else None
            )
            days_held = (
                max(0, (maturity - contribution_at).days)
                if maturity is not None and contribution_at is not None
                else max(0, (count - index + 1) * 7)
            )
            dated_rows.append((contribution_at, amount, days_held))
        cashflows = [
            ContributionCashflow(amount=amount, days_held=days_held)
            for _, amount, days_held in dated_rows
        ]
        total = sum(amounts)
        core = ContributionSchedulePlan(
            cashflows=cashflows,
            tax_rate=plan.tax_rate,
            schedule_label="WEEKLY_INCREMENTAL" if increment is not None else "WEEKLY_FIXED",
        )
        projection = ContributionProjection(
            frequency=ContributionFrequency.WEEKLY,
            contribution_count=count,
            planned_periodic_amount=planned,
            initial_amount=start,
            increment_amount=increment,
            estimated_total_principal=total,
            term_summary=term_summary(term),
            contribution_summary=contribution_summary,
            maximum_deposit_summary=maximum_summary,
            planned_contribution_summary=(
                f"주 {_money(start)} 시작 · 매주 {_money(increment)} 증액"
                if increment is not None
                else f"주 {_money(planned)}"
            ),
            cashflows=[
                PlannedCashflow(
                    sequence_no=i,
                    amount=item.amount,
                    days_held=item.days_held,
                    contribution_date=dated_rows[i - 1][0],
                )
                for i, item in enumerate(cashflows, start=1)
            ],
            assumptions=[
                "Weekly contribution dates are anchored to subscription_date at 7-day intervals."
                if subscription_date is not None
                else "Weekly cashflows use a simple day-count approximation when no subscription_date is supplied."
            ],
        )
        return PlannedContributionResult(projection=projection, core_plan=core)

    @staticmethod
    def _daily(
        plan: ContributionPlan,
        term: ContractTerm,
        planned: Decimal,
        maximum: Decimal | None,
        maximum_summary: str,
        contribution_summary: str,
        subscription_date: date | None,
    ) -> PlannedContributionResult:
        count = _term_count(term, ContributionFrequency.DAILY)
        maturity = term_end(subscription_date, term) if subscription_date is not None else None
        dated_rows: list[tuple[date | None, Decimal, int]] = []
        for index in range(1, count + 1):
            contribution_at = (
                subscription_date + timedelta(days=index - 1)
                if subscription_date is not None
                else None
            )
            days_held = (
                max(0, (maturity - contribution_at).days)
                if maturity is not None and contribution_at is not None
                else max(0, count - index + 1)
            )
            dated_rows.append((contribution_at, planned, days_held))
        cashflows = [
            ContributionCashflow(amount=amount, days_held=days_held)
            for _, amount, days_held in dated_rows
        ]
        total = planned * count
        core = ContributionSchedulePlan(
            cashflows=cashflows,
            tax_rate=plan.tax_rate,
            schedule_label="DAILY_FIXED_OR_FLEXIBLE",
        )
        projection = ContributionProjection(
            frequency=ContributionFrequency.DAILY,
            contribution_count=count,
            planned_periodic_amount=planned,
            maximum_periodic_amount=maximum,
            estimated_total_principal=total,
            term_summary=term_summary(term),
            contribution_summary=contribution_summary,
            maximum_deposit_summary=maximum_summary,
            planned_contribution_summary=f"일 {_money(planned)}",
            cashflows=[
                PlannedCashflow(
                    sequence_no=i,
                    amount=item.amount,
                    days_held=item.days_held,
                    contribution_date=dated_rows[i - 1][0],
                )
                for i, item in enumerate(cashflows, start=1)
            ],
            assumptions=[
                "Daily contribution dates are anchored to subscription_date."
                if subscription_date is not None
                else "Daily cashflows use a simple day-count approximation when no subscription_date is supplied."
            ],
        )
        return PlannedContributionResult(projection=projection, core_plan=core)

    @staticmethod
    def _schedule_policy_violation(policy, amounts: list[Decimal]) -> str | None:
        if policy.periodic_amount_min is not None and any(
            amount < policy.periodic_amount_min for amount in amounts
        ):
            return "PRODUCT_PERIODIC_MIN_VIOLATION"
        if policy.periodic_amount_max is not None and any(
            amount > policy.periodic_amount_max for amount in amounts
        ):
            return "PRODUCT_PERIODIC_MAX_VIOLATION"
        if policy.total_principal_limit is not None and sum(amounts) > policy.total_principal_limit:
            return "PRODUCT_TOTAL_PRINCIPAL_LIMIT_VIOLATION"
        return None

    @staticmethod
    def _maximum_summary(policy) -> str:
        frequency = policy.contribution_frequency
        label = {
            ContributionFrequency.MONTHLY: "월 최대",
            ContributionFrequency.WEEKLY: "주 최대",
            ContributionFrequency.DAILY: "일 최대",
            ContributionFrequency.FLEXIBLE: "회차 최대",
            None: "최대",
        }[frequency]
        if policy.periodic_amount_max is not None:
            return f"{label} {_money(policy.periodic_amount_max)}"
        if policy.total_principal_limit is not None:
            return f"총 원금 한도 {_money(policy.total_principal_limit)}"
        if policy.initial_amount_options:
            return "시작금액 " + ", ".join(_money(item) for item in policy.initial_amount_options)
        return "상품 한도 확인 필요"

    @staticmethod
    def _policy_summary(policy) -> str:
        frequency = {
            ContributionFrequency.MONTHLY: "월",
            ContributionFrequency.WEEKLY: "주",
            ContributionFrequency.DAILY: "일",
            ContributionFrequency.FLEXIBLE: "자유",
            None: "주기 확인 필요",
        }[policy.contribution_frequency]
        mode = {
            ContributionMode.FIXED: "정액",
            ContributionMode.FLEXIBLE: "자유적립",
            ContributionMode.INCREMENTAL: "점증",
            None: "",
        }[policy.contribution_mode]
        if policy.contribution_mode == ContributionMode.INCREMENTAL:
            return f"{frequency}간 {mode} 납입"
        parts = [item for item in (frequency, mode, "납입") if item]
        return " ".join(parts)

    @staticmethod
    def _ranking_input(
        product: ProductDefinition,
        *,
        required_field: str,
        allowed_options: list,
        reason: str,
        question: str,
    ) -> MissingRankingInput:
        identity = {
            "product_id": product.product_id,
            "required_field": required_field,
            "allowed_options": allowed_options,
        }
        return MissingRankingInput(
            input_id=f"RANKING-INPUT-{canonical_hash(identity)[:16]}",
            product_id=product.product_id,
            required_field=required_field,
            allowed_options=allowed_options,
            reason=reason,
            question=question,
        )

    @staticmethod
    def _start_amount_question(
        product: ProductDefinition,
        options: list[Decimal],
    ) -> str:
        rendered = ", ".join(_money(item) for item in options)
        return (
            f"{product.name}은 시작금액에 따라 이후 납입액이 달라집니다. "
            f"{rendered} 중 얼마로 시작할까요?"
        )

    @staticmethod
    def _periodic_amount_question(
        product: ProductDefinition,
        frequency: ContributionFrequency | None,
    ) -> str:
        label = {
            ContributionFrequency.MONTHLY: "월",
            ContributionFrequency.WEEKLY: "주",
            ContributionFrequency.DAILY: "일",
            ContributionFrequency.FLEXIBLE: "회차",
            None: "회차",
        }[frequency]
        return f"{product.name}에 실제로 {label} 얼마를 납입할 계획인가요?"
