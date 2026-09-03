# AI 대화 런타임 핵심 테스트 보고서

- 테스트 일자: 2026-08-21 (Asia/Seoul)
- 대상 커밋: `11dfc73`
- 서버: `http://127.0.0.1:57949`
- 실제 LLM: OpenAI Responses API / `gpt-5.6-luna`
- 대표 세션: `SEARCH-69410368d54b1168`
- 범위: 초기 의도 추출, 멀티턴 patch, 질문/설명/unknown 처리, Engine 재계산, Top 5/상세 preview, debug trace

## 1. 결론

현재 구조의 핵심 경계는 정상이다.

```text
사용자 자연어
→ LLM Structured Output
→ typed patch / operation
→ deterministic Engine 재계산
→ 질문 또는 추천 결과
```

초기 발화는 `IntentPatch`, 후속 발화는 `ConversationPlan`, 질문은 `GeneratedQuestion`으로 처리됐고 모든 실제 LLM 요청에서 `system`과 `user` 역할이 분리됐다. 금액 변경, 질문 설명 요청, `모르겠어요`, 부정 답변은 서로 다른 operation으로 올바르게 라우팅됐으며 금액·금리·이자는 Engine 결과에서 생성됐다.

다만 공모전 시연 전에 바로 잡아야 할 핵심 문제가 있다. 특히 **“이자금순” 의미 오류**, **정렬 변경 시 질문 교체**, **Top 5의 material unknown 잔존**, **과도한 LLM context/중복 호출**은 사용자 신뢰와 응답 속도에 직접 영향을 준다.

판정: **기본 아키텍처 통과, 데모 품질은 조건부 통과**

## 2. 테스트 결과 요약

### 자동·실행 검증

| 항목 | 결과 |
|---|---:|
| 전체 pytest | `423 passed in 6.70s` |
| Python compile | 통과 |
| `app.js`, `debug.js` syntax | 통과 |
| Web health | HTTP 200, `status=ok` |
| LLM health | HTTP 200, `healthy=true`, 811ms |
| Catalog | 50개 로드, 대표 검색 후보 43개 |
| 백그라운드 서비스 | launchd에서 실행 중 |
| Top 5 preview | 5/5 HTTP 200, 각각 약 4.6~8.3ms |
| UI 정렬 PATCH | 세전이자 enum 직접 반영, active question id 보존 |

### 실제 멀티턴 시나리오

초기 입력:

> 1년 동안 매달 30만원씩 넣을 적금을 찾아줘. 실제로 받을 수 있는 금리가 중요해.

추출 결과:

- `product_types = [INSTALLMENT_SAVINGS]`
- `selected_term = 1 YEAR`
- `desired_periodic_amount = 300000`
- `ranking_objective = MAX_REALIZABLE_RATE`
- capability/hard constraint는 임의 생성되지 않고 빈 배열 유지
- 기본 `requested_top_k = 5`

| 턴 | 입력 | 실제 operation | 상태 변화 | 판정 |
|---:|---|---|---|---|
| 1 | 월 납입액은 20만원으로 바꿔줘 | `UPDATE_SEARCH_INTENT` | 30만원→20만원, 1년/금리순 보존, 전체 재계산 | 통과 |
| 2 | 현재 국민은행 급여, 유리하면 이동 가능 | `SUBMIT_ACTIVE_QUESTION_ANSWER` | `SALARY_ACCOUNT_CHANGE_POSSIBLE=true` | 부분 통과 |
| 3 | 그게 뭐야? 어떤 결제야? | `EXPLAIN_ACTIVE_QUESTION` | 검색 상태를 바꾸지 않고 설명만 반환 | 부분 통과 |
| 4 | 모르겠어요 | `SKIP_ACTIVE_QUESTION` | false로 만들지 않고 질문만 완료 처리 | 통과 |
| 5 | 아니요 | `SUBMIT_ACTIVE_QUESTION_ANSWER` | 카카오 한달적금 직접입금 의향 `false` | 통과 |
| 6 | 이자금순으로 보여줘 | `UPDATE_SEARCH_INTENT` | **세후이자 objective로 잘못 변경**, 진행 질문도 교체 | 실패 |
| 7 | 질문은 그만하고 지금 결과 | `SHOW_CURRENT_RESULTS` | 상태를 확정하지 않고 후보 결과와 경고 반환 | 통과 |

