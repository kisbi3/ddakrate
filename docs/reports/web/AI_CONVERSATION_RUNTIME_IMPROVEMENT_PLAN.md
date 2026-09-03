# AI 대화 런타임 개선 계획

- 작성일: 2026-08-21
- 구현 상태: **Phase 1~7 완료 (2026-08-21)**
- 구현 결과: [AI 대화 런타임 구현 및 검증 보고서](AI_CONVERSATION_RUNTIME_IMPLEMENTATION_REPORT_2026-08-21.md)
- 근거: [AI 대화 런타임 핵심 테스트 보고서](AI_CONVERSATION_RUNTIME_TEST_REPORT_2026-08-21.md)
- 목표: 사용자의 납입계획과 확인된 조건에 맞는 설명 가능한 Top 5를 빠르고 안정적으로 제공

## 1. 최종 사용자 경험

```text
사용자: 1년 동안 월 30만원 넣고 싶어. 실제 받을 금리가 중요해.

AI
1. 월 30만원 / 12개월 / 실제 금리순으로 이해
2. 30만원을 실제로 납입할 수 있는 상품을 우선 비교
3. Top 5를 확정하는 데 영향을 주는 조건만 한 번씩 질문
4. 모르는 조건은 다시 묻지 않고 해당 혜택을 보수적으로 제외
5. 조건 확인이 끝난 상품만 확정 Top 5로 표시
6. 각 상품에 실제 납입계획, 세전이자, 적용·미적용 조건과 근거 표시
```

LLM은 자연어를 typed delta로 바꾸고, 납입액·기간·금리·이자·Top 5는 Engine만 계산한다.

## 2. 먼저 고정할 의미

### 2.1 목표 납입액과 최대 납입 가능액은 다르다

| 사용자 표현 | 저장 의미 | Engine 동작 |
|---|---|---|
| `월 30만원 넣고 싶어` | `desired_periodic_amount=300000` | 목표 월납액 30만원에 가장 잘 맞는 상품 우선 |
| `월 30만원씩 정확히` | 목표액 30만원 + hard amount constraint | 정확히 수용하지 못하면 제외 |
| `최대 월 30만원` | `maximum_affordable_periodic_amount=300000` | 어떤 실제 cashflow bucket도 상한을 넘지 않음 |
| `월 20~30만원` | numeric range preference | 범위 안에서 이자·금리 목적에 맞춰 결정 |

`desired_periodic_amount`를 상품 한도에 맞춰 조용히 낮추지 않는다.

### 2.2 목표액 일치 정책

1. 월납·자유적립 상품이 목표액을 허용하면 정확한 목표액으로 계산한다.
2. 상품 최대액이 목표액보다 작으면 `TARGET_AMOUNT_UNSUPPORTED`로 기록한다.
3. 사용자가 `정확히`, `꼭`이라고 말했으면 해당 상품을 제외한다.
4. 일반적인 `넣고 싶어`는 목표 선호로 처리하되, 큰 차이는 Top 5에서 제외하거나 강하게 감점한다.
5. 기본 허용오차는 목표액의 ±5%로 한다. 이는 discrete option 또는 비월납 상품을 비교할 때만 사용한다.
6. 정확한 목표액을 지원하는 월납 상품이 있는데 근사 상품을 우선하지 않는다.
7. 근사치를 사용하면 카드에 목표액, 실제 월 환산액, 차이와 실제 스케줄을 표시한다.

30만원의 기본 허용 범위는 285,000~315,000원이지만, 월 30만원을 직접 선택할 수 있는 상품은 항상 300,000원으로 계산한다.

### 2.3 비월납 상품 비교

26주 적금, 31일 적금, 주간·매일 납입 상품의 1회 납입액을 월 30만원으로 오해하지 않는다.

```text
목표 총원금 = 목표 월납액 × 사용자가 선택한 기간의 월수
월 환산 납입액 = 상품 실제 cashflow 총원금 ÷ 실제 가입기간 월수
목표 차이율 = abs(월 환산 납입액 - 목표 월납액) ÷ 목표 월납입액
```

상품의 허용 option 중 목표 차이율이 가장 작은 실제 스케줄을 선택한다. 동률이면 affordability 위반이 없고 action burden이 낮은 option을 우선한다. 선택한 option과 날짜별 cashflow로 이자를 다시 계산한다.

### 2.4 희망 기간

