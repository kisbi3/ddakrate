# Financial Eligibility Engine v0.3.2 — Web Interaction & Evidence Semantics Closure Report

**Sprint:** Web Interaction & Evidence Semantics Closure  
**Base:** `financial-eligibility-engine-v0.3.1`  
**Target:** `financial-eligibility-engine-v0.3.2`  
**Verdict:** `READY_FOR_APPLICATION_LAYER`

---

## A. Baseline

코드 수정 전 원본 v0.3.1 ZIP을 별도 디렉터리에 풀어 전체 테스트를 다시 실행했다.

```text
155 passed in 0.60s
0 failed
```

기록: `reports/baseline-v0.3.1-before-v0.3.2.txt`

v0.3.1에서 이미 존재하던 다음 hardening은 유지했다.

- Fact authority / semantic type 기반 검증
- Goal future opportunity double-count 방지
- RuleDraft ↔ AST identity 검증
- provenance activation 보존
- ContributionPlan 포함 evaluation identity
- post-subscription DataCoverage / DATA_SYNC_REQUIRED
- Audit credential redaction
- 카카오 26주적금 7주 streak의 `from_sequence=None`
- ProductMetadata / ContributionPolicy
- RecurringPaymentEvent
- FX planning abstraction
- QuickInput / Capability / NumericPreference
- ActionPath

이번 Sprint는 위 구조를 재설계하지 않고, **전역 trust mode를 금융 semantics에서 제거하고 per-Fact evidence semantics로 닫는 작업**에 집중했다.

---

## B. Trust Mode Simplification

### 변경 전 v0.3.1

v0.3.1은 호환성과 MVP 시연을 위해 다음 전역 모드를 도입했다.

```text
STRICT
MVP_PROVISIONAL
```

이 방식은 하나의 evaluation 안에 MyData verified fact와 사용자 자기신고 fact가 동시에 존재할 때, 전체 evaluation을 하나의 trust mode로 설명해야 한다는 문제가 있었다.

### 변경 후 v0.3.2

금융판정의 핵심 분기에서 `EvaluationTrustMode`를 제거했다.

```text
financial semantics
= Fact.semantic_type
+ Fact.source_type
+ Rule-level FactAcceptancePolicy when needed
```

`EvaluationTrustMode` enum과 `trust_mode=` 인자는 기존 호출자를 깨지 않기 위해 **deprecated compatibility wrapper**로만 남겼다. 인자를 넘겨도 acceptance, status, rate, evaluation identity가 달라지지 않는다.

`accept_records()`는 전달된 `trust_mode`를 명시적으로 무시하며, `FinancialEligibilityEngine.evaluate_product()`의 evaluation ID hash에도 trust mode가 더 이상 포함되지 않는다.

Regression:

```text
same facts + STRICT
same facts + MVP_PROVISIONAL
→ same status
→ same rate
→ same evaluation_id
```

새 핵심 원칙은 다음이다.

> `SATISFIED`는 Rule이 주어진 evidence로 참이라는 뜻이고, `VERIFIED`는 그 evidence의 출처/검증 수준을 뜻한다. 두 개념은 동일하지 않다.

---

## C. Fact Semantics

v0.3.2의 Fact semantic type은 다음 다섯 종류다.

| semantic_type | 의미 | 일반적인 source |
|---|---|---|
| `OBSERVED_FACT` | 현재/과거 상태를 권위 있는 데이터에서 관측 | Institution / MyData |
| `OBSERVED_EVENT` | 실제 발생 이벤트를 권위 있는 데이터에서 관측 | Institution / MyData |
| `DERIVED_FACT` | 다른 Fact에서 deterministic 계산 | DERIVED |
| `SELF_REPORTED_FACT` | 사용자가 Web UI에서 현재/과거 사실을 직접 응답 | USER_DECLARED |
| `FUTURE_INTENT` | 사용자의 미래 행동 의향/계획 | USER_DECLARED |

Schema validator는 다음을 유지한다.

