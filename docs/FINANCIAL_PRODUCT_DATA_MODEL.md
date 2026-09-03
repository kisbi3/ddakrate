# ddakrate 금융상품 데이터 구조

- 상태: Living Design Document
- 최종 갱신일: 2026-08-27
- 범위: 상품 데이터와 수집·검사 구조

운영자가 새 데이터를 저장할 때 따라야 할 실제 절차와 현재 발행 계약은 [`data/financial_products/README.md`](../data/financial_products/README.md)를 기준으로 한다. 이 문서는 필드와 관계의 설계 배경을 설명한다.

## 1. 원칙

- 모든 상품은 `FinancialProduct`이며 조건 변경 이력을 보존한다.
- 상품 데이터와 사용자 대화 상태를 분리한다.
- 자주 쓰는 조건만 deterministic하게 구조화하고 나머지는 LLM이 해석하는 Custom으로 저장한다.
- 금리 값과 계산은 항상 deterministic하다.
- 모든 값과 조건은 공식 출처에 연결하며 모르는 값은 만들지 않는다.
- 질문 선택, 추천 순위, 대화 LLM은 별도 설계 범위다.

## 2. 전체 구조

```text
Institution
├── FinancialProduct
│   └── FinancialProductVersion[]
│       ├── 기간·입금·금리·유동성·보호
│       ├── StandardCondition[]
│       └── ProductCustomBinding[]
├── InstitutionCustomDefinition[]
└── SourceDocument[]

ProtectionSchemeDefinition
EvidenceRef
CollectionArtifact
ValidationReport
VersionMetadata
```

상품 JSON 한 건은 특정 시점의 `FinancialProductVersion` 스냅샷이다.

## 3. 상품 분류

| `product_family` | `product_subtype` | 별도 속성 |
|---|---|---|
| `INSTALLMENT_SAVINGS` | `SCHEDULED_INSTALLMENT`, `FLEXIBLE_INSTALLMENT` | `contribution_pattern` |
| `TIME_DEPOSIT` | `STANDARD_TERM_DEPOSIT`, `REVOLVING_TERM_DEPOSIT` | 기간·이자지급·재예치 방식 |
| `PARKING_ACCOUNT` | `null` | 잔액구간·이자지급 주기 |
| `CMA` | `RP`, `MMF`, `MMW`, `ISSUED_NOTE`, `MERCHANT_BANK`, `WRAP` | 운용자산·계약방식 |

subtype enum 자체를 코드로 사용하며 별도 `product_subtype_code`는 만들지 않는다.

적금의 `contribution_pattern`은 다음 세 가지다.

```text
CONSTANT / INCREMENTAL / USER_DEFINED
```

`GOVERNMENT_BOND_RP`는 `RP + underlying_asset=GOVERNMENT_BOND`, `MMW_CONTRACTED`는 `MMW + contract_mode=CONTRACTED`로 저장한다.

정책상품·특판·청년·군인·주택청약 등 검색용 분류는 상품 코드에 넣지 않고 별도 분류로 추가한다.

## 4. 식별자와 버전

### 기관

- 기관군: KSIC 코드와 버전
- 기관의 PK는 ddakrate가 발급하는 중립적인 내부 `institution_id`다.
- 금융위원회 `fssCorpUnqNo`, 법인등록번호 `crno`, 사업자등록번호 `bzno`,
  행정표준기관코드 등은 `identifiers[]`에 `system + value`로 저장한다.
- 계좌·이체 연동: 금융결제원 금융공동망 코드
- 모든 외부 식별자는 `system + value`로 저장한다.
- 외부 식별자가 바뀌거나 특정 공식 데이터에 기관이 누락되어도 Product와
  CustomDefinition의 FK는 바뀌지 않는다.
- 발급된 내부 ID는 재사용·재정렬하지 않는다. 새 기관은 현재 매핑의 마지막
  번호 뒤에 추가하며, 외부 ID 변경은 같은 Institution의 식별자 버전으로 처리한다.
- `fssCorpUnqNo`를 포함한 모든 숫자형 외부 식별자는 앞자리 0을 보존하는
  문자열로 저장한다. 확인되지 않은 식별자는 다른 번호로 대체하지 않는다.
- 법인이 아닌 공공기관은 `crno`를 강제하지 않고 공식 행정기관코드로 식별한다.

### 상품