- 12개월을 선택할 수 있는 60개월 범위 상품은 실제 12개월 계약으로 계산한다.
- 6개월 고정 상품처럼 희망 기간과 다른 상품은 자동으로 12개월로 바꾸지 않는다.
- 짧은 대안 상품을 허용하더라도 `기간 불일치 대안`으로 분리하고 확정 Top 5보다 뒤에 둔다.
- `1년만`, `정확히 1년`은 hard term constraint로 처리한다.

## 3. 단계별 구현 계획

### Phase 1 — 납입계획 정합성

가장 먼저 수정한다. 이후의 이자순·Top 5 검증이 이 계산에 의존한다.

#### 변경

- `ContributionPlanner`의 `min(desired, product_max)` silent clamp 제거
- contribution projection에 다음 정보 추가
  - `requested_periodic_amount`
  - `planned_periodic_amount`
  - `monthly_equivalent_amount`
  - `amount_difference`
  - `amount_difference_ratio`
  - `amount_match_status`: `EXACT / WITHIN_TOLERANCE / OUTSIDE_TOLERANCE / INPUT_REQUIRED`
- 월납 상품과 비월납 상품의 목표액 비교 방식 분리
- ranking에 목표액 적합도 tie-break/penalty 추가
- 목표액을 크게 벗어난 상품은 provisional 후보로만 유지하거나 hard 표현이면 retrieval 단계에서 제외
- 기간 match도 `EXACT / SELECTABLE_EXACT / ALTERNATIVE_SHORTER / MISMATCH`로 명시

#### 주요 파일

- `src/eligibility/search/contribution.py`
- `src/eligibility/search/retrieval.py`
- `src/eligibility/search/evaluation.py`
- `src/eligibility/search/ranking.py`
- `src/eligibility/schema/search.py`
- `src/eligibility/search/recommendation.py`

#### 완료 기준

- `월 30만원, 12개월`인 월납 가능 상품은 원금 3,600,000원으로 계산
- 상품 최대가 100,000원이면 100,000원으로 몰래 계산하지 않음
- 26주 적금은 선택한 시작금액의 실제 26회 cashflow를 월 환산해 목표액과 비교
- 카드에 `월 30만원 납입 계획` 또는 `목표 30만원 · 실제 월 환산 29만원`이 명확히 표시

### Phase 2 — 정렬 의미와 active question 안정성

#### 변경

- 모든 자연어 경로에 canonical mapping을 한 곳에서 적용
  - `이자금순`, `세전이자` → `MAX_ESTIMATED_PRE_TAX_INTEREST`
  - `세후이자` → `MAX_ESTIMATED_AFTER_TAX_INTEREST`
  - `금리순`, `실제 받을 금리` → `MAX_REALIZABLE_RATE`
- Intent parser와 Conversation orchestrator가 같은 deterministic normalizer 사용
- UI 토글은 현재처럼 LLM 없이 직접 enum PATCH
- 자연어 ranking-only patch도 ApplicationService가 동일하게 판별
- 현재 질문이 후보에서 완전히 무효가 되지 않는 한 question id와 말풍선 보존
- 정렬 변경으로 새로 계산된 다음 질문은 현재 질문에 답한 뒤 노출

#### 주요 파일

- `src/eligibility/llm/system_prompts.py`
- `src/eligibility/search/intent.py`
- `src/eligibility/application_service.py`
- `src/eligibility/web/static/app.js`

#### 완료 기준

- `이자금순으로 보여줘` 후 objective가 세전이자 enum
- UI 토글과 자연어 정렬 변경 모두 기존 active question id 보존
- 정렬 변경만으로 QuestionGeneration LLM을 다시 호출하지 않음

### Phase 3 — 질문 완료 상태와 확정 Top 5

`금융적으로 모름`과 `아직 사용자에게 묻지 않음`을 분리한다.

#### 변경

- 조건 확인 상태를 다음처럼 구분
  - `UNASKED`
  - `ANSWERED_TRUE`
  - `ANSWERED_FALSE`
  - `ACKNOWLEDGED_UNKNOWN`
  - `VERIFIED`
- `모르겠어요`는 `ACKNOWLEDGED_UNKNOWN`; 질문은 완료되고 다시 묻지 않음
- acknowledged unknown 혜택은 보수적으로 미적용하여 realizable rate/interest 계산
- `unresolved_question_count`와 `material_financial_uncertainty_count`를 별도 제공
- 질문 planner는 현재 Top K와 Top K 진입 가능 frontier에 영향을 주는 조건만 선택
- 답변으로 순위가 바뀌면 새 Top K entrant의 조건을 이어서 질문
- 다음 조건을 모두 만족해야 `confirmed_top_products` 생성
  - Top K 전 상품의 unresolved question 0
  - 필요한 contribution input 0
  - 순위 안정성 충족
