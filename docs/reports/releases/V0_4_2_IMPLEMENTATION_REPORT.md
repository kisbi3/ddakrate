# Financial Eligibility Engine v0.4.2 — Implementation Report

**Sprint:** Final Application State & Web Handoff Closure  
**Base:** `financial-eligibility-engine-v0.4.1`  
**Target:** `financial-eligibility-engine-v0.4.2`  
**Date:** 2026-08-20  
**Final verdict:** `READY_FOR_WEB_UI`

---

## 1. v0.4.1 baseline

v0.4.1을 새 프로젝트로 재구현하지 않고 그대로 기준선으로 사용했다. 수정 전 기준선에서 직접 재검증한 결과는 다음과 같다.

```text
pytest:                         252 passed
compileall:                     PASS
source import/version:          0.4.1 / PASS
wheel build:                    PASS
clean install/import:           PASS
v0.4 adversarial script:        PASS
v0.4.1 adversarial script:      PASS
```

소스 working tree 자체는 설치된 distribution이 아니므로 global environment의 `importlib.metadata.version()` 조회는 기준선 source import 단계에서 distribution metadata를 찾지 못할 수 있다. 별도로 v0.4.1 wheel을 빌드해 isolated target에 설치한 뒤 package/distribution version 모두 `0.4.1`임을 확인했다.

v0.4.1의 기존 Application Layer 구조와 `FinancialEligibilityEngine.evaluate_product()` deterministic core는 유지했다.

---

## 2. 수정 파일 목록

핵심 코드 수정:

```text
src/eligibility/application_service.py
src/eligibility/schema/search.py
src/eligibility/search/contribution.py
src/eligibility/search/evaluation.py
src/eligibility/search/questions.py
src/eligibility/search/intent.py
src/eligibility/audit/models.py

src/eligibility/fixtures/shinhan_youth_first.py
src/eligibility/fixtures/kakao_26_week.py
src/eligibility/fixtures/ibk_parent_benefit.py
src/eligibility/fixtures/hana_run.py

src/eligibility/__init__.py
src/eligibility/schema/__init__.py
scripts/export_schemas.py
scripts/run_v042_adversarial.py
pyproject.toml
README.md
```

신규 regression:

```text
tests/test_v042_final_state.py
```

신규/갱신 산출물:

```text
schemas/*.v0.4.2.schema.json
examples/v0.4.2/v0.4.2-adversarial-results.json
reports/v0.4.2-*.txt
V0_4_2_IMPLEMENTATION_REPORT.md
```

대규모 Core refactor, 새로운 Ranking 모델, 새로운 Web framework는 도입하지 않았다.

---

## 3. Product-specific Contribution state 설계

### 문제

v0.4.1에서는 RankingInput answer가 `ProductSearchIntent.contribution_plan`에 들어갈 수 있었다. 이 방식은 다음 두 상태를 섞을 위험이 있었다.

```text
Global user preference:
월 300,000원 정도 납입 희망

Kakao-specific cashflow choice:
26주적금 시작금액 10,000원
```

카카오의 `10,000원`을 전역 `desired_periodic_amount` 또는 전역 `preferred_start_amount`처럼 다루면 다른 상품 cashflow와 Ranking이 오염될 수 있다.

### 수정

신규 schema:

```text
ProductContributionChoice
- choice_id
- product_id
- field
- value
- unit
- source_question_id
- answered_at
```

Application runtime은 다음 identity로 active choice를 관리한다.

```text
(product_id, field)
```

예:

```text
(KAKAO_26W, preferred_start_amount) = 10,000
(PRODUCT_B, preferred_start_amount) = 5,000
```

두 값은 독립적으로 유지된다.

`SearchSession.product_contribution_choices[]`에도 현재 active product-scoped choice를 노출하여 Web UI가 session state를 그대로 렌더링할 수 있게 했다.

### 질문 identity

`MissingRankingInput.input_id`는 기존부터 `product_id + required_field + options`를 포함한다. `PlannedQuestion.question_id`는 이 product-scoped input ID를 기반으로 생성하므로 같은 `preferred_start_amount` field를 쓰는 두 상품이 동일 질문으로 취급되지 않는다.

---