```text
[institution_id]-[상품군 1자리]-[기관·상품군 내 일련번호 4자리]

1 적금 / 2 예금 / 3 파킹통장 / 4 CMA
```

예시: `INST-KR-000123-1-0001`

상품 subtype, 금리, 기간, 판매상태 등 변경 가능한 값은 상품 코드에 넣지 않는다.

2026-08-25 이전 발행본의 FSS 번호 접두사 상품 코드는 내부 ID 마이그레이션
매핑으로 새 코드에 연결하며, 이전 파일은 발행 이력으로 보존한다.

### Custom

```text
[institution_id]-C-[기관 내 Custom 일련번호 4자리]
```

예시: `INST-KR-000123-C-0001`

### 버전

```text
UNIQUE(product_code, version)
UNIQUE(custom_code, version)
```

별도 `product_version_code`는 저장하지 않는다.

상품 버전은 공식 유효기간만 갖는다.

```text
effective_from / effective_to
```

공식 시행일을 확인하지 못하면 `effective_from`을 만들지 않는다. 금리 조회일은 각 금리의 `as_of`, 최초 관측일은 수집 메타데이터의 `captured_at`에 저장한다.

수집·검증용 값은 별도 `VersionMetadata`에 둔다.

```text
content_fingerprint
captured_at / last_verified_at
data_gaps
```

- 현재 버전은 `effective_to=null`이다.
- 매일 확인해 의미가 같으면 `last_verified_at`만 갱신한다.
- 의미가 바뀌면 이전 버전을 닫고 `version+1`을 추가한다.
- fingerprint에는 수집시각·검사결과·출처 조회시각을 포함하지 않는다.

## 5. FinancialProductVersion

공통 표현은 다음으로 제한한다.

```text
Duration: value + unit(DAY/WEEK/MONTH/YEAR)
MoneyRange: currency + min_value/max_value + min_inclusive/max_inclusive
```

금액·비율은 부동소수점 오차를 피하기 위해 decimal 문자열로 저장한다. 확인하지 못한 값은 `null`로 채우지 않고 필드를 생략한 뒤 `data_gaps`에 기록한다.

```json
{
  "product_code": "INST-KR-000123-1-0001",
  "version": 3,
  "institution_id": "INST-KR-000123",
  "name": "<공식 상품명>",
  "product_family": "INSTALLMENT_SAVINGS",
  "product_subtype": "FLEXIBLE_INSTALLMENT",
  "classifications": [],
  "sale_policy": {},
  "eligibility_policy": {},
  "term_policy": {},
  "cash_flow_policy": {},
  "investment_policy": {},
  "return_policy": {},
  "fee_policy": {},
  "tax_policy": {},
  "liquidity_policy": {},
  "protection_policy": {},
  "standard_conditions": [],
  "custom_bindings": [],
  "effective_from": "date",
  "effective_to": null
}
```

### 가입 대상

반복적으로 검색·판정할 수 있는 기본 자격만 deterministic하게 저장한다.

```text
eligibility_policy
  mode: UNRESTRICTED / RESTRICTED
  allowed_customer_types[]: INDIVIDUAL / SOLE_PROPRIETOR / CORPORATION / ORGANIZATION
  real_name_required
  age_range
    min_age / max_age
    age_basis: FULL_AGE
    reference_date: SUBSCRIPTION_DATE
  nationality_scope: ANY / KOREAN_ONLY / FOREIGNER_ONLY
  residency_scope: ANY / RESIDENT_ONLY / NON_RESIDENT_ONLY
  military_service_required
  account_limit
    max_active_accounts
    subject_scope: PERSON / BUSINESS
    product_scope: THIS_PRODUCT
  source_ref_ids[]
```

`military_service_required`는 군인 여부까지만 판정한다. 복무 형태, 자격확인서, 정책상 인정 대상처럼 상품마다 다른 세부 기준은 Custom에 저장한다. 연결계좌 보유처럼 공통 조건으로 표현 가능한 가입 요건은 `StandardCondition`을 사용한다.

### 판매

```text
sale_policy
  status: UPCOMING / ON_SALE / SUSPENDED / ENDED
  sale_period: start / end / end_inclusive
  subscription_channels[]: BRANCH / WEB / MOBILE_APP / TELEPHONE / PARTNER
  quantity_limit: max_count / unit(ACCOUNT) / exhaustion_behavior(END_SALE)
  source_ref_ids[]
```

