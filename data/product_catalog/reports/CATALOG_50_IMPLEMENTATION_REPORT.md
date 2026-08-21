# CATALOG_50_IMPLEMENTATION_REPORT

**기준일:** 2026-08-20  
**Catalog:** `financial-product-catalog-v0.1`  
**Engine baseline:** `Financial Eligibility Engine v0.4.6`  
**최종 판정:** **READY_WITH_LIMITATIONS**

## 1. 조사 방법

- 금융회사 **공식 상품 상세페이지, 공식 상품설명서/PDF, 공식 금리 공시, 공식 이벤트/프로모션 페이지**를 우선 사용했다.
- 비교사이트·검색결과는 발견 보조로만 사용하고 ProductDefinition의 공식 truth로 저장하지 않았다.
- 동적 웹페이지에서 공식 version date를 확인할 수 없으면 `version_date=None`을 유지하고 `retrieved_at=2026-08-20T22:15:00+09:00`을 sidecar manifest에 기록했다.
- 정확한 원문 인용을 확보하지 못한 신규 데이터에는 `SourceReference.source_text`를 임의 작성하지 않았다. 대신 `source_manifest.json`의 `normalized_source_note`는 **조사용 정규화 메모**로만 분리했다.
- 4개 golden fixture는 코드 회귀 기준으로 그대로 직렬화했고 덮어쓰지 않았다.

## 2. 후보 발견 수

- Discovery pool: **70**
- 최종 선정: **50**
- Reject / backlog: **20**
- 최종 기관 수: **10**
- 최종 product type: **INSTALLMENT_SAVINGS 50/50**

`research/discovery_candidates.md`와 `research/rejected_candidates.md`에 후보/제외 내역을 보존했다.

## 3. 최종 50개 선정 목록

