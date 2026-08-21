# Financial Eligibility Engine v0.4.3 Implementation Report

**Sprint:** Contribution Feasibility & Final Web Handoff Closure  
**Baseline:** `financial-eligibility-engine-v0.4.2`  
**Result:** `financial-eligibility-engine-v0.4.3`  
**Final verdict:** `READY_FOR_WEB_UI`

---

## 1. v0.4.2 baseline 결과

수정 전에 v0.4.2 ZIP을 별도 디렉터리에 해제하고 실제 working tree에서 재검증했다.

```text
full pytest:                    281 passed
compileall:                     PASS
source import:                  PASS
package version:                0.4.2
wheel build:                    PASS (--no-build-isolation)
clean wheel install/import:     PASS
v0.4 adversarial:               PASS
v0.4.1 adversarial:             PASS
v0.4.2 adversarial:             PASS
```

초기 wheel build를 build isolation 상태로 실행했을 때 실행 환경이 외부 package index에 접근할 수 없어 setuptools build dependency 조회가 실패했다. 소스/패키징 오류가 아니라 offline environment 문제였으며, 동일 repo에서 `--no-build-isolation`로 wheel을 직접 빌드하고 clean target에 설치한 뒤 import smoke까지 통과했다.

---

## 2. 수정 파일 목록

핵심 source:

- `src/eligibility/schema/search.py`
- `src/eligibility/search/contribution.py`
- `src/eligibility/search/evaluation.py`
- `src/eligibility/search/questions.py`
- `src/eligibility/application_service.py`
- `src/eligibility/audit/models.py`
- `src/eligibility/__init__.py`
- `src/eligibility/schema/__init__.py`

검증/계약:

- `tests/test_v043_contribution_feasibility.py`
- `scripts/run_v043_adversarial.py`
- `scripts/export_schemas.py`
- `README.md`
- `pyproject.toml`

기존 v0.4.1/v0.4.2 regression 중 상품별 납입 state isolation 자체를 검증하는 시나리오는 이번 affordability 기능 때문에 의미가 바뀌지 않도록 global maximum을 충분히 크게 조정했다. 이는 기존 동작을 우회하기 위한 변경이 아니라, 해당 legacy test가 원래 검증하던 **product-scoped choice isolation/completion**과 새 **affordability pruning**을 서로 분리하기 위한 fixture 조정이다.

---

## 3. Contribution Feasibility semantics

v0.4.3은 새로운 추천 엔진을 만들지 않았다. 기존 `ContributionPlanner`가 상품별 실제 cashflow를 만든 뒤, 작은 `ContributionFeasibilityEvaluator`가 그 schedule을 사용자의 **전역 affordability cadence**에 맞춰 검증한다.

```text
Global ContributionPlan
+ matching ProductContributionChoice
+ ProductMetadata.ContributionPolicy
+ subscription_date
→ dated product cashflow
→ user-frequency bucket aggregation
→ affordability decision
```

핵심 경계는 유지한다.

```text
Global ContributionPlan
= 검색 전체에 적용되는 납입 선호/최대 감당액

ProductContributionChoice
= 특정 상품 cashflow를 확정하는 선택
```

상품별 선택값을 feasibility 평가하는 동안 global plan을 수정하지 않는다.

---

## 4. Calendar bucket 계산 방식

`PlannedCashflow`에 `contribution_date`를 추가했다. `ContributionPlanner.build(..., subscription_date=...)`가 monthly/weekly/daily contribution event를 실제 날짜로 materialize한다.

사용자의 affordability frequency에 따라 bucket은 다음처럼 결정한다.

| User affordability frequency | 비교 bucket |
|---|---|
| `DAILY` | calendar date |
| `WEEKLY` | ISO calendar week |
| `MONTHLY` | calendar year-month |
| `FLEXIBLE` | event/sequence bucket |

따라서 weekly 상품을 `4 weeks = 1 month`로 환산하지 않는다.

직접 adversarial에서는 `2026-08-20`에 시작하는 주간 schedule을 생성했고, 2026년 10월에 다음 5회 event가 실제로 존재했다.

