# Financial Eligibility Engine v0.3.1 Hardening Report

**기준 코드:** `financial-eligibility-engine-v0.3`  
**산출 버전:** `financial-eligibility-engine-v0.3.1`  
**Sprint 성격:** Correctness Hardening + Application Layer 최소 Schema 보강  
**판정:** `READY_FOR_APPLICATION_LAYER`

---

# A. Baseline

## A-1. 수정 전 테스트

현재 세션의 v0.3 working tree를 별도 v0.3.1 작업 디렉터리로 복제한 뒤, 코드를 수정하기 전에 전체 테스트를 다시 실행했다.

```text
116 passed in 0.40s
0 failed
```

원본 결과는 `reports/baseline-v0.3-before-v0.3.1.txt`에 보존했다.

## A-2. 확인한 기존 모듈 구조

```text
src/eligibility/
├─ audit/          append-only Audit Event / correlation / replay
├─ engine/         deterministic Rule / Rate / Interest evaluation
├─ fixtures/       신한·카카오·IBK·하나 및 Future Goal fixtures
├─ goal/           GoalFactory / GoalTracker / AlertEngine
├─ ingestion/      Rule Extractor / Draft / Validator / Review / Activation
├─ llm/            provider-independent LLM Gateway
├─ schema/         Rule / Product / Evaluation / User Fact schema
└─ visualization/  Rule / Fact / Trace Mermaid
```

v0.3의 LLM, deterministic core, Goal Tracker, Audit 경계를 유지했다. 이번 변경은 기존 엔진을 재작성하지 않고 authority gate, temporal opportunity projection, validation, schema 계층을 보강하는 방식으로 구현했다.

---

# B. Fixed Correctness Issues

## B-1. Fact authority / semantic type

### 문제

동일한 `fact_type` 이름만 일치하면 `USER_DECLARED` 값이 기관 관측 Event처럼 집계될 수 있었다. 예를 들어 사용자가 “지난 6개월 월급봉투를 받았다”고 입력한 값을 실제 월급봉투 수령 Event로 계산하면 STRICT 판정이 오염된다.

### 구현

다음을 추가했다.

```text
FactSemanticType
- OBSERVED_FACT
- OBSERVED_EVENT
- DERIVED_FACT
- FUTURE_INTENT
- SELF_REPORTED_FACT

EvaluationTrustMode
- STRICT
- MVP_PROVISIONAL

VerificationLevel
- VERIFIED
- MIXED
- SELF_REPORTED
- UNKNOWN
```

재사용 가능한 `FactAcceptancePolicy`와 `engine/fact_acceptance.py`를 도입했다.

```text
STRICT
→ strict_semantic_types AND strict_source_types만 사용

MVP_PROVISIONAL
→ strict evidence + 허용된 SELF_REPORTED_FACT/USER_DECLARED 사용 가능
→ 사용한 Rule과 ProductEvaluation에 provisional metadata 기록
```

`COUNT_DISTINCT_PERIODS`, `COUNT_DISTINCT_MONTHS`, `COUNT_CONSECUTIVE`는 기본적으로 authority policy를 적용한다. 같은 gate를 `FactComparisonRule`에도 명시적으로 적용할 수 있으므로 과거 보유 여부, 현재 카드 보유 여부 같은 self-report 질문도 동일한 trust model로 처리한다.

핵심 결과:

```text
FUTURE_INTENT       ≠ OBSERVED_EVENT
SELF_REPORTED_FACT  ≠ INSTITUTION_VERIFIED
```

STRICT에서 self-report 6건은 실제 progress 6으로 계산되지 않고:

```text
status = UNKNOWN
reason_code = FACT_AUTHORITY_INSUFFICIENT
progress = 0 / 6 MONTH
```

MVP_PROVISIONAL에서는 같은 입력으로 가상 분석을 수행할 수 있으나:

```text
is_provisional = true
rule.verification_level = SELF_REPORTED
product.verification_level = MIXED
provisional_rule_ids = [RATE_SALARY_ENVELOPE_6M]
```

가 남는다.

주요 파일:

```text
src/eligibility/engine/fact_acceptance.py
src/eligibility/engine/fact_resolver.py
src/eligibility/engine/evaluator.py
src/eligibility/schema/enums.py
src/eligibility/schema/rule.py
src/eligibility/schema/user_fact.py
src/eligibility/schema/evaluation.py
```

## B-2. Goal remaining opportunity double count

### 문제

현재 월/일이 이미 qualifying progress에 포함됐는데 미래 opportunity에도 다시 포함될 수 있었다.