|#|Institution|Product|Base|Advertised max|Complexity|Quality|
|---:|---|---|---:|---:|---|---|
|1|SHINHAN_BANK|청년 처음적금|3.05%|6.05%|COMPLEX|B|
|2|HANA_BANK|달려라 하나 적금|1.8%|6.0%|COMPLEX|B|
|3|IBK_BANK|IBK부모급여우대적금|2.5%|6.5%|COMPLEX|B|
|4|KAKAOBANK|26주적금|2.0%|5.0%|COMPLEX|B|
|5|SHINHAN_BANK|2026 SOL KBO 적금|2.5%|5.0%|COMPLEX|B|
|6|SHINHAN_BANK|다둥이 상생 적금|2.5%|8.0%|COMPLEX|B|
|7|SHINHAN_BANK|신한 11번가 쇼핑 적금|5.5%|11.0%|MEDIUM|B|
|8|HANA_BANK|오늘부터, 하나 적금|2.0%|7.7%|MEDIUM|B|
|9|HANA_BANK|하나더이지 적금|2.0%|5.0%|COMPLEX|B|
|10|HANA_BANK|부자씨 적금|2.5%|4.5%|COMPLEX|B|
|11|HANA_BANK|손님케어 적금|3.3%|4.7%|MEDIUM|B|
|12|HANA_BANK|급여하나 월복리 적금(12개월)|2.95%|5.25%|MEDIUM|B|
|13|HANA_BANK|연금하나 월복리 적금(12개월)|2.95%|4.65%|MEDIUM|B|
|14|IBK_BANK|IBK D-day 적금|2.85%|4.35%|MEDIUM|B|
|15|IBK_BANK|IBK사랑나눔적금|4.4%|4.4%|MEDIUM|B|
|16|IBK_BANK|IBK월복리자유적금(60개월)|2.45%|2.75%|MEDIUM|B|
|17|IBK_BANK|IBK평생한가족통장 자유적금(36개월)|2.65%|3.05%|COMPLEX|B|
|18|KB_BANK|KB스타적금III|2.0%|5.0%|COMPLEX|B|
|19|KB_BANK|KB국민ONE적금 자유적립식(12개월)|2.3%|3.0%|MEDIUM|B|
|20|KB_BANK|KB국민ONE적금 정액적립식(12개월)|2.8%|4.1%|COMPLEX|B|
|21|KB_BANK|KB내맘대로적금(12개월)|2.5%|3.1%|COMPLEX|B|
|22|KB_BANK|KB맑은하늘적금(12개월)|2.65%|3.45%|MEDIUM|B|
|23|KB_BANK|KB Global Star 적금|2.0%|5.5%|COMPLEX|B|
|24|WOORI_BANK|WON 적금|3.2%|3.4%|SIMPLE|B|
|25|WOORI_BANK|우리 SUPER주거래 자유적금(36개월)|2.4%|3.8%|MEDIUM|B|
|26|WOORI_BANK|우리 사장님 성장 적금|2.0%|6.0%|COMPLEX|B|
|27|WOORI_BANK|우리 빙고 적금|2.75%|10.25%|COMPLEX|B|
|28|WOORI_BANK|우리 아이행복 적금2|2.7%|3.9%|MEDIUM|B|
|29|WOORI_BANK|우리은행 장병내일준비적금(15개월)|5.0%|6.0%|MEDIUM|B|
|30|KBANK|코드K 자유적금(12개월)|3.7%|3.7%|SIMPLE|B|
|31|KBANK|주거래우대 자유적금(12개월)|3.6%|4.2%|MEDIUM|B|
|32|KBANK|데굴데굴 농장|3.0%|3.0%|SIMPLE|B|
|33|KBANK|마이키즈 적금(5년)|3.5%|7.5%|MEDIUM|B|
|34|KAKAOBANK|자유적금(12개월)|3.65%|3.85%|MEDIUM|B|
|35|KAKAOBANK|한달적금|0.5%|6.0%|COMPLEX|B|
|36|TOSS_BANK|키워봐요 31일적금|1.0%|10.0%|COMPLEX|B|
|37|TOSS_BANK|아이적금|2.5%|5.0%|COMPLEX|B|
|38|TOSS_BANK|태아적금|1.0%|5.0%|COMPLEX|B|
|39|TOSS_BANK|자유적금(12개월)|2.5%|3.0%|MEDIUM|B|
|40|TOSS_BANK|굴비적금|1.8%|4.3%|SIMPLE|B|
|41|BNK_KYONGNAM|오늘도 세이브 적금|2.75%|4.5%|MEDIUM|B|
|42|BNK_KYONGNAM|Touch UP 적금|1.45%|7.0%|COMPLEX|B|
|43|BNK_KYONGNAM|BNK 위더스WithUs 자유적금(36개월)|2.3%|4.8%|MEDIUM|B|
|44|BNK_KYONGNAM|정기적금(60개월)|2.35%|2.35%|SIMPLE|B|
|45|BNK_BUSAN|Only One 주거래 우대적금|2.3%|4.8%|MEDIUM|B|
|46|BNK_BUSAN|저탄소 실천 적금|2.5%|3.0%|MEDIUM|B|
|47|BNK_BUSAN|BNK지역사랑자유적금|2.7%|2.8%|SIMPLE|B|
|48|BNK_BUSAN|가계우대정기적금|2.7%|2.7%|SIMPLE|B|
|49|BNK_BUSAN|정기적금|2.7%|2.7%|SIMPLE|B|
|50|BNK_BUSAN|BNK내맘대로적금|2.9%|3.1%|MEDIUM|B|

## 4. 제외 상품과 이유

|Institution|Product|Decision|Reason|
|---|---|---|---|
|TOSS_BANK|환영해요 적금|ENDED|공식 안내상 2026-05-19 판매 종료|
|KBANK|궁금한 적금|REJECTED|31회 미만 구간의 실현 보너스가 확률/추첨 성격이라 deterministic main catalog에서 제외|
|BNK_BUSAN|너만 Solo 적금(구 버전)|ENDED|보호금융상품등록부에 구 버전 판매중단 표시|
|BNK_BUSAN|BNK아기천사 적금(구 버전)|ENDED|공시실에 2025-02-10 판매중단 표시|
|BNK_BUSAN|아이사랑자유적금(구 버전)|ENDED|공시실에 2024-06-12 판매중단 표시|
|HANA_BANK|특판 소진형 후보 A|REJECTED|실시간 잔여수량 공식 확인 곤란|
|SHINHAN_BANK|정기예금 후보|REJECTED|이번 catalog의 반복납입형 범위와 불일치|
|KB_BANK|정기예금 후보|REJECTED|이번 catalog의 반복납입형 범위와 불일치|
|WOORI_BANK|정기예금 후보|REJECTED|이번 catalog의 반복납입형 범위와 불일치|
|IBK_BANK|정기예금 후보|REJECTED|이번 catalog의 반복납입형 범위와 불일치|
|BNK_KYONGNAM|오면우대!하면우대! 정기적금|BACKLOG|공식 판매 확인은 가능하나 이번 batch에서 세부 우대분해 source audit 우선순위가 낮아 대체|
|BNK_KYONGNAM|BNK더조은자유적금|BACKLOG|추가 후보; 최종 50 기관 분포상 중복을 줄이기 위해 미선정|
|BNK_KYONGNAM|BNK 공공 드림적금|BACKLOG|추가 후보; 최종 50 기관 분포상 중복을 줄이기 위해 미선정|
|KBANK|챌린지박스|REJECTED|공식 예금종류가 입출금이 자유로운 예금으로 확인되어 적금 중심 catalog 범위에서 제외|
|BNK_BUSAN|아이사랑 적금(신규 현행)|BACKLOG|후속 related-person 확장 후보|
|BNK_BUSAN|BNK 금융사다리적금|BACKLOG|복합지원센터 entitlement source integration을 후속으로 분리|
|BNK_BUSAN|챌린지적금 with 현대자동차|BACKLOG|제휴사 계약할인/우대 데이터 연동 필요|
|WOORI_BANK|우리의 소원 적금|REJECTED|상품설명서/약관은 확인되지만 2026-08-20 현재 판매중 목록에서 현재 판매 근거를 충분히 확인하지 못해 대체|
|WOORI_BANK|우리 청년미래적금|BACKLOG|정부기여금은 금리 이외 reward schema 후속 sprint가 더 적합|
|KBANK|기타 프로모션 특판 후보|BACKLOG|동적 이벤트 의존도가 높아 안정적 catalog source 우선순위에서 제외|

