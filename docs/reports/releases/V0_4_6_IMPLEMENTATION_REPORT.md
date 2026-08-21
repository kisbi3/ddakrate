# Financial Eligibility Engine v0.4.6 Implementation Report

**Sprint:** Final Conversational Runtime, Working Note & Web Handoff Closure  
**Baseline:** `financial-eligibility-engine-v0.4.5`  
**Target:** `financial-eligibility-engine-v0.4.6`  
**Final status:** `READY_FOR_WEB_UI_IMPLEMENTATION`

## 1. v0.4.5 baseline

v0.4.5 ZIP을 별도 디렉터리에 다시 해제한 뒤 수정 전에 직접 검증했다.

| Baseline check | Result |
|---|---:|
| Full pytest | **349 passed** |
| v0.3.2 regression subset | **176 passed** |
| compileall | PASS |
| source import/version | PASS / `0.4.5` |
| wheel build (`--no-build-isolation`) | PASS |
| clean wheel install/import | PASS |
| v0.3.1 / v0.3.2 / v0.4 / v0.4.1 / v0.4.2 / v0.4.3 / v0.4.4 / v0.4.5 adversarial | PASS |
| v0.4.5 schema parse | **39 schemas, PASS** |

온라인 build dependency 다운로드가 없는 실행환경이므로 wheel은 기존 repo 방식과 동일하게 `pip wheel --no-build-isolation --no-deps`로 검증했다.

## 2. 수정 파일

핵심 코드 변경:

- `src/eligibility/application_service.py`
- `src/eligibility/conversation.py`
- `src/eligibility/working_note.py` **신규**
- `src/eligibility/schema/conversation.py`
- `src/eligibility/audit/models.py`
- `src/eligibility/adapters/rest.py`
- `src/eligibility/adapters/mcp.py`
- `src/eligibility/__init__.py`
- `pyproject.toml`
- `scripts/export_schemas.py`
- `scripts/run_v046_adversarial.py` **신규**
- `tests/test_v046_final_conversational_runtime.py` **신규**
- `README.md`
- `docs/handoff/FINANCIAL_ELIGIBILITY_ENGINE_V0_4_6_WEB_HANDOFF.md` **신규**

생성 산출물:

- v0.4.6 JSON schemas 39개
- v0.4.6 validation/adversarial reports
- `examples/v0.4.6/adversarial-results.json`

## 3. 미결정 / unset semantics

미결정을 새로운 State Machine으로 만들지 않았다. 기존 Mutable Search State 수정 semantics 안에서 domain별 최소 primitive만 추가했다.

### Capability

사용자가:

> “생각해보니 급여계좌를 바꿀 수 있을지 잘 모르겠어.”

라고 하면 `CHANGE_SALARY_ACCOUNT = UNKNOWN`으로 변경한다. 기존 intent-materialized/user-declared `CAN/CANNOT` fact는 `SUPERSEDED`되어 Engine active view에서 제거된다.

### User-declared Fact

`CLEAR_USER_DECLARED_FACT`는 `USER_DECLARED`의 `FUTURE_INTENT / SELF_REPORTED_FACT` 계열만 현재 active state에서 clear할 수 있다. 과거 record는 삭제하지 않는다.

### Product-specific contribution choice

`CLEAR_PRODUCT_CONTRIBUTION_CHOICE`는 현재 ACTIVE choice를 history에서 `SUPERSEDED`로 남기고 active choice map에서 제거한다. 현재 state에서 입력이 다시 필요하면 과거 `answered_question_ids` 때문에 막히지 않도록 해당 product/field 질문을 다시 material하게 만들 수 있다.

### Intent state

Preference/HardConstraint/NumericPreference/ContributionPlan field의 unset은 기존 `IntentPatch.remove_*` / `ContributionPlanPatch.remove_fields`를 그대로 재사용한다. 범용 `NULL` schema나 universal patch language를 도입하지 않았다.

## 4. 자연어 “현재 결과 보여줘”

새로운 turn-local `ConversationOperation.SHOW_CURRENT_RESULTS`를 추가했다.

예:

> “질문은 그만하고 지금 결과 보여줘.”

처리 semantics:

1. Mutable Search State 변경 없음
2. UserFact 변경 없음
3. active question은 SearchSession에 유지
4. 기존 deterministic Ranking을 재계산하지 않고 현재 Recommendation 반환
5. UNKNOWN branch는 그대로 UNKNOWN
6. material question 또는 unstable ranking이 남으면 `unresolved_warning` 반환
7. `ConversationTurnResult.next_question`은 그 turn에서만 `None`
8. 다음 turn에서는 질문 flow를 정상 재개 가능

`SHOW_CURRENT_RESULTS`와 `NO_OP`은 persistent state operation이 아니므로 v0.4.6에서는 이 operation만 있는 turn 때문에 새로운 `ranking_run_id`를 만들지 않는다.

## 5. Question Planner와 current-result request 경계