```text
2026-10-01
2026-10-08
2026-10-15
2026-10-22
2026-10-29
```

회당 80,000원일 때:

```text
4주 근사:       320,000원
실제 10월 합계: 400,000원
월 한도:        350,000원
```

따라서 4주 근사는 잘못 FEASIBLE이라고 판단하지만 실제 calendar aggregation은 정확히 INFEASIBLE로 판정한다.

---

## 5. Cross-frequency affordability

전역 `MONTHLY=300,000원`을 weekly product의 `per-event <= 300,000원`으로 해석하는 기존 위험을 제거했다.

실제 schedule을 생성한 뒤 월별 합계를 계산한다. Kakao 26주 적금의 `2026-08-20` 가입 기준 직접 검증 결과:

| 시작금액 | 최대 calendar-month 출금액 | 결과 |
|---:|---:|---|
| 1,000원 | 90,000원 | FEASIBLE |
| 2,000원 | 180,000원 | FEASIBLE |
| 3,000원 | 270,000원 | FEASIBLE |
| 5,000원 | 450,000원 | INFEASIBLE |
| 10,000원 | 900,000원 | INFEASIBLE |

이 결과는 질문 wording이나 LLM이 아니라 deterministic backend 계산 결과다.

---

## 6. Option pruning

`MissingRankingInput.allowed_options`가 있는 경우 각 option을 사용자에게 노출하기 전에 다음 pipeline을 실행한다.

```text
allowed option
→ tentative ProductContributionChoice
→ effective product plan
→ product ContributionPolicy validation
→ dated cashflow generation
→ global affordability validation
→ FEASIBLE / INFEASIBLE
```

새 DTO:

```text
ContributionOptionFeasibility
- product_id
- field
- option_value
- status
- max_bucket_amount
- affordability_limit
- affordability_frequency
- violation_bucket
- reason_code
```

월 30만원 한도 Kakao 예에서는 질문에 `1,000 / 2,000 / 3,000`만 남고 `5,000 / 10,000`은 제거된다.

---

## 7. Single feasible option 처리

가능한 option이 하나만 남아도 자동 선택하지 않는다.

예를 들어 월 최대 100,000원 조건에서는 Kakao 시작금액 1,000원만 남는다. backend는 해당 option만 포함한 `RANKING_INPUT`을 유지하고 사용자 확인을 요구한다.

```text
현재 월 최대 100,000원 납입 가능 조건으로는 ... 시작금액 1,000원 선택만 가능합니다.
이 조건으로 진행할까요?
```

실제 `ProductContributionChoice`는 사용자가 valid answer를 제출한 후에만 생성된다.

---

## 8. All-options-infeasible clarification

모든 allowed option이 infeasible이면 상품을 조용히 제거하지 않는다. material frontier candidate라면 `ContributionFeasibilityClarification`을 Question Planner에 전달한다.

```text
ContributionFeasibilityClarification
- product_id
- current_affordability_amount
- current_affordability_frequency
- feasible_options
- minimum_required_affordability
- reason_code
- allowed_resolutions
```

resolution:

```text
ADJUST_GLOBAL_AFFORDABILITY
EXCLUDE_PRODUCT
```

Kakao 직접 검증:

```text
현재 월 최대:       80,000원
feasible options:   0개
최소 필요 월 한도: 90,000원
```

상품은 clarification 전에 candidate set에서 silent drop되지 않는다.

---

## 9. Global affordability update flow

사용자가 `ADJUST_GLOBAL_AFFORDABILITY`를 선택하면 기존 `IntentPatch / ContributionPlanPatch` infrastructure를 재사용한다.

```text
feasibility clarification
→ user: monthly max 80,000 → 300,000
→ tentative IntentPatch validation
→ global ContributionPlan commit
→ intent_version 증가
→ candidate retrieval/evaluation refresh
→ option feasibility 재계산
→ feasible option question
→ valid product choice
→ interest calculation
→ reranking
```