최종 freeze 전 source audit에서 두 후보를 특히 교체했다.

- **케이뱅크 챌린지박스**: 공식 페이지의 예금종류가 `입출금이 자유로운 예금`이므로 반복납입형 적금 catalog에서 제외하고 **부산은행 Only One 주거래 우대적금**으로 대체했다.
- **우리의 소원 적금**: 약관/설명서 존재와 별개로 2026-08-20 현재 판매중 목록에서 충분한 현행 판매 근거를 확보하지 못해 **우리 SUPER주거래 자유적금 36개월 variant**로 대체했다.

## 5. 기관별 분포

```json
{
  "SHINHAN_BANK": 4,
  "HANA_BANK": 7,
  "IBK_BANK": 5,
  "KAKAOBANK": 3,
  "KB_BANK": 6,
  "WOORI_BANK": 6,
  "KBANK": 4,
  "TOSS_BANK": 5,
  "BNK_KYONGNAM": 4,
  "BNK_BUSAN": 6
}
```

10개 기관으로 구성되어 특정 한 은행에 과도하게 집중하지 않았다.

## 6. 상품 난이도 분포

```json
{
  "COMPLEX": 20,
  "MEDIUM": 22,
  "SIMPLE": 8
}
```

권장 15/20/15와 정확히 일치시키기 위해 라벨을 인위적으로 조정하지 않았다. 실제 rule/dependency 구조 기준으로 분류했다.

## 7. ProductMetadata completeness

모든 50개는 strict `ProductMetadata` validation을 통과했다. 문서에 없는 temporal/sale field는 추측하지 않고 null로 유지했다.

|Field|Non-null / 50|해석|
|---|---:|---|
|metadata source_reference|50/50|공식 source URL 존재|
|target_customer_summary|49/50|공식 가입대상 요약|
|one_account_per_person|31/50|문서에서 명시된 경우만 저장|
|sale_start|2/50|명시된 경우만 저장|
|sale_end|4/50|명시된 경우만 저장|
|quantity_limit|6/50|명시된 경우만 저장|
|early_termination_policy|46/50|golden/공식 설명 기준|
|partial_withdrawal_policy|3/50|지원 상품만 저장|
|interest_payment_method|50/50|schema 기본/상품 표현|
|tax_treatment|50/50|schema 기본/상품 표현|
|effective_from|30/50|공식 version/effective 근거가 있는 경우|
|effective_to|0/50|공식 종료 효력일을 추측하지 않음|

기간별 금리가 다른 상품은 임의 평균하지 않았다. 예를 들어 **우리 SUPER주거래 자유적금은 36개월 fixed-term variant**로 만들었고, 1년제의 다른 기본금리를 같은 ProductDefinition에 평탄화하지 않았다.

## 8. Rule AST completeness

- ProductDefinition: **50/50 parse PASS**
- Catalog representation의 executable decision units: **252**
- PreferentialRateRule: **158**, source URL **158/158**
- Global guards: **44**, source URL **44/44**
- 4 golden fixture semantic equality: **PASS**
- Kakao 26주 7주/26주 `CountConsecutiveRule.from_sequence`: **None / None**

