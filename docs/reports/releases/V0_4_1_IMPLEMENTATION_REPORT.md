# Financial Eligibility Engine v0.4.1 Implementation Report

**Sprint:** Application Layer Correctness & Web Contract Closure  
**기준 코드:** `financial-eligibility-engine-v0.4`  
**결과 버전:** `financial-eligibility-engine-v0.4.1` / package `0.4.1`  
**판정:** `READY_FOR_WEB_APPLICATION`

---

## 1. 변경 요약

v0.4의 Application Layer 구조를 유지하고, 실제 Web 추천 결과를 틀리게 만들 수 있는 correctness 문제만 좁게 수정했다. Core Rule DSL, `FinancialEligibilityEngine.evaluate_product()`, 기존 SearchSession/Candidate/Ranking/REST/MCP/Audit 구조를 다시 설계하지 않았다.

이번 patch의 핵심 변경은 다음이다.

1. 기본 세후이자 Ranking에서 `%`를 KRW의 fallback numeric key로 쓰던 단위 혼합을 제거했다.
2. 세후이자 계산에 필요한 cashflow 입력이 부족하면 `MissingRankingInput`을 생성하고 기존 질문 flow에서 해결하도록 했다.
3. 후속 Search Intent 수정은 category replacement가 아니라 `IntentPatch` upsert/remove semantics로 바꿨다.
4. Hard Filter의 카드/신규카드 문자열 heuristic을 제거하고 typed metadata/capability semantics만 authoritative하게 사용한다.
5. Canonical Claim을 숫자뿐 아니라 ACTION/CONDITION/STATUS/PRODUCT_ATTRIBUTE/RECOMMENDATION_REASON까지 확장했다.
6. Web contract에 structured Quick Input, current clarification 조회, RankingInput Q/A를 연결했다.
7. Product Detail에서 사용자 의미가 있는 nested OR branch를 `children[]`로 노출한다.
8. 카카오 7주/26주 FutureIntent를 achievement target별로 분리했다.
9. TopK stability를 실제 Question Planner의 material question 유무와 연결했다.
10. Recommendation reason을 deterministic structured evidence에서만 생성하도록 보정했다.
11. v0.4 adversarial script의 `PYTHONPATH=src` 재현 문제를 수정하고 v0.4.1 A~G adversarial script를 추가했다.

---

## 2. v0.4 baseline test 결과

수정 전 별도 보존된 v0.4 tree에서 직접 재확인했다.

```text
package version: 0.4.0
pytest: 226 passed
compileall: PASS
import sanity: PASS
```

기록:

```text
reports/baseline-v0.4-before-v0.4.1.txt
```

또한 independent review에서 지적한 adversarial script 재현 문제도 baseline tree에서 재현했다.

```text
PYTHONPATH=src python scripts/run_v04_adversarial.py
→ ModuleNotFoundError: No module named 'tests'
```

기록:

```text
reports/baseline-v0.4-adversarial-path-issue.txt
```

---

## 3. Interest Ranking 단위 문제 원인과 수정

### 원인

v0.4 기본 Ranking에 다음과 같은 semantics가 있었다.

```python
after_tax = (
    candidate.realizable_after_tax_interest
    if candidate.realizable_after_tax_interest is not None
    else candidate.realizable_rate
)
```

따라서 어떤 후보는 `165 KRW`, 다른 후보는 `5.0 %`를 동일 numeric sort key에서 비교할 수 있었다. 이 문제는 단순 precision 문제가 아니라 dimensional correctness 위반으로, Top 5를 잘못 만들 수 있다.

### 수정

기본 objective인 `MAX_ESTIMATED_AFTER_TAX_INTEREST`와 `BALANCED`에서 다음 원칙을 강제한다.

```text
final metric: realizable_after_tax_interest (KRW)
optimistic metric: conditional_upper_after_tax_interest (KRW)
```

세후이자 값이 없으면 rate로 대체하지 않는다.

새 상태:

```text
RankingComparability
- COMPARABLE
- MISSING_CONTRIBUTION_INPUT
- NOT_COMPARABLE
```