```text
SELF_REPORTED_FACT → source_type must be USER_DECLARED
FUTURE_INTENT      → source_type must be USER_DECLARED
```

그리고 기본 Fact resolution은 `FUTURE_INTENT`를 observed fact처럼 선택하지 않는다. Future Intent를 사용하는 Rule은 `required_semantic_type=FUTURE_INTENT` 또는 FutureAchievementSpec의 `intent_fact_type`으로 명시해야 한다.

반면 `SELF_REPORTED_FACT`는 일반적인 현재/과거 FactComparison에서 정상 계산 evidence로 사용할 수 있다. 다만 실제 이벤트 집계처럼 권위 있는 관측이 필요한 Rule은 `FactAcceptancePolicy`를 사용하여 `OBSERVED_EVENT + institution/MyData`만 허용한다.

따라서 다음 두 문장은 서로 다른 데이터가 된다.

```text
"지난 1년간 해당 은행 적금이 없었습니다."
→ SELF_REPORTED_FACT

"가입 후 SuperSOL을 가입하고 유지할 수 있습니다."
→ FUTURE_INTENT
```

---

## D. User Answer Flow

Web UI 자체는 구현하지 않았지만 Application Layer가 바로 연결할 수 있는 ingest contract를 추가했다.

### 1. MissingFactRequest

`MissingFactRequest`에 안정적인 `missing_fact_id`를 추가했다. Resolver가 생성하는 request는 다음 identity로 deterministic하게 reference를 만든다.

```text
fact_type
requested_by_rule_id
expected_semantic_type
action_id
reward_id
```

### 2. UserAnswerSubmission

```text
UserAnswerSubmission
- request_reference
- answer
- answered_at
```

### 3. UserAnswerMapper

`MissingFactRequest.expected_semantic_type`을 읽어 사용자 응답을 다음으로 변환한다.

```text
SELF_REPORTED_FACT question
→ UserFact.semantic_type = SELF_REPORTED_FACT
→ source_type = USER_DECLARED

FUTURE_INTENT question
→ UserFact.semantic_type = FUTURE_INTENT
→ source_type = USER_DECLARED
```

잘못된 MissingFact reference나, ASK_USER이면서 semantic expectation이 없는 request는 mapping을 거부한다.

### 4. UserFactStore → deterministic re-evaluation

지원 flow:

```text
Rule evaluation
→ MissingFactRequest
→ Web question
→ UserAnswerSubmission
→ UserAnswerMapper
→ UserFactStore.with_fact()
→ FinancialEligibilityEngine.evaluate_product()
```

실제 test에서는 과거 보유이력 질문에 사용자가 `False`(보유 없음)라고 답한 후:

```text
UNKNOWN
→ UserAnswerSubmission
→ SELF_REPORTED_FACT
→ SATISFIED / SELF_REPORTED
```

으로 재평가된다.

Future Intent test에서는 월급봉투 6개월 조건이:

```text
UNKNOWN / ASK_USER
→ user YES
→ FUTURE_INTENT
→ ACHIEVABLE / USER_INTENT
```

으로 진행된다. `SATISFIED`로 승격되지 않는다.

---

## E. Verification / Evidence

사용자-facing evidence classification을 다음으로 세분화했다.

```text
INSTITUTION_VERIFIED
MYDATA_VERIFIED
DERIVED
SELF_REPORTED
USER_INTENT
MIXED
UNKNOWN
```

`VERIFIED`는 v0.3.1 serialized compatibility를 위해 enum에 남아 있지만 새 evaluation은 가능한 한 구체적인 verified variant를 반환한다.

### RuleEvaluation

추가/정리된 필드:

```text
verification_level
evidence_levels[]
is_provisional  # compatibility/readability: SELF_REPORTED evidence가 사용되었는지
```

예:

```text
status = SATISFIED
verification_level = SELF_REPORTED
```

은 합법적이다. 의미는:

> 조건 판정은 사용자 답변 기준으로 충족되지만, 기관 검증 결과는 아니다.