Question Planner의 current-state semantics는 유지한다.

- 현재 unresolved material information이 있으면 active question을 계속 보존한다.
- current-result 요청은 그 질문을 answered/declined로 기록하지 않는다.
- current-result 요청 후 사용자가 “질문 계속해도 돼”라고 하면 기존 active question을 다시 반환할 수 있다.
- UNKNOWN을 favorable branch로 final ranking에 넣지 않는다.

## 6. Search Working Note architecture

새 `SearchWorkingNoteStore` protocol을 도입했다.

```text
initialize
record_user_turn
update_summary
load
delete
```

구현:

- `InMemorySearchWorkingNoteStore`: 기본 runtime implementation
- `FileSearchWorkingNoteStore`: human-readable Markdown + temp-file/atomic replace

Working Note는 금융판정 Source of Truth가 아니다.

```text
Structured backend state / UserFactStore > Search Working Note
```

LLM context continuity만 지원한다.

## 7. Working Note lifecycle

### Session creation

SearchSession 생성 시 note를 초기화하고 현재 structured state summary를 기록한다.

### User turn

`handle_user_message()`는 LLM 호출 전에 먼저:

1. turn sequence 증가
2. raw user utterance를 Working Note에 best-effort 기록
3. structured backend state로 note summary 재생성
4. Working Note + current backend summary를 LLM context에 제공

turn commit 후 summary를 다시 갱신한다.

### Session close

`ApplicationService.close_search_session()`이 Working Note를 삭제하고 runtime session을 정리한다.

REST:

```text
DELETE /search-sessions/{id}
```

MCP thin adapter에도 동일 close operation만 노출했다.

Recommendation이 생성됐다는 이유만으로 note를 즉시 삭제하지 않는다. 결과 후속 대화를 위해 explicit session close까지 유지한다.

## 8. Working Note와 Audit의 차이

### Audit

- append-only
- 계산/수정 재현
- deterministic trace/debugging

### Working Note

- ephemeral
- compact current-state summary + 최근 중요 user utterance
- LLM continuity/compaction recovery
- 금융계산에는 사용하지 않음

Working Note write 실패는 금융 transaction 실패 조건이 아니다. write/update/delete 실패는 `WORKING_NOTE_WRITE_FAILED` Audit event로 남길 수 있고 다음 turn에서 structured backend state로 note를 재생성할 수 있다.

## 9. Working Note compaction recovery

adversarial/test에서 다음을 검증했다.

```text
사용자: “카카오는 3천원으로 할게.”
→ Working Note 기록 + backend choice=3000

(raw chat history를 ApplicationService가 보유하지 않는 상태)

사용자: “그거 2천원으로 바꿔줘.”
→ LLM prompt는 Working Note + current backend state만으로
  Kakao/current choice context를 확인
→ backend validates/commits 2000
```

즉 과거 전체 raw conversation replay에 의존하지 않는다.

## 10. LLM context payload 정리

v0.4.5의 backward-compatible 중복 alias를 외부 Conversation Orchestrator prompt payload에서 제거했다.

v0.4.6 canonical context:

```text
SEARCH_SESSION_SUMMARY
SEARCH_WORKING_NOTE
MUTABLE_SEARCH_STATE
AUTHORITATIVE_FACT_SUMMARY
ACTIVE_QUESTION
RECENT_PRODUCT_FOCUS
TOP_K_SUMMARY
PRODUCT_CATALOG_SUMMARY
ALLOWED_APPLICATION_OPERATIONS
```

`active_facts`, `answer_history`, duplicated `intent` dump 같은 동일 의미 payload는 prompt에 중복 전달하지 않는다.

## 11. Authoritative Fact protection

unset/clear operation에도 기존 authoritative boundary를 적용했다.

사용자가:

> “그 계좌 이력은 그냥 모르는 걸로 해줘.”

라고 말해도 active state가 `INSTITUTION_VERIFIED / MYDATA_VERIFIED / OBSERVED_*` only라면 `AUTHORITATIVE_FACT_IMMUTABLE_BY_USER`로 reject한다.

`CLEAR_USER_DECLARED_FACT`가 authoritative fact를 삭제하거나 supersede하지 않는다.

## 12. REST 변경

기존 canonical natural-language endpoint 유지:

```text
POST /search-sessions/{id}/messages
```

`ConversationTurnResult` 추가 fields:

```text
current_results_requested: bool
unresolved_warning: str | None
```

Session lifecycle close:

```text
DELETE /search-sessions/{id}
```

기존 typed API는 모두 유지한다.

## 13. MCP 변경

MCP business logic은 추가하지 않았다.

```text
MCP → ApplicationService → same deterministic core
```

추가된 것은 `close_search_session()` thin forwarding뿐이다. 자연어 orchestration은 기존 `handle_user_message()`를 그대로 사용한다.

## 14. Audit 변경

추가 event type:

- `MUTABLE_VALUE_CLEARED`
- `CURRENT_RESULTS_REQUESTED`
- `WORKING_NOTE_CREATED`
- `WORKING_NOTE_UPDATED`
- `WORKING_NOTE_REBUILT` (reserved for explicit rebuild observability)
- `WORKING_NOTE_DELETED`
- `WORKING_NOTE_WRITE_FAILED`

Working Note event도 `search_session_id` trace context를 유지한다.

## 15. 신규 regression tests

`tests/test_v046_final_conversational_runtime.py`: **16 tests**

검증 범주:

- Capability `CAN → UNKNOWN`
- ProductContributionChoice set → unset → question material again
- Preference `PREFER_PRESENT → NEUTRAL`
- current results with pending question
- current results does not mark UNKNOWN satisfied
- current results does not change ranking run
- question resume after current results
- Working Note created/updated
- user utterance recorded even when LLM structured turn fails
- File Working Note persistence / atomic file path / deletion
- sensitive long identifier redaction
- stale Working Note overwritten by backend state before LLM prompt
- compaction recovery
- authoritative clear rejection
- REST `/messages` current-results response
- MCP/ApplicationService close reuse
- Working Note write failure is not financial SPOF

## 16. Adversarial tests

`run_v046_adversarial.py` 직접 실행 결과 PASS:

1. `Capability CAN → UNKNOWN`
2. Product choice `3000 → unset` 후 question 재생성
3. questions remaining 상태에서 current Top 5 즉시 반환
4. current-result request에서 `ranking_run_id` 불변
5. unresolved warning 유지
6. Working Note compaction recovery (`3000 → 2000`)
7. Working Note session close 삭제
8. authoritative clear attempt reject

그리고 기존 v0.3.1~v0.4.5 adversarial script를 모두 v0.4.6 코드에서 다시 실행하여 PASS했다.

## 17. 전체 regression 결과

| Validation | Result |
|---|---:|
| Full pytest | **365 passed, 0 failed** |
| v0.3.2 regression | **176 passed** |
| v0.4.6 new tests | **16 passed** |
| compileall | PASS |
| source import/version | PASS / `0.4.6` |
| wheel build | PASS |
| clean wheel install/import | PASS |
| v0.3.1~v0.4.6 adversarial | PASS |
| v0.4.6 schema export/parse | **39 schemas / PASS** |
| Kakao/contribution targeted regression | PASS |

## 18. Known limitations

- 실제 외부 LLM provider 선택/credential/deployment는 Web/runtime Sprint 영역이다. pytest는 `MockLLMAdapter`로 deterministic orchestration boundary를 검증한다.
- 자연어 의미 해석은 LLM이므로 100% semantic correctness를 보장하지 않는다.
- Working Note는 MVP local/in-memory continuity layer다. Production durable DB나 distributed lock/TTL cleanup daemon은 도입하지 않았다.
- user utterance에는 간단한 sensitive-pattern redaction을 적용하지만 production-grade DLP system은 아니다.
- 실제 MyData/은행 API는 미연동이다.
- 신한 급여우대 전체 공식 ActionPath, complete Net Benefit Engine은 범위 밖이다.
- SearchSession expiration 자동 스케줄링은 아직 없으며 explicit close를 제공한다.

## 19. Web UI handoff readiness

영구 handoff 문서:

```text
docs/handoff/FINANCIAL_ELIGIBILITY_ENGINE_V0_4_6_WEB_HANDOFF.md
```

Web UI는 다음만으로 전체 MVP flow를 구현할 수 있다.

```text
POST /search-sessions
POST /search-sessions/{id}/messages
GET  /search-sessions/{id}/questions/next
GET  /search-sessions/{id}/recommendations
GET  /search-sessions/{id}/recommendations/{product_id}
DELETE /search-sessions/{id}
```

UI financial fields는 structured Recommendation/List/Detail DTO에서 직접 렌더링하고 LLM prose를 Source of Truth로 사용하지 않는다.

## 20. 최종 READY 판정

# `READY_FOR_WEB_UI_IMPLEMENTATION`

판정 근거:

- 자연어-only conversational UX 유지
- mutable value 수정 + unset 지원
- authoritative evidence 보호
- current-results turn-local response 지원
- UNKNOWN semantics 보존
- current-results 후 질문 재개 가능
- Working Note 실시간 user-turn 기록
- Working Note current state summary / compaction recovery
- Working Note stale state보다 backend state 우선
- Working Note explicit session close deletion
- LLM prompt duplicate context 축소
- deterministic Eligibility/Rate/Interest/Ranking/Top 5 유지
- 전체 기존 regression + 신규 regression PASS
- 카카오 7주 `from_sequence=None` / late streak / manual-fill semantics 유지
- REST/MCP Web handoff contract 검증 완료

v0.4.6을 backend closure의 최종 기준선으로 동결하고 다음 Sprint는 실제 Web UI 구현으로 전환한다.