비교 불가능 후보는 추천 session에 남지만 세후이자 objective에서 `%`를 KRW로 취급하지 않는다. `RankingService.realizable_metric()` / `optimistic_metric()`의 missing sentinel도 rate가 아니라 `0`이며, 이 값은 financial equivalence가 아니라 unresolved state의 보수적 queue value다. 실제 List/Detail에는 `ranking_comparability`와 missing input이 별도로 노출된다.

Adversarial A에서:

```text
Product A-E: after_tax_interest = 165 KRW, rate = 0.01%
Product F: rate = 5.0%, after_tax_interest = unresolved
```

결과:

```text
F의 5.0%가 5 KRW처럼 substitute되지 않음
F = MISSING_CONTRIBUTION_INPUT
TopK stability = false
```

---

## 4. ContributionPlan completion flow

세후이자가 `None`이 되는 원인은 금리를 계산할 수 없어서가 아니라 실제 cashflow schedule을 만들 입력이 부족할 수 있기 때문이다.

`ContributionPlanner`는 이제 계산 불가능한 상품별 input을 구조화한다.

```text
ContributionPlan
→ ContributionPlanner
→ complete cashflow
   또는
→ MissingRankingInput[]
```

예: 카카오 26주적금에 사용자가 “월 30만원”만 입력한 경우 이 값을 주간 점증식 cashflow로 임의 변환하지 않는다.

```text
required_field = preferred_start_amount
allowed_options = 1,000 / 2,000 / 3,000 / 5,000 / 10,000 KRW
```

사용자가 10,000원을 선택하면:

```text
1주차 10,000
2주차 20,000
...
26주차 260,000
총 26회
총 원금 3,510,000 KRW
```

그 후 기존 Interest Engine으로 예상 세전/세후이자를 계산한다. v0.4.1 adversarial 실행에서는 세후이자 `26,576 KRW`가 생성되었다.

---

## 5. RankingInput Question

새 schema:

```text
MissingRankingInput
- input_id
- input_type = CONTRIBUTION_PLAN
- product_id
- required_field
- allowed_options
- reason
- affected_metric = ESTIMATED_AFTER_TAX_INTEREST
- status
- question
```

`RankingAwareQuestionPlanner`는 이제 두 종류의 unresolved input을 처리한다.

```text
A. FINANCIAL_FACT
   MissingFactRequest

B. RANKING_INPUT
   MissingRankingInput
```

RankingInput은 동일 단위 비교를 성립시키기 위한 prerequisite이므로, 세후이자 objective에서 frontier 밖에 있더라도 unresolved contribution input이 있으면 질문 후보가 될 수 있다.

Web에서는 기존 flow를 그대로 사용한다.

```text
GET  /search-sessions/{id}/questions/next
POST /search-sessions/{id}/answers
```

사용자가 명시적으로 거절하면 값을 추측하지 않고:

```text
ranking_comparability = MISSING_CONTRIBUTION_INPUT
estimated_after_tax_interest = null
```

로 남긴다.

---

## 6. IntentPatch semantics

v0.4 후속 intent update는 LLM/parsing 결과의 non-empty category가 기존 category 전체를 교체할 수 있었다. 이 방식은 “신규카드는 싫어” 한 문장 때문에 기존 급여계좌/영업점 capability가 사라지는 문제를 만들 수 있었다.

v0.4.1에서는 `IntentPatch`를 도입했다.

```text
IntentPatch
- upsert_product_types / remove_product_types
- upsert_hard_constraints / remove_hard_constraint_keys
- upsert_preferences / remove_preference_keys
- upsert_capabilities / remove_capability_keys
- upsert_numeric_preferences / remove_numeric_preference_keys
- contribution_plan_patch
- ranking_objective_patch
- requested_top_k_patch
```

기본 semantics:

```text
omitted = preserve
upsert = matching key만 수정
remove = 명시한 key만 제거
```

예:

```text
before:
CHANGE_SALARY_ACCOUNT = CAN
BRANCH_VISIT = CAN
NEW_CARD_ISSUANCE = UNKNOWN

utterance:
“카드 새로 만드는 건 싫어.”

after:
CHANGE_SALARY_ACCOUNT = CAN
BRANCH_VISIT = CAN
NEW_CARD_ISSUANCE = CANNOT
```

`ContributionPlanPatch`도 월 납입액 하나를 수정할 때 affordability/term 등 다른 field를 유지한다.