`sale_period`, `subscription_channels`, `quantity_limit`은 적용되는 경우에만 둔다. 가입자격은 `eligibility_policy`, StandardCondition 또는 Custom으로 저장한다.

카카오뱅크·케이뱅크·토스뱅크는 기관 데이터에 인터넷전문은행임이 확인되면 `MOBILE_APP` 가입 채널을 기본 적용한다. `WEB` 등 다른 채널은 상품 근거가 있을 때만 추가한다.

### 기간

```text
term_policy
  kind: FIXED / DISCRETE / RANGE / OPEN_ENDED
  unit: DAY / WEEK / MONTH / YEAR
  selection_units[]           가입자가 기간을 지정할 수 있는 단위
  fixed_value                 FIXED
  allowed_values[]            DISCRETE
  min_value / max_value / step RANGE
  source_ref_ids[]
```

`kind`에 필요한 값만 저장한다. `OPEN_ENDED`에는 기간 값을 두지 않는다. `selection_units`는 카카오뱅크 정기예금처럼 같은 범위 안에서 월·일 단위 지정을 모두 허용할 때만 사용한다.

### 입금

```text
cash_flow_policy
  funding_type: LUMP_SUM / RECURRING / ON_DEMAND
  currency
  amount_rules[]
    scope: INITIAL_DEPOSIT / PER_CONTRIBUTION / PER_PERIOD / TOTAL_PRINCIPAL / BALANCE
    min_value / max_value / step / allowed_values[]
  contribution_frequency: DAILY / WEEKLY / MONTHLY / IRREGULAR
  contribution_pattern: CONSTANT / INCREMENTAL / USER_DEFINED
  max_contribution_count
  source_ref_ids[]
```

금액은 decimal 문자열로 저장한다. 납입 주기와 패턴은 반복납입 상품에만 둔다.

### 금리·수익

```text
return_policy
  return_kind: INTEREST / POSTED_YIELD / PERFORMANCE_LINKED
  calculation_method: SIMPLE / COMPOUND / DAILY_BALANCE / PERFORMANCE_LINKED
  accrual_basis: PRINCIPAL / EACH_CONTRIBUTION / END_OF_DAY_BALANCE
  day_count_basis: ACTUAL_365_FIXED / ACTUAL_366_FIXED / ACTUAL_ACTUAL
  payment_timing: MATURITY / PERIODIC / ON_REDEMPTION
  payment_frequency: Duration
  payment_schedule
    calculation_day: ordinal / weekday / holiday_adjustment
    credit_offset: value / unit(BUSINESS_DAY)
    credit_destination: PRINCIPAL_BALANCE / LINKED_ACCOUNT
  balance_tier_method: MARGINAL / WHOLE_BALANCE
  rate_entries[]
    rate_id
    role: BASE / PREFERENTIAL / EARLY_TERMINATION / POST_MATURITY
    application_event: MATURITY / EARLY_TERMINATION / POST_MATURITY / ANY
    calculation
      type: FIXED / VARIABLE_POSTED / REFERENCE_MULTIPLIER
      value / unit: PERCENT | PERCENTAGE_POINT
      reference_rate / multiplier / time_proration / floor_rate / rounding
    applies_to[]
      basis: CONTRACT_TERM / ELAPSED_TERM / POST_MATURITY_ELAPSED / ACCOUNT_AGE / BALANCE
      range
    as_of
    effective_from / effective_to
    source_ref_ids[]
  preferential_application: mode(SUM) / cap_value / cap_unit(PERCENTAGE_POINT)
  advertised_max_rate: value / unit / source_ref_ids[]
  source_ref_ids[]
```

- 비율은 decimal 문자열로 저장하고 `%`와 `%p`를 구분한다.
- `applies_to`는 기간·잔액 구간이 있을 때만 사용하며 경계의 포함 여부를 명시한다.
- 잔액 구간별로 해당 금액에 각각 금리를 적용하면 `MARGINAL`, 전체 잔액에 하나의 구간금리를 적용하면 `WHOLE_BALANCE`다.
- `REFERENCE_MULTIPLIER`는 공식 기준금리에 배율을 적용하는 중도해지·만기후 금리에만 사용한다. 자유식 계산 표현 언어는 만들지 않는다.
- `PREFERENTIAL` 항목은 StandardCondition 또는 ProductCustomBinding의 `reward_refs`가 참조한다.
- 광고 최고금리는 합계 검증용이며 계산 입력으로 사용하지 않는다.
- 실적배당형은 확정 값으로 만들지 않는다. 공시수익률이 있으면 `POSTED_YIELD + VARIABLE_POSTED + as_of`로만 저장한다.
- 개별 금리쿠폰이 확인되지 않은 기본 추천은 쿠폰 미보유로 계산한다. 실제 쿠폰을 수집하면 조건은 Custom, 숫자 우대금리는 deterministic RateEntry로 만들고 상품에 binding한다.

