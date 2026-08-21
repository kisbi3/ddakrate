from __future__ import annotations

from eligibility.schema.enums import EvaluationStatus


def combine_and(statuses: list[EvaluationStatus]) -> tuple[EvaluationStatus, str]:
    if any(status == EvaluationStatus.UNSATISFIABLE for status in statuses):
        return EvaluationStatus.UNSATISFIABLE, "AND_CHILD_UNSATISFIABLE"
    if any(status == EvaluationStatus.UNKNOWN for status in statuses):
        return EvaluationStatus.UNKNOWN, "AND_CHILD_UNKNOWN"
    if any(status == EvaluationStatus.ACHIEVABLE for status in statuses):
        return EvaluationStatus.ACHIEVABLE, "AND_CHILD_ACHIEVABLE"
    return EvaluationStatus.SATISFIED, "ALL_AND_CHILDREN_SATISFIED"


def combine_or(statuses: list[EvaluationStatus]) -> tuple[EvaluationStatus, str]:
    if any(status == EvaluationStatus.SATISFIED for status in statuses):
        return EvaluationStatus.SATISFIED, "OR_CHILD_SATISFIED"
    if any(status == EvaluationStatus.ACHIEVABLE for status in statuses):
        return EvaluationStatus.ACHIEVABLE, "OR_CHILD_ACHIEVABLE"
    if any(status == EvaluationStatus.UNKNOWN for status in statuses):
        return EvaluationStatus.UNKNOWN, "OR_CHILD_UNKNOWN"
    return EvaluationStatus.UNSATISFIABLE, "ALL_OR_CHILDREN_UNSATISFIABLE"


def negate_status(status: EvaluationStatus) -> tuple[EvaluationStatus, str]:
    if status == EvaluationStatus.SATISFIED:
        return EvaluationStatus.UNSATISFIABLE, "NEGATED_SATISFIED_CHILD"
    if status == EvaluationStatus.UNSATISFIABLE:
        return EvaluationStatus.SATISFIED, "NEGATED_UNSATISFIABLE_CHILD"
    if status == EvaluationStatus.UNKNOWN:
        return EvaluationStatus.UNKNOWN, "NEGATED_UNKNOWN_CHILD"
    # ACHIEVABLE expresses future feasibility, not a current Boolean truth value.
    # Its negation cannot be determined without a temporal policy.
    return EvaluationStatus.UNKNOWN, "NEGATED_ACHIEVABLE_CHILD"