### ProductEvaluation

```text
evidence_levels[]
verification_level
self_reported_rule_ids[]
user_intent_rule_ids[]
provisional_rule_ids[]  # v0.3.1 compatibility alias of self_reported_rule_ids
```

서로 다른 evidence가 하나의 Rule/Result에 결합되면 `MIXED`가 된다.

---

## F. Rate Semantics

네 rate 개념을 유지하면서 evidence semantics를 명시했다.

### 1. advertised_max_rate

상품 자체의 광고상 최고금리다.

### 2. confirmed_rate

```text
base rate
+ SATISFIED
+ authoritative evidence
  (INSTITUTION_VERIFIED / MYDATA_VERIFIED / DERIVED)
```

`SELF_REPORTED_FACT`만으로 SATISFIED한 reward는 confirmed에 들어가지 않는다.

### 3. realizable_rate

```text
confirmed component
+ SELF_REPORTED SATISFIED reward
+ FUTURE_INTENT 기반 ACHIEVABLE reward
```

따라서 Web MVP의 실제 개인화 계산에 사용자 응답과 계획이 반영된다.

### 4. user_specific_conditional_upper_rate

```text
realizable component
+ favorable UNKNOWN reward
- already UNSATISFIABLE reward
```

### Evidence breakdown

`RateSummary.evidence_breakdown`:

```text
base_rate
verified_reward_pp
self_reported_reward_pp
future_action_reward_pp
unknown_conditional_reward_pp
```

Regression example:

```text
base                   3.05
verified reward       +1.00
self-reported reward  +0.50
future action reward  +0.50
unknown reward        +1.00

confirmed             4.05
realizable            5.05
conditional upper     6.05
```

이 구조를 통해 UI는 나중에 다음을 분리해 표시할 수 있다.

```text
금융데이터 확인 금리
사용자 응답 기준 추가분
계획대로 달성 시 추가분
확인이 필요한 조건부 상한
```

---

## G. Absence Rule

`NOT_EXISTS`의 coverage-aware 원칙은 유지하되 Web MVP self-report fallback을 추가했다.

### Priority 1 — authoritative coverage

```text
권위 있는 DataCoverage가 lookback 전체를 덮음
+ matching entity 없음
→ SATISFIED
→ MYDATA_VERIFIED 또는 INSTITUTION_VERIFIED
```

### Priority 2 — explicit self-reported assertion

권위 있는 coverage가 없더라도 사용자가 명시적으로:

```text
"최근 1년간 해당 은행 정기예금/적금을 보유한 적이 없습니다."
```

라고 답하면:

```text
self_reported_exists = false
→ NOT_EXISTS = SATISFIED
→ verification = SELF_REPORTED
```

반대로 보유했다고 답하면:

```text
self_reported_exists = true
→ NOT_EXISTS = UNSATISFIABLE
→ verification = SELF_REPORTED
```

이를 위해 generic `ExistenceAssertionFallback`을 추가했다.

중요하게도 self report를 `DataCoverage` 객체로 변환하지 않는다. Trace에:

```text
evidence_origin = SELF_REPORTED
authoritative_coverage_fabricated = false
```

가 남는다.

### Priority 3 — 둘 다 없음

```text
UNKNOWN
+ ASK_USER
```

신한 첫거래 branch fixture에 이 fallback을 실제 연결했다.

---

## H. Shinhan Fixture Migration

`U001`과 `청년 처음적금` fixture를 새 semantic model로 migration했다.

### Future Intent

```text
SALARY_ACCOUNT_CHANGE_POSSIBLE
→ FUTURE_INTENT

CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE
→ FUTURE_INTENT

SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING
→ FUTURE_INTENT
```

Salary/Card capability Rule도 `required_semantic_type=FUTURE_INTENT`로 고정했다.

### Self-reported current/historical fact

```text
SPECIAL_RATE_COUPON_VALID 사용자 답변
→ SELF_REPORTED_FACT

SHINHAN_RELEVANT_HOLDING_IN_PRIOR_1Y 사용자 답변
→ SELF_REPORTED_FACT
```

