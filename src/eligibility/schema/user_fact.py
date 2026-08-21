from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eligibility.schema.enums import (
    AccountLifecycleEventType,
    FactRecordStatus,
    FactSemanticType,
    FactSourceType,
    RecurringPaymentCategory,
    RecurringPaymentMethod,
    ScheduledOccurrenceMethod,
    ScheduledOccurrenceStatus,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactProvenance(StrictModel):
    reference: str
    description: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class UserFact(StrictModel):
    fact_id: str
    user_id: str
    fact_type: str
    value: Any
    # The store owner and the fact subject are not assumed to be the same
    # person. This is required for parent/child performance facts.
    subject_person_id: str | None = None
    related_person_id: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source_type: FactSourceType
    semantic_type: FactSemanticType = FactSemanticType.OBSERVED_FACT
    provenance: list[FactProvenance] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    record_status: FactRecordStatus = FactRecordStatus.ACTIVE
    supersedes_fact_id: str | None = None
    request_reference: str | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_interval(self) -> "UserFact":
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        if (
            self.semantic_type == FactSemanticType.FUTURE_INTENT
            and self.source_type != FactSourceType.USER_DECLARED
        ):
            raise ValueError("FUTURE_INTENT facts must be USER_DECLARED")
        if (
            self.semantic_type == FactSemanticType.SELF_REPORTED_FACT
            and self.source_type != FactSourceType.USER_DECLARED
        ):
            raise ValueError("SELF_REPORTED_FACT facts must be USER_DECLARED")
        return self

    @property
    def effective_subject_person_id(self) -> str:
        return self.subject_person_id or self.user_id

    def is_effective_at(self, at: date) -> bool:
        if self.valid_from is not None and at < self.valid_from:
            return False
        if self.valid_to is not None and at > self.valid_to:
            return False
        return True


class AccountHoldingInterval(StrictModel):
    account_id: str
    user_id: str
    institution: str
    product_type: str
    product_id: str | None = None
    product_name: str | None = None
    held_from: date
    held_to: date | None = None
    ownership: str = "USER"
    subject_person_id: str | None = None
    source_type: FactSourceType
    provenance: list[FactProvenance] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> "AccountHoldingInterval":
        if self.held_to is not None and self.held_from > self.held_to:
            raise ValueError("held_from must not be after held_to")
        return self

    @property
    def effective_subject_person_id(self) -> str:
        return self.subject_person_id or self.user_id


class PersonRelationship(StrictModel):
    relationship_id: str
    person_a: str
    person_b: str
    relationship_type: str
    valid_from: date | None = None
    valid_to: date | None = None
    source_type: FactSourceType
    source_reference: str | None = None
    provenance: list[FactProvenance] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relationship(self) -> "PersonRelationship":
        if self.person_a == self.person_b:
            raise ValueError("A person relationship must connect two different people")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        return self

    def is_effective_at(self, at: date) -> bool:
        if self.valid_from is not None and at < self.valid_from:
            return False
        if self.valid_to is not None and at > self.valid_to:
            return False
        return True

    def other_person(self, person_id: str) -> str | None:
        if self.person_a == person_id:
            return self.person_b
        if self.person_b == person_id:
            return self.person_a
        return None


class DataCoverage(StrictModel):
    coverage_id: str
    user_id: str
    fact_domain: str
    institution: str | None = None
    covered_from: date
    covered_to: date
    source_type: FactSourceType
    provenance: list[FactProvenance] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> "DataCoverage":
        if self.covered_from > self.covered_to:
            raise ValueError("covered_from must not be after covered_to")
        return self

    def covers(self, required_from: date, required_to: date) -> bool:
        return self.covered_from <= required_from and self.covered_to >= required_to


class ScheduledOccurrence(StrictModel):
    occurrence_id: str
    user_id: str
    schedule_id: str
    sequence_no: int = Field(ge=1)
    scheduled_at: datetime
    method: ScheduledOccurrenceMethod
    status: ScheduledOccurrenceStatus
    observed_event_id: str | None = None
    subject_person_id: str | None = None
    source_type: FactSourceType
    semantic_type: FactSemanticType = FactSemanticType.OBSERVED_EVENT
    provenance: list[FactProvenance] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_semantics(self) -> "ScheduledOccurrence":
        if self.semantic_type == FactSemanticType.FUTURE_INTENT:
            raise ValueError("ScheduledOccurrence cannot be FUTURE_INTENT")
        if (
            self.semantic_type == FactSemanticType.SELF_REPORTED_FACT
            and self.source_type != FactSourceType.USER_DECLARED
        ):
            raise ValueError("SELF_REPORTED_FACT occurrences must be USER_DECLARED")
        return self

    @property
    def effective_subject_person_id(self) -> str:
        return self.subject_person_id or self.user_id


class RecurringPaymentEvent(StrictModel):
    """Generic recurring-payment observation shared across institutions."""

    event_id: str
    user_id: str
    subject_person_id: str | None = None
    event_type: str = "QUALIFIED_RECURRING_PAYMENT"
    occurred_at: datetime
    amount: Decimal = Field(ge=0)
    currency: str = "KRW"
    category: RecurringPaymentCategory
    payment_method: RecurringPaymentMethod
    source_account: str | None = None
    institution: str | None = None
    settlement_bank: str | None = None
    source_type: FactSourceType
    semantic_type: FactSemanticType = FactSemanticType.OBSERVED_EVENT
    provenance: list[FactProvenance] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_semantics(self) -> "RecurringPaymentEvent":
        if self.semantic_type == FactSemanticType.FUTURE_INTENT:
            raise ValueError("RecurringPaymentEvent cannot be FUTURE_INTENT")
        if (
            self.semantic_type == FactSemanticType.SELF_REPORTED_FACT
            and self.source_type != FactSourceType.USER_DECLARED
        ):
            raise ValueError("SELF_REPORTED_FACT recurring payments must be USER_DECLARED")
        return self

    @property
    def effective_subject_person_id(self) -> str:
        return self.subject_person_id or self.user_id


class AccountLifecycleEvent(StrictModel):
    event_id: str
    user_id: str
    account_id: str
    event_type: AccountLifecycleEventType
    occurred_at: datetime
    original_opened_at: date | None = None
    previous_owner_person_id: str | None = None
    new_owner_person_id: str | None = None
    source_type: FactSourceType
    provenance: list[FactProvenance] = Field(default_factory=list)


class NormalizedMonetaryAmount(StrictModel):
    original_amount: Decimal
    original_currency: str
    normalized_amount: Decimal
    normalized_currency: str
    conversion_basis: str
    conversion_at: datetime
    source_reference: str


class UserFactStore(StrictModel):
    user_id: str
    facts: list[UserFact] = Field(default_factory=list)
    account_holdings: list[AccountHoldingInterval] = Field(default_factory=list)
    relationships: list[PersonRelationship] = Field(default_factory=list)
    scheduled_occurrences: list[ScheduledOccurrence] = Field(default_factory=list)
    recurring_payment_events: list[RecurringPaymentEvent] = Field(default_factory=list)
    data_coverages: list[DataCoverage] = Field(default_factory=list)
    account_lifecycle_events: list[AccountLifecycleEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_user_ids(self) -> "UserFactStore":
        mismatched_facts = [fact.fact_id for fact in self.facts if fact.user_id != self.user_id]
        mismatched_accounts = [
            account.account_id
            for account in self.account_holdings
            if account.user_id != self.user_id
        ]
        mismatched_occurrences = [
            occurrence.occurrence_id
            for occurrence in self.scheduled_occurrences
            if occurrence.user_id != self.user_id
        ]
        mismatched_recurring = [
            event.event_id
            for event in self.recurring_payment_events
            if event.user_id != self.user_id
        ]
        mismatched_coverages = [
            coverage.coverage_id
            for coverage in self.data_coverages
            if coverage.user_id != self.user_id
        ]
        mismatched_lifecycle_events = [
            event.event_id
            for event in self.account_lifecycle_events
            if event.user_id != self.user_id
        ]
        if (
            mismatched_facts
            or mismatched_accounts
            or mismatched_occurrences
            or mismatched_recurring
            or mismatched_coverages
            or mismatched_lifecycle_events
        ):
            raise ValueError(
                "All owned records must belong to the store user: "
                f"facts={mismatched_facts}, accounts={mismatched_accounts}, "
                f"occurrences={mismatched_occurrences}, recurring={mismatched_recurring}, "
                f"coverages={mismatched_coverages}, "
                f"lifecycle_events={mismatched_lifecycle_events}"
            )
        return self

    def with_fact(self, fact: UserFact) -> "UserFactStore":
        if fact.user_id != self.user_id:
            raise ValueError("fact user_id does not match store user_id")
        return self.model_copy(update={"facts": [*self.facts, fact]}, deep=True)

    @property
    def active_facts(self) -> list[UserFact]:
        return [
            fact for fact in self.facts
            if fact.record_status == FactRecordStatus.ACTIVE
        ]

    def effective_view(self) -> "UserFactStore":
        """Return an evaluation view containing only active facts.

        Other event/history collections are copied unchanged because their
        lifecycle semantics are already explicit in their typed records.
        """

        return self.model_copy(update={"facts": self.active_facts}, deep=True)

    def with_answer_fact(self, fact: UserFact) -> tuple["UserFactStore", list[UserFact]]:
        """Append an answer and supersede the prior active answer deterministically.

        Matching is anchored to ``request_reference`` when available. This
        avoids accidentally superseding an authoritative fact of the same
        fact_type. The full history remains physically present.
        """

        if fact.user_id != self.user_id:
            raise ValueError("fact user_id does not match store user_id")
        if fact.request_reference is None:
            return self.with_fact(fact), []

        superseded: list[UserFact] = []
        next_version = 1
        rewritten: list[UserFact] = []
        for existing in self.facts:
            if (
                existing.request_reference == fact.request_reference
                and existing.record_status == FactRecordStatus.ACTIVE
                and existing.source_type == FactSourceType.USER_DECLARED
            ):
                next_version = max(next_version, existing.version + 1)
                superseded_fact = existing.model_copy(
                    update={"record_status": FactRecordStatus.SUPERSEDED},
                    deep=True,
                )
                rewritten.append(superseded_fact)
                superseded.append(superseded_fact)
            else:
                rewritten.append(existing)

        active = fact.model_copy(
            update={
                "record_status": FactRecordStatus.ACTIVE,
                "version": next_version,
                "supersedes_fact_id": (
                    superseded[-1].fact_id if superseded else fact.supersedes_fact_id
                ),
            },
            deep=True,
        )
        return self.model_copy(update={"facts": [*rewritten, active]}, deep=True), superseded

    def answer_history(self, request_reference: str) -> list[UserFact]:
        return sorted(
            [fact for fact in self.facts if fact.request_reference == request_reference],
            key=lambda item: (item.version, item.collected_at, item.fact_id),
        )

    def with_relationship(self, relationship: PersonRelationship) -> "UserFactStore":
        return self.model_copy(
            update={"relationships": [*self.relationships, relationship]}, deep=True
        )

    def with_scheduled_occurrence(
        self, occurrence: ScheduledOccurrence
    ) -> "UserFactStore":
        if occurrence.user_id != self.user_id:
            raise ValueError("occurrence user_id does not match store user_id")
        return self.model_copy(
            update={"scheduled_occurrences": [*self.scheduled_occurrences, occurrence]},
            deep=True,
        )

    def with_recurring_payment_event(
        self, event: RecurringPaymentEvent
    ) -> "UserFactStore":
        if event.user_id != self.user_id:
            raise ValueError("recurring payment user_id does not match store user_id")
        return self.model_copy(
            update={"recurring_payment_events": [*self.recurring_payment_events, event]},
            deep=True,
        )