### 구현

`engine.temporal.project_remaining_opportunities()`를 공통 primitive로 추가하고 RuleEvaluator와 GoalFactory/GoalTracker가 같은 bucket semantics를 사용하도록 통합했다.

```text
future_periods
= available DAY/WEEK/MONTH buckets from as_of to deadline
- already qualified bucket keys
```

현재 bucket은:

```text
아직 미달성 → 남은 opportunity로 유지
이미 달성   → future opportunity에서 제외
```

또한 sync가 끊긴 Goal update에서는 요청된 progress뿐 아니라 새 `qualified_opportunity_keys`도 신뢰하지 않는다. 따라서 coverage가 없는 입력이 future bucket을 잘못 소비하지 않는다.

검증 결과:

```text
월급봉투 현재 월 1개월 달성
current_progress = 1
future_remaining_opportunities = 11

현재 일자 이미 달성
current_progress = 1
future_remaining_opportunities = 364
```

과거 여러 bucket과 deadline 당일/당월 boundary도 테스트했다.

## B-3. RuleDraft / AST identity

다음을 semantic validation error로 고정했다.

```text
RuleDraft.rule_id != RuleAST.rule_id
→ RULE_IDENTITY_MISMATCH
→ activation blocked
```

name 정책은 warning이 아니라 error를 선택했다.

```text
RuleDraft.name != RuleAST.name
→ RULE_NAME_IDENTITY_MISMATCH
→ activation blocked
```

선택 이유는 RequiredFact, Audit, Golden Diff, Evaluation Trace, ProductDefinition이 하나의 executable Rule identity를 공유해야 하기 때문이다. 이름이 다르면 사용자 Trace와 내부 Rule Store가 서로 다른 Rule처럼 보일 수 있다.

## B-4. Provenance preservation

`RuleDraft.provenance`가 있고 `ast.source`가 없으면 activation 시 검증된 provenance를 `SourceReference`로 materialize한다.

보존 필드:

```text
document
document_id
page
section
source_url
source_text
```

AST source와 Draft provenance가 모두 존재할 때 다음이 충돌하면 overwrite하지 않고 차단한다.

```text
document / document_id / page / section / source_url / source_text
```

하나의 RuleDraft에 서로 다른 provenance 위치가 여러 개 있어도 `CONFLICTING_RULE_PROVENANCE` error가 된다.

검증된 흐름:

```text
RuleDraft provenance
→ ACTIVE ProductDefinition Rule.source
→ RuleEvaluation.source_provenance
→ Evaluation Trace의 PRODUCT_DOCUMENT 근거
```

## B-5. Evaluation identity

선택한 방식은 **A. ContributionPlan을 evaluation_id hash에 포함**이다.

```text
evaluation_id hash input
= ProductDefinition
+ UserFactStore
+ EvaluationContext
+ ContributionPlan
+ EvaluationTrustMode
```

이 방식을 선택한 이유:

1. 현재 `ProductEvaluation` 하나가 eligibility/rate뿐 아니라 interest estimates까지 포함한다.
2. 별도의 InterestCalculationId를 추가하면 결과 모델과 Audit correlation을 더 크게 변경해야 한다.
3. ContributionPlan을 hash에 포함하는 것이 최소 변경으로 현재 aggregate의 identity를 일관되게 만든다.

검증:

```text
월 100,000원 → EVAL-c168fee4447cacc9 / 세전 예상이자 26,325원
월 500,000원 → EVAL-912cf6be9101c6b7 / 세전 예상이자 131,625원
identity collision = false
```

## B-6. Data missing ≠ performance zero

post-subscription aggregation에 `CoverageRequirement`를 연결했다.

```text
no event + covered interval
→ observed zero

no event + uncovered/sync unavailable interval
→ UNKNOWN / DATA_SYNC_REQUIRED
```

GoalTracker는 coverage가 불충분하면 기존 trusted progress를 보존하고:

```text
GoalStatus = PAUSED
data_sync_required = true
AlertType = DATA_SYNC_REQUIRED
trigger_reason = OBSERVATION_DATA_SYNC_UNAVAILABLE
```

를 생성한다.

## B-7. Audit sensitive-data redaction

key normalization에서 `_`, `-`, camelCase 차이를 제거하고 nested dict/list 전체에 재귀 적용했다.

최소 masking 대상:

```text
api_key / apikey
access_token / refresh_token / id_token
token / api_token
authorization
password / passwd
secret / client_secret
credential
```

