# KB내맘대로적금(12개월)

- product_id: `KB_MYWAY_SAVINGS_12M_20260818`
- institution: `KB_BANK`
- quality: **B**
- complexity: COMPLEX
- rate: 2.5% → 3.1% (cap 0.6%p)
- source: https://obank.kbstar.com/quics?page=C016613
- validation: PASS

## Dependencies
- resolver: ELIGIBILITY_PROFILE_RESOLVER
- service: KB_MYWAY_SELECTED_BONUS_SERVICE
- user fact/future intent: none

## Warnings
- 공식 상품은 9개 중 6개 선택형. Catalog는 선택 세트와 달성 결과를 institution service fact로 보존해 core operator에 상품고유 의미를 넣지 않음.