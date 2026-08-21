# 우리은행 장병내일준비적금(15개월)

- product_id: `WOORI_SOLDIER_TOMORROW_SAVINGS_15M_2026`
- institution: `WOORI_BANK`
- quality: **B**
- complexity: MEDIUM
- rate: 5.0% → 6.0% (cap 1.0%p)
- source: https://spot.wooribank.com/pot/Dream?PRD_CD=P010002283&PRD_YN=Y&cc=c007095%3Ac009166%3Bc012263%3Ac012399&withyou=PODEP0019
- validation: PASS

## Dependencies
- resolver: ACCOUNT_HOLDING_RESOLVER, AUTO_TRANSFER_RESOLVER, CARD_PERFORMANCE_RESOLVER, ELIGIBILITY_PROFILE_RESOLVER
- service: OFFICIAL_OTHER_PREFERENTIAL_WOORI_SOLDIER_TOMORROW_SAVINGS_15M_2026
- user fact/future intent: none

## Warnings
- 공식 우대 최대 1.0%p 중 공개 본문에서 확인된 0.8%p 외 잔여 0.2%p는 자동 생성 residual institution entitlement로 보존.
- 공개 웹 본문에서 세부 reward allocation이 완전하지 않아 0.2%p를 institution entitlement로 보존.