### CMA 운용·자동재투자

CMA의 운용자산과 자동 운용 여부는 subtype만으로 추론하지 않고, 공식 설명이 있는 경우 상품에 선택적으로 저장한다.

```text
investment_policy
  underlying_asset: BONDS / GOVERNMENT_BOND / MONEY_MARKET_FUND / ...
  automatic_investment: boolean
  source_ref_ids[]
```

`underlying_asset`는 공식 원문이 확인한 범위만 사용한다. 예를 들어 “회사 보유 채권”까지만 확인된 RP형은 `BONDS`로 저장하며 국공채로 확장하지 않는다.

무기한·수시입출금 CMA의 accrued return이 일정 주기로 원금에 재투자되고 공시금리가 갱신되는 경우, 예금 만기 재예치인 `liquidity_policy.rollover_policy`와 구분해 다음을 사용한다.

```text
return_policy.reinvestment_policy
  mode: NONE / AUTOMATIC
  interval: Duration
  principal_treatment: REINVEST_PRINCIPAL / REINVEST_PRINCIPAL_AND_RETURN
  rate_reset: KEEP_ORIGINAL / REFRESH_AT_INTERVAL
  source_ref_ids[]
```

`rate_reset`은 공식 문서가 재투자 시점의 금리 갱신을 명시할 때만 저장한다.

### 수수료

```text
fee_policy.entries[]
  fee_id
  fee_type: MANAGEMENT / TRANSFER / WITHDRAWAL / REDEMPTION / OTHER
  value / unit: KRW | PERCENT
  channel_scope[] / network_scope[] / count_limit
  condition_ref_ids[]
  source_ref_ids[]
```

해당하지 않으면 `fee_policy` 자체를 생략한다. 복잡한 면제 설명은 Custom에 두고, 계산 가능한 값과 연결만 여기에 둔다.

### 세제

```text
tax_policy
  default_treatment: TAXABLE
  available_treatments[]: TAXABLE / CONDITIONAL_TAX_EXEMPT
  treatment_code
  post_maturity_interest_treatment
  source_ref_ids[]
```

상품이 허용하는 세제 선택지만 저장한다. 사용자 자격과 세법 계산은 별도 정책·Custom으로 연결하며 상품 데이터에서 추론하지 않는다. 해당하지 않으면 생략한다.

### 유동성

```text
liquidity_policy
  access_mode: ON_DEMAND / AT_MATURITY / REDEMPTION_REQUIRED
  early_termination_allowed
  early_termination_rate_refs[]
  partial_withdrawal
    allowed / max_count_per_contract_period / min_remaining_balance
    withdrawn_amount_rate_role: EARLY_TERMINATION
  maturity_options[]: MANUAL_CLOSE / AUTO_CLOSE / AUTO_ROLLOVER
  rollover_policy
    max_count
    principal_mode: PRINCIPAL / PRINCIPAL_AND_INTEREST
    term_mode: SAME_AS_ORIGINAL
    rate_reset: REFRESH_AT_ROLLOVER
  settlement_delay: min_value / max_value / unit(CALENDAR_DAY | BUSINESS_DAY)
  source_ref_ids[]
```

적용되지 않는 세부 항목은 생략한다. 선택 가능한 자동연장이 있어도 상품 자체가 회전식인 것은 아니므로 `STANDARD_TERM_DEPOSIT`을 유지하고 `rollover_policy`로 표현한다.

### 보호

공통 보호한도와 유효기간은 `ProtectionSchemeDefinition`으로 한 번 저장한다. 상품에는 다음만 둔다.

```text
coverage_status: PROTECTED / NOT_PROTECTED / PARTIALLY_PROTECTED
scheme_ref
condition_ref_ids
source_ref_ids
```

