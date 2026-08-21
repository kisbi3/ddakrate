# Financial Eligibility Engine v0.4.4 Implementation Report

**Sprint:** Conversational State Revision & Final Web Handoff Closure  
**Baseline:** `financial-eligibility-engine-v0.4.3`  
**Result:** `financial-eligibility-engine-v0.4.4`  
**Final verdict:** `READY_FOR_WEB_UI`

---

## 1. v0.4.3 baseline 결과

수정 전에 현재 v0.4.3 working tree에서 직접 재검증했다. 과거 보고서의 숫자를 복사하지 않았다.

```text
full pytest:                    305 tests / PASS
compileall:                     PASS
source import:                  PASS
package version:                0.4.3
wheel:                          existing 0.4.3 wheel verified
clean wheel install/import:     PASS
v0.4 adversarial:               PASS
v0.4.1 adversarial:             PASS
v0.4.2 adversarial:             PASS
v0.4.3 adversarial:             PASS
```

Clean install에서는 `eligibility.__version__ == 0.4.3`, installed distribution version `0.4.3`, `ApplicationService` import를 다시 확인했다.

---

## 2. 수정 파일 목록

핵심 source:

- `src/eligibility/application_service.py`
- `src/eligibility/conversation.py` **신규**
- `src/eligibility/schema/conversation.py` **신규**
- `src/eligibility/schema/search.py`
- `src/eligibility/search/evaluation.py`
- `src/eligibility/search/contribution.py`
- `src/eligibility/llm/models.py`
- `src/eligibility/llm/profiles.py`
- `src/eligibility/audit/models.py`
- `src/eligibility/adapters/rest.py`
- `src/eligibility/adapters/mcp.py`
- `src/eligibility/__init__.py`
- `src/eligibility/schema/__init__.py`

검증/계약:

- `tests/test_v044_conversational_revision.py` **신규**
- `scripts/run_v044_adversarial.py` **신규**
- `scripts/export_schemas.py`
- `schemas/*.v0.4.4.schema.json` 39개
- `README.md`
- `pyproject.toml`

기존 deterministic Core, Rule DSL, RankingService, Interest Engine을 다시 설계하지 않았다.

---

## 3. Conversational revision architecture

v0.4.4는 사용자에게 범용 patch language를 요구하지 않는다. 사용자는 평범한 자연어를 입력한다.

```text
natural-language follow-up
↓
ConversationOrchestrator
  - current SearchSession/context를 LLM에 제공
  - 사용자의 의미와 수정 대상을 해석
  - 좁은 domain operation을 선택
↓
ApplicationService
  - deterministic validation
  - active/superseded state 관리
  - dependency re-evaluation
  - Question Planner / Ranking 재실행
↓
next question 또는 refreshed Top 5
```

내부적으로 LLM structured output은 다음과 같은 **기존 domain operation 선택 계약**만 사용한다.

```text
UPDATE_SEARCH_INTENT
REVISE_PRODUCT_CONTRIBUTION_CHOICE
REVISE_USER_ANSWER
REVISE_USER_DECLARED_FACT
SUBMIT_ACTIVE_QUESTION_ANSWER
EXCLUDE_PRODUCT
NO_OP
```

이는 사용자-facing `UserStatePatch` DSL이 아니다. 사용자는 operation 이름, semantic key, scope 등을 알거나 입력할 필요가 없다.

---

## 4. LLM natural-language orchestration 방식

새 `LLMPurpose.CONVERSATION_ORCHESTRATION`과 `ConversationOrchestrator`를 추가했다.

LLM에 제공하는 context는 최소한 다음을 포함한다.

- current `SearchSession`
- current `ProductSearchIntent`
- active question / clarification
- active `ProductContributionChoice`
- candidate product ID/name
- active fact summary + `user_mutable` 여부
- answer history
- 허용된 ApplicationService operation 이름

LLM prompt는 명시적으로 다음을 금지한다.

- affordability 계산
- cashflow 계산
- eligibility/status 결정
- 금리/이자 계산
- Top 5 계산
- authoritative evidence 수정

예를 들어 stale-choice clarification이 `1,000 / 2,000 / 3,000`을 제공한 상태에서 사용자가 `“제일 큰 걸로.”`라고 하면 LLM은 context의 allowed option 중 `3,000`을 선택하여 `REVISE_PRODUCT_CONTRIBUTION_CHOICE`를 호출할 수 있다. **가능한 option 자체는 LLM이 계산하지 않는다.**