46개 data-driven 상품의 가입대상은 아직 완전한 leaf AST로 모두 세분화하지 않고 `ELIGIBILITY_PROFILE_RESOLVER`가 생성하는 권위 있는 fact로 연결했다. 이 때문에 데이터는 실행 가능하지만 A가 아니라 B로 보수적으로 등급화했다.

## 9. ProductFeature coverage

- Typed ProductFeature total: **94**
- 테스트: 모든 feature_id가 strict schema에서 typed/searchable 형태로 validation **PASS**
- ProductFeature는 검색 index이고 실제 판정은 Rule AST가 수행하도록 분리했다.

## 10. Resolver dependency

- Unique resolver requirement: **19**
- 가장 반복되는 dependency는 `ELIGIBILITY_PROFILE_RESOLVER`로 **46 products**다.

- `ELIGIBILITY_PROFILE_RESOLVER` → 46 products
- `ACCOUNT_HOLDING_RESOLVER` → 7 products
- `CARD_PERFORMANCE_RESOLVER` → 7 products
- `SALARY_TRANSACTION_RESOLVER` → 7 products
- `AUTO_TRANSFER_RESOLVER` → 6 products
- `RELATED_PERSON_RESOLVER` → 5 products
- `ACCOUNT_LIFECYCLE_RESOLVER` → 3 products
- `CERTIFICATE_RESOLVER` → 3 products
- `SCHEDULED_OCCURRENCE_RESOLVER` → 3 products
- `GOVERNMENT_BENEFIT_RESOLVER` → 2 products
- `RECURRING_PAYMENT_RESOLVER` → 2 products
- `AGE_RESOLVER` → 1 products
- `CONTRIBUTION_SUM_RESOLVER` → 1 products
- `FX_NORMALIZATION_RESOLVER` → 1 products
- `MULTI_TRANSACTION_RESOLVER` → 1 products
- `NATIONAL_FITNESS_CERT_RESOLVER` → 1 products
- `PAYMENT_METHOD_RESOLVER` → 1 products
- `PENSION_INFLOW_RESOLVER` → 1 products
- `REMITTANCE_RESOLVER` → 1 products

다음 Main Track 우선순위는 `ELIGIBILITY_PROFILE_RESOLVER`, `ACCOUNT_HOLDING_RESOLVER`, `CARD_PERFORMANCE_RESOLVER`, `SALARY_TRANSACTION_RESOLVER`, `AUTO_TRANSFER_RESOLVER`, `RELATED_PERSON_RESOLVER` 순으로 보는 것이 타당하다.

## 11. Institution Service dependency

- Unique service knowledge requirement: **31**

- `HANA_MARKETING_CONSENT_SERVICE` → 3 products
- `BNK_KYONGNAM_CONSENT_SERVICE` → 2 products
- `BNK_BUSAN_LOCAL_LOVE_SERVICE` → 1 products
- `BNK_BUSAN_LOW_CARBON_SERVICE` → 1 products
- `BNK_BUSAN_MYWAY_SERVICE` → 1 products
- `BNK_BUSAN_ONLY_ONE_PRIMARY_SERVICE` → 1 products
- `HANA_HAP_MYDATA_SERVICE` → 1 products
- `HANA_HEALTH_ASSET_SERVICE` → 1 products
- `HANA_ONEQ_LOGIN_SERVICE` → 1 products
- `HANA_PENSION_EVENT_COUPON` → 1 products
- `HANA_RUNNING_CREW` → 1 products
- `HANA_VALUED_CUSTOMER_SERVICE` → 1 products
- `HANA_YOUTH_SPECIAL_ELIGIBILITY` → 1 products
- `IBK_FAMILY_PERFORMANCE_REGISTRATION` → 1 products
- `IBK_SOCIAL_ELIGIBILITY_SERVICE` → 1 products
- `KB_ECO_QUIZ_SERVICE` → 1 products
- `KB_FINANCIAL_COUPON_SERVICE` → 1 products
- `KB_MYWAY_SELECTED_BONUS_SERVICE` → 1 products
- `OFFICIAL_OTHER_PREFERENTIAL_WOORI_SOLDIER_TOMORROW_SAVINGS_15M_2026` → 1 products
- `SHINHAN_EVENT_COUPON` → 1 products
- `SHINHAN_KBO_RESULT_SERVICE` → 1 products
- `SHINHAN_MULTICHILD_CARD_SERVICE` → 1 products
- `SHINHAN_SALARY_CLUB` → 1 products
- `SHINHAN_SOL_FANTASY` → 1 products
- `SHINHAN_SUPERSOL` → 1 products
- `TOSS_PARENT_CHILD_VERIFICATION_SERVICE` → 1 products
- `TOSS_RPG_PROGRESS_SERVICE` → 1 products
- `WOORI_BINGO_SERVICE` → 1 products
- `WOORI_BUSINESS_GROWTH_SERVICE` → 1 products
- `WOORI_OPEN_BANKING_SERVICE` → 1 products
- `WOORI_PRIMARY_RELATIONSHIP_SERVICE` → 1 products