기관군이나 CMA subtype만 보고 보호 여부를 추론하지 않는다.

### 검색 분류

`classifications[]`에는 코드에 넣지 않기로 한 검색용 속성과 근거를 저장한다.

```text
tag: SPECIAL_SALE / YOUTH / MILITARY / HOUSING_SUBSCRIPTION / YOUTH_LEAP_ACCOUNT
source_ref_ids[]
```

기관군은 Institution, 자유·정기적금은 `product_subtype`에서 판단하므로 중복 태그를 만들지 않는다.

### 네 상품군 대입

| 상품군 | `term_policy` | `cash_flow_policy` | `return_policy` | `liquidity_policy` |
|---|---|---|---|---|
| 적금 | 고정·선택·범위 | 반복납입 | 기본·우대금리 | 만기·중도해지 |
| 예금 | 고정·선택·범위 | 일시납 | 기간별 금리 | 만기·중도해지 |
| 파킹통장 | 무기한 | 수시입출금 | 일잔·잔액구간 금리 | 수시입출금 |
| CMA | 무기한 | 수시입출금 | 공시수익률·실적배당 | 수시입출금 또는 환매 |

상품군별 별도 최상위 schema를 만들지 않고 subtype과 정책 값만 달리한다.

## 6. 조건

### StandardCondition

다음 열 가지는 전용 schema와 evaluator를 갖는다.

```text
NON_FACE_TO_FACE_SIGNUP   비대면가입
BANK_APP_USAGE           은행앱사용
SALARY_LINKAGE           급여연동
PENSION_LINKAGE          연금연동
UTILITY_PAYMENT_LINKAGE  공과금연동
CARD_USAGE               카드사용
FIRST_TRANSACTION        첫거래
DEMAND_DEPOSIT_ACCOUNT   입출금통장 연계
ROLLOVER                 재예치
MARKETING_CONSENT        마케팅 동의
```

```json
{
  "condition_id": "COND-001",
  "condition_type": "SALARY_LINKAGE",
  "schema_version": 1,
  "purpose": "PREFERENTIAL_RETURN",
  "criteria": {},
  "reward_refs": ["RATE-PREF-001"],
  "source_ref_ids": ["SRCREF-COND-001"]
}
```

`purpose`는 `ELIGIBILITY` 또는 `PREFERENTIAL_RETURN`이다. `criteria`에는 공식 금액·횟수·기간 등 판정 기준을 저장한다. `reward_refs`는 구간별 우대금리처럼 여러 금리 항목을 연결할 수 있다. 이는 상품 데이터이며 사용자에게 필드별로 질문하지 않는다. 필요한 조건만 한 번에 확인하고 같은 답변을 여러 상품에 공유한다.

#### criteria 공통형

```json
{
  "subject": {},
  "requirements": [
    {
      "metric": "BOOLEAN | EVENT_COUNT | DISTINCT_PERIOD_COUNT | AMOUNT_PER_PERIOD | TOTAL_AMOUNT | ABSENCE",
      "operator": "EQ | GTE | LTE",
      "value": "boolean 또는 decimal 문자열",
      "unit": "COUNT | KRW",
      "period_unit": "DAY | WEEK | MONTH | YEAR",
      "consecutive": false
    }
  ],
  "window": {
    "start_ref": "SUBSCRIPTION_DATE | MATURITY_DATE | EVALUATION_DATE",
    "start_offset": {"value": 0, "unit": "DAY | WEEK | MONTH | YEAR"},
    "start_inclusive": true,
    "end_ref": "SUBSCRIPTION_DATE | MATURITY_DATE | EVALUATION_DATE",
    "end_offset": {"value": 0, "unit": "DAY | WEEK | MONTH | YEAR"},
    "end_inclusive": false
  },
  "evaluation_events": ["SUBSCRIPTION | INTEREST_CALCULATION | ACCOUNT_CLOSE | MATURITY"]
}
```

필요한 필드만 사용하며 `requirements[]`는 기본적으로 모두 충족해야 한다. OR는 별도 조건들을 `ConditionGroup(ANY)`로 묶는다.