Quick Input은 같은 key에 대해 natural-language/LLM inferred value보다 우선하며, unrelated inferred values는 보존한다.

---

## 7. Typed Hard Filter

v0.4의 Hard Filter에 남아 있던 rule/action label 문자열 검색을 authoritative 판단에서 제거했다.

새 우선순위:

```text
ProductMetadata.features (ProductFeature)
→ mandatory eligibility ActionPath.required_capabilities
→ conservative UNKNOWN
```

`ProductFeature`:

```text
feature_id
present
required_for_subscription
required_capabilities[]
source_reference
```

중요한 경계:

```text
NEW_CARD_ISSUANCE = CANNOT
```

은 우대 ActionPath를 닫을 수 있지만 상품 전체를 탈락시키는 조건이 아니다. `NEW_CARD_REQUIRED`가 가입 자체의 mandatory typed feature일 때만 hard exclusion할 수 있다.

Regression에서 Korean/English rule label에 `카드`, `신규카드`, `NEW_CARD_REQUIRED`, `CARD`를 넣어도 typed metadata가 없다면 hard filter가 이를 authoritative signal로 사용하지 않음을 검증했다.

---

## 8. Canonical nonnumeric claims

기존 typed numeric grounding을 유지하면서 Claim type을 확장했다.

```text
RATE
AMOUNT
DURATION
TARGET
ACTION
CONDITION
STATUS
PRODUCT_ATTRIBUTE
RECOMMENDATION_REASON
```

MVP에서는 완전한 semantic equivalence engine을 만들지 않았다. 대신 canonical concept + 허용 paraphrase alias 방식으로 구현했다.

예:

```text
SALARY_ENVELOPE
SALARY_ACCOUNT_CHANGE
NEW_CARD_ISSUANCE
DEMAND_DEPOSIT_ACCOUNT_OPENING
SUPERSOL_MEMBERSHIP
FIRST_TRANSACTION
EVENT_COUPON
AUTO_TRANSFER
...
```

Question/Explanation output에서 canonical payload에 없는 신규 action/condition concept가 발견되면 검증에 실패하고 deterministic fallback을 사용한다.

Regression:

```text
test_question_rejects_new_non_numeric_action
test_explainer_rejects_new_non_numeric_condition
test_allowed_action_paraphrase_remains_possible
test_invalid_claim_uses_deterministic_fallback
```

Explanation regression은 allowed claim bindings를 정상적으로 제공한 상태에서도 source에 없는 `신규 입출금통장 개설` action 때문에 reject되는 것을 검증한다.

---

## 9. Web Quick Input / Clarification contract

REST adapter는 이제 search 생성 시 structured Quick Input을 직접 받을 수 있다.

```json
{
  "user_id": "USER-001",
  "natural_language_query": "1년 정도 월 30만원을 넣을 적금 찾아줘",
  "quick_input": {
    "capabilities": [
      {"capability_id": "NEW_CARD_ISSUANCE", "state": "CANNOT"}
    ]
  },
  "as_of": "2026-08-20",
  "subscription_date": "2026-08-20"
}
```

Quick Input priority:

```text
Quick Input direct structured value
> natural-language / LLM inferred value for same key
```

Clarification contract:

```text
GET  /search-sessions/{id}/clarifications/current
POST /search-sessions/{id}/clarifications
```

따라서 Web가 `CLARIFICATION_REQUIRED` 상태만 받고 실제 질문을 조회할 수 없는 문제가 없다.

RankingInput도 기존 question/answer endpoint에서 처리한다.

---

## 10. Nested Detail Breakdown

`RateBreakdownItem`에 recursive `children[]`를 추가했다.

모든 AST debug node를 dump하지 않고 사용자 의미가 있는 logical OR branch만 노출한다.

신한 fixture의 `첫거래 또는 이벤트 +1.0%p` 결과는 다음과 같이 표현된다.

```text
RATE_FIRST_OR_EVENT = UNKNOWN
├─ RATE_FIRST_TRANSACTION_BRANCH
│  UNSATISFIABLE
│  PRIOR_HOLDING_OVERLAPS_LOOKBACK
└─ RATE_EVENT_BRANCH
   UNKNOWN
   EVENT_COUPON_STATUS_UNRESOLVED
```