## 4. Global ContributionPlan과의 경계

전역 `ContributionPlan`은 검색 전체에 적용되는 사용자 납입 선호/여력이다.

```text
desired_periodic_amount
maximum_affordable_periodic_amount
frequency
selected_term
currency
tax_rate
```

v0.4.1 direct-evaluator compatibility를 위해 legacy `preferred_start_amount / incremental_amount` field는 schema에 남겼지만, **ApplicationService의 RankingInput answer flow는 이 필드를 더 이상 수정하지 않는다.** 기존 private global-plan mutation helper도 v0.4.2에서 제거했다.

상품별 Interest 계산은 다음 방식으로만 수행한다.

```text
Global ContributionPlan
+
matching ProductContributionChoice(product_id)
+
ProductMetadata.ContributionPolicy
→ effective product contribution plan
→ product-specific cashflow
→ deterministic interest calculation
```

`apply_product_contribution_choices()`는 matching `product_id`의 choice만 overlay하며 원본 global plan을 mutate하지 않는다.

검증 예:

```text
Global desired amount = 300,000 KRW / MONTH
Product A start amount = 10,000 KRW
Product B start amount = 5,000 KRW

A principal = 3,510,000 KRW
B principal = 1,755,000 KRW
Global desired amount remains 300,000 KRW
```

---

## 5. ProductFeature 실제 fixture wiring

`ProductFeature`를 Rule AST 대체물로 사용하지 않았다.

```text
Rule AST       = 금융판정 Source of Truth
ProductFeature = search/filter/preference typed index
```

실제 4개 regression fixture에 최소 검색 feature를 연결했다.

### 신한은행 청년 처음적금

```text
FIRST_TRANSACTION_BENEFIT
CARD_BENEFIT
APP_SERVICE_BENEFIT
```

모두 preferential/search feature이며 가입 필수 신규카드 requirement가 아니다.

### 카카오뱅크 26주적금

```text
INCREMENTAL_CONTRIBUTION
CONSECUTIVE_AUTO_TRANSFER_BENEFIT
```

### IBK 부모급여우대적금

```text
RELATED_PERSON_PERFORMANCE
BRANCH_FAMILY_REGISTRATION_PATH
```

`BRANCH_FAMILY_REGISTRATION_PATH` 역시 특정 우대 경로이며 상품 전체 가입 필수로 표시하지 않았다.

### 하나은행 달려라 하나 적금

```text
HEALTH_DATA_BENEFIT
MYDATA_SERVICE_BENEFIT
APP_SERVICE_BENEFIT
```

### present vs required

다음 의미를 계속 분리한다.

```text
CARD_BENEFIT.present = true
!=
NEW_CARD_REQUIRED.required_for_subscription = true
```

따라서 `NEW_CARD_ISSUANCE=CANNOT`만으로 신한 상품 전체를 제거하지 않는다. 실제 mandatory feature만 `required_for_subscription=true`와 typed required capability를 통해 hard filter될 수 있다.

Rule ID와 실제 feature wiring의 명백한 불일치를 잡는 regression도 추가했다. 자동 feature extraction framework는 만들지 않았다.

---

## 6. User-declared Fact supersession

### 문제

`ProductSearchIntent.capabilities`와 기존 질문 답변에서 생성된 `USER_DECLARED` Fact가 서로 다른 request reference를 사용하면 동일 semantic fact가 동시에 active로 남을 수 있었다.

예:

```text
기존 질문 답변:
SALARY_ACCOUNT_CHANGE_POSSIBLE = true

후속 Intent patch:
CHANGE_SALARY_ACCOUNT = CANNOT
```

### 수정

`ApplicationService._materialize_capabilities()`가 capability를 user-declared fact로 materialize할 때 동일 `fact_type`의 active user declaration을 semantic revision으로 처리한다.

적용 대상:

```text
source_type = USER_DECLARED
semantic_type in {
  SELF_REPORTED_FACT,
  FUTURE_INTENT
}
```

처리:

```text
prior active user declaration → SUPERSEDED
new capability declaration    → ACTIVE
```

동일 intent-created reference의 true→false / false→true revision은 기존 `UserFactStore.with_answer_fact()` versioning을 재사용한다.