질문 fallback에서도 둘을 구분한다.

```text
SELF_REPORTED_FACT
→ "과거 또는 현재 ... 해당했습니까?"

FUTURE_INTENT
→ "앞으로 ... 수행하고 목표로 관리하시겠습니까?"
```

---

## I. Product Metadata

v0.3.1에서 schema만 존재하던 ProductMetadata를 실제 4개 regression product에 연결했다.

### 신한은행 청년 처음적금

Source-supported fields:

```text
term                 12 MONTH
periodic min         1,000 KRW
periodic max         300,000 KRW
frequency            MONTHLY
mode                 FLEXIBLE
base rate            3.05
advertised max       6.05
preferential cap     3.0
age/target summary   18~39세 실명의 개인/개인사업자
one account/person   true
source version       2026-07-22
```

### 카카오뱅크 26주적금

```text
term                 26 WEEK
initial options      1,000 / 2,000 / 3,000 / 5,000 / 10,000 KRW
frequency            WEEKLY
mode                 INCREMENTAL
increment options    selected initial amount와 동일한 option set
base rate            2.0
advertised max       5.0
preferential cap     3.0
```

카카오는 증액액이 하나의 고정값이 아니라 **선택한 최초금액과 동일**하므로 `increment_amount_options`를 추가했다. 임의로 1,000원 고정 증액이라고 저장하지 않는다.

### IBK부모급여우대적금

```text
term                 12 MONTH
periodic max         500,000 KRW
frequency            MONTHLY
mode                 unspecified (supplied source에 FIXED/FLEXIBLE 명시 부족)
base rate            2.5
advertised max       6.5
preferential cap     4.0
```

### 하나은행 달려라 하나 적금

```text
term                 12 MONTH
periodic max         300,000 KRW
frequency            MONTHLY
mode                 unspecified
base rate            1.8
advertised max       6.0
preferential cap     4.2
```

Source에 없는 channel, 세금, 중도해지 세부 정책 등은 추측하지 않고 비워 뒀다.

### ProductDefinition ↔ ProductMetadata consistency

다음을 검증한다.

```text
product_id
institution_id
name
product_type
base_rate
advertised_max_rate
preferential_rate_cap
fixed contract term
available term set
```

직접 adversarial test:

```text
ProductDefinition = 12 months
Metadata fixed     = 6 months
→ ValidationError
```

---

## J. ActionPath Validation

ActionPath는 별도 금융판정 엔진이 아니라 기존 Rule AST의 사용자 행동 관점 표현이라는 경계를 유지했다.

ProductDefinition validation에서 다음 lineage를 강제한다.

```text
ActionPath.rule_id
== owning PreferentialRule.rule.rule_id
```

그리고:

```text
source_rule_node_id
∈ owning Rule AST의 실제 node ids
```

기존 월급봉투 ActionPath의 임의 pseudo-node:

```text
RATE_SALARY_ENVELOPE_6M:ENVELOPE_BRANCH
```

를 실제 AST node인:

```text
RATE_SALARY_ENVELOPE_6M
```

로 수정했다.

Wrong owner와 nonexistent node 모두 validation error test가 있다.

---

## K. QuickInput Conflict Handling

Preference / Capability의 soft semantics는 그대로 유지했다.

```text
PREFER_PRESENT ≠ REQUIRE
PREFER_ABSENT  ≠ EXCLUDE
CANNOT         ≠ product hard-filter
```

단 하나의 QuickInputProfile 안에서 의미가 동시에 모순되는 입력은 막는다.

### Capability

```text
CHANGE_SALARY_ACCOUNT = CAN
CHANGE_SALARY_ACCOUNT = CANNOT
→ ValidationError
```

### Preference

```text
FIRST_TRANSACTION_BONUS = PREFER_PRESENT
FIRST_TRANSACTION_BONUS = PREFER_ABSENT
→ ValidationError
```

