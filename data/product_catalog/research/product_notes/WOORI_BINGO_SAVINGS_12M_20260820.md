# 우리 빙고 적금

- product_id: `WOORI_BINGO_SAVINGS_12M_20260820`
- institution: `WOORI_BANK`
- quality: **B**
- complexity: COMPLEX
- rate: 2.75% → 10.25% (cap 7.50%p)
- source: https://spot.wooribank.com/pot/Dream?PRD_CD=P010002584&PRD_YN=Y&withyou=PODEP0001
- validation: PASS

## Dependencies
- resolver: ELIGIBILITY_PROFILE_RESOLVER
- service: WOORI_BINGO_SERVICE
- user fact/future intent: none

## Warnings
- 빙고 내부 칸별 의미는 bank-service knowledge로 유지하고 DSL operator로 승격하지 않음.