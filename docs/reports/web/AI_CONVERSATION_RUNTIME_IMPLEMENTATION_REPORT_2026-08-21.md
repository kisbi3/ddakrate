# AI 대화 런타임 구현 및 검증 보고서

- 구현·검증일: 2026-08-21
- 기준 문서: [AI 대화 런타임 개선 계획](AI_CONVERSATION_RUNTIME_IMPROVEMENT_PLAN.md)
- 실제 검증 환경: 백그라운드 Web `127.0.0.1:57949`, OpenAI Responses API, `gpt-5.6-luna`

## 구현 결과

Phase 1~7을 모두 반영했다.

- 목표 납입액과 상품의 실제 계획액·월 환산액·차이율을 분리했다. 정확한 금액을 지원하는 상품을 먼저 배치하고 ±5% 및 불일치 상태를 명시한다.
- 희망 기간과 실제 선택 기간을 구분하고, 12개월 선택이 가능한 장기 범위 상품은 실제 12개월 cashflow로 계산한다.
- 자연어와 UI의 금리·세전이자·세후이자 정렬 enum을 통일했다. 정렬만 바뀌면 기존 평가를 재사용하고 active question을 유지한다.
- 미확인 후보는 `PROVISIONAL`, 질문과 계산 입력이 끝난 결과만 `CONFIRMED`로 노출한다. `모르겠어요`는 답변 완료 상태로 저장하되 해당 우대는 보수적으로 제외한다.
- 현재 급여 은행과 변경 의향처럼 한 답변에 포함된 상태를 별도 typed fact로 저장한다.
- 질문에는 상품·은행·공식 source·확인 경로·알 수 없는 데이터 범위를 구조화해 붙인다. source에 없는 이벤트 발급법이나 앱 메뉴는 생성하지 않는다.
- active 금융사실 답변은 작은 difference 전용 Structured Output을 사용한다. 일반 ConversationPlan 전체를 반복 전송하지 않는다.
- 알려진 질문 유형은 Engine의 grounded template을 바로 사용하고, 동일 canonical payload의 LLM 문구는 캐시한다.
- 디버그 화면은 domain schema와 실제 OpenAI transport schema를 따로 보여준다. schema는 hash로 중복 제거하고, 전체 audit와 상품별 상세 평가는 선택할 때 지연 로드한다.

## 2026-08-22 사전 질문 상태 머신 추가

[대화형 탐색 프로필 및 사전 질문 설계](../../guides/PRE_SEARCH_QUESTION_DESIGN.md)에 따라 상품별 검증 이전 단계를 별도 상태 머신으로 구현했다.

- `PRE_SEARCH_PROFILE` 질문은 Backend가 고정 순서·고정 문구로 선택한다. LLM은 질문을 선택하거나 작성하지 않는다.
- 최초 발화에 포함된 상품 형태와 금액·납입 주기·기간은 완료 처리해 다시 묻지 않는다.
- 금액·납입 주기·기간은 한 질문으로 합쳤다.
- 추천 기준 질문은 `총 이자 금액이 중요하세요, 아니면 높은 금리가 중요하세요?`로 고정했다.
- 자연어 답변은 작은 `PreSearchAnswerPlan` strict Structured Output으로 차이만 추출한다.
- 답변 진행 상태를 `NOT_ASKED`, `ANSWERED`, `ACKNOWLEDGED_UNKNOWN`, `NOT_APPLICABLE`로 분리했다.
- 새 은행·급여계좌·카드 관련 공통 우대 의향은 하나의 질문으로 묻되, 자연어 답변은 행동별 `WILLING`, `UNWILLING`, `CONDITIONAL`로 분리 저장한다.
- 가입 가능 시점과 일반 자동이체 의향 질문은 제거했다. 매일 직접 입금처럼 실제 부담이 큰 조건은 상품별 단계에서 묻는다.
- 기본 정렬을 `MAX_ESTIMATED_PRE_TAX_INTEREST`로 바꾸고 UI의 금리·이자 표기를 세전 기준으로 명시했다.
- 사전 질문 중에는 추천 카드를 숨기고, 완료 후 좁혀진 `PROVISIONAL` 후보와 상품별 검증 질문을 표시한다.
- 사전 질문 완료 후 ranking-aware planner가 현재 후보의 중요한 미확인 조건을 선택하고, LLM 질문 표현·답변 해석을 거쳐 Engine 재평가로 반복 연결한다.
- 내부 `UNKNOWN` 판정은 화면에서 `확인 전`으로 통일했다. 가입자격 질문은 확인 전 상태로 건너뛸 수 없고, 확인할 수 없다면 상품 제외를 안내한다.
- 복합 자연어 질문은 `FREE_TEXT`, 단순 참·거짓 질문은 `BINARY`로 Backend가 지정한다. 현재 카드 결제계좌와 변경 의향처럼 서술이 필요한 질문에는 선택 버튼을 표시하지 않는다.
- 토스뱅크 아이적금과 IBK사랑나눔적금의 뭉뚱그린 공식 자격 fact를 공식 대상 조건이 드러나는 사용자 확인 규칙으로 데이터화했다.
- 전체 audit는 지연 로드 endpoint에 보존하고 기본 debug snapshot에는 최근 200건만 넣어 대화가 길어져도 응답 크기가 누적되지 않게 했다.