| `condition_type` | `subject`의 허용 필드 | 주로 쓰는 metric |
|---|---|---|
| `NON_FACE_TO_FACE_SIGNUP` | `channels[]` | `BOOLEAN` |
| `BANK_APP_USAGE` | `app_ref`, `action` | `BOOLEAN`, `EVENT_COUNT` |
| `SALARY_LINKAGE` | `destination_scope` | `AMOUNT_PER_PERIOD`, `DISTINCT_PERIOD_COUNT` |
| `PENSION_LINKAGE` | `pension_types[]`, `destination_scope` | `AMOUNT_PER_PERIOD`, `DISTINCT_PERIOD_COUNT` |
| `UTILITY_PAYMENT_LINKAGE` | `utility_categories[]`, `payment_scope` | `EVENT_COUNT`, `DISTINCT_PERIOD_COUNT` |
| `CARD_USAGE` | `issuer_id`, `card_scope` | `TOTAL_AMOUNT`, `AMOUNT_PER_PERIOD`, `DISTINCT_PERIOD_COUNT` |
| `FIRST_TRANSACTION` | `institution_id` 또는 `institution_scope=PRODUCT_INSTITUTION`, `absence_scope` | `ABSENCE` |
| `DEMAND_DEPOSIT_ACCOUNT` | `institution_id`, `relationship` | `BOOLEAN` |
| `ROLLOVER` | `source_product_scope`, `rollover_mode` | `BOOLEAN`, `EVENT_COUNT` |
| `MARKETING_CONSENT` | `institution_id` 또는 `institution_scope=PRODUCT_INSTITUTION`, `consent_scope` | `BOOLEAN` |

표에 없는 기관 고유 개념이나 인정방식이 필요하면 `subject`를 확장하지 않고 Custom으로 보낸다. 예를 들어 일반 급여입금은 `SALARY_LINKAGE`, 신한 급여클럽의 `월급봉투` 인정은 Custom이다.

### InstitutionCustomDefinition

표준 타입으로 정확히 표현할 수 없는 조건은 기관 단위 Custom으로 한 번 저장한다.

```json
{
  "custom_code": "INST-KR-000123-C-0001",
  "version": 2,
  "institution_id": "INST-KR-000123",
  "definition_type": "PROGRAM",
  "title": "월급봉투",
  "semantic_tags": ["SALARY_LINKAGE"],
  "evaluation_mode": "LLM",
  "content_blocks": [
    {
      "block_id": "BLOCK-001",
      "block_type": "DEFINITION | REQUIREMENT | EXCEPTION | PROCEDURE | DETAIL | OTHER",
      "content": {},
      "source_ref_ids": ["SRCREF-001"]
    }
  ],
  "links": [
    {
      "relation": "PROVIDED_BY",
      "target_type": "INSTITUTION",
      "target_ref": "INST-KR-000123",
      "source_ref_ids": ["SRCREF-001"]
    }
  ],
  "effective_from": "2026-01-01",
  "effective_to": null
}
```

`definition_type`은 `CONDITION`, `SERVICE`, `PROGRAM`, `POLICY`, `OTHER` 중 하나다. `content_blocks`는 공식 조건·예외·상세 설명을 담고, `links`는 기관·계좌·카드·앱·서비스·다른 Custom·공식 문서와 연결한다. 내용과 연결에는 각각 공식 근거를 붙인다.

### ProductCustomBinding

```json
{
  "product_code": "INST-KR-000123-1-0001",
  "product_version": 3,
  "custom_code": "INST-KR-000123-C-0001",
  "custom_version": 2,
  "purpose": "PREFERENTIAL_RETURN",
  "reward_refs": ["RATE-PREF-001"],
  "source_ref_ids": []
}
```

```text
UNIQUE(product_code, product_version, custom_code, custom_version)
```

- Custom 하나는 여러 상품에 연결할 수 있다.
- 상품 하나도 여러 Custom을 가질 수 있다.
- binding의 `purpose`로 가입조건·우대조건·설명 연결을 구분한다.
- 상품별 우대금리는 binding의 `reward_refs`에 연결한다.
- 과거 상품은 당시 Custom 버전을 계속 참조한다.

### 판정 경계

```text
Standard 조건 판정        deterministic
Custom 조건 판정          LLM
우대금리 값·적용·합산     deterministic
```

단순 `ALL`·`ANY` 조합은 `ConditionGroup`으로 처리한다. 복잡한 중첩 조건은 Custom으로 저장한다.

`NOT_ASKED`와 `ACKNOWLEDGED_UNKNOWN`은 상품이 아니라 검색 세션의 상태다.