- 그 전에는 API/UI 모두 `provisional_candidates`로 표현

#### 주요 파일

- `src/eligibility/search/questions.py`
- `src/eligibility/search/ranking.py`
- `src/eligibility/search/evaluation.py`
- `src/eligibility/schema/search.py`
- `src/eligibility/application_service.py`
- `src/eligibility/web/static/app.js`

#### 완료 기준

- `모르겠어요`로 넘긴 조건을 다시 질문하지 않음
- 확정 Top 5에는 미응답 질문이 없음
- acknowledged unknown이 있으면 `확인하지 않음 → 해당 우대 제외`를 상품 상세에 표시
- `Top 5`라는 제목은 confirmed 상태에서만 사용

### Phase 4 — 사용자 typed fact와 grounded 질문 설명

#### 변경

- 현재 상태와 변경 의향을 별도 fact로 저장
  - `CURRENT_SALARY_BANK`
  - `SALARY_ACCOUNT_CHANGE_POSSIBLE`
  - `CURRENT_CARD_PAYMENT_BANK`
  - `CARD_PAYMENT_ACCOUNT_CHANGE_POSSIBLE`
- 사전 질문 한 답변에서 복수 fact를 atomic하게 반영
- “AI가 이해한 조건”에 현재 은행과 변경 의향을 함께 표시
- active question 설명 payload 추가
  - 조건의 쉬운 정의
  - 대상 상품/은행
  - 충족 방법
  - 앱/공식 채널 확인 경로
  - 이벤트 대상·기간·수량
  - 데이터 기준일과 source
  - 현재 데이터로 알 수 없는 범위
- `EXPLAIN_ACTIVE_QUESTION`은 generic static explanation이 아니라 이 grounded payload를 설명
- 상품 데이터가 부족하면 사용자가 “공식 대상인지 직접 확인”하게 떠넘기지 않고 `KNOWLEDGE_GAP`으로 기록

#### 주요 파일

- `src/eligibility/schema/conversation.py`
- `src/eligibility/schema/search.py`
- `src/eligibility/application_service.py`
- `src/eligibility/search/questions.py`
- `src/eligibility/llm/system_prompts.py`
- Catalog product knowledge JSON

#### 완료 기준

- “현재 국민은행, 유리하면 변경 가능”이 두 개의 typed fact로 남음
- “그게 뭐예요?”에 11Pay 대상 결제와 확인 방법을 상품 source에 근거해 설명
- source에 없는 발급법·기간을 모델이 만들어내지 않음

### Phase 5 — LLM 비용과 응답속도

#### 변경

- Conversation prompt를 operation에 필요한 projection으로 축소
  - mutable intent delta 대상
  - active question
  - 최근 product focus
  - 허용 product/fact id
  - 최근 1~2턴 요약
- 43개 전체 evaluation, 전체 audit, 중복 working note는 prompt에서 제외
- 동일 `CanonicalQuestionPayload`는 session/request cache로 한 번만 생성
- quick answer 버튼은 typed `/answers` 사용, LLM 미호출
- ranking 토글은 typed `/intent` 사용, LLM 미호출
- 질문 문구는 생성 후 question id 기준 재사용
- token/latency budget을 자동 테스트 및 debug에 노출

#### 목표 지표

- Conversation orchestration 평균 prompt < 3,000 tokens
- 사용자 한 턴당 orchestration 최대 1회
- 사용자 한 턴당 QuestionGeneration 최대 1회
- quick answer/정렬 토글 LLM 호출 0회
- 일반 후속 턴 목표 5초 이내, deterministic 조작 500ms 이내

### Phase 6 — debug 정확성 및 크기

#### 변경

- `domain_schema`와 실제 전송된 `transport_schema` 분리
- OpenAI `text.format.schema`, `strict`, `store`, reasoning control을 포함한 실제 transport payload 표시
- API key/header는 계속 redaction
- schema는 hash로 deduplicate하고 call에는 schema reference만 저장
- Engine 최신 snapshot과 turn별 delta 분리
- 큰 evaluation 배열은 기본 접기와 product별 lazy endpoint 제공

#### 목표 지표