질문 문구 개선도 실제로 확인됐다.

- 카카오뱅크 한달적금: `31일 동안 매일 직접 입금`으로 표현
- BNK경남 Touch UP 적금: `1개월 가입 기간 동안 ... 25일 이상`으로 기간 명시
- 토스뱅크 공통 자동이체: `12개월 동안 매달 ...`로 상품 기간과 공통 적용 범위 명시

## 3. 정상 동작한 핵심 설계

### 3.1 초기값 + difference 기반 patch

초기 상태는 사용자가 말하지 않은 조건을 `false`나 `CANNOT`으로 채우지 않았다. 첫 발화도 전체 Intent를 자유 생성하지 않고 현재 빈 Intent에 대한 `IntentPatch`로 처리됐다. 후속 금액 변경은 `contribution_plan_patch.desired_periodic_amount=200000`만 포함했고 기간과 정렬 기준은 유지됐다.

### 3.2 Structured Output 경계

실제 trace에서 다음 schema가 확인됐다.

- 초기 의도: `IntentPatch`
- 멀티턴 라우팅: `ConversationPlan`
- 질문 작성: `GeneratedQuestion`

각 요청은 `[system, user]` 역할을 사용했고, 모델 출력은 Pydantic으로 다시 검증됐다. LLM은 operation과 문구를 만들고 Engine이 43개 후보의 판정, 예상 원금, 세전·세후이자, 순위를 계산했다.

### 3.3 unknown 의미 보존

`모르겠어요`는 `SKIP_ACTIVE_QUESTION`으로 처리됐다. 사용자 fact를 false로 저장하지 않았고, 해당 질문을 다시 묻지 않도록 완료 이력만 남긴 뒤 다음 질문으로 이동했다. 이는 “모름은 부정이 아니다”라는 요구사항에 맞는다.

### 3.4 상세 preview 선로딩 경로

현재 Top 5의 `/preview` endpoint는 모두 LLM 호출 없이 HTTP 200을 반환했다. 사용자 카드 클릭 전에 결정론적 상세 데이터를 미리 불러오는 경로는 정상이다.

## 4. 핵심 발견사항

### P1 — “이자금순”이 세후이자로 잘못 해석됨

기대값은 `MAX_ESTIMATED_PRE_TAX_INTEREST`지만 실제 `ConversationPlan`은 `MAX_ESTIMATED_AFTER_TAX_INTEREST`를 반환했다. 초기 Intent parser system prompt에는 올바른 매핑이 있지만 후속 발화를 담당하는 Conversation orchestrator system prompt에는 같은 정규화 규칙이 없다.

현재 상품들의 세율이 같아 순서가 우연히 같을 수 있지만, 비과세·세금우대 상품이 섞이면 사용자가 요청한 순서와 실제 순서가 달라질 수 있다.

권고:

1. Conversation orchestrator에도 정렬 용어의 canonical mapping을 명시한다.
2. UI 토글은 LLM을 거치지 않고 enum을 직접 전달한다.
3. `이자금순`, `세전이자`, `세후이자`, `금리순` 각각의 실제 OpenAI 회귀 테스트를 추가한다.

### P1 — 자연어 정렬 변경 시 진행 중 질문이 바뀜

정렬 전 질문은 BNK경남 Touch UP 적금의 25일 직접입금 여부였지만, `이자금순으로 보여줘` 이후 토스뱅크 12개월 자동이체 질문으로 교체됐다. 정렬 변경이 다음 질문의 중요도를 바꿀 수는 있어도 사용자가 답하려던 말풍선이 즉시 교체되면 맥락이 끊긴다.