`LLM_API_KEY`, nested `api_token`, Bearer 문자열도 `FULL_DEBUG`에서 남지 않는다. `token_usage`와 같은 비인증 observability metadata는 과도하게 제거하지 않도록 exact/suffix normalized key policy를 사용했다.

## B-8. Question Generator grounding

숫자 일치만으로 질문을 승인하지 않는다. LLM structured output은 다음 값을 입력과 정확히 echo해야 한다.

```text
fact_type
rule_id
action_id
reward_id
```

또한 `grounding_terms`를 검사한다. 따라서 월급봉투 6개월 Rule을 “카드 6개월 사용”으로 바꾸는 질문은 숫자가 같아도 fallback된다.

fallback도 구분했다.

```text
FUTURE_INTENT
→ 앞으로 수행·관리할 의향 질문

SELF_REPORTED_FACT
→ 과거/현재 사실 질문
→ "사용자 입력 기준 예상 결과" 및 "기관 검증 정보가 아님" 고지
```

---

# C. Kakao 26-week Fixture

## C-1. 변경 전

v0.3 fixture는 7주 Rule과 26주 Rule 모두:

```text
from_sequence = 1
```

을 사용했다. 이 때문에 1~7회차 중 실패가 있으면 이후 7주 연속 성공이 존재해도 +1.0%p를 받을 수 없었다.

예전 regression은 4회차 실패 시:

```text
7주 reward = UNSATISFIABLE
26주 reward = UNSATISFIABLE
confirmed rate = 2.0%
```

을 기대했다.

## C-2. 변경 후

7주 Rule:

```text
COUNT_CONSECUTIVE(
  predicate = AUTO_TRANSFER AND SUCCESS,
  from_sequence = None  # ordered sequence 어디서든 longest run
) >= 7
```

26주 Rule:

```text
COUNT_CONSECUTIVE(AUTO_TRANSFER SUCCESS, from_sequence=None) >= 26
expected_occurrence_count = 26
```

26개 occurrence 안에서 26-run은 전체 성공과 동일하다.

수동입금은 `method=MANUAL_TRANSFER`이므로 AUTO_TRANSFER SUCCESS predicate를 충족하지 못한다. 실패한 AUTO occurrence와 같은 sequence에 수동 성공 record가 추가돼도 AUTO streak를 복구하지 않는다.

두 Rule 모두:

```text
evaluation_phase = POST_SUBSCRIPTION
achievement_mode = CONSECUTIVE
```

이며 가입 전 progress 0을 실패로 보지 않는다.

```text
가입 전 + intent 없음 → UNKNOWN / ASK_USER
가입 전 + intent YES  → ACHIEVABLE
```

## C-3. 테스트 결과

```text
sequence 3~9 AUTO SUCCESS
→ 7주 reward SATISFIED

1~6 SUCCESS + 7 FAILED
→ 7주 reward UNSATISFIABLE

26회 모두 AUTO SUCCESS
→ +1.0%p +2.0%p, 최고 5.0%

중간 1회 FAILED
→ 26주 +2.0%p 불가
→ 이후 7주 streak가 있으면 +1.0%p는 가능

manual fill
→ AUTO 26-run 복구 불가
```

관련 테스트: `tests/test_kakao_26_week.py` 7개.

---

# D. Product Metadata

## D-1. 추가 schema

`ProductMetadata`와 `ContributionPolicy`를 추가하고 `ProductDefinition.metadata`에 optional, identity-checked envelope로 연결했다.

### Basic

```text
institution_id
product_id
product_name
product_type
sale_status
```

### Term

```text
min_term
max_term
available_terms
```

### Contribution

```text
initial_amount_min
initial_amount_max
initial_amount_options
periodic_amount_min
periodic_amount_max
contribution_frequency = DAILY | WEEKLY | MONTHLY | FLEXIBLE
contribution_mode = FIXED | FLEXIBLE | INCREMENTAL
increment_amount
total_principal_limit
currency
```

### Rate / Subscription / Liquidity / Versioning

```text
base_rate
advertised_max_rate
preferential_rate_cap
allowed_channels
target_customer_summary
one_account_per_person
sale_start / sale_end / quantity_limit
early_termination_policy
partial_withdrawal_policy
interest_payment_method
tax_treatment
effective_from / effective_to
source_reference
```

## D-2. Metadata로 둔 이유

다음은 상품 후보를 빠르게 좁히거나 화면에 표시하는 정적 속성이다.