여러 수정이 한 문장에 있으면 여러 action을 생성할 수 있다. ApplicationService는 dependency를 고려하여 global intent/contribution 변경을 product-choice revision보다 먼저 적용한다.

---

## 5. ProductContributionChoice revision

v0.4.3에서 deferred였던 정상 commit 이후 상품별 납입 선택 revision을 구현했다.

`ProductContributionChoice`에 최소 revision metadata를 추가했다.

```text
version
record_status {ACTIVE, SUPERSEDED}
supersedes_choice_id
```

ApplicationService:

```text
revise_product_contribution_choice(...)
```

semantics:

```text
active old choice
→ 현재 global plan + product policy + subscription date 기준 새 값 검증
→ 성공 시 old = SUPERSEDED
→ new = ACTIVE, version + 1
→ 해당 product cashflow / affordability / interest 재평가
→ Ranking 재계산
```

이전 값은 삭제하지 않고 `product_contribution_choice_history`에 유지한다. Engine/Ranking에는 active choice만 전달한다.

직접 regression에서 Kakao `3,000 → 2,000` 수정 후 총 원금/세후이자가 감소했고, 경쟁 상품과의 실제 ranking order가 바뀌는 것까지 검증했다.

---

## 6. stale choice revalidation

Global ContributionPlan이 바뀌면 기존 active product-specific choice를 현재 조건으로 다시 검증한다.

대표 flow:

```text
Global monthly max = 1,000,000
Kakao start = 10,000
↓
Global monthly max → 300,000
↓
기존 Kakao 10,000 schedule 재검증
↓
monthly affordability 위반
↓
현재 조건에서 같은 field의 feasible replacement option 재계산
→ 1,000 / 2,000 / 3,000
```

기존 choice가 infeasible해졌다고 상품을 즉시 제거하지 않는다. `ContributionFeasibilityClarification`에 다음을 추가했다.

- stale `field`
- `current_choice_id`
- `current_choice_value`
- `feasible_options`
- `CHANGE_PRODUCT_CONTRIBUTION_CHOICE`
- 기존 `ADJUST_GLOBAL_AFFORDABILITY`
- 기존 `EXCLUDE_PRODUCT`

모든 replacement option도 기존 v0.4.3의 실제 dated cashflow + calendar affordability evaluator로 계산한다.

### 요청 시나리오의 50만원 → Kakao 1만원에 대한 correctness 주석

프롬프트의 첫 acceptance 예시는 `월 최대 50만원` 상태에서 Kakao `10,000원 시작`을 먼저 commit하는 흐름을 제시한다. 그러나 v0.4.3에서 이미 고정된 실제 calendar cashflow semantics에 따르면 이 선택의 최대 calendar-month 출금액은 약 **900,000원**이다. 따라서 `월 최대 500,000원`에서는 10,000원 선택이 처음부터 deterministic하게 reject되어야 한다.

v0.4.4는 이 선행 P0 correctness를 약화시키지 않았다. stale-choice end-to-end 검증은 동일 의미를 유지하면서 실제로 valid한 초기 상태인 `월 최대 1,000,000원 → Kakao 10,000원 → 월 최대 300,000원`으로 수행했다.

---

## 7. dependency invalidation / re-evaluation

상태 변경별 최소 영향 범위는 다음처럼 유지했다.

| 변경 | deterministic 후속 처리 |
|---|---|
| Global ContributionPlan | 후보 재검색/평가 + 모든 active product choice affordability 재검증 |
| ProductContributionChoice | 해당 product cashflow / interest / evaluation 재계산 + rerank |
| Capability | 기존 intent capability materialization으로 관련 user-declared Fact supersession + reevaluation |
| Preference | preference score / ranking 갱신 |
| Hard Constraint | CandidateRetriever 재실행 |
| Ranking objective | question relevance + ranking 재계산 |

`DEPENDENCY_INVALIDATED` Audit event로 product-choice revision의 cashflow/affordability/interest/ranking invalidation을 남긴다.

정확성을 우선하여 기존 `_run_pipeline()`을 필요한 경우 재사용했으며, 별도의 거대한 dependency graph subsystem은 만들지 않았다.

---

## 8. question reopening

과거에 답했다는 사실만으로 질문을 영구 skip하지 않는다.

v0.4.4에서는 특히 **global contribution dependency가 바뀌어 기존 ProductContributionChoice가 stale해진 경우** 현재 feasible replacement options를 가진 새 `CONTRIBUTION_FEASIBILITY` question을 생성한다.

