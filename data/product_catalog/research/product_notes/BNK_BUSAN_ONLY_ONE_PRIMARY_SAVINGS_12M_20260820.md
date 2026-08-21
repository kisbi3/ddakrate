# Only One 주거래 우대적금

- product_id: `BNK_BUSAN_ONLY_ONE_PRIMARY_SAVINGS_12M_20260820`
- institution: `BNK_BUSAN`
- quality: **B**
- complexity: MEDIUM
- rate: 2.3% → 4.8% (cap 2.5%p)
- source: https://www.busanbank.co.kr/ib20/mnu/FPMDPO012002001
- validation: PASS

## Dependencies
- resolver: ELIGIBILITY_PROFILE_RESOLVER
- service: BNK_BUSAN_ONLY_ONE_PRIMARY_SERVICE
- user fact/future intent: none

## Warnings
- 공식 현재 상품목록의 기본/최고금리와 상품 성격은 확인. 공개 목록에서 세부 우대 배분이 완전하지 않아 aggregate institution entitlement로 보존.