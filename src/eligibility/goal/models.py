from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eligibility.schema.enums import AchievementMode, PeriodUnit


class GoalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    AT_RISK = "AT_RISK"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


class AlertType(StrEnum):
    ACTION_REQUIRED = "ACTION_REQUIRED"
    UPCOMING_OCCURRENCE = "UPCOMING_OCCURRENCE"
    PROGRESS_UPDATE = "PROGRESS_UPDATE"
    DEADLINE_APPROACHING = "DEADLINE_APPROACHING"
    AT_RISK = "AT_RISK"
    CRITICAL = "CRITICAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DATA_SYNC_REQUIRED = "DATA_SYNC_REQUIRED"


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class GoalInstance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_id: str

    user_id: str
    product_id: str
    subscription_id: str
    rule_id: str

    tracking_mode: AchievementMode
    metric: str
    opportunity_unit: PeriodUnit
    required_target: int = Field(ge=0)
    current_progress: int = Field(ge=0)

    window_start: date
    deadline: date

    remaining_required: int = Field(ge=0)
    remaining_opportunities: int = Field(ge=0)
    buffer: int
    qualified_opportunity_keys: list[str] = Field(default_factory=list)

    rate_reward_pp: Decimal = Field(ge=0)
    status: GoalStatus
    safety_threshold: int = Field(default=0, ge=0)

    coverage_fact_domain: str | None = None
    coverage_institution: str | None = None
    data_sync_required: bool = False

    created_from_evaluation_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    next_check_at: datetime | None = None
    alert_policy: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window_and_progress(self) -> "GoalInstance":
        if self.window_start > self.deadline:
            raise ValueError("window_start must not be after deadline")
        expected_remaining = max(0, self.required_target - self.current_progress)
        if self.remaining_required != expected_remaining:
            raise ValueError(
                "remaining_required must equal max(0, required_target-current_progress)"
            )
        if self.buffer != self.remaining_opportunities - self.remaining_required:
            raise ValueError("buffer must equal remaining_opportunities-remaining_required")
        if len(set(self.qualified_opportunity_keys)) != len(self.qualified_opportunity_keys):
            raise ValueError("qualified_opportunity_keys must be unique")
        if self.data_sync_required and self.status != GoalStatus.PAUSED:
            raise ValueError("data_sync_required goals must be PAUSED")
        return self


class AlertEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    alert_id: str
    user_id: str
    product_id: str
    goal_id: str

    alert_type: AlertType
    severity: AlertSeverity
    trigger_reason: str

    deterministic_payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GoalUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    previous_goal: GoalInstance
    goal: GoalInstance
    alert: AlertEvent | None = None
