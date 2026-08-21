from __future__ import annotations

from datetime import date, datetime, timezone

from eligibility.engine.fact_resolver import FactResolver
from eligibility.schema.enums import FactSourceType, ResolutionStatus
from eligibility.schema.user_fact import UserFact, UserFactStore


def _fact(fact_id: str, value: bool, source: FactSourceType) -> UserFact:
    return UserFact(
        fact_id=fact_id,
        user_id="TEST_USER",
        fact_type="SERVICE_MEMBER",
        value=value,
        source_type=source,
        collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_institution_verified_fact_overrides_user_declared_fact():
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[
            _fact("USER", True, FactSourceType.USER_DECLARED),
            _fact("BANK", False, FactSourceType.INSTITUTION_VERIFIED),
        ],
    )

    resolution = FactResolver(store).resolve("SERVICE_MEMBER", date(2026, 1, 1))

    assert resolution.status == ResolutionStatus.RESOLVED
    assert resolution.fact is not None
    assert resolution.fact.fact_id == "BANK"
    assert resolution.value is False


def test_equal_priority_conflicting_facts_are_not_silently_overwritten():
    store = UserFactStore(
        user_id="TEST_USER",
        facts=[
            _fact("BANK-TRUE", True, FactSourceType.INSTITUTION_VERIFIED),
            _fact("BANK-FALSE", False, FactSourceType.INSTITUTION_VERIFIED),
        ],
    )

    resolution = FactResolver(store).resolve("SERVICE_MEMBER", date(2026, 1, 1))

    assert resolution.status == ResolutionStatus.CONFLICT
    assert resolution.fact is None
    assert {fact.fact_id for fact in resolution.competing_facts} == {
        "BANK-TRUE",
        "BANK-FALSE",
    }
