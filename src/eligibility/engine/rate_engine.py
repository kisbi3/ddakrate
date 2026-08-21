from __future__ import annotations

from decimal import Decimal

from eligibility.schema.enums import EvaluationStatus, VerificationLevel
from eligibility.schema.evaluation import (
    AppliedReward,
    RateEvidenceBreakdown,
    RateSummary,
    RuleEvaluation,
)
from eligibility.schema.product import ProductDefinition


_AUTHORITATIVE_LEVELS = {
    VerificationLevel.INSTITUTION_VERIFIED,
    VerificationLevel.MYDATA_VERIFIED,
    VerificationLevel.DERIVED,
    # v0.3.1 compatibility; new evaluations should not emit this value.
    VerificationLevel.VERIFIED,
}


class RateEngine:
    """Calculate rate layers while preserving evidence semantics."""

    @staticmethod
    def calculate(
        product: ProductDefinition,
        preferential_results: list[RuleEvaluation],
        guard_results: list[RuleEvaluation],
    ) -> RateSummary:
        result_by_rule_id = {result.rule_id: result for result in preferential_results}

        guard_status = RateEngine._combine_guard_status(guard_results)
        rewards_blocked = guard_status == EvaluationStatus.UNSATISFIABLE
        guard_unknown = guard_status == EvaluationStatus.UNKNOWN

        verified_reward = Decimal("0")
        self_reported_reward = Decimal("0")
        future_action_reward = Decimal("0")
        unknown_reward = Decimal("0")
        applied: list[AppliedReward] = []

        for preferential_rule in product.preferential_rules:
            result = result_by_rule_id[preferential_rule.rule.rule_id]
            reward = preferential_rule.reward.value
            bucket = RateEngine._evidence_bucket(result)

            include_confirmed = (
                not rewards_blocked
                and not guard_unknown
                and result.status == EvaluationStatus.SATISFIED
                and bucket == "VERIFIED"
            )
            include_realizable = (
                not rewards_blocked
                and not guard_unknown
                and (
                    (result.status == EvaluationStatus.SATISFIED and bucket in {"VERIFIED", "SELF_REPORTED"})
                    or result.status == EvaluationStatus.ACHIEVABLE
                )
            )
            include_upper = (
                not rewards_blocked
                and result.status
                in {
                    EvaluationStatus.SATISFIED,
                    EvaluationStatus.ACHIEVABLE,
                    EvaluationStatus.UNKNOWN,
                }
            )

            if include_confirmed:
                verified_reward += reward
            elif include_realizable and bucket == "SELF_REPORTED":
                self_reported_reward += reward
            elif include_realizable and result.status == EvaluationStatus.ACHIEVABLE:
                future_action_reward += reward
            if include_upper and not include_realizable:
                unknown_reward += reward

            applied.append(
                AppliedReward(
                    rule_id=result.rule_id,
                    rule_name=result.rule_name,
                    status=result.status,
                    reward_pp=reward,
                    verification_level=result.verification_level,
                    evidence_levels=RateEngine._levels(result),
                    evidence_bucket=bucket,
                    included_in_confirmed=include_confirmed,
                    included_in_realizable=include_realizable,
                    included_in_user_specific_conditional_upper=include_upper,
                )
            )

        cap = product.preferential_rate_cap
        capped_verified = min(verified_reward, cap)
        remaining = max(Decimal("0"), cap - capped_verified)
        capped_self = min(self_reported_reward, remaining)
        remaining -= capped_self
        capped_future = min(future_action_reward, remaining)
        remaining -= capped_future
        capped_unknown = min(unknown_reward, remaining)

        confirmed_total = capped_verified
        realizable_total = capped_verified + capped_self + capped_future
        upper_total = realizable_total + capped_unknown

        return RateSummary(
            advertised_max_rate=product.advertised_max_rate,
            confirmed_rate=product.base_rate + confirmed_total,
            realizable_rate=product.base_rate + realizable_total,
            user_specific_conditional_upper_rate=product.base_rate + upper_total,
            preferential_cap=cap,
            guard_status=guard_status,
            applied_rewards=applied,
            evidence_breakdown=RateEvidenceBreakdown(
                base_rate=product.base_rate,
                verified_reward_pp=capped_verified,
                self_reported_reward_pp=capped_self,
                future_action_reward_pp=capped_future,
                unknown_conditional_reward_pp=capped_unknown,
            ),
        )

    @staticmethod
    def _levels(result: RuleEvaluation) -> list[VerificationLevel]:
        if result.evidence_levels:
            return list(dict.fromkeys(result.evidence_levels))
        if result.verification_level != VerificationLevel.UNKNOWN:
            return [result.verification_level]
        return []

    @staticmethod
    def _evidence_bucket(result: RuleEvaluation) -> str:
        if result.status == EvaluationStatus.UNSATISFIABLE:
            return "UNSATISFIABLE"
        levels = set(RateEngine._levels(result))
        if result.status == EvaluationStatus.ACHIEVABLE:
            return "FUTURE_ACTION"
        if result.status == EvaluationStatus.UNKNOWN:
            return "UNKNOWN"
        if VerificationLevel.SELF_REPORTED in levels or result.is_provisional:
            return "SELF_REPORTED"
        if result.verification_level == VerificationLevel.MIXED and not result.is_provisional:
            # Mixed institution/MyData/derived evidence is still authoritative.
            return "VERIFIED"
        if levels and levels <= _AUTHORITATIVE_LEVELS:
            return "VERIFIED"
        return "UNKNOWN"

    @staticmethod
    def _combine_guard_status(guard_results: list[RuleEvaluation]) -> EvaluationStatus:
        if not guard_results:
            return EvaluationStatus.SATISFIED
        statuses = [result.status for result in guard_results]
        if EvaluationStatus.UNSATISFIABLE in statuses:
            return EvaluationStatus.UNSATISFIABLE
        if EvaluationStatus.UNKNOWN in statuses:
            return EvaluationStatus.UNKNOWN
        if EvaluationStatus.ACHIEVABLE in statuses:
            return EvaluationStatus.ACHIEVABLE
        return EvaluationStatus.SATISFIED
