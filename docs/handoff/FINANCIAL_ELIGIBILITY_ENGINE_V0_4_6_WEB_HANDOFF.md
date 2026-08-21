# Financial Eligibility Engine v0.4.6 — Web Handoff

**Status:** `READY_FOR_WEB_UI_IMPLEMENTATION`  
**Purpose:** v0.4.5 → Web UI 사이의 최종 backend handoff delta. v0.4 전체 설계를 반복하지 않는다.

## 1. 최종 서비스 목표

사용자의 현재 금융상태, 실제 납입계획, 앞으로 수행 가능한 행동을 반영하여 **개인화 금융상품 Top 5**를 deterministic하게 계산한다.

```text
사용자 자연어 / Quick Input
→ LLM conversational orchestration
→ Mutable Search State
→ deterministic validation
→ Candidate Retrieval
→ FinancialEligibilityEngine × N
→ Interest
→ Ranking
→ Top 5
→ List / Detail structured DTO
```

### Catalog50 runtime delta

Web runtime은 `data/product_catalog/products/`의 50개 executable `ProductDefinition`을 generic loader로 읽는다. Candidate/Ranking/Question Planner에는 product-id 분기를 추가하지 않았으며, 기존 4개 golden fixture는 catalog representation과 semantic-equivalence test를 유지한다. Resolver/Institution Service Knowledge가 아직 연결되지 않은 조건은 false로 축약하지 않고 기존 `UNKNOWN` 경계를 따른다.

## 2. 자연어 conversational search loop

사용자는 Search State나 backend operation을 직접 조작하지 않는다. 사용자는 자연어로 말하고 LLM이 현재 검색 상태와 대화 맥락을 이해하여 적절한 Application Layer operation을 수행한다.

LLM은 의미 해석·operation 선택·질문/설명 wording을 담당한다. 가입 가능성, Rule status, cashflow, affordability, 금리, 세후이자, Ranking, Top 5는 deterministic backend가 계산한다.

## 3. Mutable Search State

사용자가 대화 중 수정 가능한 현재 상태:

- Search Intent: product type, Hard Constraint, Preference, Numeric Preference, 기간, Ranking Objective
- User-declared State: Capability, FUTURE_INTENT, SELF_REPORTED_FACT
- Contribution: Global `ContributionPlan`, Product-specific `ProductContributionChoice`
- Search Selection: session-local excluded products

최신 명시적 사용자 의미가 effective state다. 이전 값은 Audit/history에 남을 수 있지만 현재 금융계산에는 active state만 사용한다.

## 4. 미결정 / UNKNOWN으로 되돌리기

미결정은 별도 workflow가 아니다. 자연어 수정의 한 형태다.

```text
Capability CAN/CANNOT → UNKNOWN
Preference → NEUTRAL 또는 제거
Hard Constraint → 제거
Numeric Preference → 제거
FUTURE_INTENT / SELF_REPORTED_FACT → active user declaration clear
ProductContributionChoice → active choice clear
ContributionPlan field → remove_fields
```

`CLEAR_USER_DECLARED_FACT`, `CLEAR_PRODUCT_CONTRIBUTION_CHOICE`는 내부 primitive이며 사용자에게 노출하는 UX 개념이 아니다. Product choice를 clear하면 현재 state에서 다시 필요할 경우 Question Planner가 해당 입력을 다시 질문할 수 있다.

## 5. Authoritative Fact 경계

사용자 선언·선호·계획·상품별 선택은 수정 가능하지만, 다음 authoritative evidence는 사용자 자연어만으로 변경되지 않는다.

```text
OBSERVED_FACT
OBSERVED_EVENT
INSTITUTION_VERIFIED
MYDATA_VERIFIED
authoritative DERIVED_FACT
```

사용자가 authoritative evidence 삭제를 요청하면 backend가 거부한다. MVP에는 별도 dispute-resolution system이 없다.

