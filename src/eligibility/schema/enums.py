from __future__ import annotations

from enum import StrEnum


class EvaluationStatus(StrEnum):
    SATISFIED = "SATISFIED"
    ACHIEVABLE = "ACHIEVABLE"
    UNSATISFIABLE = "UNSATISFIABLE"
    UNKNOWN = "UNKNOWN"


class EvaluationTrustMode(StrEnum):
    """Deprecated v0.3.1 compatibility enum.

    v0.3.2 no longer changes financial semantics from a global trust mode.
    Evidence is accepted and labelled per Fact.  The enum remains importable so
    older callers do not break while they migrate away from the argument.
    """

    STRICT = "STRICT"
    MVP_PROVISIONAL = "MVP_PROVISIONAL"


class VerificationLevel(StrEnum):
    """User-facing evidence classification for an evaluated rule/result."""

    INSTITUTION_VERIFIED = "INSTITUTION_VERIFIED"
    MYDATA_VERIFIED = "MYDATA_VERIFIED"
    DERIVED = "DERIVED"
    SELF_REPORTED = "SELF_REPORTED"
    USER_INTENT = "USER_INTENT"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"
    # Compatibility with serialized v0.3.1 outputs. New code should emit one
    # of the precise verified variants above instead.
    VERIFIED = "VERIFIED"


class FactSemanticType(StrEnum):
    OBSERVED_FACT = "OBSERVED_FACT"
    OBSERVED_EVENT = "OBSERVED_EVENT"
    DERIVED_FACT = "DERIVED_FACT"
    SELF_REPORTED_FACT = "SELF_REPORTED_FACT"
    FUTURE_INTENT = "FUTURE_INTENT"


class FactSourceType(StrEnum):
    INSTITUTION_VERIFIED = "INSTITUTION_VERIFIED"
    MYDATA_VERIFIED = "MYDATA_VERIFIED"
    DERIVED = "DERIVED"
    USER_DECLARED = "USER_DECLARED"
    UNKNOWN = "UNKNOWN"


class ResolutionStrategy(StrEnum):
    QUERY_INSTITUTION = "QUERY_INSTITUTION"
    QUERY_MYDATA = "QUERY_MYDATA"
    DERIVE = "DERIVE"
    ASK_USER = "ASK_USER"
    UNRESOLVABLE = "UNRESOLVABLE"


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"


class ComparisonOperator(StrEnum):
    EQ = "EQ"
    NEQ = "NEQ"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    IN = "IN"
    NOT_IN = "NOT_IN"
    BETWEEN_INCLUSIVE = "BETWEEN_INCLUSIVE"


class EvaluationPhase(StrEnum):
    PRE_SUBSCRIPTION = "PRE_SUBSCRIPTION"
    POST_SUBSCRIPTION = "POST_SUBSCRIPTION"
    BOTH = "BOTH"


class AchievementMode(StrEnum):
    ACCUMULATIVE = "ACCUMULATIVE"
    CONSECUTIVE = "CONSECUTIVE"
    MAINTAIN_UNTIL_DEADLINE = "MAINTAIN_UNTIL_DEADLINE"
    ONE_TIME_BEFORE_DEADLINE = "ONE_TIME_BEFORE_DEADLINE"


class RulePurpose(StrEnum):
    ELIGIBILITY = "ELIGIBILITY"
    PREFERENTIAL_RATE = "PREFERENTIAL_RATE"
    GLOBAL_GUARD = "GLOBAL_GUARD"
    SUPPORTING = "SUPPORTING"


class RewardType(StrEnum):
    INTEREST_RATE = "INTEREST_RATE"


class RewardUnit(StrEnum):
    PERCENTAGE_POINT = "PERCENTAGE_POINT"


class ContextDateField(StrEnum):
    AS_OF = "as_of"
    SUBSCRIPTION_DATE = "subscription_date"
    MATURITY_DATE = "maturity_date"