```text
old start-amount question = answered
↓
global affordability changed
↓
old answer no longer valid
↓
new stale-choice clarification/question
↓
QUESTION_REOPENED audit
```

기존 answered question ID는 과거 이력으로 남는다. 새 질문은 현재 active state를 기준으로 별도 material question으로 생성되므로 `answered_question_ids` 때문에 차단되지 않는다.

반대로 기존 choice가 새 조건에서도 여전히 feasible하면 불필요하게 재질문하지 않는다.

---

## 9. user-declared state supersession

자연어 follow-up은 기존 v0.4.2의 supersession infrastructure를 그대로 재사용한다.

예:

```text
“급여계좌 바꿀 수 있어요.”
→ USER_DECLARED true ACTIVE

“급여계좌는 역시 못 바꿀 것 같아.”
→ old true SUPERSEDED
→ new false ACTIVE
```

`ConversationOrchestrator`가 `UPDATE_SEARCH_INTENT`를 선택하면 `ProductSearchIntent.capabilities`가 patch되고, ApplicationService의 capability materialization이 동일 semantic fact의 이전 `USER_DECLARED` 값을 supersede한다.

Preference, NumericPreference, HardConstraint, RankingObjective, Global ContributionPlan도 기존 `IntentPatch` semantics를 재사용한다.

---

## 10. authoritative Fact protection

사용자 자연어 revision은 authoritative evidence를 삭제하거나 덮어쓰지 못한다.

보호 대상:

- `OBSERVED_FACT`
- `OBSERVED_EVENT`
- `INSTITUTION_VERIFIED`
- `MYDATA_VERIFIED`
- 기타 user-declared가 아닌 authoritative source

`revise_user_declared_fact()`는 동일 fact type에 mutable `USER_DECLARED` fact가 없고 authoritative active fact만 존재하면:

```text
AUTHORITATIVE_FACT_IMMUTABLE_BY_USER
```

으로 reject하고 `AUTHORITATIVE_REVISION_REJECTED` Audit event를 남긴다.

full dispute-resolution workflow는 이번 범위에 포함하지 않았다.

---

## 11. invalid revision atomicity

### Product choice revision

새 값을 먼저 tentative하게 만들고 다음을 모두 통과한 뒤에만 commit한다.

```text
value/type
→ currently feasible option set
→ Product ContributionPolicy
→ actual dated cashflow
→ current global affordability
→ evaluation sanity
→ commit
```

예: 현재 allowed replacement가 `1,000 / 2,000 / 3,000`인데 `7,000`으로 수정하려고 하면 old active choice를 유지하고 아무 business state도 변경하지 않는다.

### Multi-action conversational turn

한 자연어 발화가 여러 action으로 해석된 경우 `handle_user_message()`는 business-state snapshot을 만든 뒤 실행한다. 중간 action 하나라도 실패하면:

- `SearchSession`
- active intent
- fact store
- active/history product choices
- active question / question history
- evaluations
- ranking/recommendation

을 turn 시작 상태로 복원한다.

Audit는 append-only이므로 rejected attempt 기록은 남길 수 있고, 최종적으로 `CONVERSATION_TURN_ROLLED_BACK`을 기록하여 해당 turn이 business state에 commit되지 않았음을 명시한다.

---

## 12. REST conversational flow

새 thin endpoint:

```text
POST /search-sessions/{id}/messages
```

request 예:

```json
{
  "message": "카카오는 아까 1만원이라고 했는데 3천원으로 바꿔줘."
}
```

REST Adapter는 해석/금융판정을 구현하지 않는다.

```text
REST
→ ApplicationService.handle_user_message()
→ ConversationOrchestrator + deterministic domain operations
→ ConversationTurnResult
```

응답은 structured하게:

- 실행된 internal operation 목록
- 최신 SearchSession
- next question (있을 경우)
- refreshed recommendations (바로 확정 가능한 경우)

을 제공한다.

Web UI는 structured DTO의 금리/이자/status/ranking을 직접 렌더링하며 LLM prose를 금융 Source of Truth로 사용하지 않는다.

---

## 13. MCP 변경 여부

MCP는 계속 thin adapter다.

추가 노출:

```text
handle_user_message(...)
revise_product_contribution_choice(...)
```

두 operation 모두 같은 `ApplicationService`를 호출한다. MCP 내부에는 금융판정, state supersession, affordability, ranking logic을 복제하지 않았다.

---

## 14. Audit 확장

v0.4.4 추가/활용 event:

```text
USER_STATE_REVISION_REQUESTED
PRODUCT_CONTRIBUTION_CHOICE_SUPERSEDED
DEPENDENCY_INVALIDATED
QUESTION_REOPENED
AUTHORITATIVE_REVISION_REJECTED
CONVERSATION_TURN_ROLLED_BACK
```

기존 v0.4.3 event도 유지한다.

- `CONTRIBUTION_ANSWER_REJECTED`
- `PRODUCT_EXCLUDED_BY_USER`
- `GLOBAL_AFFORDABILITY_UPDATED`
- `RANKING_CALCULATED`
- 기타 Search/Question/Evaluation event

Audit context는 기존 `search_session_id / intent_version / ranking_run_id / question_id / evaluation_id` chain을 유지한다. 사용자 자연어는 전체 원문을 무조건 저장하지 않고 conversation request에서 hash를 남긴다.

---

## 15. 신규 regression tests

새 파일:

```text
tests/test_v044_conversational_revision.py
```

총 **27 tests**.

### ProductContributionChoice revision

```text
test_product_contribution_choice_can_be_revised
test_revised_choice_supersedes_old_choice
test_revised_choice_recalculates_interest
test_revised_choice_reranks_product
test_invalid_revision_is_atomic
```

### Global constraint → stale choice

```text
test_global_affordability_change_revalidates_product_choices
test_stale_choice_generates_new_valid_options
test_stale_choice_can_be_revised_instead_of_excluding_product
test_valid_existing_choice_is_not_reasked
```

### Question reopening

```text
test_answered_question_can_reopen_after_dependency_change
test_irrelevant_old_question_does_not_reopen
```

### Authoritative protection

```text
test_user_cannot_supersede_observed_fact
test_user_cannot_supersede_institution_verified_fact
```

### Natural-language conversational revision

```text
test_natural_language_changes_global_contribution
test_natural_language_changes_desired_contribution_from_context
test_natural_language_changes_product_specific_choice
test_natural_language_changes_capability
test_natural_language_changes_supersol_capability
test_natural_language_changes_new_card_capability
test_natural_language_changes_preference
test_natural_language_changes_term_constraint
test_natural_language_changes_ranking_objective
test_conversational_combined_revision_revalidates_and_reranks
test_largest_feasible_option_natural_language_uses_current_clarification_context
```

### Atomicity / Adapters

```text
test_invalid_conversational_revision_rolls_back_business_state
test_rest_conversational_follow_up_endpoint
test_mcp_reuses_same_conversational_application_service
```

자연어 테스트에서는 사용자가 JSON patch를 입력하지 않는다. `MockLLMAdapter`가 LLM interpretation 결과만 deterministic하게 고정하여 LLM/router와 backend 금융판정 경계를 검증한다.

---

## 16. adversarial tests

새 script:

```text
scripts/run_v044_adversarial.py
```

직접 실행한 시나리오:

### A. Product choice revision

Kakao `3,000 → 2,000`; old choice SUPERSEDED, new ACTIVE, principal/interest 재계산 확인.

### B. stale choice + 자연어 “제일 큰 걸로”

```text
monthly max 1,000,000
Kakao 10,000
→ monthly max 300,000
→ current choice stale
→ feasible 1,000 / 2,000 / 3,000
→ user: “제일 큰 걸로.”
→ LLM routes 3,000
→ backend validate/commit
```

### C. 한 문장 다중 revision

```text
“생각해보니 월 30만원까지만 가능하고
급여계좌도 못 바꿀 것 같아.
카카오는 3천원으로 바꿔줘.”
```

Global max, salary capability, Kakao choice를 함께 수정하고 user-declared fact supersession + ranking recalculation 확인.

### D. Invalid natural revision

Kakao `7,000` revision을 backend가 reject하고 business snapshot equality + rollback audit 확인.

### E. Authoritative protection

Institution verified observed fact를 user revision으로 덮어쓰려는 시도 reject.

### F. REST / MCP

둘 다 동일 ApplicationService conversational operation을 재사용함을 확인.

기존 v0.4 / v0.4.1 / v0.4.2 / v0.4.3 adversarial scripts도 v0.4.4 working tree에서 다시 실행하여 모두 PASS했다.

---

## 17. existing regression 결과

최종 working tree에서 직접 실행한 결과:

```text
v0.3.2 regression subset:      176 tests / PASS
v0.4 regression subset:         50 tests / PASS
v0.4.1 regression subset:       26 tests / PASS
v0.4.2 regression subset:       29 tests / PASS
v0.4.3 regression subset:       24 tests / PASS
v0.4.4 new regression:          27 tests / PASS
-----------------------------------------------
full suite:                    332 tests / PASS
```