## 6. “현재 결과 보여줘” semantics

`SHOW_CURRENT_RESULTS`는 **turn-local response intent**다. Persistent Search State가 아니다.

사용자가 “질문은 그만하고 지금 결과 보여줘”라고 하면:

- 현재 mutable state는 변경하지 않는다.
- 현재 active question은 session에 유지한다.
- 기존 deterministic Ranking/Recommendation을 즉시 반환한다.
- UNKNOWN을 `SATISFIED`/`ACHIEVABLE`로 승격하지 않는다.
- Top 5가 불안정하거나 material question이 남아 있으면 `unresolved_warning`을 반환할 수 있다.
- 이후 대화에서 질문/수정 flow를 정상 재개할 수 있다.

## 7. Question Planner는 current-state 기반

질문 필요 여부는 과거 workflow history가 아니라 **현재 CandidateEvaluation에서 unresolved인가**로 판단한다.

- 과거에 답했어도 active value를 clear하여 다시 필요해지면 재질문 가능
- 과거에 decline했어도 나중에 explicit value를 제공하면 바로 현재 state로 반영
- 현재 active state가 충분하면 old question history 때문에 다시 묻지 않음

`answered_question_ids`, decline 이력 등은 호환/Audit state일 수 있으나 business source of truth가 아니다.

## 8. Search Working Note

`Search Working Note`는 긴 대화와 context compaction을 위한 **ephemeral continuity document**다.

세 계층을 구분한다.

```text
Structured Application State / UserFact Store
= 금융계산 Source of Truth

Audit Event Log
= 재현 / 검증 / 디버깅 이력

Search Working Note
= LLM conversation continuity 보조
```

Working Note를 다시 parse하여 Eligibility/Rate/Interest/Ranking을 계산하지 않는다. 충돌 시 항상 structured backend state가 우선한다.

## 9. Working Note format

MVP note는 사람이 읽을 수 있는 Markdown으로 렌더링할 수 있다.

핵심 내용:

- current user goal
- current mutable search state
- product-specific choices
- excluded products
- unresolved material information
- current Top 5 summary / stability
- active question
- recent important raw user turns

전체 raw conversation을 무한 누적하지 않는다. current state는 compact summary로, 최근 중요 turn만 제한적으로 보존한다.

## 10. Working Note lifecycle / deletion

`SearchWorkingNoteStore` protocol:

```text
initialize
record_user_turn
update_summary
load
delete
```

구현:

- `InMemorySearchWorkingNoteStore`: 기본 MVP runtime
- `FileSearchWorkingNoteStore`: Markdown file + temporary file/atomic replace

Working Note는 search session close 시 삭제할 수 있다. Recommendation이 `COMPLETED`/`RANKING_READY`라는 이유만으로 즉시 삭제하지 않는다. 사용자는 결과 이후에도 대화를 이어갈 수 있기 때문이다.

`DELETE /search-sessions/{id}`는 session close + Working Note cleanup을 수행한다. Working Note 삭제는 Audit/UserFact/ProductDefinition 삭제를 의미하지 않는다.

## 11. Working Note와 Audit 차이

Audit는 append-only 재현 이력이다. Working Note는 현재 대화 continuity를 위한 요약 메모다.

Working Note write failure는 금융 state transaction의 single point of failure가 아니다. 실패는 Audit에 남기고, 다음 turn 전에 structured backend state로 summary를 재생성할 수 있다.

## 12. LLM context 구성

Conversation Orchestrator prompt는 중복을 줄여 다음 중심으로 구성한다.

```text
SEARCH_WORKING_NOTE
SEARCH_SESSION_SUMMARY
MUTABLE_SEARCH_STATE
AUTHORITATIVE_FACT_SUMMARY
ACTIVE_QUESTION
RECENT_PRODUCT_FOCUS
TOP_K_SUMMARY
PRODUCT_CATALOG_SUMMARY
ALLOWED_APPLICATION_OPERATIONS
CURRENT USER MESSAGE
```