기관 고유 의미를 `HAS_SUPERSOL`, `HAS_HANA_*` 같은 custom DSL operator로 만들지 않고 service fact로 격리했다.

## 12. User Fact / Future Intent dependency

Sidecar에서 명시적으로 추적한 future-intent fact type은 **9개**다.

- `KAKAO_26W_26W_INTENT`
- `KAKAO_26W_7W_INTENT`
- `KAKAO_FREE_AUTO_TRANSFER_MONTH`
- `KAKAO_M1_MANUAL_DEPOSIT_DAY`
- `KBANK_MYKIDS_QUALIFYING_DEPOSIT_MONTH`
- `KN_TOUCH_DEPOSIT_DAY`
- `TOSS_CHILD_AUTO_TRANSFER_ALL`
- `TOSS_FREE_AUTO_TRANSFER_ALL`
- `WILL_KEEP_HEALTH_DATA_PERMISSIONS`

반복납입·자동이체·건강데이터 유지 등 가입 후 조건은 FutureAchievement / ASK_USER semantics로 처리하고, 실제 기관 사실을 사용자 선언으로 대체하지 않았다.

## 13. Schema gaps

최종 50개 본선 catalog에는 **blocking schema/DSL gap 0개**로 기록했다.

- 케이뱅크 챌린지박스는 DSL gap이 아니라 **product type 범위 mismatch**로 제외했다.
- 기간별 금리 table은 schema를 왜곡하지 않고 fixed-term variant로 해결했다.
- 세부 공식 reward allocation을 공개 HTML에서 완전히 확인할 수 없는 일부 상품은 새 operator를 만들지 않고 B-grade institution entitlement로 보존했다.

`manifests/schema_gap_backlog.json` 참고.

## 14. 기존 10개 stress-test와 비교

기존 10개 / 110 natural-language atomic node 분석은 DSL 50.9%, Resolver 14.5%, Service Knowledge 26.4%, User Fact 4.5%, DSL extension 3.6%였다.

이번 50개 report의 coverage는 **ProductDefinition에 실제 저장된 executable decision unit boundary**를 세므로 완전히 같은 분모가 아니다. Service fact 하나가 내부의 여러 은행 고유 절차를 캡슐화한 경우 이를 다시 자연어 atomic node로 재분해해 중복 집계하지 않았다. 따라서 비율은 방향성 비교로만 사용한다.

현재 catalog decision-unit coverage:

- DSL_SUPPORTED: 52 / 252 = 20.6%
- RESOLVER_NEEDED: 50 / 252 = 19.8%
- SERVICE_KNOWLEDGE_NEEDED: 81 / 252 = 32.1%
- USER_FACT_NEEDED: 69 / 252 = 27.4%
- SCHEMA_OR_DSL_GAP: 0 / 252 = 0.0%

## 15. 전체 validation 결과

- Catalog-specific tests: **13/13 PASS**
- Integrated engine full pytest suite: **382/382 PASS**
- ProductDefinition JSON validation: **50/50 PASS**
- Unique product_id: **PASS**
- ProductDefinition ↔ ProductMetadata identity: **PASS**
- official source reference present: **50/50 PASS**
- advertised max <= base + preferential cap: **PASS**
- action path lineage / strict round-trip validation: **PASS**
- generic catalog loader: **50/50 load PASS**

Exact machine-readable result: `manifests/validation_results.json`.

## 16. ApplicationService integration 결과

50개 catalog를 실제 `ApplicationService`에 주입해:

```text
50 products
→ Candidate Retrieval
→ FinancialEligibilityEngine multi-product evaluation
→ Ranking-aware QUESTIONING state
→ Top 5 Recommendation
```

flow를 실행했고 runtime exception 없이 통과했다.

- candidate count: **50**
- SearchSession status: **QUESTIONING**
- active ranking-impact question: **present**
- Top 5 response count: **5**