branch child의 `nominal_reward_pp`는 `None`으로 두어 하나의 parent reward를 각 OR branch에 중복 귀속하지 않는다.

---

## 11. 카카오 FutureIntent 처리

v0.4 fixture는 7주 +1.0%p와 26주 추가 +2.0%p가 동일 FutureIntent fact를 공유했다.

v0.4.1은 target별 intent를 분리한다.

```text
WILL_ATTEMPT_KAKAO_7_CONSECUTIVE
WILL_ATTEMPT_KAKAO_26_CONSECUTIVE
```

따라서:

```text
7주까지는 시도 = YES
26주 전체는 시도 = NO
```

일 때 7주 가능성까지 닫히지 않는다.

최신 상품 Rule semantics는 변경하지 않았다.

```text
7-week from_sequence = None
```

따라서:

```text
1 SUCCESS
2 FAILED
3~9 SUCCESS
→ 3~9의 7연속으로 +1.0%p
```

가 그대로 유지된다. Manual fill도 AUTO_TRANSFER SUCCESS를 복구하지 않는다.

Kakao/consecutive targeted regression: `11 passed`.

---

## 12. TopK stability 연결

v0.4의 `TopKStabilityResult.material_internal_question_remaining`을 실제 Question Planner 결과와 연결했다.

ApplicationService는 ranking 후 실제 `get_next_question` materiality를 사용해 stability를 refresh한다.

최종 stable 조건:

```text
Top 5 membership 안정
AND
material user-answerable question 없음
AND
세후이자 objective에서 direct comparison metric이 모두 comparable
```

6개 synthetic product adversarial에서 5위/6위 membership은 이미 안정적이지만 1위 상품의 material question이 남아 있는 경우:

```text
stable_membership = true
material_internal_question_remaining = true
stable = false
reason_code = MATERIAL_QUESTION_REMAINS
```

을 확인했다.

---

## 13. Recommendation reason 보정

generic 문구를 무조건 생성하지 않고 structured reason evidence를 먼저 만든다.

현재 reason code 예:

```text
TERM_MATCH
CONTRIBUTION_PLAN_FULLY_SUPPORTED
HIGHEST_AFTER_TAX_INTEREST
LOW_ACTION_BURDEN
NO_NEW_CARD_REQUIRED
MISSING_CONTRIBUTION_INPUT
```

각 reason은 실제 evidence가 있을 때만 생성한다.

예:

- 사용자가 기간을 지정하지 않았으면 `TERM_MATCH`를 만들지 않음
- interest objective의 1위이면서 comparable일 때만 `HIGHEST_AFTER_TAX_INTEREST`
- typed metadata가 명시할 때만 `NO_NEW_CARD_REQUIRED`
- contribution metric이 unresolved면 positive financial reason 대신 missing input unknown을 생성

LLM은 structured reason을 자연스럽게 표현할 수 있지만 새로운 추천 factor를 만들 수 없다.

---

## 14. REST 변경

추가/보강된 contract:

```text
POST /search-sessions
- natural_language_query
- quick_input

GET /search-sessions/{id}/clarifications/current

GET /search-sessions/{id}/questions/next
POST /search-sessions/{id}/answers
- FINANCIAL_FACT와 RANKING_INPUT 공통 flow

PATCH /search-sessions/{id}/intent
- utterance 또는 structured IntentPatch
```

기존 recommendations/detail/trace route는 유지한다.

FastAPI/Flask 자체를 추가하지 않았다. Adapter는 기존처럼 framework-neutral thin contract다.

---

## 15. MCP 변경

MCP-style adapter 구조는 그대로 유지했다.

```text
MCP Adapter
→ ApplicationService
→ Financial Eligibility Engine
```

최소 추가 노출:

```text
search_products(... quick_input=...)
get_current_clarification(...)
update_search_intent(... patch=...)
```

RankingInput은 기존 `get_next_question` / `submit_user_fact`를 통해 처리된다. MCP에 금융판정/Ranking business logic을 복제하지 않았다.

---

## 16. Audit 변경

기존 append-only Audit taxonomy를 재사용했다. 새로운 금융 로직을 Audit layer에 넣지 않았다.

RankingInput 처리 시 `SEARCH_INTENT_UPDATED` event에 다음 operation을 기록한다.

```text
RANKING_INPUT_RESOLVED
RANKING_INPUT_DECLINED
```

