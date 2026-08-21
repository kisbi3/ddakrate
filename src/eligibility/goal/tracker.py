from __future__ import annotations

from datetime import date

from eligibility.audit import AuditEventType, AuditSession
from eligibility.engine.temporal import period_key, project_remaining_opportunities
from eligibility.goal.alert import AlertEngine
from eligibility.goal.models import GoalInstance, GoalStatus, GoalUpdateResult


class GoalTracker:
    def __init__(self, audit: AuditSession | None = None) -> None:
        self.audit = audit

    @staticmethod
    def derive_status(
        *,
        current_progress: int,
        required_target: int,
        remaining_opportunities: int,
        safety_threshold: int,
    ) -> tuple[int, int, GoalStatus]:
        remaining_required = max(0, required_target - current_progress)
        buffer = remaining_opportunities - remaining_required
        if current_progress >= required_target:
            return remaining_required, buffer, GoalStatus.COMPLETED
        if buffer < 0:
            return remaining_required, buffer, GoalStatus.FAILED
        if buffer == 0:
            return remaining_required, buffer, GoalStatus.AT_RISK
        if 0 < buffer <= safety_threshold:
            return remaining_required, buffer, GoalStatus.AT_RISK
        return remaining_required, buffer, GoalStatus.ACTIVE

    def update_accumulative(
        self,
        goal: GoalInstance,
        *,
        current_progress: int,
        as_of: date,
        remaining_opportunities: int | None = None,
        qualified_opportunity_keys: list[str] | None = None,
        coverage_complete: bool = True,
    ) -> GoalUpdateResult:
        if goal.tracking_mode.value != "ACCUMULATIVE":
            raise ValueError("update_accumulative only supports ACCUMULATIVE goals")
        if current_progress < goal.current_progress:
            raise ValueError("Accumulative goal progress cannot decrease")

        # Progress and qualifying-period keys are one trust boundary. When the
        # observation domain is not covered, neither the caller's requested
        # progress nor newly supplied bucket keys may become trusted Goal state.
        # Previously, a sync failure could preserve ``current_progress`` while
        # still appending the current bucket, thereby consuming a future
        # opportunity with no verified achievement.
        source_keys = (
            goal.qualified_opportunity_keys
            if not coverage_complete
            else (
                qualified_opportunity_keys
                if qualified_opportunity_keys is not None
                else goal.qualified_opportunity_keys
            )
        )
        keys = list(dict.fromkeys(source_keys))
        # Legacy callers may provide only a new progress count. When exactly one
        # new unit was observed in the current bucket, materialize that bucket so
        # it is not counted again as a future opportunity.
        if (
            coverage_complete
            and qualified_opportunity_keys is None
            and current_progress > goal.current_progress
            and goal.window_start <= as_of <= goal.deadline
        ):
            current_key = period_key(as_of, goal.opportunity_unit)
            if current_key not in keys:
                keys.append(current_key)

        if remaining_opportunities is None:
            projection = project_remaining_opportunities(
                window_start=goal.window_start,
                deadline=goal.deadline,
                as_of=as_of,
                subscription_date=goal.window_start,
                period=goal.opportunity_unit,
                qualifying_periods=keys,
            )
            opportunities = projection.remaining_opportunities
        else:
            opportunities = remaining_opportunities

        effective_progress = current_progress if coverage_complete else goal.current_progress
        remaining_required, buffer, status = self.derive_status(
            current_progress=effective_progress,
            required_target=goal.required_target,
            remaining_opportunities=opportunities,
            safety_threshold=goal.safety_threshold,
        )
        if not coverage_complete:
            status = GoalStatus.PAUSED

        updated = goal.model_copy(
            update={
                "current_progress": effective_progress,
                "remaining_required": remaining_required,
                "remaining_opportunities": opportunities,
                "buffer": buffer,
                "qualified_opportunity_keys": keys,
                "status": status,
                "data_sync_required": not coverage_complete,
            },
            deep=True,
        )

        if self.audit is not None:
            self.audit.emit(
                "GOAL_TRACKER",
                AuditEventType.GOAL_PROGRESS_UPDATED,
                entity_refs={"goal_id": goal.goal_id, "rule_id": goal.rule_id},
                input_data=goal,
                output_data=updated,
                payload={
                    "previous_progress": goal.current_progress,
                    "requested_progress": current_progress,
                    "current_progress": effective_progress,
                    "required_target": goal.required_target,
                    "coverage_complete": coverage_complete,
                },
            )
            self.audit.emit(
                "GOAL_TRACKER",
                AuditEventType.GOAL_FEASIBILITY_RECALCULATED,
                entity_refs={"goal_id": goal.goal_id, "rule_id": goal.rule_id},
                input_data={
                    "current_progress": effective_progress,
                    "required_target": goal.required_target,
                    "remaining_opportunities": opportunities,
                    "safety_threshold": goal.safety_threshold,
                    "qualified_opportunity_keys": keys,
                    "coverage_complete": coverage_complete,
                },
                output_data={
                    "remaining_required": remaining_required,
                    "buffer": buffer,
                    "status": status.value,
                },
                payload={
                    "remaining_required": remaining_required,
                    "remaining_opportunities": opportunities,
                    "buffer": buffer,
                    "status": status.value,
                    "coverage_complete": coverage_complete,
                },
            )

        alert = AlertEngine.from_goal(updated)
        if alert is not None and self.audit is not None:
            self.audit.emit(
                "ALERT_ENGINE",
                AuditEventType.ALERT_TRIGGERED,
                entity_refs={
                    "alert_id": alert.alert_id,
                    "goal_id": goal.goal_id,
                    "rule_id": goal.rule_id,
                },
                input_data=updated,
                output_data=alert,
                payload={
                    "alert_type": alert.alert_type.value,
                    "severity": alert.severity.value,
                    "trigger_reason": alert.trigger_reason,
                    **alert.deterministic_payload,
                },
            )
        return GoalUpdateResult(previous_goal=goal, goal=updated, alert=alert)