별도로 UI 토글이 사용하는 직접 `PATCH /intent` 경로는 `MAX_ESTIMATED_PRE_TAX_INTEREST`를 정확히 반영했고 기존 active question id도 보존했다. 따라서 이 문제는 직접 PATCH가 아니라 자연어 `UPDATE_SEARCH_INTENT` 실행 경로에 한정된다.

권고: active question이 현재 후보에서 더 이상 무효가 된 경우가 아니라면 정렬 변경 동안 question id와 말풍선을 고정하고, 답변 후 새 ranking 기준으로 다음 질문을 선택한다.

### P1 — 최종 Top 5 조건 확정 기준 미충족

현재 결과의 Top 5 `material_unknown_count`는 각각 `1 / 6 / 1 / 2 / 3`이었다. Backend는 이를 확정 Top 5가 아닌 후보 목록으로 보고 `ranking_stable=false`와 unresolved warning을 반환하므로 의미론적 안전장치는 있다. 그러나 DTO 이름은 여전히 `top_products`이고 사용자가 강제로 현재 결과를 보면 미확인 조건이 남은 5개가 노출된다.

권고:

- `candidate_products`와 `confirmed_top_products`를 API/화면에서 명시적으로 분리한다.
- `Top 5` 표시는 상위 5개 모두 `material_unknown_count=0`이고 ranking stable일 때만 사용한다.
- 사용자가 `모르겠어요`로 완료한 조건은 별도 `acknowledged_unknown`으로 표시하되 미확인 질문으로 다시 세지 않는다.

### P1 — LLM 호출량과 context가 과도함

대표 세션 7턴에서 LLM은 16회 호출됐고 총 82,586 tokens를 사용했다.

| Purpose | 호출 | Prompt tokens | Completion tokens | 합계 |
|---|---:|---:|---:|---:|
| Intent parsing | 1 | 1,929 | 239 | 2,168 |
| Conversation orchestration | 7 | 69,003 | 1,198 | 70,201 |
| Question generation | 8 | 8,106 | 2,111 | 10,217 |

Conversation orchestration 한 번에 평균 약 9,858 prompt tokens가 전달된다. 메시지 응답시간은 약 1.8~10.4초였고, 일부 상태 변경 턴에서는 질문 생성이 한 요청 안에서 두 번 호출됐다.

권고:

- Conversation context를 현재 mutable state, active question, 최근 focus, 허용 operation에 필요한 product/fact 식별자로 projection한다.
- 43개 전체 평가 결과나 중복 working note를 orchestration prompt에서 제거한다.
- 동일 question payload는 request 단위로 memoize하여 한 턴에 한 번만 생성한다.
- quick answer와 UI ranking toggle은 deterministic endpoint로 우회한다.

### P1 — 사전 질문에서 받은 현재 은행이 typed state에 남지 않음

사용자는 “현재 국민은행으로 급여를 받는다”고 답했지만 structured state에는 `SALARY_ACCOUNT_CHANGE_POSSIBLE=true`만 저장됐다. 현재 급여 은행 `국민은행`은 rationale/원문에는 남지만 Engine fact와 “AI가 이해한 조건”에 사용할 typed 값으로 승격되지 않았다.

권고: `CURRENT_SALARY_BANK`, `CURRENT_CARD_PAYMENT_BANK`처럼 현재 상태와 변경 의향을 분리된 fact로 저장하고, 한 발화에서 두 fact를 함께 반영한다.

### P1 — 질문 설명이 상품별 확인 방법을 제공하지 못함

11Pay/신한카드 결제 우대 질문에 “그게 뭐야?”라고 물었을 때 답변은 “원래 은행 또는 마이데이터 조회 항목이며 네/아니요/모름으로 답하라”는 일반 설명뿐이었다. 어떤 결제가 대상인지, 앱에서 어디를 확인하는지, 행사/발급 조건이 무엇인지는 제공하지 못했다.