Intent version은 contribution plan patch 후 증가하며 이후 candidate re-evaluation/ranking과 동일 `search_session_id` chain으로 연결된다.

기존 chain도 유지된다.

```text
SEARCH_SESSION_CREATED
→ SEARCH_INTENT_PARSED / UPDATED
→ CANDIDATE_EVALUATED
→ QUESTION_SELECTED
→ USER ANSWER / INTENT UPDATE
→ RANKING_CALCULATED
→ TOP_K_STABILITY_CHECKED
→ RECOMMENDATION_CREATED
→ PRODUCT_DETAIL_OPENED
→ EXPLANATION_GENERATED
```

v0.4 adversarial script를 v0.4.1 환경에서 다시 실행해 List/Detail/Audit chain이 여전히 materialize되는 것을 확인했다.

---

## 17. 신규 regression tests

v0.4.1 전용 테스트 파일 4개, 총 26개를 추가했다.

```text
tests/test_v041_ranking_inputs.py
tests/test_v041_intent_patch_and_hard_filter.py
tests/test_v041_grounding_web_detail.py
tests/test_v041_kakao_stability_reasons.py
```

주요 coverage:

### Ranking / Contribution

```text
test_interest_ranking_never_substitutes_percentage_for_missing_krw_interest
test_kakao_missing_start_amount_becomes_ranking_input_question
test_kakao_start_amount_answer_builds_26_week_cashflow_interest_and_ranking
test_declined_contribution_question_remains_explicitly_not_comparable
```

### Intent Patch

```text
test_intent_patch_preserves_unmentioned_capabilities
test_intent_patch_updates_single_capability
test_intent_patch_removes_only_explicit_target
test_contribution_plan_patch_preserves_other_fields
```

### Typed Hard Filter

```text
test_new_card_exclusion_does_not_remove_existing_card_path_product
test_required_capability_feature_can_filter_when_truly_mandatory
test_preferential_card_rule_is_not_equivalent_to_new_card_requirement
test_hard_filter_does_not_depend_on_korean_or_english_keyword_matching
```

### Canonical Claim / Web / Detail

```text
test_question_rejects_new_non_numeric_action
test_explainer_rejects_new_non_numeric_condition
test_allowed_action_paraphrase_remains_possible
test_invalid_claim_uses_deterministic_fallback
test_web_search_session_accepts_quick_input
test_web_can_fetch_current_clarification
test_web_can_answer_ranking_input_question
test_detail_breakdown_exposes_meaningful_or_branches
test_first_transaction_unsat_event_unknown_visible_separately
```

### Kakao / Stability / Reasons

```text
test_kakao_26_week_decline_does_not_close_7_week_future_intent
test_topk_stability_uses_actual_question_planner_materiality
test_recommendation_reason_does_not_claim_term_match_without_period_preference
test_recommendation_reason_term_match_has_structured_evidence_when_true
test_quick_input_overrides_same_key_llm_or_natural_inference
```

최종 test matrix:

```text
v0.3.2 regression subset: 176 passed
v0.4 regression subset:    50 passed
v0.4.1 new tests:          26 passed
--------------------------------------
full suite:               252 passed, 0 failed
```

---

## 18. 직접 실행한 adversarial tests

`scripts/run_v041_adversarial.py`를 `PYTHONPATH=src`만으로 실행했다.

### A. Interest Ranking

- 165 KRW 후보 5개
- 5.0%이나 contribution unresolved 후보 1개
- rate가 KRW substitute가 되지 않음
- unresolved candidate는 `MISSING_CONTRIBUTION_INPUT`
- stability false

### B. 카카오 ContributionPlan

- 월 납입 희망만 있음
- 시작금액 없음
- RankingInput 질문 생성
- 10,000원 답변
- 26주 점증 cashflow 생성
- 총 원금 3,510,000원
- 예상 세후이자 생성
- Ranking comparable 상태로 전환

### C. Intent Patch

capability 3개 중 신규카드만 수정하고 나머지 두 capability가 보존됨.

### D. Hard Filter

카드 우대 rule label에 카드 관련 문자열이 있어도 신규카드 mandatory typed feature가 아니면 상품 전체가 제거되지 않음.

### E. LLM nonnumeric hallucination