## 7. 출처·수집·검사

### Evidence

`SourceDocument`와 문서 내 위치인 `EvidenceRef`를 분리한다.

```text
SourceDocument: 발행기관, 문서종류, 제목, 버전일, URL, content hash
EvidenceRef: source_id, 페이지·섹션·원문, supports[]
```

출처 권위:

```text
OFFICIAL_INSTITUTION / REGULATORY_DISCLOSURE / OFFICIAL_ASSOCIATION
COMPARISON_SERVICE / SECONDARY
```

- 계산과 설명에 쓰는 조건은 공식 근거가 필요하다.
- 비교 사이트는 발견용이며 공식 검증 근거가 아니다.
- 적용되지만 확인하지 못한 필드는 상품 본체가 아니라 `VersionMetadata.data_gaps`에 기록한다.
- 적용되지 않는 필드는 생략한다.

### 수집과 검사 분리

```text
공식 원문
  → 수집 LLM
  → CollectionArtifact
       ├── deterministic 검사
       └── 독립 LLM 의미 검사
             ↓ 둘 다 PASS
       FinancialProductVersion 또는 CustomDefinition 발행
```

deterministic 검사는 schema, 참조, 금액·기간·금리, 버전, 우대금리 합계를 확인한다.

검사 LLM은 원문 누락, 논리 왜곡, 잘못된 연결, 근거 없는 내용을 확인한다. 수집 결과를 직접 수정하지 않고 `ValidationReport`만 만든다.

둘 중 하나라도 실패하거나 `NEEDS_REVIEW`이면 자동 발행하지 않는다.

### CollectionArtifact

수집 결과는 곧바로 상품으로 저장하지 않고, 불변 후보 스냅샷으로 보관한다.

```json
{
  "artifact_id": "ART-000001",
  "revision": 2,
  "target_type": "FINANCIAL_PRODUCT_VERSION | INSTITUTION_CUSTOM_DEFINITION",
  "candidate": {},
  "source_ref_ids": ["SRCREF-001"],
  "data_gaps": [],
  "collector_version": "product-collector-v1",
  "collected_at": "datetime"
}
```

`UNIQUE(artifact_id, revision)`으로 관리한다. 수정이 필요하면 기존 결과를 덮어쓰지 않고 `revision+1`을 만든다. 원문 전체, LLM 응답 원본, 신뢰도 점수는 중복 저장하지 않는다. 원문은 `SourceDocument`, 근거 위치는 `EvidenceRef`가 담당한다.

### ValidationReport

```json
{
  "report_id": "VAL-000001",
  "artifact_ref": {"artifact_id": "ART-000001", "revision": 2},
  "validator": {"type": "DETERMINISTIC", "version": "1"},
  "result": "PASS | FAIL | NEEDS_REVIEW",
  "issues": [
    {
      "code": "MISSING_OFFICIAL_EVIDENCE",
      "path": "candidate.standard_conditions[0].criteria",
      "message": "공식 인정기준 근거가 없음",
      "source_ref_ids": []
    }
  ],
  "validated_at": "datetime"
}
```

`validator.type`은 `DETERMINISTIC` 또는 `LLM_SEMANTIC`이다. 별도 workflow 상태는 저장하지 않고 해당 artifact revision에 연결된 검사 결과로 판단한다.

### 발행 규칙

1. 정확히 같은 artifact revision에 대한 두 검사 결과가 모두 `PASS`여야 한다.
2. candidate의 모든 참조 대상은 이미 발행되어 있어야 한다. 따라서 Custom을 먼저 발행하고 상품을 발행한다.
3. 기존 현재 버전과 fingerprint가 같으면 새 버전을 만들지 않고 `last_verified_at`만 갱신한다.
4. fingerprint가 다르면 기존 버전의 `effective_to`를 채우고 새 버전을 추가한다.
5. `FAIL` 또는 `NEEDS_REVIEW`이면 새 artifact revision으로 수정한 뒤 두 검사를 다시 수행한다.

### 기존 데이터 대입 결과

