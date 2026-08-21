# 청년 처음적금

- product_id: `SHINHAN_YOUTH_FIRST_SAVINGS_20260722`
- institution: `SHINHAN_BANK`
- quality: **B**
- complexity: COMPLEX
- rate: 3.05% → 6.05% (cap 3.0%p)
- source: https://img.shinhan.com/sbank2016/seol/20240202000000990012LC000030.PDF
- validation: PASS

## Dependencies
- resolver: SALARY_TRANSACTION_RESOLVER, ACCOUNT_HOLDING_RESOLVER, CARD_PERFORMANCE_RESOLVER
- service: SHINHAN_SALARY_CLUB, SHINHAN_SUPERSOL, SHINHAN_EVENT_COUPON
- user fact/future intent: none

## Warnings
- Golden regression fixture preserved verbatim; semantics cross-checked against current official source.