```text
판매 여부
가입기간 선택지
최소·최대 납입액
납입 빈도와 방식
가입 채널
판매기간·수량
기본/광고 금리
중도해지·부분인출 요약
```

이 값들은 복잡한 Rule 실행 없이 index/filter/ranking feature로 사용할 수 있다.

## D-3. Rule AST에 남긴 정보

다음은 metadata로 평탄화하지 않았다.

```text
가입자격 AND/OR/NOT
과거 보유이력 lookback
가입 후 N개월 실적
연속 자동이체 성공
급여·카드·서비스 경로의 nested OR
coverage requirement
future intent / capability / action path
reward guard와 rate application
```

즉 metadata는 후보 검색용 정적 contract이고, 금융판정 의미는 계속 Rule AST에 있다.

검증 fixture:

```text
monthly flexible product
weekly incremental product
initial amount options
term/channel serialization
```

---

# E. MVP Trust Model

## E-1. STRICT

```text
required authority가 기관/MyData/검증 resolver인 Rule
+ USER_DECLARED self-report만 존재
→ verified SATISFIED 금지
→ UNKNOWN / FACT_AUTHORITY_INSUFFICIENT
```

FUTURE_INTENT는 사용자가 자신의 향후 행동을 선언하는 정보이므로 future achievement 가능성에 사용할 수 있으나 실제 과거 performance progress로 count되지 않는다.

## E-2. MVP_PROVISIONAL

실제 MyData가 없는 MVP에서 사용자 self-report를 이용한 가상 분석을 허용한다.

다만 다음 metadata가 반드시 남는다.

```text
trust_mode = MVP_PROVISIONAL
is_provisional = true
verification_level = SELF_REPORTED 또는 MIXED
provisional_rule_ids
```

Application Layer는 이를 이용해:

> 사용자 입력 기준 예상 결과

라고 표시할 수 있다.

## E-3. SELF_REPORTED_FACT

```text
source_type = USER_DECLARED
semantic_type = SELF_REPORTED_FACT
```

이어야 한다. 사용자가 입력한 과거 사실을 `OBSERVED_EVENT`로 승격하지 않는다.

`UserFact`, `ScheduledOccurrence`, `RecurringPaymentEvent`는 FUTURE_INTENT와 self-report authority에 대한 schema validation을 수행한다.

---

# F. Recurring Payment

## F-1. Schema

`RecurringPaymentEvent`:

```text
event_id
user_id / subject_person_id
event_type
occurred_at
amount / currency
category
payment_method
source_account
institution
settlement_bank
source_type
semantic_type
provenance
```

Category:

```text
UTILITY
TELECOM
APARTMENT_MANAGEMENT
INSURANCE
TAX_OR_PUBLIC_FEE
OTHER
```

Payment method:

```text
AUTO_DEBIT
CARD
TRANSFER
OTHER
```

## F-2. Evaluator compatibility example

```text
COUNT_DISTINCT_PERIODS(
  period = MONTH,
  event_entity = RECURRING_PAYMENT_EVENT,
  fact_type = QUALIFIED_RECURRING_PAYMENT,
  where = category == UTILITY
          AND payment_method == AUTO_DEBIT
          AND settlement_bank == TARGET_BANK
) >= 2
```

2026년 1월과 2월의 공과금 자동납부 Event를 넣은 테스트에서 distinct month progress 2로 `SATISFIED`가 계산됐다. 통신비 Event는 category filter 때문에 제외됐다.

---

# G. FX Planning

## G-1. Provider abstraction

```text
FxQuoteProvider Protocol
└─ get_quote(base_currency, quote_currency)

MockFxQuoteProvider
└─ 테스트에서만 명시적으로 주입한 quote 사용
```

core에 live 환율이나 임의 고정환율을 production truth로 저장하지 않았다.

추가 모델:

```text
PlannedMonetaryAmount
FxQuote
FxEstimate
FxThresholdPolicy
FxPlanningService
```

`PlannedMonetaryAmount`는 `FUTURE_INTENT` 또는 `SELF_REPORTED_FACT`, `USER_DECLARED`만 허용한다.

## G-2. Threshold classification

```text
estimated < threshold
→ BELOW_THRESHOLD

estimated >= threshold
AND buffer < configurable safety margin
→ NEAR_THRESHOLD
→ FX_VOLATILITY_WARNING

buffer >= safety margin
→ CLEARLY_ABOVE
```

margin은 ratio와 absolute 값 중 큰 값을 사용한다.

## G-3. Adversarial example