### authoritative evidence 보호

다음 evidence는 intent update로 supersede하지 않는다.

```text
INSTITUTION_VERIFIED
MYDATA_VERIFIED
DERIVED authoritative observations
OBSERVED_FACT / OBSERVED_EVENT
```

FactResolver의 기존 source authority 우선순위도 유지되므로 authoritative fact와 user declaration이 함께 있으면 높은 authority source가 deterministic하게 선택된다.

Audit에는 과거/현재 Fact 모두 남는다.

---

## 7. Objective-aware Question Planner

`RankingAwareQuestionPlanner`가 `MissingRankingInput.affected_metric`과 현재 `RankingObjective`를 함께 본다.

### MAX_ESTIMATED_AFTER_TAX_INTEREST

```text
interest-only product contribution input
→ 필요하면 질문
```

카카오 26주적금의 start amount가 없으면 `preferred_start_amount` RankingInput을 만든다.

### MAX_REALIZABLE_RATE

```text
interest 계산에만 필요한 contribution input
→ 질문하지 않음
```

사용자가 “금리가 가장 높은 상품”을 요청한 상황에서 세후이자 계산용 시작금액을 먼저 묻지 않는다.

단 contribution 관련 정보가 Eligibility 또는 Rate Rule 자체에 필요한 금융조건이면 기존 `MissingFactRequest` flow를 통해 계속 질문한다. 즉 objective-aware suppression은 **interest-only RankingInput**에만 적용한다.

고정 질문 횟수 제한은 추가하지 않았다.

---

## 8. NumericPreference patch semantics

기존 field-only merge key는 다음 valid range를 깨뜨릴 수 있었다.

```text
MONTHLY_CONTRIBUTION / AT_LEAST / 200,000
MONTHLY_CONTRIBUTION / AT_MOST  / 300,000
```

v0.4.2 identity:

```text
(field, direction, currency)
```

따라서:

- lower bound update는 upper bound를 보존
- upper bound update는 lower bound를 보존
- 같은 field + direction + currency만 replace
- plain field remove는 해당 field 모든 bound 제거
- `FIELD:DIRECTION` remove도 지원

Quick Input의 contradictory HARD lower > upper validation은 기존 deterministic validation을 그대로 유지한다.

---

## 9. LLM free-prose explanation policy

이번 Sprint에서는 LLM explanation semantic NLP validator를 새로 확장하지 않았다.

현재 정책:

```text
Structured deterministic result
→ Web UI Source of Truth

Structured deterministic result
→ LLM context
→ auxiliary free prose explanation
```

LLM에는 다음 structured context를 준다.

```text
rank
reason_codes
canonical claims
rate condition status/reward
structured recommendation reason
provenance
```

LLM은 자연스러운 문장으로 설명할 수 있다. 기존 v0.4.1 canonical grounding/fallback safety는 regression 호환을 위해 유지하지만, 이번 v0.4.2에서 새로운 자연어 semantic equivalence engine은 추가하지 않았다.

중요하게 다음 reverse path는 존재하지 않는다.

```text
LLM prose
→ parse
→ Eligibility / Rate / Interest / Ranking 수정
```

테스트에서는 의도적으로 `99%`를 말하는 custom explainer prose를 넣어도 List/Detail의 structured rate와 Top ranking이 전혀 변하지 않는 것을 확인했다.

---

## 10. Structured UI reason

List / Detail UI의 추천 이유는 deterministic `RecommendationReason`과 reason code에서 나온다.

예:

```text
HIGHEST_AFTER_TAX_INTEREST
TERM_MATCH
CONTRIBUTION_PLAN_FULLY_SUPPORTED
HIGH_REALIZABLE_RATE
NO_NEW_CARD_REQUIRED
LOW_ACTION_BURDEN
```

각 reason은 실제 structured evidence가 있을 때만 생성하는 기존 v0.4.1 원칙을 유지했다.

LLM explanation을 다시 파싱하여 UI reason으로 사용하지 않는다.

---

## 11. REST 변경

새 HTTP framework는 도입하지 않았다. 기존 `RestApplicationAdapter → ApplicationService` 구조를 유지한다.

기존 flow:

```text
GET  /search-sessions/{id}/questions/next
POST /search-sessions/{id}/answers
GET  /search-sessions/{id}/recommendations
GET  /search-sessions/{id}/recommendations/{product_id}
```

RankingInput question은 이미 `product_id`, `required_field`, `allowed_options`를 포함한다. 답변 후 returned `SearchSession`에:

```text
product_contribution_choices[]
```

가 포함된다.

검증:

```text
GET next question
→ Kakao preferred_start_amount

POST answer = 10,000
→ ProductContributionChoice(Kakao, preferred_start_amount, 10,000)
→ global desired_periodic_amount remains 300,000
→ recommendation refresh
```

### Quick Input malformed payload

이번 Sprint에서 동일 Quick Input field에 `CAN`과 `CANNOT`을 동시에 넣는 payload semantics는 바꾸지 않았다.

```text
malformed structured Quick Input
→ schema validation error / 400

valid input sources 간 semantic contradiction
→ IntentConflict
→ clarification
```

---

## 12. MCP 변경

MCP는 기존 thin adapter 그대로다.

```text
MCPToolAdapter
→ ApplicationService
→ FinancialEligibilityEngine
```

새 business logic은 MCP에 추가하지 않았다. 기존 `get_next_question` / `submit_user_fact`가 같은 ApplicationService session을 사용하므로 ProductContributionChoice도 별도 MCP 로직 없이 그대로 처리된다.

MCP regression에서 5,000원 start amount를 submit한 뒤 동일 ApplicationService가 같은 product-specific choice를 보유하고 global plan은 300,000원으로 유지되는 것을 확인했다.

---

## 13. Audit 변경

신규 event type:

```text
PRODUCT_CONTRIBUTION_INPUT_REQUESTED
PRODUCT_CONTRIBUTION_INPUT_RECORDED
USER_DECLARED_FACT_SUPERSEDED_BY_INTENT_UPDATE
```

Product contribution event에는 최소 다음 lineage를 남긴다.

```text
search_session_id
question_id
product_id
ranking_input_id
choice_id
required_field
```

Intent update supersession event에는:

```text
prior_fact_id
new_fact_id
fact_type
capability_id
prior/new value hash
authoritative_fact_superseded = false
```

를 기록한다.

기존 search / evaluation / ranking audit chain은 유지한다.

---

## 14. 신규 regression tests

`tests/test_v042_final_state.py`: **29 passed**

### Product-specific Contribution

```text
test_product_specific_contribution_choice_does_not_mutate_global_plan
test_two_products_can_have_different_start_amounts
test_product_specific_choice_used_only_for_matching_product
test_product_choice_survives_reranking
test_answered_question_identity_is_product_scoped
```

### ProductFeature wiring

```text
test_real_product_fixtures_have_search_features
test_search_feature_wiring_matches_real_fixture_semantics
test_first_transaction_preference_affects_real_fixture
test_first_transaction_preference_affects_shinhan_ranking
test_card_benefit_not_equal_new_card_required_real_fixture
test_incremental_feature_wired_to_kakao
test_preferential_feature_does_not_hard_filter_for_new_card_capability
```

기존 v0.4.1의 mandatory typed feature hard-filter regression도 그대로 통과한다.

### UserFact supersession

```text
test_intent_update_supersedes_prior_user_declared_fact
test_intent_revision_history_remains_auditable
test_true_to_false_leaves_single_active_declaration
test_false_to_true_leaves_single_active_declaration
test_authoritative_fact_not_superseded
test_revision_re_evaluates_products
```

### Objective-aware questions

```text
test_max_rate_objective_skips_interest_only_contribution_question
test_contribution_question_still_asked_if_needed_for_eligibility
test_after_tax_objective_requests_missing_product_contribution
```

### Numeric range patch

```text
test_numeric_range_survives_patch
test_lower_bound_update_preserves_upper_bound
test_upper_bound_update_preserves_lower_bound
test_same_field_same_direction_replaces_old_value
```

### LLM/UI separation and adapters

```text
test_ui_reason_comes_from_structured_reason_code
test_llm_explanation_not_used_as_financial_source
test_rest_product_specific_choice_isolated_from_global_plan
test_mcp_product_specific_choice_uses_shared_application_service
```