class TimeExpressionType(StrEnum):
    LITERAL = "LITERAL"
    CONTEXT = "CONTEXT"
    START_OF_MONTH = "START_OF_MONTH"
    END_OF_MONTH = "END_OF_MONTH"
    ADD_DAYS = "ADD_DAYS"
    ADD_MONTHS = "ADD_MONTHS"
    ADD_YEARS = "ADD_YEARS"
    BUSINESS_DAY_OFFSET = "BUSINESS_DAY_OFFSET"


class EntityType(StrEnum):
    ACCOUNT_HOLDING_INTERVAL = "ACCOUNT_HOLDING_INTERVAL"
    SCHEDULED_OCCURRENCE = "SCHEDULED_OCCURRENCE"
    RECURRING_PAYMENT_EVENT = "RECURRING_PAYMENT_EVENT"


class PeriodUnit(StrEnum):
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"
    FLEXIBLE = "FLEXIBLE"


class FactSubjectMode(StrEnum):
    STORE_USER = "STORE_USER"
    EXPLICIT = "EXPLICIT"
    RELATED_PERSON = "RELATED_PERSON"


class TermUnit(StrEnum):
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"


class ScheduledOccurrenceMethod(StrEnum):
    AUTO_TRANSFER = "AUTO_TRANSFER"
    MANUAL_TRANSFER = "MANUAL_TRANSFER"
    DIRECT_ACTION = "DIRECT_ACTION"
    OTHER = "OTHER"


class ScheduledOccurrenceStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class AccountLifecycleEventType(StrEnum):
    OPENED = "OPENED"
    CANCELLED = "CANCELLED"
    WITHDRAWN = "WITHDRAWN"
    RENAMED = "RENAMED"
    CLOSED = "CLOSED"


class SaleStatus(StrEnum):
    UPCOMING = "UPCOMING"
    ON_SALE = "ON_SALE"
    SOLD_OUT = "SOLD_OUT"
    ENDED = "ENDED"
    SUSPENDED = "SUSPENDED"
    UNKNOWN = "UNKNOWN"


