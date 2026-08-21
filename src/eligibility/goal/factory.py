from __future__ import annotations

import hashlib
import json
from datetime import date

from eligibility.audit import AuditEventType, AuditSession
from eligibility.engine.temporal import project_remaining_opportunities, resolve_window
from eligibility.goal.models import GoalInstance
from eligibility.goal.tracker import GoalTracker
from eligibility.schema.enums import EvaluationPhase, EvaluationStatus, PeriodUnit
from eligibility.schema.evaluation import EvaluationContext, ProductEvaluation
from eligibility.schema.product import ProductDefinition
from eligibility.schema.rule import CountDistinctMonthsRule, CountDistinctPeriodsRule


class GoalFactory:
    def __init__(self, audit: AuditSession | None = None) -> None:
        self.audit = audit

    def create_from_evaluation(
        self,
        *,
        product: ProductDefinition,
        evaluation: ProductEvaluation,
        context: EvaluationContext,
        subscription_id: str,
        subscription_confirmed: bool,
        user_tracking_intent: bool,
        as_of: date | None = None,
    ) -> list[GoalInstance]:
        if not subscription_confirmed or not user_tracking_intent:
            return []
        result_by_rule_id = {
            result.rule_id: result for result in evaluation.preferential_rule_results
        }
        goals: list[GoalInstance] = []
        effective_as_of = as_of or max(context.as_of, context.subscription_date)

        for preferential in product.preferential_rules:
            rule = preferential.rule
            result = result_by_rule_id.get(rule.rule_id)
            if result is None or result.status != EvaluationStatus.ACHIEVABLE:
                continue
            if rule.evaluation_phase not in {
                EvaluationPhase.POST_SUBSCRIPTION,
                EvaluationPhase.BOTH,
            }:
                continue
            if not isinstance(rule, (CountDistinctPeriodsRule, CountDistinctMonthsRule)):
                continue
            future = rule.future_achievement
            if future is None or future.goal_template is None:
                continue

            period = (
                rule.period if isinstance(rule, CountDistinctPeriodsRule) else PeriodUnit.MONTH
            )
            window_start, deadline = resolve_window(rule.window, context)
            qualified_periods = list(
                result.evidence.get(
                    "qualifying_periods",
                    result.evidence.get("qualifying_months", []),
                )
            )
            projection = project_remaining_opportunities(
                window_start=window_start,
                deadline=deadline,
                as_of=effective_as_of,
                subscription_date=context.subscription_date,
                period=period,
                qualifying_periods=qualified_periods,
            )
            remaining_opportunities = projection.remaining_opportunities
            current_progress = int(result.progress.current if result.progress else 0)
            remaining_required, buffer, status = GoalTracker.derive_status(
                current_progress=current_progress,
                required_target=rule.expected,
                remaining_opportunities=remaining_opportunities,
                safety_threshold=future.goal_template.safety_threshold,
            )
            goal_id = self._goal_id(
                evaluation.user_id,
                product.product_id,
                subscription_id,
                rule.rule_id,
            )
            goal = GoalInstance(
                goal_id=goal_id,
                user_id=evaluation.user_id,
                product_id=product.product_id,
                subscription_id=subscription_id,
                rule_id=rule.rule_id,
                tracking_mode=future.goal_template.tracking_mode,
                metric=future.goal_template.metric,
                opportunity_unit=period,
                required_target=rule.expected,
                current_progress=current_progress,
                window_start=window_start,
                deadline=deadline,
                remaining_required=remaining_required,
                remaining_opportunities=remaining_opportunities,
                buffer=buffer,
                qualified_opportunity_keys=qualified_periods,
                rate_reward_pp=preferential.reward.value,
                status=status,
                safety_threshold=future.goal_template.safety_threshold,
                coverage_fact_domain=future.goal_template.coverage_fact_domain,
                coverage_institution=future.goal_template.coverage_institution,
                created_from_evaluation_id=evaluation.evaluation_id,
                alert_policy={
                    "buffer_safety_threshold": future.goal_template.safety_threshold
                },
            )
            goals.append(goal)
            if self.audit is not None:
                self.audit.emit(
                    "GOAL_FACTORY",
                    AuditEventType.GOAL_CREATED,
                    entity_refs={
                        "goal_id": goal.goal_id,
                        "product_id": product.product_id,
                        "rule_id": rule.rule_id,
                    },
                    input_data={
                        "evaluation_id": evaluation.evaluation_id,
                        "rule": rule,
                        "subscription_id": subscription_id,
                    },
                    output_data=goal,
                    payload={
                        "tracking_mode": goal.tracking_mode.value,
                        "metric": goal.metric,
                        "required_target": goal.required_target,
                        "current_progress": goal.current_progress,
                        "remaining_opportunities": goal.remaining_opportunities,
                        "buffer": goal.buffer,
                        "status": goal.status.value,
                    },
                )
        return goals

    @staticmethod
    def _goal_id(
        user_id: str,
        product_id: str,
        subscription_id: str,
        rule_id: str,
    ) -> str:
        payload = json.dumps(
            [user_id, product_id, subscription_id, rule_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"GOAL-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"
