from __future__ import annotations

import hashlib
import json

from eligibility.goal.models import (
    AlertEvent,
    AlertSeverity,
    AlertType,
    GoalInstance,
    GoalStatus,
)


class AlertEngine:
    """Deterministic alert trigger and severity mapping."""

    @staticmethod
    def from_goal(goal: GoalInstance) -> AlertEvent | None:
        if goal.data_sync_required:
            alert_type = AlertType.DATA_SYNC_REQUIRED
            severity = AlertSeverity.WARNING
            reason = "OBSERVATION_DATA_SYNC_UNAVAILABLE"
        elif goal.status == GoalStatus.COMPLETED:
            alert_type = AlertType.COMPLETED
            severity = AlertSeverity.INFO
            reason = "REQUIRED_TARGET_REACHED"
        elif goal.status == GoalStatus.FAILED:
            alert_type = AlertType.FAILED
            severity = AlertSeverity.CRITICAL
            reason = "INSUFFICIENT_REMAINING_OPPORTUNITIES"
        elif goal.status == GoalStatus.AT_RISK and goal.buffer == 0:
            alert_type = AlertType.CRITICAL
            severity = AlertSeverity.CRITICAL
            reason = "NO_REMAINING_BUFFER"
        elif goal.status == GoalStatus.AT_RISK:
            alert_type = AlertType.AT_RISK
            severity = AlertSeverity.WARNING
            reason = "BUFFER_WITHIN_SAFETY_THRESHOLD"
        else:
            return None

        payload = {
            "required_target": goal.required_target,
            "current_progress": goal.current_progress,
            "remaining_required": goal.remaining_required,
            "remaining_opportunities": goal.remaining_opportunities,
            "buffer": goal.buffer,
            "rate_reward_pp": str(goal.rate_reward_pp),
            "goal_status": goal.status.value,
            "data_sync_required": goal.data_sync_required,
            "coverage_fact_domain": goal.coverage_fact_domain,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(
            f"{goal.goal_id}:{alert_type.value}:{canonical}".encode("utf-8")
        ).hexdigest()[:20]
        return AlertEvent(
            alert_id=f"ALERT-{digest}",
            user_id=goal.user_id,
            product_id=goal.product_id,
            goal_id=goal.goal_id,
            alert_type=alert_type,
            severity=severity,
            trigger_reason=reason,
            deterministic_payload=payload,
        )
