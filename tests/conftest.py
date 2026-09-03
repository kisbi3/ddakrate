from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from eligibility.schema.evaluation import EvaluationContext
from eligibility.schema.user_fact import UserFact, UserFactStore
from eligibility.schema.enums import FactSourceType


@pytest.fixture(autouse=True)
def isolate_debug_history(monkeypatch, tmp_path) -> None:
    """Keep TestClient traces out of the developer's persistent inspector."""

    monkeypatch.setenv(
        "ELIGIBILITY_DEBUG_HISTORY_DIR",
        str(tmp_path / "debug-history"),
    )


@pytest.fixture
def basic_context() -> EvaluationContext:
    return EvaluationContext(
        as_of=date(2026, 1, 1),
        subscription_date=date(2026, 1, 2),
        maturity_date=date(2027, 1, 2),
    )


@pytest.fixture
def fact_factory():
    def make(fact_id: str, fact_type: str, value: object, *, valid_from: date | None = None) -> UserFact:
        return UserFact(
            fact_id=fact_id,
            user_id="TEST_USER",
            fact_type=fact_type,
            value=value,
            valid_from=valid_from,
            source_type=FactSourceType.MYDATA_VERIFIED,
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    return make


@pytest.fixture
def empty_store() -> UserFactStore:
    return UserFactStore(user_id="TEST_USER")