class ContributionFrequency(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    FLEXIBLE = "FLEXIBLE"


class ContributionMode(StrEnum):
    FIXED = "FIXED"
    FLEXIBLE = "FLEXIBLE"
    INCREMENTAL = "INCREMENTAL"


class SubscriptionChannel(StrEnum):
    MOBILE = "MOBILE"
    WEB = "WEB"
    BRANCH = "BRANCH"
    CALL_CENTER = "CALL_CENTER"
    PARTNER = "PARTNER"
    OTHER = "OTHER"


class InterestPaymentMethod(StrEnum):
    AT_MATURITY = "AT_MATURITY"
    MONTHLY = "MONTHLY"
    UPFRONT = "UPFRONT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class RecurringPaymentCategory(StrEnum):
    UTILITY = "UTILITY"
    TELECOM = "TELECOM"
    APARTMENT_MANAGEMENT = "APARTMENT_MANAGEMENT"
    INSURANCE = "INSURANCE"
    TAX_OR_PUBLIC_FEE = "TAX_OR_PUBLIC_FEE"
    OTHER = "OTHER"


class RecurringPaymentMethod(StrEnum):
    AUTO_DEBIT = "AUTO_DEBIT"
    CARD = "CARD"
    TRANSFER = "TRANSFER"
    OTHER = "OTHER"


class FxThresholdClassification(StrEnum):
    CLEARLY_ABOVE = "CLEARLY_ABOVE"
    NEAR_THRESHOLD = "NEAR_THRESHOLD"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"


class PreferenceValue(StrEnum):
    PREFER_PRESENT = "PREFER_PRESENT"
    NEUTRAL = "NEUTRAL"
    PREFER_ABSENT = "PREFER_ABSENT"


class HardConstraintValue(StrEnum):
    REQUIRE = "REQUIRE"
    EXCLUDE = "EXCLUDE"


class CapabilityState(StrEnum):
    CAN = "CAN"
    UNKNOWN = "UNKNOWN"
    CANNOT = "CANNOT"


class NumericPreferenceDirection(StrEnum):
    AT_LEAST = "AT_LEAST"
    AT_MOST = "AT_MOST"
    AROUND = "AROUND"


class PreferenceStrictness(StrEnum):
    SOFT = "SOFT"
    HARD = "HARD"


class FactRecordStatus(StrEnum):
    """Lifecycle state for append-only user facts.

    Superseded facts remain in the store for audit and replay, but deterministic
    resolution only considers ACTIVE facts.
    """

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class RankingObjective(StrEnum):
    MAX_ESTIMATED_AFTER_TAX_INTEREST = "MAX_ESTIMATED_AFTER_TAX_INTEREST"
    MAX_ESTIMATED_PRE_TAX_INTEREST = "MAX_ESTIMATED_PRE_TAX_INTEREST"
    MAX_REALIZABLE_RATE = "MAX_REALIZABLE_RATE"
    MIN_ACTION_BURDEN = "MIN_ACTION_BURDEN"
    BALANCED = "BALANCED"


class RankingComparability(StrEnum):
    """Whether a candidate can participate in the active ranking metric.

    For the default interest objective, COMPARABLE means the candidate has a
    deterministic KRW after-tax-interest value.  A rate percentage is never a
    substitute for a missing KRW amount.
    """

    COMPARABLE = "COMPARABLE"
    MISSING_CONTRIBUTION_INPUT = "MISSING_CONTRIBUTION_INPUT"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class RankingInputStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"
    DECLINED = "DECLINED"


class UserConditionStatus(StrEnum):
    """Session-local lifecycle of a canonical user variable."""

    NOT_ASKED = "NOT_ASKED"
    DECLINED = "DECLINED"
    DECLARED_FEASIBLE = "DECLARED_FEASIBLE"
    WILLING_UNSPECIFIED = "WILLING_UNSPECIFIED"
    ACKNOWLEDGED_UNKNOWN = "ACKNOWLEDGED_UNKNOWN"
    VERIFIED = "VERIFIED"


class PreSearchAnswerStatus(StrEnum):
    """Lifecycle state of one deterministic pre-search question.

    ``NOT_ASKED`` is deliberately distinct from a user explicitly answering
    that they do not know.  The latter closes the question for this session and
    is represented by ``ACKNOWLEDGED_UNKNOWN``.
    """

    NOT_ASKED = "NOT_ASKED"
    ANSWERED = "ANSWERED"
    ACKNOWLEDGED_UNKNOWN = "ACKNOWLEDGED_UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PreSearchWillingness(StrEnum):
    WILLING = "WILLING"
    UNWILLING = "UNWILLING"
    CONDITIONAL = "CONDITIONAL"


class SearchSessionStatus(StrEnum):
    INTENT_COLLECTION = "INTENT_COLLECTION"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CANDIDATES_RETRIEVED = "CANDIDATES_RETRIEVED"
    QUESTIONING = "QUESTIONING"
    RANKING_READY = "RANKING_READY"
    COMPLETED = "COMPLETED"


class IntentConflictType(StrEnum):
    CAPABILITY_CONTRADICTION = "CAPABILITY_CONTRADICTION"
    PREFERENCE_CONTRADICTION = "PREFERENCE_CONTRADICTION"
    HARD_CONSTRAINT_CONTRADICTION = "HARD_CONSTRAINT_CONTRADICTION"
    NUMERIC_HARD_CONTRADICTION = "NUMERIC_HARD_CONTRADICTION"
    HARD_SOFT_CONTRADICTION = "HARD_SOFT_CONTRADICTION"
    HARD_CAPABILITY_CONTRADICTION = "HARD_CAPABILITY_CONTRADICTION"
    INTENT_UPDATE_CONTRADICTION = "INTENT_UPDATE_CONTRADICTION"


class EligibilityBadge(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    PLAN_REQUIRED = "PLAN_REQUIRED"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
    INELIGIBLE = "INELIGIBLE"


class VerificationBadge(StrEnum):
    FINANCIAL_DATA_VERIFIED = "FINANCIAL_DATA_VERIFIED"
    USER_RESPONSE_INCLUDED = "USER_RESPONSE_INCLUDED"
    PLAN_BASED = "PLAN_BASED"
    ADDITIONAL_VERIFICATION_REQUIRED = "ADDITIONAL_VERIFICATION_REQUIRED"