실제 OpenAI acceptance 결과:

| 항목 | 결과 |
|---|---:|
| 고정 추천 기준 문구 | 일치 |
| 사전 질문 답변 schema | `PreSearchAnswerPlan`, strict |
| 사전 질문 prompt | 1,619 tokens |
| `모르겠어요` | 0.14초, LLM 0회 |
| 정렬 전환 | 0.21초, LLM 0회 |
| debug 기본 bundle | 약 0.30MB |
| 사전 질문 완료 상태 | 6 / 6 |
| 상품별 검증 질문 진입 | 자동 연결 |
| 상품별 자연어 답변 | `ActiveFinancialFactPlan` → Engine 재계산 → 다음 질문 |
| 기본 계산·정렬 | 세전 이자금 |

## 실제 OpenAI Acceptance 결과

대표 입력은 `1년 동안 월 30만원 넣고 싶어. 실제로 받을 수 있는 금리가 중요해.`였다.

| 항목 | 결과 |
|---|---:|
| Top 5 목표/계획/월 환산 납입액 | 전부 300,000원 `EXACT` |
| Top 5 계산 원금 | 전부 3,600,000원 |
| Top 5 기간 | 전부 12개월 `EXACT` |
| 초기 검색 | 4.46초 |
| 급여 사전 질문 자연어 응답 | 1.73초 |
| 해당 orchestration prompt | 480 tokens |
| 자연어 `이자금순` 변경 | 0.21초, LLM 0회 |
| `모르겠어요` 처리 | 0.15초, LLM 0회 |
| active question id | 정렬 전후 동일 |
| debug 기본 bundle | 약 0.68MB |
| OpenAI transport | `store=false`, strict schema, lookaround 없음 |

추천 전 질문이 남아 있으므로 이 시점의 제목과 API 상태는 `후보 5개` / `PROVISIONAL`이다. 확정 조건을 만족하기 전에는 `Top 5`라고 표시하지 않는다.

## 자동 검증

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/acceptance_test_web_openai.py \
  --base-url http://127.0.0.1:57949
```

최종 회귀 검증은 434개 테스트가 모두 통과했다. 실제 OpenAI acceptance에서는 통합 우대 의향을 행동별로 분리했고, 상품별 급여이체 질문 답변 후 `카카오뱅크 · 한달적금`의 `31일 동안 매일 직접 입금` 질문으로 이어지는 것까지 확인했다. 테스트 수는 이후 변경될 수 있으므로 운영 문서에서는 위 실행 명령과 최근 검증일을 기준으로 삼는다.

## 남은 데이터 범위

코드 흐름과 상태 의미론은 구현됐지만, 공식 상품설명서에 없는 실시간 이벤트 대상·발급 기간·수량과 앱의 현재 메뉴 경로는 별도 데이터 수집 또는 금융기관 연동이 필요하다. 현재 구현은 이 값을 추측하지 않고 `data_gaps`로 노출한다.