직접 adversarial에서 80,000원 → 300,000원으로 올린 뒤 `1,000 / 2,000 / 3,000`이 다시 나타났고, 3,000원 선택 후 총 원금 1,053,000원 / 세후이자 7,973원이 계산되어 ranking이 완료됐다.

---

## 10. User-driven product exclusion

사용자가 `EXCLUDE_PRODUCT`를 선택하면 `SearchSession.excluded_product_ids`에 session-local exclusion을 기록한다.

- 다른 session이나 상품에는 영향을 주지 않는다.
- CandidateRetriever의 기존 결과 위에서 user exclusion만 추가 적용한다.
- 다른 후보를 즉시 재평가/재순위한다.
- `PRODUCT_EXCLUDED_BY_USER` Audit event를 남긴다.

즉 Engine이 infeasible product를 몰래 제거하는 것과 사용자가 명시적으로 제외하는 것을 구분한다.

---

## 11. Answer validation-before-commit

Ranking-input answer 처리 순서를 validation-first로 고정했다.

```text
1. raw answer 수신
2. type / numeric / allowed-option validation
3. tentative ProductContributionChoice 생성
4. matching effective product plan 생성
5. Product ContributionPolicy validation
6. dated cashflow 생성
7. global affordability validation
8. evaluation/interest 전제 sanity
9. 모두 PASS
10. ProductContributionChoice commit
11. answered_question_ids commit
12. active question 이동
13. reevaluate / rerank
```

특히 allowed option은 질문 생성 시점의 snapshot만 믿지 않는다. submit 시점의 current global plan / subscription date / product policy를 기준으로 다시 검증한다.

---

## 12. Invalid-answer atomicity

다음 invalid answer를 regression으로 고정했다.

- negative amount
- allowed option 밖의 값
- 현재 affordability를 초과하는 값
- 잘못된 type/object
- 이전에는 feasible했지만 global limit 변경 후 stale해진 값

실패 후 다음 business state가 answer 전 snapshot과 동일함을 확인한다.

```text
ProductContributionChoice
SearchSession.answered_question_ids
active_question
Global ContributionPlan
intent_version
candidate evaluations
ranking state
recommendation state
```

`CONTRIBUTION_ANSWER_REJECTED` 같은 append-only failure Audit event는 남길 수 있지만 business state는 변경하지 않는다. 실패한 질문은 answered 처리되지 않으며 같은 `question_id`로 다시 valid answer를 제출할 수 있다.

REST adapter에서도 invalid RankingInput answer가 HTTP-like `400 BAD_REQUEST`를 반환하고 동일 business state / active question을 유지하는 regression을 추가했다.

---

## 13. Question Planner 연결

기존 ranking-aware frontier 원칙을 유지했다.

- contribution option pruning은 candidate evaluation 시 deterministic하게 계산
- 실제 사용자 질문은 current Top-K frontier에 있는 material candidate에 대해서만 생성
- `CONTRIBUTION_FEASIBILITY` clarification은 all-options-infeasible material candidate에 우선 배치
- optimistic upper로도 Top 5에 들어올 수 없는 후보 때문에 질문 폭탄을 만들지 않음

`MAX_REALIZABLE_RATE` semantics도 유지한다.

- interest 계산에만 필요한 start amount는 묻지 않는다.
- 하지만 모든 contribution option이 global hard affordability를 위반하면 이는 이자 계산 문제가 아니라 실제 유지 가능성 문제이므로 `CONTRIBUTION_FEASIBILITY` clarification을 물을 수 있다.

---

## 14. REST 변경

새 route를 추가하지 않았다. 기존 thin REST adapter가 동일 `ApplicationService`를 그대로 호출한다.

```text
GET  /search-sessions/{id}/questions/next
POST /search-sessions/{id}/answers
```

동일 endpoint에서 다음을 처리할 수 있다.

1. feasible-option RankingInput question
2. `CONTRIBUTION_FEASIBILITY` adjust/exclude clarification
3. global affordability adjustment
4. user-driven product exclusion
5. invalid answer → 400 without business-state commit

