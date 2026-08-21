# 우리 사장님 성장 적금

- product_id: `WOORI_OWNER_GROWTH_SAVINGS_12M_20260820`
- institution: `WOORI_BANK`
- quality: **B**
- complexity: COMPLEX
- rate: 2.0% → 6.0% (cap 4.0%p)
- source: https://spot.wooribank.com/pot/Dream?TAB_GBN=2&withyou=PODEP0001
- validation: PASS

## Dependencies
- resolver: ELIGIBILITY_PROFILE_RESOLVER
- service: WOORI_BUSINESS_GROWTH_SERVICE
- user fact/future intent: none

## Warnings
- 공식 공개 본문에서 세부 reward allocation 대신 총 우대 entitlement를 institution service fact로 모델링.