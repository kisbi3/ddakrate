# 우리 SUPER주거래 자유적금(36개월)

- product_id: `WOORI_SUPER_PRIMARY_FREE_SAVINGS_36M_20260820`
- institution: `WOORI_BANK`
- quality: **B**
- complexity: MEDIUM
- rate: 2.4% → 3.8% (cap 1.4%p)
- source: https://spot.wooribank.com/pot/Dream?ALL_GB=&PLM_PDCD=P010000110&PRD_CD=P010000110&cc=c007095%3Ac009166%3Bc012263%3Ac012399&depKind=A04&withyou=PODEP0021
- validation: PASS

## Dependencies
- resolver: ELIGIBILITY_PROFILE_RESOLVER
- service: WOORI_PRIMARY_RELATIONSHIP_SERVICE
- user fact/future intent: none

## Warnings
- 36개월 고정 variant로 수록. 1년제는 기본 2.45%로 달라 동일 ProductDefinition에 기간별 금리를 평탄화하지 않음.