권고: `EXPLAIN_ACTIVE_QUESTION`에서 정적 explanation을 재사용하지 말고, 상품 knowledge의 조건 정의·확인 경로·기간·대상·예외를 grounded payload로 전달한다. 데이터가 없으면 없는 범위를 명시한다.

### P2 — debug 화면의 Schema가 실제 전송본과 다름

debug trace의 `response_json_schema`에는 Pydantic Decimal이 만든 `(?!...)` lookaround가 보인다. 실제 OpenAI adapter는 전송 직전 `openai_strict_json_schema()`로 이를 제거하므로 요청은 성공했다. 즉 화면의 메시지는 실제 요청과 같지만 **Schema는 실제 OpenAI 전송 payload가 아니라 정규화 전 원본**이다.

이 차이는 과거 HTTP 400 원인을 조사할 때 “지금도 잘못된 Schema를 보내고 있다”는 오해를 만든다.

권고: debug에 `domain_schema`와 `transport_schema`를 분리하고, 실제 POST payload의 `text.format.schema` 및 schema hash를 보여준다.

### P2 — debug 상세 payload가 빠르게 커짐

대표 세션의 debug 상세 JSON은 15,090,492 bytes였고 localhost 응답에도 약 0.77초가 걸렸다. 각 LLM call에 큰 JSON Schema가 반복 저장되는 영향이 크다.

권고: schema를 hash 기반으로 한 번만 저장하고 call에는 참조만 둔다. Engine snapshot도 request마다 복제하지 말고 최신 snapshot과 event delta를 분리한다.

### P2 — 금리순 상위 상품의 예상이자 null

초기 금리순 1위였던 신한 11번가 쇼핑 적금은 `realizable_rate=5.5`였지만 예상 원금과 세전이자가 null이었다. 이자순으로 변경한 뒤에는 비교 가능한 상품만 상위에 왔지만, 금리순 카드에서도 사용자가 기대하는 예상 수령액이 비어 보일 수 있다.

권고: 금리순이라도 Top 카드에 필요한 contribution input을 우선 질문하거나, 계산 불가 사유를 명시적으로 표시한다.

### P3 — 실행 가이드 드리프트

`LOCAL_RUN_AND_TEST_GUIDE.md`에는 현재 구현과 다른 내용이 남아 있다.

- 샘플 사용자 `U001`가 자동 연결된다고 설명하지만 runtime은 `sample_user_id=null`, `CONVERSATIONAL_INPUT`이다.
- 기준 테스트 수가 400개로 적혀 있으나 현재는 423개다.
- 추천 설명 일부가 세후이자 중심의 과거 UI 문구다.

권고: 공모전 전달 전에 현재 무상태 대화 방식과 세전이자 UI 기준으로 가이드를 갱신한다.

## 5. 우선 수정 순서

1. “이자금순” canonical mapping 및 정렬 토글 deterministic 처리
2. 정렬 변경 중 active question 보존
3. 현재 은행 typed fact 저장 + 상세 질문 설명용 grounded knowledge 연결
4. confirmed Top 5와 provisional candidates 분리
5. Conversation context 축소 및 중복 QuestionGeneration 제거
6. debug transport schema 표시와 payload deduplication
7. 로컬 실행 가이드 갱신

## 6. 재현 정보

Debug 화면에서 세션 `SEARCH-69410368d54b1168`을 선택하면 다음을 확인할 수 있다.

- 초기 `IntentPatch` system/user prompt와 Structured Output
- 각 후속 발화의 `ConversationPlan.actions`
- 질문별 `GeneratedQuestion`
- 43개 후보 Engine evaluation과 ranking
- 요청별 latency/token usage

로컬 주소:

- 제출 화면: `http://127.0.0.1:57949/`
- 내부 debug: `http://127.0.0.1:57949/debug`

이 보고서는 상태를 진단한 결과이며 발견된 문제의 코드 수정은 포함하지 않는다.