### Numeric HARD

```text
MONTHLY_CONTRIBUTION >= 300,000 HARD
MONTHLY_CONTRIBUTION <= 200,000 HARD
→ ValidationError
```

Soft numeric preferences는 서로 긴장되는 방향이어도 저장 가능하다. Ranking Layer가 soft utility로 해석할 수 있게 core에서 과도하게 차단하지 않는다.

동일 항목을 완전히 중복 입력하는 경우도 deduplicate 대신 validation error를 선택했다. Application Layer가 명확한 canonical input을 만들도록 하기 위한 정책이다.

---

## L. FX / Recurring Payment Regression

v0.3.1 구조를 변경하지 않았다.

### Recurring Payment

```text
UTILITY
TELECOM
APARTMENT_MANAGEMENT
INSURANCE
TAX_OR_PUBLIC_FEE
OTHER
```

실제 납부 Event는 `OBSERVED_EVENT`이며, "공과금 자동납부를 변경할 수 있다"는 사용자 응답은 별도 `FUTURE_INTENT / Capability`로 처리하는 경계를 유지한다.

은행별 공과금 DSL operator는 추가하지 않았다.

### FX planning

다음을 유지한다.

```text
PlannedMonetaryAmount
FxQuote
FxEstimate
FxQuoteProvider
MockFxQuoteProvider
```

그리고 threshold classification:

```text
CLEARLY_ABOVE
NEAR_THRESHOLD
BELOW_THRESHOLD
```

`NEAR_THRESHOLD`에서는 `FX_VOLATILITY_WARNING`을 반환한다. planning quote는 institution-verified remittance performance가 아니다.

Kakao / FX / Recurring Payment targeted regression suite를 별도로 실행했고 모두 통과했다.

기록: `reports/targeted-regression-v0.3.2.txt`

---

## M. Adversarial Test Results

`python scripts/run_v032_adversarial.py`를 직접 실행했다.

### Case 1 — self-reported historical absence, no DataCoverage

```text
status            SATISFIED
verification      SELF_REPORTED
confirmed_rate    2.0
realizable_rate   3.0
DataCoverage      0
```

사용자 응답이 개인화 계산에는 반영되지만 verified history로 위조되지 않는다.

### Case 2 — same condition with MyData coverage

```text
status            SATISFIED
verification      MYDATA_VERIFIED
confirmed_rate    3.0
realizable_rate   3.0
```

### Case 3 — SuperSOL future answer

```text
status            ACHIEVABLE
verification      USER_INTENT
SATISFIED?        false
```

### Case 4 — same capability CAN + CANNOT

```text
ValidationError
```

### Case 5 — ProductDefinition 12 months / Metadata 6 months

```text
ValidationError
```

### Case 6 — ActionPath owning Rule mismatch

```text
ValidationError
```

결과 파일:

`examples/v0.3.2/adversarial-results-v0.3.2.json`

실행 로그:

`reports/adversarial-run-v0.3.2.txt`

---

## N. Full Test Results

v0.3.1 원본 baseline:

```text
155 passed
0 failed
```

v0.3.2에서 기존 155개 test case를 삭제하지 않고 유지했다. 전역 trust mode를 금융 semantics로 사용하던 일부 v0.3.1 assertion은 이번 Sprint의 명시적 Source of Truth에 맞추어 동일 test case 안에서 새 contract로 갱신했다.

기존 v0.3.1 test set만 v0.3.2 코드 위에서 실행:

```text
155 passed
0 failed
```

v0.3.2 신규 test case:

```text
21 added
```

최종 전체:

```text
176 passed
0 failed
```

추가 검증:

```text
compileall                       PASS
v0.3.2 JSON Schema export       PASS
Kakao/FX/Recurring regression   PASS
adversarial 6 cases             PASS
package version                 0.3.2
```

---

## O. Remaining Issues

### BLOCKER

없음.

### MINOR

