from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from eligibility.schema.enums import FactSemanticType, FactSourceType
from eligibility.schema.user_fact import (
    AccountHoldingInterval,
    DataCoverage,
    FactProvenance,
    UserFact,
    UserFactStore,
)


USER_ID = "U001"
_COLLECTED_AT = datetime(
    2026,
    8,
    19,
    15,
    0,
    tzinfo=timezone(timedelta(hours=9)),
)


def _fact(
    fact_id: str,
    fact_type: str,
    value: object,
    source_type: FactSourceType,
    *,
    valid_from: date | None = None,
    valid_to: date | None = None,
    reference: str,
    semantic_type: FactSemanticType = FactSemanticType.OBSERVED_FACT,
) -> UserFact:
    return UserFact(
        fact_id=fact_id,
        user_id=USER_ID,
        fact_type=fact_type,
        value=value,
        valid_from=valid_from,
        valid_to=valid_to,
        source_type=source_type,
        semantic_type=semantic_type,
        provenance=[FactProvenance(reference=reference)],
        collected_at=_COLLECTED_AT,
        confidence=1.0,
    )


def user_001() -> UserFactStore:
    facts = [
        _fact(
            "F-BIRTH",
            "BIRTH_DATE",
            date(1998, 4, 10),
            FactSourceType.MYDATA_VERIFIED,
            reference="virtual-mydata/user-profile",
        ),
        _fact(
            "F-SAME-PRODUCT-COUNT",
            "SHINHAN_YOUTH_FIRST_ACTIVE_ACCOUNT_COUNT",
            0,
            FactSourceType.MYDATA_VERIFIED,
            valid_from=date(2026, 8, 19),
            valid_to=date(2026, 8, 20),
            reference="virtual-mydata/account-snapshot",
        ),
        _fact(
            "F-SALARY-BANK",
            "SALARY_RECEIVING_BANK",
            "OTHER_BANK",
            FactSourceType.MYDATA_VERIFIED,
            reference="virtual-mydata/salary-transaction-history",
        ),
        _fact(
            "F-SALARY-CHANGE",
            "SALARY_ACCOUNT_CHANGE_POSSIBLE",
            True,
            FactSourceType.USER_DECLARED,
            reference="fixture/user-intent/salary-account-change",
            semantic_type=FactSemanticType.FUTURE_INTENT,
        ),
        _fact(
            "F-CARD-HELD",
            "SHINHAN_CARD_HELD",
            True,
            FactSourceType.MYDATA_VERIFIED,
            reference="virtual-mydata/card-holding",
        ),
        _fact(
            "F-CARD-TYPE",
            "SHINHAN_CARD_TYPE",
            "CHECK",
            FactSourceType.MYDATA_VERIFIED,
            reference="virtual-mydata/card-holding",
        ),
        _fact(
            "F-CARD-SETTLEMENT-BANK",
            "SHINHAN_CARD_SETTLEMENT_BANK",
            "OTHER_BANK",
            FactSourceType.MYDATA_VERIFIED,
            reference="virtual-mydata/card-settlement-account",
        ),
        _fact(
            "F-CARD-SETTLEMENT-CHANGE",
            "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE",
            True,
            FactSourceType.USER_DECLARED,
            reference="fixture/user-intent/card-account-change",
            semantic_type=FactSemanticType.FUTURE_INTENT,
        ),
        _fact(
            "F-HISTORY-COVERAGE",
            "SHINHAN_RELEVANT_HOLDING_HISTORY_COMPLETE",
            True,
            FactSourceType.MYDATA_VERIFIED,
            reference="virtual-mydata/shinhan-holding-history-coverage",
        ),
    ]

    account_holdings = [
        AccountHoldingInterval(
            account_id="A-SHINHAN-DEMAND-001",
            user_id=USER_ID,
            institution="SHINHAN_BANK",
            product_type="DEMAND_DEPOSIT",
            product_name="신한 입출금통장",
            held_from=date(2024, 1, 5),
            held_to=None,
            source_type=FactSourceType.MYDATA_VERIFIED,
            provenance=[
                FactProvenance(reference="virtual-mydata/account-history/current")
            ],
        ),
        AccountHoldingInterval(
            account_id="A-SHINHAN-SAVINGS-OLD",
            user_id=USER_ID,
            institution="SHINHAN_BANK",
            product_type="INSTALLMENT_SAVINGS",
            product_name="과거 신한 정기적금",
            held_from=date(2025, 11, 15),
            held_to=date(2026, 4, 12),
            source_type=FactSourceType.MYDATA_VERIFIED,
            provenance=[
                FactProvenance(reference="virtual-mydata/account-history/closed")
            ],
        ),
    ]
    data_coverages = [
        DataCoverage(
            coverage_id="COV-SHINHAN-HOLDING-HISTORY",
            user_id=USER_ID,
            fact_domain="ACCOUNT_HOLDING_HISTORY",
            institution="SHINHAN_BANK",
            covered_from=date(2024, 1, 1),
            covered_to=date(2026, 8, 19),
            source_type=FactSourceType.MYDATA_VERIFIED,
            provenance=[
                FactProvenance(
                    reference="virtual-mydata/shinhan-holding-history-coverage-interval"
                )
            ],
        )
    ]
    return UserFactStore(
        user_id=USER_ID,
        facts=facts,
        account_holdings=account_holdings,
        data_coverages=data_coverages,
    )


def with_supersol_answer(store: UserFactStore, willing: bool) -> UserFactStore:
    return store.with_fact(
        _fact(
            f"F-SUPERSOL-INTENT-{str(willing).upper()}",
            "SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING",
            willing,
            FactSourceType.USER_DECLARED,
            valid_from=date(2026, 8, 19),
            reference="fixture/user-answer/supersol",
            semantic_type=FactSemanticType.FUTURE_INTENT,
        )
    )


def with_event_coupon_answer(store: UserFactStore, valid: bool) -> UserFactStore:
    return store.with_fact(
        _fact(
            f"F-EVENT-COUPON-{str(valid).upper()}",
            "SPECIAL_RATE_COUPON_VALID",
            valid,
            FactSourceType.USER_DECLARED,
            valid_from=date(2026, 8, 19),
            reference="fixture/user-answer/event-coupon",
            semantic_type=FactSemanticType.SELF_REPORTED_FACT,
        )
    )


def with_holding_history_answer(store: UserFactStore, had_holding: bool) -> UserFactStore:
    """MVP Web answer: matching Shinhan holding existed in the prior-year lookback."""

    return store.with_fact(
        _fact(
            f"F-HISTORY-SELF-REPORT-{str(had_holding).upper()}",
            "SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y",
            had_holding,
            FactSourceType.USER_DECLARED,
            valid_from=date(2026, 8, 19),
            reference="fixture/user-answer/prior-holding",
            semantic_type=FactSemanticType.SELF_REPORTED_FACT,
        )
    )