```text
planned = 14,000,000 KRW/year
mock quote = 0.000723 USD per KRW
estimated = 10,122 USD
threshold = 10,000 USD
distance = +122 USD
safety margin = 3%
```

결과:

```text
classification = NEAR_THRESHOLD
warning = FX_VOLATILITY_WARNING
is_planning_estimate = true
authoritative_for_final_reward = false
```

최종 우대조건 실적판정은 추후 institution-verified normalized amount를 사용해야 한다.

---

# H. Preference / Capability / Numeric Preference

## H-1. HardConstraint

```text
constraint = REQUIRE | EXCLUDE
candidate_filter = true
```

사용자가 “있는 것만”, “있는 상품은 제외”라고 명시했을 때만 사용한다.

## H-2. Preference

```text
PREFER_PRESENT
NEUTRAL
PREFER_ABSENT
candidate_filter = false
```

Quick selector의 ↑/중립/↓는 ranking signal이며 자동 filter가 아니다.

## H-3. Capability

```text
CAN
UNKNOWN
CANNOT
candidate_filter = false
```

`CANNOT`은 해당 ActionPath의 realizable path를 닫지만 상품 자체를 제거하지 않는다. 기본금리 또는 다른 우대 경로로 상품이 유리할 수 있기 때문이다.

## H-4. NumericPreference

```text
field
value
direction = AT_LEAST | AT_MOST | AROUND
strictness = SOFT | HARD
currency optional
```

SOFT와 HARD는 serialization에서도 별도 값으로 보존한다.

---

# I. ActionPath

## I-1. Schema

```text
action_path_id
rule_id
label
source_rule_node_id
required_capabilities
one_time_actions
recurring_actions
required_amounts
required_duration
requires_external_party
required_service_refs
required_fact_types
provenance
```

ActionPath는 새로운 판정 엔진이 아니다. 기존 Rule AST/FutureAchievement의 공식 branch를 사용자 행동 관점으로 설명하는 metadata다.

## I-2. 신한 급여클럽 example

```text
action_path_id = SHINHAN_SALARY_CLUB_ENVELOPE_PATH
rule_id = RATE_SALARY_ENVELOPE_6M
source_rule_node_id = RATE_SALARY_ENVELOPE_6M:ENVELOPE_BRANCH
```

required capabilities:

```text
JOIN_BANK_SERVICE
ACCEPT_MARKETING_CONSENT
MANAGE_QUALIFYING_INCOME_CREDIT
MAINTAIN_RECURRING_CONDITION
```

service/fact linkage:

```text
required_service_refs = [SHINHAN_SALARY_CLUB]
required_fact_types = [SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED]
required_duration = 6 MONTH
```

공식 근거 provenance를 포함하며 “자가이체로 우회” 같은 미확인 경로는 넣지 않았다.

---

# J. Adversarial Test Results

`PYTHONPATH=src python scripts/run_v031_adversarial.py`를 직접 실행했다. 스크립트 내부 assertion이 하나라도 실패하면 non-zero로 종료한다.

| # | 공격 시나리오 | 결과 |
|---|---|---|
| 1 | 월급봉투 6개월 self-report | STRICT `UNKNOWN / FACT_AUTHORITY_INSUFFICIENT`; verified SATISFIED 금지 |
| 2 | 현재 월 1개월 이미 달성 | `current_progress=1`, `future_remaining_opportunities=11` |
| 3 | RuleDraft ID와 AST ID mismatch | validation invalid, activation blocked |
| 4 | 월 10만원 vs 월 50만원 | interest 다름, evaluation ID 충돌 없음 |
| 5 | 1회 성공·2회 실패 후 3~9회 성공 | 7주 reward `SATISFIED`, streak sequence 3..9 |
| 6 | FX threshold 바로 위 | `10,122 USD`, `NEAR_THRESHOLD`, `FX_VOLATILITY_WARNING` |

원본:

```text
examples/v0.3.1/adversarial-results.json
reports/adversarial-results-v0.3.1.txt
```

---

# K. Full Test Results

최종 전체 테스트:

```text
155 passed in 0.74s
0 failed
```

변화:

```text
v0.3 baseline = 116 passed
v0.3.1 final  = 155 passed
net additional coverage = 39 tests
```

추가 검증:

```text
compileall = PASS
JSON Schema export = PASS
v0.3 end-to-end demo on v0.3.1 = PASS
adversarial assertions = PASS
package version = 0.3.1
wheel build (--no-build-isolation) = PASS
ZIP package re-extraction regression = 155 passed / 0 failed
```

