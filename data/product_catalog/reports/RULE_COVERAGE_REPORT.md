# RULE_COVERAGE_REPORT

## 1. Coverage 정의

이번 50-product 통계는 **ProductDefinition에 실제 저장된 executable decision unit**을 분류한다. 기존 10-product stress test의 110개 natural-language atomic node와는 granularity가 다르다. 은행 고유 서비스의 내부 여러 절차를 하나의 authoritative service fact로 모델링한 경우 그 내부 문장을 다시 atomic node로 중복 집계하지 않았다.

따라서 아래 비율은 현재 engine catalog의 **실행 경계에서 필요한 dependency mix**를 보는 지표다.

## 2. 50-product executable decision-unit coverage

Total: **252**

|Category|Count|Share|
|---|---:|---:|
|DSL_SUPPORTED|52|20.6%|
|RESOLVER_NEEDED|50|19.8%|
|SERVICE_KNOWLEDGE_NEEDED|81|32.1%|
|USER_FACT_NEEDED|69|27.4%|
|SCHEMA_OR_DSL_GAP|0|0.0%|

## 3. 기존 10-product stress test와 비교

|Study|DSL|Resolver|Service|User Fact|Gap|
|---|---:|---:|---:|---:|---:|
|10-product adversarial / 110 natural-language atomic nodes|50.9%|14.5%|26.4%|4.5%|3.6%|
|50-product catalog / 252 executable decision units|20.6%|19.8%|32.1%|27.4%|0.0%|

직접 비율 비교는 금지한다. 이번 catalog는 FutureAchievement/ASK_USER를 더 적극적으로 노출하고, public source에서 서비스 내부 의미를 완전 분해하지 못한 경우 service fact를 의도적으로 남겼기 때문에 User Fact/Service 비중이 높다. 중요한 결과는 **선정 50개를 막는 반복 Core DSL/schema gap이 0**이었다는 점이다.

## 4. Resolver reuse matrix

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

`ELIGIBILITY_PROFILE_RESOLVER`가 46개인 이유는 46 data-driven 상품의 가입대상을 이번 sprint에서 product-specific Python branch로 만들지 않고 generic authoritative eligibility fact로 연결했기 때문이다. 이는 다음 A-grade uplift의 최우선 대상이다.

## 5. Institution Service Knowledge reuse matrix

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

가장 반복되는 명시적 service type은 마케팅/동의 계열이며, 나머지는 은행별 쿠폰·앱·관계·특정 이벤트 semantics로 분산된다.

## 6. User Fact / Future Intent

Sidecar에서 explicit future-intent fact types: **9**

- `KAKAO_26W_26W_INTENT`
- `KAKAO_26W_7W_INTENT`
- `KAKAO_FREE_AUTO_TRANSFER_MONTH`
- `KAKAO_M1_MANUAL_DEPOSIT_DAY`
- `KBANK_MYKIDS_QUALIFYING_DEPOSIT_MONTH`
- `KN_TOUCH_DEPOSIT_DAY`
- `TOSS_CHILD_AUTO_TRANSFER_ALL`
- `TOSS_FREE_AUTO_TRANSFER_ALL`
- `WILL_KEEP_HEALTH_DATA_PERMISSIONS`

Executable coverage 분류의 `USER_FACT_NEEDED=69`는 이 sidecar unique type 수와 같은 의미가 아니다. 하나의 future plan이 여러 rule/goal decision unit에 나타날 수 있다.

## 7. Schema / DSL gap backlog

Final main catalog blocker: **0**.

- `CountConsecutiveRule`과 `CountDistinctPeriodsRule`은 이미 v0.4.6 Core에 존재한다.
- 카카오 26주 7주 rule은 `from_sequence=None`으로 late streak를 허용한다.
- 기간별 rate table은 fixed-term variant 전략으로 왜곡을 피했다.
- 제휴/기관 고유 의미는 resolver/service facts로 유지했다.

## 8. 다음 투자 우선순위

1. `ELIGIBILITY_PROFILE_RESOLVER`
2. `ACCOUNT_HOLDING_RESOLVER`
3. `CARD_PERFORMANCE_RESOLVER`
4. `SALARY_TRANSACTION_RESOLVER`
5. `AUTO_TRANSFER_RESOLVER`
6. `RELATED_PERSON_RESOLVER`
7. coupon/event/sold-out service adapters