Question과 Explanation에 source에 없는 `신규 입출금통장 개설` action을 넣은 mock output을 reject하고 deterministic fallback 사용.

### F. Nested Detail

신한 fixture에서:

```text
첫거래 branch = UNSATISFIABLE
이벤트 branch = UNKNOWN
```

을 user-facing children으로 분리 노출.

### G. Top 5 stability

6개 synthetic product의 5위/6위 경계를 검증하고, membership이 안정적이어도 actual material question이 남아 있으면 `stable=false`임을 확인.

결과 파일:

```text
examples/v0.4.1/adversarial-results-v0.4.1.json
reports/adversarial-run-v0.4.1.txt
```

추가로 기존 v0.4 adversarial script도 path를 보정한 뒤 v0.4.1 tree에서 재실행하여 PASS했다.

---

## 19. Known limitations

이번 patch에서 의도적으로 해결하지 않은 범위:

- 실제 Web UI / React
- FastAPI/Flask production HTTP server
- 실제 MyData API
- 실제 은행 API / Institution Service API
- 전체 금융상품 자동 수집
- Production DB
- 신한 급여우대 모든 공식 대체 ActionPath 완성
- 완전한 Net Benefit Engine
- 행동 성공확률 예측
- 사용자 피로도를 고려한 질문 횟수 최적화
- 자유로운 LLM Planner
- Graph RAG / Vector DB
- 실제 은행별 일수/절사까지 100% 동일한 Interest settlement

세후이자 계산 입력을 사용자가 거절한 후보는 `MISSING_CONTRIBUTION_INPUT` 상태로 남으며, rate는 정보로 노출할 수 있지만 세후이자 기준으로 정확히 비교되었다고 표시하지 않는다.

---

## 20. Accepted AI risk

이번 Sprint에서 다음 위험은 의도적으로 완전히 해결하지 않았다.

> 최초 사용자 자연어를 LLM이 Structured Intent로 변환할 때 사용자의 의미를 잘못 해석할 가능성

현재 MVP의 safety boundary는 다음이다.

```text
사용자가 무엇을 원하는가?
→ LLM 해석 가능

Structured Intent schema/enum 밖의 값
→ validation 차단

Quick Input의 직접 structured value
→ 같은 key의 LLM inferred value보다 우선

Structured Intent 내부 충돌
→ deterministic IntentConflictValidator

원본 utterance ↔ parsed intent
→ Search/Audit chain에 보존

상품 가입조건 판정
→ deterministic

금리
→ deterministic

이자
→ deterministic

Ranking
→ deterministic
```

즉 자연어 intent parser를 이번 patch에서 규칙기반 parser로 다시 만들지 않았다. 이 항목은 MVP `Accepted AI Risk`다.

---

## 21. 최종 READY 판정

### Validation 결과

```text
v0.4 baseline before patch: 226 passed
v0.3.2 regression:          176 passed
v0.4 regression:             50 passed
v0.4.1 new regression:       26 passed
full suite:                 252 passed, 0 failed
compileall:                 PASS
import/package sanity:      PASS
v0.4.1 schema export:        33 schemas
v0.4 adversarial rerun:     PASS with PYTHONPATH=src
v0.4.1 adversarial A-G:     PASS
Kakao targeted regression:   11 passed
wheel build:                PASS
wheel install/service smoke: PASS
```

### Acceptance Criteria 판정

- 기존 regression PASS: **YES**
- KRW/% Ranking 혼합 제거: **YES**
- contribution input 질문으로 resolve: **YES**
- intent update patch/merge: **YES**
- typed hard filter / keyword false positive 제거: **YES**
- nonnumeric canonical claim grounding: **YES**
- Web Quick Input: **YES**
- Web current clarification: **YES**
- Web RankingInput answer flow: **YES**
- nested Detail branch: **YES**
- Top 5 synthetic adversarial: **YES**
- 카카오 최신 7주 semantics: **YES**
- Audit chain 유지: **YES**

# `READY_FOR_WEB_APPLICATION`

v0.4.1은 실제 Web UI가 바로 연결할 backend contract 기준선으로 사용할 수 있다. 다만 Section 19~20의 제한사항과 Accepted AI Risk는 MVP 문서와 Web UX에서 과장 없이 유지해야 한다.