legacy alias (`active_facts`, `answer_history`, duplicated intent dumps 등)는 외부 LLM prompt에 중복 전달하지 않는다.

Authoritative context도 raw MyData dump 대신 필요한 summary만 제공한다.

## 13. Structured backend result vs LLM prose

Web UI의 다음 값은 반드시 structured backend DTO를 직접 렌더링한다.

- rank / Top 5 membership
- realizable_rate / advertised_max_rate
- expected pre-tax / after-tax interest
- term / contribution amount
- SATISFIED / ACHIEVABLE / UNSATISFIABLE / UNKNOWN
- reason badge
- rate breakdown
- evidence/provenance

LLM prose는 설명용이다. LLM prose를 다시 parse하여 금융판정에 사용하지 않는다.

## 14. REST conversational entrypoint

Canonical conversational entrypoint:

```text
POST /search-sessions/{id}/messages
{
  "message": "생각해보니 급여계좌는 잘 모르겠어."
}
```

응답은 structured `ConversationTurnResult`이며 상황에 따라:

- `next_question`
- `recommendations`
- `current_results_requested`
- `unresolved_warning`
- updated `session`

을 포함한다.

Session lifecycle:

```text
POST   /search-sessions
POST   /search-sessions/{id}/messages
GET    /search-sessions/{id}/recommendations
GET    /search-sessions/{id}/recommendations/{product_id}
GET    /search-sessions/{id}/questions/next
DELETE /search-sessions/{id}
```

기존 typed Intent/Answer API는 Quick UI 및 테스트를 위해 유지한다.

## 15. Web UI가 사용할 주요 DTO/API

- `SearchSession`
- `ConversationTurnResult`
- `PlannedQuestion`
- `ProductRecommendationResult`
- `RecommendationListItem`
- `ProductRecommendationDetail`
- `RateBreakdownItem`
- `ContributionFeasibilityClarification`
- `ProductContributionChoice`

Web UI는 `/messages`를 자연어 loop의 중심으로 사용하고, List/Detail 화면은 Recommendation DTO를 직접 렌더링한다.

## 16. 알려진 MVP limitations

- 자연어 의미 해석은 LLM이므로 100% semantic correctness를 보장하지 않는다.
- 실제 MyData/은행 API는 아직 연결하지 않았다.
- Institution-specific resolver coverage는 fixture 수준이다.
- Production DB / distributed session locking / TTL cleanup daemon은 없다.
- Working Note는 best-effort local/in-memory continuity layer이며 production durable store가 아니다.
- 완전한 Net Benefit Engine과 행동 성공확률 모델은 범위 밖이다.
- 신한 급여우대의 모든 공식 ActionPath를 완전 구현하지 않았다.

## 17. Web UI Sprint에서 backend를 건드릴 때 지킬 원칙

1. 사용자는 state operation을 입력하지 않는다. 자연어 또는 Quick UI를 사용한다.
2. LLM은 금융판정/금리/이자/Ranking을 계산하지 않는다.
3. UI 숫자와 status는 structured DTO가 Source of Truth다.
4. Product-specific contribution choice를 global contribution plan과 섞지 않는다.
5. UNKNOWN favorable branch는 optimistic search에만 사용한다.
6. current-results request는 persistent state로 저장하지 않는다.
7. Working Note를 금융 Source of Truth로 사용하지 않는다.
8. 새 상품은 ProductMetadata/ProductFeature/Rule AST/Resolver/Institution Service Knowledge로 연결하고 Candidate/Ranking/Question Planner에 상품별 분기를 추가하지 않는다.

---

**Handoff conclusion:** v0.4.6 backend는 자연어 수정, 미결정 복귀, 현재 결과 즉시 보기, 장기 대화 continuity, Web session close까지 포함해 Web UI가 바로 붙을 수 있는 기준선으로 동결한다.