---

## 15. 직접 adversarial tests

`scripts/run_v042_adversarial.py`를 `PYTHONPATH=src`로 직접 실행했다.

### Scenario A — 두 점증식 상품

```text
Global desired monthly = 300,000
A start = 10,000
B start = 5,000
```

결과:

```text
Global = 300,000 유지
A principal = 3,510,000
B principal = 1,755,000
A/B 세후이자 각각 독립 계산
Top ranking 정상
PASS
```

### Scenario B — 사용자 능력 수정

```text
salary account change: CAN → CANNOT
```

결과:

```text
old user declaration = SUPERSEDED
new false declaration = ACTIVE
active user declaration count = 1
realizable rate 3.0 → 2.0
Audit supersession event = present
PASS
```

### Scenario C — 신규카드 싫음

```text
NEW_CARD_ISSUANCE = CANNOT
```

신한의 `CARD_BENEFIT`은 preferential feature이고 `NEW_CARD_REQUIRED` mandatory feature가 아니므로 상품은 후보에 남았다.

### Scenario D — MAX_REALIZABLE_RATE

카카오 start amount가 interest 계산에만 필요할 때 RankingInput question을 생성하지 않았다. Realizable rate는 deterministic하게 계산됐다.

### Scenario E — Numeric range

```text
AT_LEAST 200,000
AT_MOST  300,000
→ lower patch 220,000
```

결과:

```text
AT_LEAST 220,000
AT_MOST  300,000
PASS
```

### Scenario F — REST

Kakao ranking-input answer가 `product_contribution_choices`에 들어가고 global plan은 유지됐다.

### Scenario G — MCP

동일 ApplicationService를 통해 product-specific choice가 기록되며 adapter에 business logic이 없음을 확인했다.

기존 v0.4 및 v0.4.1 adversarial script도 v0.4.2에서 다시 실행해 모두 PASS했다.

---

## 16. 기존 regression 결과

최종 working tree 검증:

```text
v0.3.2 regression subset:      176 passed
v0.4 regression subset:         50 passed
v0.4.1 regression subset:       26 passed
v0.4.2 new regression:          29 passed
-----------------------------------------
full suite:                    281 passed, 0 failed
```

추가 targeted:

```text
Kakao/consecutive targeted:      11 passed
ranking/application-state:        8 passed
user-fact supersession:           4 passed
REST/MCP targeted:               10 passed
```

기타:

```text
compileall:                       PASS
source import/version 0.4.2:      PASS
schema export:                    34 schemas
wheel build:                      PASS
clean wheel install/import:       PASS
installed service smoke:          PASS
v0.4 adversarial:                 PASS
v0.4.1 adversarial:               PASS
v0.4.2 adversarial:               PASS
```

### Kakao rule regression

최신 semantics 유지:

```text
7주 연속 AUTO_TRANSFER SUCCESS → +1.0%p
26주 연속 AUTO_TRANSFER SUCCESS → 추가 +2.0%p
7주 from_sequence = None
```

따라서:

```text
1 SUCCESS
2 FAILED
3~9 SUCCESS
→ 3~9의 7연속 인정
```

Manual fill은 AUTO_TRANSFER SUCCESS를 복구하지 않는다.

v0.4.1에서 분리한 7주/26주 future intent도 그대로 유지했다. 26주 전체 시도를 거절해도 7주 가능성을 자동으로 닫지 않는다.

### Ranking unit regression

v0.4.1의 핵심 KRW/% separation도 그대로 유지한다.

```text
MAX_ESTIMATED_AFTER_TAX_INTEREST
→ KRW ↔ KRW only
→ unresolved contribution input에 realizable_rate를 KRW substitute로 사용하지 않음
```

---

## 17. Known limitations

