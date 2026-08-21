from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, TypeVar

from eligibility.schema.enums import (
    EvaluationTrustMode,
    FactSemanticType,
    FactSourceType,
    VerificationLevel,
)
from eligibility.schema.rule import FactAcceptancePolicy


class AuthorityBearingRecord(Protocol):
    source_type: FactSourceType
    semantic_type: FactSemanticType


T = TypeVar("T", bound=AuthorityBearingRecord)


@dataclass(frozen=True)
class AcceptanceResult:
    accepted: tuple[AuthorityBearingRecord, ...]
    rejected: tuple[AuthorityBearingRecord, ...]
    self_reported_record_ids: tuple[str, ...]
    evidence_levels: tuple[VerificationLevel, ...]
    verification_level: VerificationLevel

    @property
    def provisional_record_ids(self) -> tuple[str, ...]:
        """v0.3.1 compatibility alias."""

        return self.self_reported_record_ids

    @property
    def is_provisional(self) -> bool:
        return VerificationLevel.SELF_REPORTED in self.evidence_levels


def _record_id(record: AuthorityBearingRecord) -> str:
    for field in ("fact_id", "occurrence_id", "event_id"):
        value = getattr(record, field, None)
        if isinstance(value, str):
            return value
    return repr(record)


def verification_for_record(record: AuthorityBearingRecord) -> VerificationLevel:
    """Classify evidence without changing the financial status it may support."""

    if record.semantic_type == FactSemanticType.FUTURE_INTENT:
        return VerificationLevel.USER_INTENT
    if (
        record.semantic_type == FactSemanticType.SELF_REPORTED_FACT
        or record.source_type == FactSourceType.USER_DECLARED
    ):
        return VerificationLevel.SELF_REPORTED
    if (
        record.semantic_type == FactSemanticType.DERIVED_FACT
        or record.source_type == FactSourceType.DERIVED
    ):
        return VerificationLevel.DERIVED
    if record.source_type == FactSourceType.INSTITUTION_VERIFIED:
        return VerificationLevel.INSTITUTION_VERIFIED
    if record.source_type == FactSourceType.MYDATA_VERIFIED:
        return VerificationLevel.MYDATA_VERIFIED
    return VerificationLevel.UNKNOWN


def combine_verification_levels(
    levels: Iterable[VerificationLevel],
) -> VerificationLevel:
    meaningful = {
        level for level in levels if level != VerificationLevel.UNKNOWN
    }
    if not meaningful:
        return VerificationLevel.UNKNOWN
    if len(meaningful) == 1:
        return next(iter(meaningful))
    return VerificationLevel.MIXED


def _default_accepts(record: AuthorityBearingRecord) -> bool:
    # Historical/current user answers are ordinary evidence in v0.3.2.  Future
    # intent, however, is never allowed to masquerade as an observed fact when
    # a rule did not explicitly request FUTURE_INTENT.
    return record.semantic_type != FactSemanticType.FUTURE_INTENT


def accept_records(
    records: Iterable[T],
    *,
    policy: FactAcceptancePolicy | None,
    trust_mode: EvaluationTrustMode | None = None,
    allow_future_intent: bool = False,
) -> AcceptanceResult:
    """Apply a per-Fact evidence policy.

    ``trust_mode`` is accepted only for source compatibility with v0.3.1 and
    deliberately does not alter acceptance.  v0.3.2 makes trust/evidence a
    property of each Fact, not a global evaluation switch.
    """

    del trust_mode
    record_list = list(records)
    accepted: list[T] = []
    rejected: list[T] = []

    if policy is None:
        for record in record_list:
            allowed = _default_accepts(record) or (
                allow_future_intent
                and record.semantic_type == FactSemanticType.FUTURE_INTENT
            )
            (accepted if allowed else rejected).append(record)
    else:
        authoritative_semantics = set(policy.strict_semantic_types)
        authoritative_sources = set(policy.strict_source_types)
        self_report_semantics = set(policy.provisional_semantic_types)
        self_report_sources = set(policy.provisional_source_types)
        for record in record_list:
            authoritative = (
                record.semantic_type in authoritative_semantics
                and record.source_type in authoritative_sources
            )
            self_reported = (
                policy.allow_provisional
                and record.semantic_type in self_report_semantics
                and record.source_type in self_report_sources
            )
            if authoritative or self_reported:
                accepted.append(record)
            else:
                rejected.append(record)

    levels = tuple(verification_for_record(record) for record in accepted)
    self_reported_ids = tuple(
        _record_id(record)
        for record in accepted
        if verification_for_record(record) == VerificationLevel.SELF_REPORTED
    )
    return AcceptanceResult(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        self_reported_record_ids=self_reported_ids,
        evidence_levels=levels,
        verification_level=combine_verification_levels(levels),
    )
