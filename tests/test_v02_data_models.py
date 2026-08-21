from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from eligibility.schema.enums import (
    AccountLifecycleEventType,
    FactSourceType,
    ScheduledOccurrenceMethod,
    ScheduledOccurrenceStatus,
)
from eligibility.schema.user_fact import (
    AccountLifecycleEvent,
    NormalizedMonetaryAmount,
    ScheduledOccurrence,
)


def test_scheduled_occurrence_preserves_sequence_method_and_status():
    occurrence = ScheduledOccurrence(
        occurrence_id="O-4",
        user_id="U",
        schedule_id="S",
        sequence_no=4,
        scheduled_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        method=ScheduledOccurrenceMethod.AUTO_TRANSFER,
        status=ScheduledOccurrenceStatus.FAILED,
        observed_event_id=None,
        source_type=FactSourceType.INSTITUTION_VERIFIED,
    )

    assert occurrence.sequence_no == 4
    assert occurrence.method == ScheduledOccurrenceMethod.AUTO_TRANSFER
    assert occurrence.status == ScheduledOccurrenceStatus.FAILED


def test_account_lifecycle_event_distinguishes_cancelled_withdrawn_and_renamed():
    events = [
        AccountLifecycleEvent(
            event_id=f"E-{event_type.value}",
            user_id="U",
            account_id="A",
            event_type=event_type,
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            original_opened_at=date(2025, 1, 1),
            source_type=FactSourceType.INSTITUTION_VERIFIED,
        )
        for event_type in (
            AccountLifecycleEventType.CANCELLED,
            AccountLifecycleEventType.WITHDRAWN,
            AccountLifecycleEventType.RENAMED,
        )
    ]

    assert {event.event_type for event in events} == {
        AccountLifecycleEventType.CANCELLED,
        AccountLifecycleEventType.WITHDRAWN,
        AccountLifecycleEventType.RENAMED,
    }


def test_normalized_monetary_amount_keeps_conversion_provenance():
    amount = NormalizedMonetaryAmount(
        original_amount=Decimal("1000000"),
        original_currency="JPY",
        normalized_amount=Decimal("6800"),
        normalized_currency="USD",
        conversion_basis="BANK_OFFICIAL_REMITTANCE_RATE",
        conversion_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        source_reference="hana/fx/rate/20260819",
    )

    assert amount.normalized_currency == "USD"
    assert amount.normalized_amount == Decimal("6800")
    assert amount.source_reference == "hana/fx/rate/20260819"