추가 targeted:

```text
Kakao/consecutive targeted:     12 tests / PASS
v0.4 adversarial:               PASS
v0.4.1 adversarial:             PASS
v0.4.2 adversarial:             PASS
v0.4.3 adversarial:             PASS
v0.4.4 adversarial:             PASS
compileall:                     PASS
source import/version:          0.4.4 / PASS
wheel build:                    PASS (--no-build-isolation)
clean wheel install/import:     0.4.4 / PASS
installed ApplicationService:   PASS
JSON Schema v0.4.4:             39 files
```

Build isolation 상태의 `pip wheel`은 실행 환경에서 외부 package index를 사용할 수 없어 setuptools dependency 조회가 실패할 수 있다. 따라서 기존 Sprint와 동일하게 `--no-build-isolation`을 사용해 현재 설치된 build backend로 wheel을 생성했고 clean target install/import를 검증했다.

---

## 18. Known limitations

1. **실제 외부 LLM provider 연동은 deployment concern이다.** 코드에는 LLM Gateway/Profile/ConversationOrchestrator 계약을 구현했고 tests/adversarial에서는 `MockLLMAdapter`로 interpretation을 고정했다.
2. 자연어 의미를 LLM이 잘못 해석할 수 있다. backend는 그 결과를 schema/domain validation하고 금융 계산은 직접 수행하지 못하게 한다.
3. authoritative fact에 대한 사용자의 이의제기를 처리하는 full dispute-resolution workflow는 없다. v0.4.4는 user override를 거절하는 것까지만 구현한다.
4. universal dependency graph/versioned question engine을 새로 만들지 않았다. 이번 Sprint에서 필요한 stale ProductContributionChoice 재검증과 질문 재오픈을 기존 Question Planner 위에 최소 구현했다.
5. in-memory ApplicationService 기준 transactional rollback이다. Production DB transaction/locking/idempotency는 범위 밖이다.
6. 실제 MyData/은행 API, Production DB, 실제 Web UI는 포함하지 않는다.
7. 신한 급여우대의 모든 공식 ActionPath는 여전히 완전 구현 범위 밖이다.
8. LLM result explanation은 자유 prose이며 semantic hallucination의 완벽한 NLP 검증은 하지 않는다. UI 숫자/status/reason은 structured DTO만 사용한다.

---

## 19. Accepted AI risks

### Initial / follow-up natural-language interpretation

사용자 자연어를 LLM이 다음 의미로 해석하는 과정에는 오해 가능성이 있다.

- HardConstraint / Preference / Capability / NumericPreference
- Global ContributionPlan
- ProductContributionChoice revision
- RankingObjective
- active clarification answer

이 위험 때문에 사용자에게 구조화 command를 요구하지 않는다. 대신 LLM에는 현재 SearchSession과 허용 domain operations를 제공하고 backend가 결과를 검증한다.

경계는 유지한다.

```text
사용자의 의미와 수정 의도
→ LLM 해석 가능

active/superseded state
→ ApplicationService

affordability / cashflow
→ deterministic code

eligibility / rate / interest
→ deterministic Core

Top 5
→ deterministic RankingService
```

### Result explanation

LLM prose는 structured backend result의 보조 설명이다. prose를 다시 parse해 eligibility/rate/interest/ranking을 변경하는 경로는 없다.

---

## 20. 최종 READY 판정

# `READY_FOR_WEB_UI`

판정 근거:

- 자연어 후속 발화로 기존 검색조건/사용자 선언/상품별 납입 선택 수정 가능
- user-facing structured command 불필요
- ProductContributionChoice revision + supersession history 구현
- Global 조건 변경 후 stale product choice 재검증
- feasible replacement option 제시 후 자연어 재선택 가능
- 필요한 stale question 재오픈 가능
- invalid direct/conversational revision atomic
- 최신 user-declared state 하나만 effective
- authoritative evidence 보호
- revision 후 deterministic evaluation / interest / Top 5 재계산
- REST natural-language follow-up endpoint 구현
- MCP thin adapter 유지
- UI 숫자/status/ranking은 structured DTO Source of Truth
- LLM은 meaning/router와 prose만 담당
- 전체 332 tests PASS
- 기존 v0.4~v0.4.3 adversarial PASS
- Kakao late 7-streak / `from_sequence=None` / manual-fill regression PASS
- wheel clean install/import PASS
- Audit chain 유지

v0.4.4는 Web UI 구현 직전 backend 기준선으로 사용할 수 있다.