격리 wheel build는 실행환경의 외부 네트워크 차단으로 build dependency 조회가 실패했고, 이미 설치된 setuptools를 사용하는 `--no-build-isolation` 방식으로 동일 source wheel build를 완료했다.

주요 결과 파일:

```text
reports/test-results-v0.3.1.txt
reports/compileall-v0.3.1.txt
reports/schema-export-v0.3.1.txt
reports/adversarial-run-v0.3.1.txt
reports/wheel-build-v0.3.1-offline.txt
reports/package-retest-v0.3.1.txt
```

---

# L. Remaining Issues

## BLOCKER

없음.

## MINOR

1. `ProductMetadata.min_term/max_term`이 서로 다른 단위일 때 DAY/WEEK/MONTH 정규화 비교는 Application Layer의 term normalizer가 필요하다.
2. Question grounding은 안전한 lexical allow-list 방식이다. 유효한 paraphrase도 fallback될 수 있으나, 행동 의미 hallucination을 통과시키는 것보다 안전한 선택이다.
3. Audit redaction은 credential key family와 Bearer value를 강제 masking한다. 자유형 문장 전체의 범용 DLP는 아직 포함하지 않았다.
4. authority-sensitive `FactComparisonRule`은 Rule 작성자가 `FactAcceptancePolicy`를 명시해야 한다. 반복 performance aggregation은 안전한 default policy를 가진다.
5. `ProductKnowledgeDraft`의 기존 최소 metadata draft와 새 full `ProductMetadata` 사이의 자동 merge UI는 아직 없다. schema와 ProductDefinition attachment boundary는 구현됐다.

## DEFERRED_TO_APPLICATION_LAYER

```text
전체 Candidate Retrieval / Ranking
Web UI / Quick Selector UI
실제 MyData 및 기관 API
실제 RecurringPayment resolver
live FxQuoteProvider
기관 검증 normalized FX amount
대규모 상품 DB
ActionPath의 임의 OR branch 자동 생성
CONSECUTIVE Goal Tracker 고도화
실제 Push Notification
production durable Audit/Goal/Product store
```

의도적으로 제외한 항목은 core hardening 범위를 벗어나며, 현재 schema와 provider/resolver boundary로 다음 Sprint에서 연결할 수 있다.

---

# M. 최종 판단

## `READY_FOR_APPLICATION_LAYER`

판정 근거:

```text
v0.3 baseline 116 tests 보존
최종 155 tests / 0 failed
기관 검증 Event와 self-report 분리
STRICT와 MVP_PROVISIONAL 분리
현재 period opportunity double count 수정
RuleDraft/AST identity 및 provenance activation 보호
ContributionPlan evaluation identity 충돌 제거
post-subscription DataCoverage와 DATA_SYNC_REQUIRED 연결
카카오 7주/26주 corrected semantics 회귀 완료
Product/Contribution/RecurringPayment/FX/QuickInput/ActionPath schema 완료
LLM-independent deterministic core 유지
```

## 최종 확인 질문에 대한 답

> 실제 MyData가 없는 MVP에서도 사용자 입력을 이용해 provisional 개인화를 제공하되 그 입력을 기관 검증 데이터와 혼동하지 않고, 상품의 금액·기간·납입방식 및 사용자의 선호·가능행동을 다음 Application Layer에서 안전하게 사용할 수 있으며, 기존 deterministic 금융판정의 정확성은 유지되는가?

**예. 구현된 v0.3.1 범위에서는 코드와 테스트로 확인했다.**

사용자 입력은 `SELF_REPORTED_FACT` 또는 `FUTURE_INTENT`로 저장되고, `MVP_PROVISIONAL`에서만 허용된 self-report를 가상 판정에 사용할 수 있다. 그 경우 Rule과 ProductEvaluation에 provisional/verification metadata가 남는다. STRICT에서는 기관/MyData authority가 필요한 실적을 self-report만으로 `SATISFIED` 처리하지 않는다.

상품의 금액·기간·납입방식은 `ProductMetadata/ContributionPolicy`, 사용자 입력은 `HardConstraint/Preference/Capability/NumericPreference`, 달성 경로는 `ActionPath`, 공과금은 `RecurringPaymentEvent`, 외화 계획은 provider-independent FX schema로 분리됐다.

가입조건, Rule AST, 날짜, 집계, 연속성, 금리, 이자, Goal risk, Alert trigger는 계속 deterministic code가 계산한다. LLM은 질문 표현과 구조화 초안에만 관여한다.
