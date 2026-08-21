# Financial Eligibility Engine v0.4.6 — Web UI Implementation Sprint 1 Report

**Backend baseline:** `financial-eligibility-engine-v0.4.6`  
**Backend status before Web work:** `READY_FOR_WEB_UI_IMPLEMENTATION`  
**Sprint type:** Web runtime + MVP interaction layer  
**Important:** 이것은 `v0.4.7` backend closure patch가 아니다. 금융판정 semantics와 Core Architecture는 `v0.4.6`을 그대로 유지한다.

## 1. 사전 독립 확인

Web 구현 전에 실제 zip을 풀어 다음을 직접 확인했다.

- `docs/handoff/FINANCIAL_ELIGIBILITY_ENGINE_V0_4_6_WEB_HANDOFF.md`
- `README.md`
- `ApplicationService.handle_user_message()`
- `RestApplicationAdapter`
- `ConversationTurnResult`, `SearchSession`, Recommendation/List/Detail DTO
- Working Note lifecycle 및 authoritative fact protection
- v0.4.6 conversational runtime regression

검증:

```text
Original v0.4.6 pytest: 365 passed
Original v0.4.6 compileall: PASS
```

따라서 추가 backend closure Sprint는 만들지 않았다.

## 2. Web 직전 동결한 결정

Web UI 구현을 막는 Core 설계 의사결정은 없었다. 다만 구현 수준에서 다음 8가지만 동결했다.

1. **Layout** — Desktop은 왼쪽 Chat, 오른쪽 Top 5/가용 추천 결과의 split view.
2. **Search-state visibility** — “현재 AI가 이해한 검색 기준”을 포함한다. Working Note가 아니라 structured Application state를 직접 읽는다.
3. **Question UX** — 한 번에 active question 하나를 보여주고, allowed option은 버튼으로 빠르게 답할 수 있다. 자유 입력은 항상 유지한다.
4. **Current results** — 질문이 남아 있어도 추천 목록을 숨기지 않는다. “지금 정보로 결과 먼저 보기”는 persistent state를 변경하지 않는다.
5. **Detail UX** — List에서 상품 클릭 시 drawer를 열고, structured numeric/status/evidence를 AI prose보다 먼저 보여준다.
6. **Frontend stack** — MVP는 FastAPI same-origin + build-step 없는 HTML/CSS/ES module로 간다. React/Next 도입은 현재 가치 대비 배포·빌드 위험이 더 크므로 보류한다.
7. **Demo identity** — 로그인/MyData 실연동 대신 `U001` 샘플 금융데이터 사용. UI에는 사용자 식별정보 대신 “샘플 금융데이터 연결”만 표시한다.
8. **LLM runtime** — provider는 코드에 고정하지 않고 기존 OpenAI-compatible gateway를 환경변수로 연결한다. `MOCK`은 자연어를 이해한 것처럼 가장하지 않는다.

## 3. 구현 내용

### 3.1 FastAPI Web runtime

신규:

```text
src/eligibility/web/__init__.py
src/eligibility/web/runtime.py
src/eligibility/web/app.py
```

- 기존 `ApplicationService`를 그대로 조립한다.
- REST business logic은 `RestApplicationAdapter`를 그대로 재사용한다.
- `/api/{...}`는 기존 REST contract로 전달한다.
- `/healthz`, `/api/runtime` 제공.
- static Web asset을 같은 origin에서 제공한다.

### 3.2 샘플 상품/사용자 bootstrap

실제 regression fixture 4개를 Web runtime에 연결했다.

```text
신한 청년 처음적금
카카오뱅크 26주적금
IBK 부모급여우대적금
하나 달려라 하나 적금
```

샘플 사용자:

```text
U001 / virtual MyData + user-declared state
```

Top 5가 기본 contract지만 현재 실제 fixture가 4개이므로 Web은 “4개 후보 추천”으로 자연스럽게 표시한다. synthetic 5번째 상품을 끼워 넣지 않았다.

### 3.3 structured current-state view

신규 read-only Application/REST view:

```text
ApplicationService.get_mutable_search_state()
GET /search-sessions/{id}/state
```

포함:

- current ProductSearchIntent
- active user declarations
- product-specific contribution choices
- excluded product IDs

중요:

```text
Structured backend state → Web summary
```

이며:

```text
Search Working Note → parse → Web/finance state
```

가 아니다.

### 3.4 Chat UX

- 최초 자연어 질의로 session 생성
- 후속 자연어는 canonical `/messages`
- active question은 `PlannedQuestion` 직접 렌더링
- Ranking input의 allowed option은 typed `/answers` 사용
- “지금 정보로 결과 먼저 보기”는 `GET recommendations`로 현재 결과만 표시
- LLM 미연결 시 typed question flow는 동작하고 자유 자연어 후속 수정만 명확히 비활성 표시

### 3.5 Recommendation List

카드 Source of Truth:

```text
RecommendationListItem
```

표시:

- rank / institution / product
- realizable_rate (주요 숫자)
- advertised_max_rate (보조)
- term
- planned contribution
- maximum contribution
- estimated total principal
- estimated after-tax interest
- eligibility badge
- verification badge
- material unknown count
- missing contribution input state

### 3.6 Product Detail

Source of Truth:

```text
ProductRecommendationDetail
```

표시:

- realizable / confirmed / advertised rate
- term / plan / principal / pre-tax & after-tax interest
- positives / limitations / actions / unknowns
- RateBreakdownItem tree
- `SATISFIED / ACHIEVABLE / UNSATISFIABLE / UNKNOWN` 사용자-facing label
- action summary
- official source URL/page/section
- aggregate rate-cap adjustment
- grounded explanation

“판정 근거 맵”은 별도 Graph renderer를 금융 Source of Truth로 두지 않고, 현재 Sprint에서는 `RateBreakdownItem.children`을 nested evidence tree로 렌더링한다.

## 4. LLM 경계

외부 LLM provider가 설정되면 하나의 기존 gateway를 다음에 재사용한다.

```text
IntentParser
QuestionGenerator
ConversationOrchestrator
GroundedResultExplainer
```

LLM이 계산하지 않는 것:

```text
eligibility status
rate
interest
ranking
badges
Top K membership
```

Web UI는 위 값을 DTO에서 직접 렌더링한다.

## 5. 테스트

신규:

```text
tests/test_web_mvp_runtime.py
```

검증:

- root/static Web page
- runtime metadata
- session creation
- structured `/state`
- next question
- recommendations
- typed ranking-input answer
- product-specific contribution choice
- detail DTO
- SHOW_CURRENT_RESULTS semantics proxy
- session delete

최종:

```text
pytest: 369 passed, 0 failed
python -m compileall: PASS
node --check app.js: PASS
wheel build: PASS
static assets included in wheel: PASS
uvicorn startup + /api/runtime smoke: PASS
```

## 6. 현재 남은 실제 작업

다음은 backend closure가 아니라 **Web MVP Sprint 2 / deployment 작업**이다.

1. 실제 대화용 LLM provider/model/credential을 배포 환경에 연결하고 adversarial natural-language test 수행.
2. 실제 브라우저에서 desktop/mobile visual QA 및 accessibility/keyboard QA.
3. 배포 플랫폼 선택 + persistent availability 확인 + contest submission window 운영 계획.
4. fixture 상품 추가는 Web과 병렬 진행. Candidate/Ranking/Question Planner에 product-id 분기를 추가하지 않는다.
5. 심사 demo script를 고정하고 예상 결과를 regression으로 묶는다.
6. 기획서/기능명세서는 실제 배포된 UI와 구현 범위만 기준으로 작성한다.

## 7. 판정

**GO — Web UI 구현을 시작했고 Sprint 1의 end-to-end shell과 핵심 interaction plumbing은 완료.**

Core backend를 더 닫는 단계가 아니라, 이제 실제 LLM 연결 → 브라우저 adversarial QA → 배포 → 심사 시나리오 순으로 밀면 된다.