- debug 화면에서 lookaround 제거 전/후를 구분 가능
- 새 10턴 세션 debug bundle < 2MB
- session list polling에서 full bundle 다운로드 없음
- schema hash와 실제 transport payload를 이용한 회귀 테스트 추가

### Phase 7 — 문서와 운영 검증

#### 변경

- 로컬 가이드의 샘플 사용자 설명 제거
- 현재 테스트 수를 고정 숫자로 문서화하지 않고 실행 명령과 최근 검증일 기록
- 세전이자 UI, 후보/확정 Top 5, unknown 의미 반영
- background service와 `/debug` 사용법 연결
- 실제 OpenAI acceptance script 제공

## 4. 통합 Acceptance Test

### 시나리오 A — 목표 납입액

```text
1년 동안 월 30만원 넣고 싶어.
```

- 목표액 300,000원, 기간 12개월
- 30만원 지원 월납 상품은 계획액 300,000원, 원금 3,600,000원
- 최대 100,000원 상품은 exact match로 표시되지 않음
- 비월납 상품은 월 환산액과 실제 스케줄 표시

### 시나리오 B — 최대 상한

```text
월 최대 30만원까지 가능해.
```

- affordability만 300,000원
- Engine이 사용자의 목표액을 임의로 300,000원이라고 가정하지 않음
- 이자 계산에 목표액이 필요하면 추가 질문

### 시나리오 C — 정렬

```text
이자금순으로 보여줘.
세후이자로 바꿔줘.
금리순으로 보여줘.
```

- 각각 PRE_TAX / AFTER_TAX / REALIZABLE_RATE enum
- active question id 불변
- 한 턴에 orchestration 1회 이하, 질문 생성 중복 없음

### 시나리오 D — 사전 fact

```text
현재 국민은행으로 급여를 받고 있고, 더 유리하면 옮길 수 있어.
```

- 현재 은행과 변경 의향이 모두 typed state/chip에 표시
- 다른 은행으로 추론하거나 현재 은행을 잃지 않음

### 시나리오 E — 질문 설명과 unknown

```text
그 특별금리 쿠폰이 뭐예요? 어디서 받고 어떻게 확인해요?
모르겠어요.
```

- 상품·은행·대상·발급/확인 경로를 source 범위에서 설명
- 모르는 데이터는 명시
- unknown은 false가 아니며 질문은 완료
- 해당 우대는 보수적으로 제외하고 다시 묻지 않음

### 시나리오 F — Top 5 수렴

- provisional 상태에서는 `후보 5개`
- 질문과 contribution input이 모두 완료된 뒤에만 `Top 5`
- Top 5 전 상품의 `unresolved_question_count=0`
- 모든 카드의 목표/실제 납입액, 기간, 세전이자가 계산 또는 명시적 불가 사유를 가짐

## 5. 구현 순서와 배포 단위

| 순서 | 배포 단위 | 선행 조건 | 사용자에게 보이는 결과 |
|---:|---|---|---|
| 1 | 납입액·기간 정합성 | 없음 | 30만원 요청이 실제 30만원 계획으로 계산 |
| 2 | 정렬/질문 안정성 | 1 | 정렬 의미 정확, 말풍선 유지 |
| 3 | Top 5 수렴 의미론 | 1, 2 | 후보와 확정 추천 구분 |
| 4 | typed fact/질문 설명 | 3 | AI가 사용자 상태와 상품 조건을 제대로 설명 |
| 5 | LLM 최적화 | 2~4의 payload 확정 | 대기시간과 비용 감소 |
| 6 | debug 개선 | 5의 transport 구조 확정 | 실제 prompt/payload를 정확하고 가볍게 확인 |
| 7 | 문서/최종 acceptance | 1~6 | 재현 가능한 제출본 |

각 배포 단위는 기존 423개 회귀 테스트와 해당 phase의 실제 OpenAI acceptance test를 모두 통과한 뒤 다음 단계로 넘어간다.

## 6. 이번 계획의 기본 결정

- 월 목표액의 근사 허용오차는 우선 ±5%
- exact 지원 상품은 항상 exact 금액 사용
- unknown 답변은 질문 완료지만 우대 혜택은 보수적으로 제외
- provisional candidates와 confirmed Top 5를 API부터 분리
- UI quick action은 가능한 한 LLM을 거치지 않음
- 실제 OpenAI 전송 payload를 debug의 최종 진실로 취급

허용오차는 추후 사용자 설정으로 열 수 있지만 MVP에서는 위 기준을 deterministic 상수로 고정한다.
