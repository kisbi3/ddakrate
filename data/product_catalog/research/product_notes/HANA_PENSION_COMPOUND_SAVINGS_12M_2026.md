# 연금하나 월복리 적금(12개월)

- product_id: `HANA_PENSION_COMPOUND_SAVINGS_12M_2026`
- institution: `HANA_BANK`
- quality: **B**
- complexity: MEDIUM
- rate: 2.95% → 4.65% (cap 1.70%p)
- source: https://www.hanabank.com/cont/mall/mall08/mall0801/mall080102/1455928_115157.jsp
- validation: PASS

## Dependencies
- resolver: ELIGIBILITY_PROFILE_RESOLVER, PENSION_INFLOW_RESOLVER
- service: HANA_PENSION_EVENT_COUPON
- user fact/future intent: none

## Warnings
- 최고 4.65%는 2026-07-10 기준 1만명 한정 이벤트 쿠폰 0.50%p를 포함. 실시간 쿠폰 잔여수량은 기관 확인 필요.