| 사례 | 새 구조에서의 처리 | 현재 데이터 판정 |
|---|---|---|
| 하나은행 급여실적 우대 | `SALARY_LINKAGE` StandardCondition과 deterministic 우대금리 | 구조화 가능하지만 공식 원문 인용이 없어 재수집 전 `NEEDS_REVIEW` |
| 신한 급여클럽 월급봉투 | 기관 CustomDefinition → 상품 binding → deterministic 우대금리 | 연결 가능하지만 월급봉투 인정 정의의 별도 공식 근거가 없어 재수집 전 `NEEDS_REVIEW` |
| BNK 위더스WithUs 자유적금 | 공식 페이지를 다시 수집해 1~36개월 기간별 금리·중도해지·만기후 금리까지 bundle 작성. 가입자격은 policy, 첫거래·마케팅동의는 Standard, ESG는 Custom | 참조와 `2.3 + 2.5 = 4.8` 검사는 통과. 기관 ID·정확한 효력 시작일 재확인과 독립 LLM 검사가 남아 발행은 `NEEDS_REVIEW` |
| 카카오뱅크 정기예금 | 가입자격, 월·일 단위 기간 선택, 기간별 금리, 긴급출금, 자동연장을 예금 policy로 작성 | `selection_units`와 상세 유동성 구조를 보완. 개별 금리쿠폰은 공통 금리로 만들지 않고 data gap 처리 |
| BNK파킹통장 | 잔액구간별 일별 이자, 월별 원가, 첫거래 가입자격, 3개월 마케팅 우대를 작성 | `MARGINAL` 잔액구간, 지급일정, `ACCOUNT_AGE`를 보완. 기관 선행 수집 전이라 ID와 상품 코드는 발급하지 않음 |
| KB CMA (RP형) | 회사 보유 채권 RP 자동운용, 31일 단위 재투자, 개인·법인 공시수익률, 비예금자보호를 작성 | CMA `investment_policy`와 무기한 상품의 `reinvestment_policy`를 보완. 기관 선행 수집 전이라 ID와 상품 코드는 발급하지 않음 |

실제 예시는 [BNK 적금 bundle](../data/financial_product_model_examples/bnk_withus/bundle.json), [카카오뱅크 예금 bundle](../data/financial_product_model_examples/kakaobank_term_deposit/bundle.json), [BNK 파킹통장 bundle](../data/financial_product_model_examples/bnk_parking/bundle.json)에 저장했다. 실제 상품 대입에서 발견된 필드만 schema에 반영했다.

추가 CMA 예시는 [KB CMA RP bundle](../data/financial_product_model_examples/kb_cma_rp/bundle.json)에 저장했다.

네 상품군 대입으로 policy, Standard, Custom의 수집·검사·발행 경계를 확인했다. 기존 데이터에 근거가 빠져 있으면 공식 자료를 재수집하며, 확인되지 않은 값은 꾸며내지 않는다. 현재 요약형 예금·파킹통장·CMA JSON도 동일하게 공식 근거가 부족하면 발행하지 않는다.

## 8. 현재 데이터와 마이그레이션

현재 발행 건수와 상품군별 분포는 `data/financial_products/normalized/index.json` 및 해당 index가 가리키는 manifest의 `counts`를 기준으로 한다. 문서에 고정 수치를 복사하지 않는다. 기존 200개 multi-product catalog와 50개 적금 catalog는 회귀·마이그레이션 원본으로 보존하며, 서비스의 기본 진입점은 normalized index다.

기존 적금 조건 다수는 상품별 Boolean으로 뭉쳐 있어 공식 금액·기간·인정방식이 손실되어 있다. 추측해서 변환하지 않고 공식 자료에서 다시 수집한다.

마이그레이션 순서:

1. canonical schema와 기존 JSON adapter를 추가한다.
2. 변환 결과를 `CollectionArtifact`로 저장한다.
3. 두 검사를 통과한 데이터만 새 버전으로 발행한다.
4. 공식 근거가 부족한 값은 `VersionMetadata.data_gaps`로 남긴다.
5. 기존·신규 로더를 병행 검증한다.
6. 마지막에 `application_service.py`의 상품별 조건·설명 하드코딩을 제거한다.

현재 canonical JSON은 `data/financial_products/normalized/products/`에 발행되어 있고, `src/eligibility/catalog/normalized_loader.py`가 index·manifest·sha256·기관 및 참조 무결성을 검증한 뒤 runtime 모델로 변환한다. 독립된 단일 canonical JSON Schema는 아직 없으므로, 새 수집은 운영 README의 필드 계약과 현행 loader 검사를 함께 통과해야 한다.