No-fact smoke에서 `VERIFICATION_REQUIRED`가 많이 남는 것은 B-grade resolver/service fact를 의도적으로 UNKNOWN으로 보존했기 때문이며 false로 강제하지 않았다.

## 17. Top 5 smoke test

`MAX_REALIZABLE_RATE`, 빈 UserFactStore 기준 smoke 결과:

|Rank|product_id|realizable_rate|eligibility|material_unknowns|
|---:|---|---:|---|---:|
|1|SHINHAN_11ST_SHOPPING_SAVINGS_20260520|5.5%|VERIFICATION_REQUIRED|3|
|2|WOORI_SOLDIER_TOMORROW_SAVINGS_15M_2026|5.0%|VERIFICATION_REQUIRED|6|
|3|IBK_LOVE_SHARING_SAVINGS_20260805|4.4%|VERIFICATION_REQUIRED|2|
|4|TOSS_GULBI_SAVINGS_6M_20260713|4.3%|VERIFICATION_REQUIRED|2|
|5|KBANK_CODE_K_FREE_SAVINGS_12M_20260721|3.7%|VERIFICATION_REQUIRED|2|

이 표는 경제적으로 최적이라는 주장용 결과가 아니라 **50-product pipeline no-exception / same-unit ranking smoke**다.

## 18. Known limitations

1. 최종 50개는 모두 **B-grade**다. 실패가 아니라 보수적 quality policy 결과로, golden은 resolver/service dependency가 있고 46 data-driven 상품은 coarse eligibility resolver dependency가 남아 있다.
2. 일부 B 상품의 공개 official HTML은 최고금리/cap은 확인되지만 모든 우대항목의 내부 allocation이 완전하지 않아 aggregate institution entitlement로 남겼다.
3. live MyData, 은행 entitlement API, coupon inventory, sold-out counter는 구현하지 않았다.
4. 동적 페이지의 문서 version date가 없으면 null로 둔다.
5. 완전한 Net Benefit 비용 모델은 이 sprint 범위 밖이다.
6. generated ProductDefinition의 `source_text`는 정확한 원문 문구를 확보하지 않은 경우 비워 두었다. 출처 URL/section은 유지하며 조사 메모는 sidecar에만 둔다.

Source audit에서 바로 수정한 대표 항목:

- 우리은행 장병내일준비적금 월 한도 **300,000원**으로 교정.
- 하나 연금하나 월복리 적금은 2026-07-10 공식 이벤트의 **+0.50%p / 최고 4.65% / 1만명 한정**을 별도 service rule/source로 연결.
- 우리 SUPER주거래 자유적금은 current rate source와 product detail source를 분리해 provenance 2개를 연결.

## 19. 후속 우선순위

1. 상위 demo 후보 10~15개부터 coarse eligibility fact를 실제 leaf Eligibility AST로 분해해 **A-grade**로 승격.
2. `ELIGIBILITY_PROFILE_RESOLVER`, `ACCOUNT_HOLDING_RESOLVER`, `SALARY_TRANSACTION_RESOLVER`, `CARD_PERFORMANCE_RESOLVER` 구현/연결.
3. 한 상품 내부 세부 reward allocation이 aggregate로 남은 B 상품을 최신 PDF/상품설명서로 재감사.
4. live coupon/sold-out availability adapter 추가.
5. 사용자 데모 시나리오용 사실 set을 연결해 UNKNOWN을 해소하고 Top 5 ranking stability 테스트 강화.
6. 후속 Product Type Sprint에서 일시예치형 정기예금을 별도로 설계.

## 20. 최종 Catalog READY 판정

**READY_WITH_LIMITATIONS**

판정 근거:

- 50개 모두 strict ProductDefinition으로 load/validate/evaluate 가능
- 공식 source URL 및 sidecar provenance 존재
- 4 golden fixtures 보존
- Kakao late-streak semantics 보존
- 382/382 기존+신규 regression PASS
- 50-product ApplicationService / Top 5 smoke PASS
- 상품별 ApplicationService/Search/Ranking branch 추가 없음

다만 `READY_FOR_MVP_CATALOG_INTEGRATION`을 선언하기에는 **모든 50개가 B-grade이고, 일부는 가입대상 또는 우대 세부의 institution/service fact dependency가 남아 있어 source-to-leaf-rule 완전성에 제한**이 있다. 현재 artifact는 MVP에 통합해 실제 검색·질문·UNKNOWN 흐름을 검증하기에는 충분하지만, 금융조건 데이터가 완전히 닫혔다고 과장하지 않는다.
