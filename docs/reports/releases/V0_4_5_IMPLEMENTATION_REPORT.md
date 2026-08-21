# Financial Eligibility Engine v0.4.5 Implementation Report

**Sprint:** Conversational Search State Generalization  
**Baseline:** `financial-eligibility-engine-v0.4.4`  
**Result:** `financial-eligibility-engine-v0.4.5`  
**Final verdict:** `READY_FOR_WEB_UI_IMPLEMENTATION`

---

## 1. v0.4.4 baseline

수정 전에 `financial-eligibility-engine-v0.4.4` working tree에서 직접 baseline을 다시 실행했다. 과거 Implementation Report의 숫자를 복사하지 않았다.

```text
full pytest:                    332 tests / PASS
compileall:                     PASS
source import/version:          PASS / 0.4.4
wheel build:                    PASS
clean wheel install/import:     PASS
v0.4 adversarial:               PASS
v0.4.1 adversarial:             PASS
v0.4.2 adversarial:             PASS
v0.4.3 adversarial:             PASS
v0.4.4 adversarial:             PASS
```

이번 환경에는 `python -m build`가 설치되어 있지 않아, repository가 기존부터 사용하던 방식과 동일하게 다음 명령으로 wheel build sanity를 수행했다.

```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

baseline은 정상으로 확인된 뒤에만 v0.4.5 수정에 들어갔다.

---

## 2. 수정 파일

핵심 source 변경:

- `src/eligibility/application_service.py`
- `src/eligibility/conversation.py`
- `src/eligibility/schema/conversation.py`
- `src/eligibility/search/questions.py`
- `src/eligibility/audit/models.py`
- `src/eligibility/adapters/mcp.py`
- `src/eligibility/__init__.py`

검증/계약/문서 변경:

- `tests/test_v045_conversational_state_generalization.py` **신규**
- `scripts/run_v045_adversarial.py` **신규**
- `scripts/run_v031_adversarial.py` — 현재 per-Fact evidence semantics에 맞춘 legacy compatibility assertion 보정
- `scripts/export_schemas.py`
- `schemas/*.v0.4.5.schema.json` — 39개 export
- `README.md`
- `pyproject.toml`
- `examples/v0.4.5/adversarial-results.json`

기존 deterministic Core, Rule DSL, Interest Engine, Rate Engine, RankingService를 다시 설계하지 않았다.

---

## 3. Mutable Search State의 개념적 경계

v0.4.5의 generalization은 새로운 거대한 universal state object나 JSON Patch DSL을 만든 것이 아니다. 기존 모델을 그대로 유지하면서 **사용자가 자연어로 바꿀 수 있는 현재 검색 상태**라는 공통 semantics를 Application Layer에 부여했다.

Mutable Search State에 포함되는 의미는 다음과 같다.

```text
Search Intent
- Hard Constraint
- Preference
- Numeric Preference
- Ranking Objective
- 기간/상품유형 조건

User-declared State
- Capability
- FUTURE_INTENT
- SELF_REPORTED_FACT

Contribution State
- Global ContributionPlan
- ProductContributionChoice

Search Selection State
- excluded product 여부
```

핵심 규칙은 단순하다.

```text
사용자의 최신 명시적 의미
→ 현재 effective mutable state

과거 값
→ Audit history
```

사용자는 `IntentPatch`, `semantic_key`, `REVISE`, `UPSERT`, `restore product` 같은 backend 용어를 알 필요가 없다.

---

## 4. 기존 state/revision 코드 재사용 방식

v0.4.5는 기존 v0.4.x closure를 폐기하지 않고 통합했다.

재사용한 기존 primitive:

- `update_search_intent(...)`
- 기존 `IntentPatch` merge semantics
- user-declared Fact supersession
- `revise_user_answer(...)`
- `ProductContributionChoice` version/supersession history
- cross-frequency affordability / actual dated cashflow validation
- candidate exclusion state
- `QuestionPlanner`
- `_run_pipeline()` multi-product deterministic search
- 기존 REST `/search-sessions/{id}/messages`
- 기존 MCP thin adapter

새로 추가한 domain-oriented operation은 edge-case workflow가 아니라 **현재 값을 직접 설정하는 의미**로 최소화했다.

```text
SET_PRODUCT_CONTRIBUTION_CHOICE
SET_PRODUCT_EXCLUSION
```

따라서 다음 세 경우는 하나의 semantics로 처리된다.

```text
과거 질문에 답하지 않았지만 지금 값을 말함
기존 상품별 선택을 새 값으로 바꿈
과거 제외했던 상품을 다시 포함함
```

별도의 `reopen declined input`, `restore product`, `recover stale question` 사용자 workflow를 만들지 않았다.

---

## 5. LLM conversational orchestration

사용자-facing canonical flow는 다음과 같다.

```text
사용자 자연어
↓
ConversationOrchestrator / LLM
- 현재 SearchSession 이해
- 현재 Mutable Search State 이해
- active question / clarification 이해
- 최근 product focus와 Top 5 context 이해
- 필요한 domain operation 선택
↓
ApplicationService typed operation
↓
deterministic validation
↓
full re-search
↓
next question 또는 refreshed Top 5
```

LLM이 사용할 수 있는 주요 operation은 다음과 같다.

```text
UPDATE_SEARCH_INTENT
SET_PRODUCT_CONTRIBUTION_CHOICE
SET_PRODUCT_EXCLUSION
REVISE_USER_DECLARED_FACT
SUBMIT_ACTIVE_QUESTION_ANSWER
```

v0.4.4의 기존 operation은 backward compatibility를 위해 삭제하지 않았다.

LLM context는 현재 상태 중심으로 정리했다.

```text
MUTABLE_SEARCH_STATE
AUTHORITATIVE_FACT_SUMMARY
ACTIVE_USER_DECLARATIONS
ACTIVE_QUESTION
RECENT_PRODUCT_FOCUS
TOP_K_SUMMARY
PRODUCT_CATALOG_SUMMARY
WORKFLOW_HISTORY_SUMMARY
ALLOWED_APPLICATION_OPERATIONS
```

중요하게도 LLM은 다음을 계산하지 않는다.

- cashflow
- affordability
- Eligibility status
- rate
- interest
- candidate removal correctness
- Top 5 ranking

예를 들어 현재 backend가 허용 옵션을 `1000 / 2000 / 3000`으로 제공한 상황에서 사용자가 `“제일 큰 걸로.”`라고 하면 LLM은 **현재 허용 option 중 3000을 의미하는 것으로 context resolution**할 수 있다. 가능한 option 자체는 deterministic backend가 계산한다.

외부 실제 LLM call은 pytest 안정성의 전제가 아니다. `MockLLMAdapter`/Fake LLM을 사용하되 테스트 입력은 실제 사용자 자연어 문장으로 시작하도록 했다.

---

## 6. 최신 state semantics

사용자 선언은 최신 명시값 하나만 현재 금융 계산에 사용한다.

예:

```text
salary account change
CAN
→ CANNOT
→ CAN
```

최종 effective state:

```text
CAN
```

이전 `CAN → CANNOT`은 superseded history로 남는다.

`ProductContributionChoice`도 동일하다.

```text
Kakao start = 10,000
→ 3,000
→ 2,000
```

현재 cashflow와 interest 계산에는 `2,000` 하나만 사용하며 과거 선택은 Audit/history에 남긴다.

상품 제외도 현재 bool state로 취급한다.

```text
“카카오는 빼줘.”
→ excluded = true

“카카오도 다시 보여줘.”
→ excluded = false
```

과거 exclusion event 자체가 재포함을 차단하지 않는다.

과거 ranking-input decline 역시 현재 explicit value보다 우선하지 않는다.

```text
과거: “지금 답 안 할래.”
현재: “카카오는 3천원으로 할게.”
→ current Kakao start = 3000
```

별도 reopen operation을 사용자에게 요구하지 않는다.

---

## 7. Authoritative Fact 경계

Mutable Search State와 authoritative financial evidence를 명확히 분리했다.

사용자가 바꿀 수 있는 것:

```text
USER_DECLARED
SELF_REPORTED_FACT
FUTURE_INTENT
Capability
Preference
ContributionPlan
ProductContributionChoice
product exclusion
```

사용자 자연어만으로 덮어쓸 수 없는 것:

```text
OBSERVED_FACT
OBSERVED_EVENT
INSTITUTION_VERIFIED
MYDATA_VERIFIED
authoritative DERIVED_FACT
```

`revise_user_declared_fact(...)`는 evidence authority를 확인하고 authoritative Fact 수정 시도를 reject한다.

Conversation LLM context도 두 영역을 분리한다.

```text
MUTABLE_SEARCH_STATE
AUTHORITATIVE_FACT_SUMMARY
```

즉 LLM에게 authoritative financial fact를 사용자 말만으로 수정하는 tool path를 제공하지 않는다. 별도 dispute-resolution system은 이번 MVP 범위 밖이다.

---

## 8. Question Planner current-state semantics

v0.4.x에서 존재해 온 `answered_question_ids`, declined ranking input 등은 history/compatibility 목적으로 유지한다. 하지만 **과거 workflow state만으로 현재 질문을 영구 suppress하지 않는다.**

Question Planner의 기준을 다음으로 고정했다.

> 현재 Mutable Search State + Authoritative Facts + 현재 CandidateEvaluation에서 지금 이 정보가 unresolved하고 material한가?

구체적으로:

- 현재 `UNRESOLVED` RankingInput이면 과거 동일 question ID가 answered history에 있어도 다시 material할 수 있다.
- 현재 `DECLINED` 상태이면 사용자가 새 값을 명시하기 전까지 자동 질문 폭탄을 만들지 않는다.
- 사용자가 자연어로 explicit value를 제공하면 해당 declined state를 clear하고 현재 값으로 설정한다.
- 기존 contribution choice가 현재 state에서도 valid하면 다시 묻지 않는다.
- global constraint 변경 등으로 answer가 더 이상 valid하지 않으면 현재 evaluator state에서 새 질문을 만들 수 있다.

따라서 질문 여부는 `“전에 질문했는가?”`가 아니라 `“지금 필요한가?”`에서 파생된다.

---

## 9. full re-search 정책

v0.4.5에서는 state generalization을 위해 복잡한 dependency optimization을 추가하지 않았다.

한 conversational turn에서 의미 있는 mutable state update가 성공하면 최종적으로 `_run_pipeline()`을 다시 실행한다.

```text
Mutable Search State update
↓
validation
↓
Candidate Retrieval
↓
FinancialEligibilityEngine × candidates
↓
Interest calculation
↓
Question materiality refresh
↓
Ranking
↓
Top 5
```

기존 operation 내부의 최소 영향 재평가 로직은 재사용할 수 있지만, conversational turn의 최종 correctness는 **full re-search**로 닫는다.

현재 MVP 상품 수에서는 이 방식이 dependency graph를 과도하게 복잡하게 만드는 것보다 안전하고 설명 가능하다. 향후 실제 성능 병목이 관측되면 incremental recomputation을 별도 최적화할 수 있다.

---

## 10. transactionality

v0.4.4의 multi-action rollback semantics를 유지하고 generalization에도 적용했다.

예:

```text
현재 월 최대 300,000
Kakao start = 3,000

사용자:
“월 최대는 20만원으로 줄이고, 카카오는 7천원으로 바꿔줘.”
```

`7,000`이 허용되지 않는 option이면 **앞의 20만원 변경만 partial commit하지 않는다.**

처리:

```text
turn 시작 business-state snapshot
↓
LLM interpreted actions
↓
전체 candidate update 적용/검증
↓
한 action이라도 실패
→ full business-state rollback
→ CONVERSATION_TURN_ROLLED_BACK audit
```

rollback 대상에는 최소 다음이 포함된다.

- SearchSession
- ProductSearchIntent
- UserFact Store
- ProductContributionChoice/history active state
- excluded products
- active question
- CandidateEvaluation / ranking state

Audit failure event는 append-only로 남길 수 있지만 금융/검색 business state는 변경되지 않는다.

---

## 11. REST conversational entrypoint

Web UI의 canonical natural-language entrypoint는 기존 v0.4.4 endpoint를 그대로 유지한다.

```text
POST /search-sessions/{id}/messages
```

예:

```json
{
  "message": "아까 카카오 뺀 건 취소하고 다시 같이 보여줘."
}
```

REST adapter는 business logic을 복제하지 않는다.

```text
REST
→ ApplicationService.handle_user_message(...)
→ ConversationOrchestrator
→ typed ApplicationService operation
→ deterministic full re-search
```

응답은 현재 state에 따라 기존 contract 안에서 next question / clarification / refreshed recommendations를 제공한다.

기존 structured API (`update_search_intent`, `submit_user_answer`, `revise_product_contribution_choice` 등)는 Quick UI, 테스트, 향후 form 기반 frontend를 위해 삭제하지 않았다.

---

## 12. MCP 영향

MCP는 계속 thin adapter다.

```text
LLM / Agent
→ MCP Adapter
→ ApplicationService
→ Financial Eligibility Engine
```

새 business logic을 MCP에 넣지 않았다.

v0.4.5에서는 conversational generalization과 동일한 service primitive를 사용할 수 있도록 다음 typed forwarding operation을 보강했다.

- product contribution choice의 현재값 설정
- product exclusion의 현재 bool 설정
- 기존 `handle_user_message(...)` 재사용

따라서 Web과 MCP가 서로 다른 state semantics를 갖지 않는다.

---

## 13. Audit

v0.4.5에서는 자연어 turn → mutable state update → full re-search 흐름을 추적하기 위해 다음 Audit event를 보강했다.

```text
CONVERSATION_TURN_RECEIVED
MUTABLE_SEARCH_STATE_UPDATED
PRODUCT_CHOICE_UPDATED
PRODUCT_EXCLUSION_CHANGED
SEARCH_RECALCULATED
```

기존 event도 계속 사용한다.

- user declaration supersession
- product contribution supersession
- intent update
- question selected/reopened
- ranking calculated
- conversation rollback

Audit chain에는 가능한 범위에서 다음 ID를 연결한다.

```text
search_session_id
intent_version
product_id
question_id
ranking_run_id
evaluation_id
```

현재 금융 계산에는 최신 active mutable state만 사용하지만 과거 선언/선택은 append-only history로 보존한다.

---

## 14. 신규 regression tests

새 파일:

```text
tests/test_v045_conversational_state_generalization.py
```

신규 테스트 **17개**를 추가했고 모두 통과했다.

주요 coverage:

### Conversational Mutable State

- 자연어 product exclusion 복원
- 과거 decline 후 explicit product choice 설정
- 동일 conversational loop에서 ProductContributionChoice 반복 수정
- 한 자연어 turn에서 복수 mutable state 변경
- Capability `CAN → CANNOT → CAN` 최신 state semantics

### Current-state Question Semantics

- historical `answered_question_id`가 stale/material question을 막지 않음
- valid existing choice는 full re-search 후 다시 질문하지 않음
- old decline이 새 explicit value를 막지 않음

### Transactionality / Protection

- invalid multi-change turn 전체 rollback
- institution-verified / observed / MyData fact 보호

### Re-search / Web handoff

- state update 후 candidate retrieval 재실행
- ProductContributionChoice update 후 interest 재계산
- Top 5 reranking
- REST `/messages` flow
- MCP ApplicationService reuse
- mutable/authoritative context separation
- follow-up pronoun/current product context 제공

결과:

```text
17 passed
```

---

## 15. adversarial tests

신규 script:

```text
scripts/run_v045_adversarial.py
```

결과 파일:

```text
examples/v0.4.5/adversarial-results.json
```

직접 검증한 주요 시나리오:

1. 동일 capability `CAN → CANNOT → CAN`
2. product `exclude → include`
3. ranking input `decline → explicit value`
4. ProductContributionChoice `10,000 → 3,000 → 2,000`
5. 한 문장에서 4개 mutable state 동시 변경 + rerank
6. 한 action invalid 시 multi-change 전체 rollback
7. authoritative fact 수정 시도 reject
8. context-dependent follow-up / active product focus
9. `“제일 큰 걸로”`가 현재 backend allowed options 중 최대값으로 resolve
10. mutable state update → deterministic search recalculation Audit chain

모든 신규 adversarial scenario가 PASS했다.

또한 v0.4.5 working tree에서 기존 adversarial script 전체를 다시 실행했다.

```text
run_v031_adversarial PASS
run_v032_adversarial PASS
run_v04_adversarial  PASS
run_v041_adversarial PASS
run_v042_adversarial PASS
run_v043_adversarial PASS
run_v044_adversarial PASS
run_v045_adversarial PASS
```

`run_v031_adversarial.py`의 기존 한 assertion은 이미 폐기된 global `MVP_PROVISIONAL` trust behavior를 기대하고 있어 현재 v0.3.2+ per-Fact evidence semantics와 충돌했다. Engine behavior는 변경하지 않고 **legacy script expectation만 현재 semantics에 맞게 수정**했다. 즉 compatibility parameter가 self-reported evidence를 authoritative `SATISFIED`로 승격시키지 않는 것을 검증하도록 바꿨다.

---

## 16. 기존 regression 결과

최종 v0.4.5 working tree에서 직접 실행한 결과:

| Test set | Result |
|---|---:|
| v0.3.2 regression | **176 passed** |
| v0.4 acceptance | **50 passed** |
| v0.4.1 acceptance | **26 passed** |
| v0.4.2 acceptance | **29 passed** |
| v0.4.3 acceptance | **24 passed** |
| v0.4.4 acceptance | **27 passed** |
| v0.4.5 신규 | **17 passed** |
| 전체 pytest | **349 passed, 0 failed** |
| Kakao/consecutive targeted | **15 passed** |

추가 sanity:

```text
compileall:                       PASS
source eligibility.__version__:  0.4.5
wheel build:                      PASS
clean wheel install/import:       PASS
installed conversational smoke:   PASS
schema export:                    39 schemas / v0.4.5
```

카카오 최신 semantics도 유지했다.

```text
7-week condition: from_sequence = None
late 7-streak: PASS
manual fill does not restore AUTO_TRANSFER SUCCESS: PASS
```

KRW/% ranking 단위 분리, product-specific contribution isolation, cross-frequency affordability, calendar cashflow, invalid answer atomicity, UserFact supersession, NumericPreference range, REST/MCP, Audit 회귀도 전체 suite에서 유지된다.

---

## 17. Known limitations

v0.4.5에서 의도적으로 해결하지 않은 범위:

1. **실제 외부 LLM provider 품질/운영 안정성**  
   pytest는 Mock/Fake LLM으로 orchestration contract를 검증한다. 실제 provider/model 선택, latency, token cost, prompt tuning은 Web/Deployment 단계의 concern이다.

2. **Authoritative Fact dispute resolution 미구현**  
   사용자는 MyData/기관 확인 사실을 자연어로 덮어쓸 수 없다. 실제 데이터 오류를 이의제기하고 정정하는 별도 workflow는 MVP 범위 밖이다.

3. **Production DB transaction 미구현**  
   현재 in-memory ApplicationService에서 conversational multi-action rollback을 검증했다. Production DB 도입 시 동일 atomic semantics를 DB transaction으로 보존해야 한다.

4. **Full re-search 우선**  
   mutable state 변경 후 correctness를 위해 전체 candidate retrieval/evaluation/ranking을 재실행한다. 대규모 상품 universe에서의 incremental recomputation optimization은 후속 성능 작업이다.

5. **기존 workflow history field 유지**  
   `answered_question_ids`, declined ranking-input 상태, exclusion history 등은 regression/backward compatibility를 위해 남아 있다. v0.4.5의 핵심은 이를 삭제하는 것이 아니라 **새 explicit 사용자 의도보다 우선하지 않게 하는 것**이다.

6. 실제 MyData API, 실제 은행 API, Production DB, 전체 금융상품 수집, 완전한 Net Benefit Engine은 여전히 범위 밖이다.

---

## 18. Accepted AI risks

가장 중요한 Accepted AI Risk는 **자연어 의미 해석 오류**다.

LLM은 다음을 담당한다.

```text
사용자가 무엇을 바꾸려는가?
현재 대화에서 “그거”, “제일 큰 것”, “아까 카카오”가 무엇을 가리키는가?
어떤 ApplicationService operation을 호출해야 하는가?
```

따라서 사용자의 의도를 100% 정확하게 해석한다고 보장하지 않는다.

대신 안전 경계는 유지한다.

```text
LLM interpretation
→ typed operation/schema validation
→ deterministic mutable-state validation
→ authoritative evidence protection
→ contribution/cashflow/affordability validation
→ deterministic eligibility/rate/interest/ranking
```

LLM이 금융판정, 금리, 세후이자, Top 5를 직접 만들지 않는다.

UI의 숫자, 상태, recommendation reason, rate breakdown의 Source of Truth는 계속 structured backend DTO다. LLM 설명 prose를 다시 parsing하여 금융 계산에 사용하지 않는다.

---

## 19. 최종 READY 판정

# `READY_FOR_WEB_UI_IMPLEMENTATION`

다음 조건을 실제 코드와 테스트에서 확인했다.

- 사용자가 자연어만으로 검색조건을 변경 가능
- Capability를 자연어로 반복 수정하고 최신 값 하나만 effective
- ProductContributionChoice를 자연어로 반복 수정 가능
- 제외한 상품을 자연어로 다시 포함 가능
- 과거 답변 거절 후 나중 자연어로 값을 제공 가능
- 과거 workflow state가 새 explicit 사용자 의도를 차단하지 않음
- 현재 mutable state만 금융 계산에 사용
- authoritative evidence 보호
- state 변경 후 deterministic full re-search
- interest와 Top 5 재계산
- invalid multi-change 전체 atomic rollback
- Question Planner가 current state에서 질문 필요성을 재계산
- REST `/search-sessions/{id}/messages` 하나로 conversational loop 사용 가능
- MCP가 동일 ApplicationService를 재사용
- LLM은 자연어 의미 해석/operation 선택만 수행하고 금융판정·금리·이자·Ranking은 수행하지 않음
- 기존 전체 regression PASS
- 카카오 `from_sequence=None` 최신 semantics 유지

v0.4.5에서는 edge case마다 새 workflow state를 계속 늘리지 않고, 기존 v0.4.x 기능을 **“최신 mutable search state를 자연어로 갱신하고 정확하게 다시 검색한다”**는 하나의 conversational semantics 아래 묶었다.

따라서 이 버전을 backend feature closure 기준선으로 고정하고 다음 단계는 **실제 Web UI 구현 Sprint**로 전환하는 것이 적절하다.