business logic은 REST layer에 복제하지 않았다.

---

## 15. MCP 변경

MCP 구조는 변경하지 않았다.

```text
MCP adapter
→ ApplicationService
→ Financial Eligibility Engine
```

기존 `get_next_question / submit_user_fact` 계열 thin tool contract가 새 question payload를 전달한다. feasibility/atomicity business logic은 전부 `ApplicationService`와 shared evaluation service에 있다.

---

## 16. Audit 변경

추가 event type:

```text
CONTRIBUTION_OPTION_FEASIBILITY_EVALUATED
CONTRIBUTION_OPTION_PRUNED
CONTRIBUTION_FEASIBILITY_CLARIFICATION_CREATED
CONTRIBUTION_ANSWER_REJECTED
PRODUCT_EXCLUDED_BY_USER
GLOBAL_AFFORDABILITY_UPDATED
```

가능한 범위에서 `search_session_id / product_id / question_id / intent_version / ranking_run_id` chain을 기존 `AuditSession` context와 entity refs에 유지한다.

---

## 17. 신규 tests

`tests/test_v043_contribution_feasibility.py`: **24 passed**

포함 범주:

- calendar month bucket / 5-occurrence month
- four-week approximation rejection
- Kakao option feasibility pruning
- single-feasible-option confirmation
- all-options-infeasible clarification
- affordability increase → option → interest → ranking
- user-driven exclusion
- stale option submission-time revalidation
- invalid answer atomicity
- successful validation-before-commit
- `MAX_REALIZABLE_RATE` suppression + hard-affordability exception
- product-scoped state isolation
- global plan isolation
- REST/MCP feasibility flow
- REST invalid answer 400 + no state mutation

---

## 18. 직접 수행한 adversarial tests

`PYTHONPATH=src:. python scripts/run_v043_adversarial.py`

### Scenario A — Cross-frequency affordability

월 300,000원 limit에서 Kakao 실제 dated schedule을 생성했다.

```text
1,000 / 2,000 / 3,000 → FEASIBLE
5,000 / 10,000        → INFEASIBLE
```

### Scenario B — 5회 출금 월

```text
2026-10 weekly events = 5
actual total          = 400,000
4-week approximation = 320,000
limit                 = 350,000
actual result         = INFEASIBLE
```

### Scenario C — 모든 option 불가

월 80,000원 limit에서 모든 Kakao option이 infeasible. Candidate를 silent drop하지 않고 adjust/exclude clarification 생성, minimum required 90,000원 확인.

### Scenario D — 한도 상향

80,000 → 300,000 update 후 feasible option 재생성, 3,000원 선택, interest 생성, rank 1 확인.

### Scenario E — invalid answer

10,000원 제출을 reject하고 business snapshot equality 확인. 같은 question 재시도 후 2,000원 valid commit 확인.

### Scenario F — product state isolation

A=3,000 / B=2,000을 독립 저장하고 global monthly desired/max 300,000원이 그대로 유지됨을 확인.

기존 v0.4 / v0.4.1 / v0.4.2 adversarial script도 v0.4.3 working tree에서 다시 실행해 모두 PASS했다.

---

## 19. 기존 regression 결과

최종 working tree:

```text
v0.3.2 regression subset:      176 passed
v0.4 regression subset:         50 passed
v0.4.1 regression subset:       26 passed
v0.4.2 regression subset:       29 passed
v0.4.3 new regression:          24 passed
-----------------------------------------
full suite:                    305 passed, 0 failed
```

추가 targeted:

```text
Kakao/consecutive:              15 passed
ranking KRW/% unit:              1 passed
product state isolation:         4 passed
user-fact supersession:          3 passed
numeric range patch:             1 passed
objective-aware questions:       2 passed
calendar affordability:          4 passed
feasibility flow:                3 passed
answer atomicity:                9 passed
REST/MCP:                        3 passed
```

기타:

```text
compileall:                      PASS
source import/version 0.4.3:     PASS
v0.4.3 schema export:            36 schemas
wheel build:                     PASS
clean wheel install/import:      PASS
installed ApplicationService:    PASS
v0.4 adversarial:                PASS
v0.4.1 adversarial:              PASS
v0.4.2 adversarial:              PASS
v0.4.3 adversarial:              PASS
```

### Kakao rule semantics regression

변경하지 않았다.

```text
7주 연속 AUTO_TRANSFER SUCCESS  → +1.0%p
26주 연속 AUTO_TRANSFER SUCCESS → 추가 +2.0%p
7주 from_sequence               = None
```

따라서 1 실패 이후 3~9가 연속 성공이면 7주 reward를 받을 수 있다. Manual fill은 AUTO_TRANSFER SUCCESS를 복구하지 않는다. Contribution affordability schedule과 streak rule semantics는 별개다.

### Ranking unit regression

v0.4.1의 핵심 원칙도 유지했다.

```text
MAX_ESTIMATED_AFTER_TAX_INTEREST
→ KRW ↔ KRW only
→ unresolved contribution input에 %를 KRW substitute로 사용하지 않음
```

---

## 20. Known limitations

1. 실제 MyData/은행 API, production DB/session persistence, production HTTP server는 범위 밖이다.
2. 실제 금융상품 fixture는 기존 범위를 유지하며 전체 시장 coverage를 의미하지 않는다.
3. 예상이자는 기존 ContributionPolicy와 deterministic interest approximation을 사용하므로 실제 은행의 개별 일수/절사 규칙과 차이가 있을 수 있다.
4. 신한 급여우대의 모든 공식 ActionPath는 완전 구현하지 않았다.
5. 최초 자연어 → Structured Intent 오해 가능성은 기존 Accepted AI Risk로 유지한다.
6. LLM Result Explanation은 structured DTO를 근거로 자유 prose를 생성하지만 금융판정 Source of Truth는 아니다.
7. `FLEXIBLE` affordability는 명시적인 day/week/month cadence가 아니므로 event bucket fallback을 사용한다. MVP fixture의 핵심 cross-frequency 검증은 DAILY/WEEKLY/MONTHLY에 집중한다.

---

## 21. ProductContributionChoice revision deferred

이번 Sprint의 명시적 non-goal을 유지했다.

이미 정상 commit된:

```text
Kakao preferred_start_amount = 10,000
```

을 이후 대화에서:

```text
5,000으로 바꿀래
```

라고 수정하는 product-specific contribution revision UX는 구현하지 않았다.

다만 **validation 실패한 answer를 같은 질문에 다시 제출하는 것**은 revision이 아니며 완전히 지원한다.

또한 이미 commit된 product choice가 이후 global affordability patch 때문에 infeasible해지면 choice를 임의 변경하지 않고 `ADJUST_GLOBAL_AFFORDABILITY / EXCLUDE_PRODUCT` clarification을 제공한다.

---

## 22. 최종 READY 판정

# `READY_FOR_WEB_UI`

판정 근거:

- 전체 regression PASS
- weekly/daily product cashflow를 실제 날짜로 생성
- MONTHLY affordability를 weekly per-event limit로 잘못 사용하지 않음
- calendar month 5회 출금 정확히 계산
- infeasible option deterministic pruning
- single feasible option도 user confirmation
- all-options-infeasible 시 silent drop 없이 adjust/exclude clarification
- minimum required affordability structured 제공
- global affordability update 후 feasibility/evaluation/ranking refresh
- user-driven product exclusion 지원
- invalid answer business-state atomicity 보장
- validation 실패 후 동일 질문 재시도 가능
- submission-time revalidation 수행
- ProductContributionChoice state isolation 유지
- `MAX_REALIZABLE_RATE` interest-only question suppression 유지
- KRW/% ranking separation 유지
- Kakao latest streak semantics 유지
- REST/MCP가 동일 ApplicationService를 통해 전체 flow 사용
- Audit chain 유지

v0.4.3부터 backend는 Web UI가 structured question/option/clarification/result state를 직접 렌더링하여 MVP flow를 구현할 수 있는 기준선으로 판단한다.