1. `FactAcceptancePolicy`의 `strict_* / provisional_*` 필드명은 v0.3.1 serialization 호환 때문에 유지했다. 실제 의미는 더 이상 global STRICT/MVP mode가 아니라 **rule-local authoritative/self-report acceptance sets**다. Application Layer가 안정된 뒤 v0.4 schema에서 이름 정리를 검토할 수 있다.
2. `is_provisional`과 `provisional_rule_ids` 역시 v0.3.1 client compatibility를 위해 유지했다. v0.3.2의 실제 source of truth는 `verification_level / evidence_levels / self_reported_rule_ids`다.
3. ProductMetadata는 supplied source로 확인된 값만 연결했다. 정확한 가입채널, 중도해지, 세제 등은 원문 확인이 가능한 상품만 확장해야 한다.
4. UserAnswerMapper는 구조화된 MissingFact answer를 fact로 매핑한다. 자유형 자연어 답변을 boolean/date/amount로 파싱하는 Intent/Answer Parser는 Application Layer의 별도 책임이다.
5. 동일 fact type에 verified와 self-reported fact가 동시에 있으면 FactResolver의 source priority가 verified를 우선 선택한다. 충돌 정보를 UI에 어떻게 표현할지는 Application Layer 정책이 필요하다.

### DEFERRED_TO_APPLICATION_LAYER

- 실제 Web UI
- Candidate Retrieval
- Ranking Engine
- Natural-language ProductSearchIntent 전체
- Question Planner 전체
- 실제 MyData API
- 실제 institution supplemental API
- live FX provider
- 실제 Push
- 대규모 Product DB
- LLM Planner

---

## P. Final Verdict

# `READY_FOR_APPLICATION_LAYER`

판정 이유:

```text
전역 trust mode → per-Fact evidence semantics 전환 완료
SATISFIED ≠ VERIFIED 고정
SELF_REPORTED historical/current fact 계산 사용 가능
FUTURE_INTENT observed performance 오염 방지
Web answer ingest → UserFactStore → deterministic re-evaluation 완료
absence self-report fallback과 authoritative coverage 분리 완료
confirmed / realizable / conditional upper evidence 분리 완료
4 regression product metadata 연결 완료
ProductMetadata consistency validation 완료
ActionPath lineage validation 완료
QuickInput conflict validation 완료
Kakao / FX / Recurring semantics 보존
기존 155 test set 보존
총 176 tests passed
```

### 최종 확인 질문에 대한 답

> 실제 MyData가 없는 Web MVP에서 사용자가 질문에 직접 답해도, 그 응답을 정상적으로 개인화 계산에 활용하면서 사용자 자기신고·미래 의향·실제 금융데이터를 서로 혼동하지 않고, 확정금리와 사용자 응답/계획 기반 실현 가능 금리를 구분하여 다음 Application Layer가 안전하게 사용할 수 있는가?

**예. v0.3.2의 코드와 테스트 기준으로 가능하다.**

사용자의 과거/현재 답변은 `SELF_REPORTED_FACT / USER_DECLARED / SELF_REPORTED`, 미래 행동 답변은 `FUTURE_INTENT / USER_DECLARED / USER_INTENT`, MyData/기관 근거는 `MYDATA_VERIFIED / INSTITUTION_VERIFIED`로 끝까지 구분된다.

Engine은 이 evidence를 deterministic Rule 평가에 사용하되, **self-reported SATISFIED reward는 realizable rate에는 반영하고 confirmed rate에는 넣지 않는다.** Future Intent는 시간·구조상 가능한 경우 `ACHIEVABLE`에만 사용되며 실제 progress로 count되지 않는다.

따라서 다음 Application Layer는 하나의 ProductEvaluation만 받아도:

```text
어떤 조건이 참인가?
그 조건은 어떤 evidence로 판단됐는가?
금융데이터 확인 금리는 얼마인가?
사용자 응답/계획까지 반영한 실현 가능 금리는 얼마인가?
무엇을 추가 질문해야 하는가?
```

를 구분하여 안전하게 UI에 표현할 수 있다.