1. 실제 MyData API와 은행 API는 연결하지 않는다.
2. Production DB/session persistence는 구현하지 않는다. 현재 ApplicationService state는 in-memory MVP state다.
3. 실제 금융상품 fixture는 4개이며 Top 5 boundary coverage는 synthetic fixture를 병행한다.
4. ProductFeature는 수동 curated search index다. 자동 Rule→Feature extraction framework는 이번 범위가 아니다.
5. 신한 급여우대의 모든 공식 ActionPath는 아직 완전 모델링하지 않는다. 미평가 대체경로는 기존 보수적 `UNKNOWN` 정책을 유지한다.
6. legacy direct-evaluator compatibility를 위해 `ContributionPlan.preferred_start_amount / incremental_amount` field 자체는 남아 있다. 다만 Web/ApplicationService의 product ranking-input flow는 이를 수정하지 않으며 product-scoped state를 사용한다.
7. Product-specific contribution choice의 별도 “revision endpoint”는 추가하지 않았다. 현재 Sprint가 요구한 question answer state isolation과 reranking persistence는 지원한다. Web에서 전체 검색 조건을 바꾸는 것은 기존 IntentPatch flow를 사용한다.
8. REST adapter는 framework-neutral contract이며 실제 FastAPI/Flask production server가 아니다.
9. MCP adapter는 SDK 없는 thin adapter다.
10. 완전한 Net Benefit Engine, 행동 성공확률, 질문 피로 최적화는 범위 밖이다.
11. 예상이자는 현재 ContributionPolicy와 deterministic approximation을 사용하며 은행 실제 일수·절사 규칙과 소액 차이가 날 수 있다.
12. RankingInput의 Top-K relevance pruning은 기존 frontier semantics를 유지하며, 이번 Sprint에서는 새로운 uncertainty/value-of-information engine을 만들지 않았다.

---

## 18. Accepted AI risks

### A. 최초 자연어 → Structured Intent

사용자의 첫 자연어를 LLM이 다음 schema로 해석할 때 의미를 잘못 이해할 가능성은 MVP에서 완전히 제거하지 않는다.

```text
HardConstraint
Preference
Capability
NumericPreference
```

유지한 안전장치:

```text
schema validation
enum validation
Quick Input > LLM inferred value
deterministic IntentConflict validation
raw utterance ↔ parsed intent Audit
```

그 이후:

```text
Eligibility
Rate
Interest
Ranking
```

은 deterministic code가 수행한다.

### B. Result Explanation 자유 prose

LLM은 structured reason codes / claims / rule status를 근거로 자연스럽게 설명하지만, prose 자체의 모든 의미를 완벽히 검증하는 NLP engine은 만들지 않는다.

안전 경계:

```text
Structured DTO → UI Source of Truth
Structured DTO → LLM → auxiliary prose
```

LLM prose는 Eligibility / Rate / Interest / Ranking 또는 UI structured reason을 변경하지 못한다.

---

## 19. 최종 READY 판정

# `READY_FOR_WEB_UI`

Acceptance 확인:

```text
[PASS] 기존 regression 전부 PASS
[PASS] product-specific contribution input이 global plan을 오염하지 않음
[PASS] 여러 상품의 contribution choices가 독립적으로 유지됨
[PASS] product choice가 rerank 후 유지됨
[PASS] product-scoped question identity
[PASS] 실제 4개 fixture의 typed search feature wiring
[PASS] preferential feature와 mandatory feature 구분
[PASS] Intent Update가 이전 user-declared fact를 supersede
[PASS] true/false active conflict 제거
[PASS] authoritative fact 보호
[PASS] MAX_REALIZABLE_RATE에서 interest-only contribution question 억제
[PASS] Eligibility에 필요한 사용자 질문은 objective와 무관하게 유지
[PASS] NumericPreference lower/upper range patch 보존
[PASS] Structured backend DTO가 List/Detail 금융 사실 Source of Truth
[PASS] LLM prose가 금융판정 source로 재사용되지 않음
[PASS] KRW/% ranking separation 유지
[PASS] Top 5 flow 정상
[PASS] Kakao 7-week from_sequence=None 유지
[PASS] Manual fill non-recovery 유지
[PASS] REST question→answer→recommendation flow
[PASS] MCP thin adapter 동일 ApplicationService 재사용
[PASS] Audit chain 유지
[PASS] wheel build / clean install / service smoke
```

v0.4.2는 새로운 기능 확장 Sprint가 아니라, Web UI가 바로 소비할 Application state의 경계를 최종적으로 닫는 patch로 구현됐다.
