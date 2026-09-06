# 상위 후보 우대조건의 실시간 의미 분석 (실험적 MVP)

## 범위와 활성화

이 기능은 기존 deterministic pre-search 질문 순서와 실제 금융 계산기를 교체하지 않습니다.
사전 질문이 끝난 뒤 현재 추천과 optimistic challenger의 **정형화되지 않은 우대조건**을
한 묶음으로 분석하고, 기존 `RankingAwareQuestionPlanner`에 공통 missing fact를 추가합니다.
전체 카탈로그에 대한 LLM 전처리나 데이터 재발행은 하지 않습니다.

기본값은 꺼짐입니다. 기존 LLM 설정과 함께 다음을 설정하고 Web 서버를 재시작합니다.

```bash
export ELIGIBILITY_SEMANTIC_CONDITIONS=1
eligibility-web
```

PowerShell에서는 `$env:ELIGIBILITY_SEMANTIC_CONDITIONS = "1"`을 사용합니다.
실제 LLM gateway가 없으면 활성화되지 않습니다. 별도 API 키나 모델을 요구하지 않고
기존 gateway의 `SEMANTIC_CONDITION_COMPILATION` 목적을 사용합니다.
원문과 structured-output이 많아질 수 있으므로 공급자의 토큰 제한과 비용을 확인해야 합니다.
질문 생성이 완료되기 전 로딩은 MVP에서 동기적으로 처리합니다.

## 실행 경로

1. 기존 사전 질문과 조건 수정, 후보 필터, typed evaluation을 그대로 실행합니다.
2. 기존 ranking frontier, 요청한 Top-K, 비교 가능한 upper가 없는 후보를 확인합니다.
3. 후보의 canonical `preferential_policy.rules` 중 runtime adapter가 없는 rule과
   명시적인 `SOURCE_CLAUSE_GATE` boolean placeholder를 수집합니다. genuinely typed
   comparison은 분석 대상이 아니며 read-only 참고 정보로만 모델에 전달합니다.
   카드·급여·첫거래·마케팅으로 분류되었다는 이유만으로 분석을 건너뛰지 않습니다.
   `SOURCE_CLAUSE_GATE`와 금액·기간·대체 경로가 있는 원문은 기존 질문 답을 재사용하면서
   컴파일러가 구조를 읽습니다. 새 질문 칸은 만들지 않습니다.
4. 원문과 기존 typed rules를 함께 공급하고, 고정된 표현식 언어로 해석합니다.
5. 전체 batch의 원문 hash, clause ID, 인용문, 타입, 숫자 근거, 깊이/크기를 검증합니다.
6. 원본 상품은 보존하고 세션의 임시 overlay에 해석을 적용합니다. 금리, 이자, 자격,
   우대 합산 및 순위는 기존 엔진이 계산합니다.
7. 기존 typed `CHILD_COUNT` 등과 AI 조건이 공통 사용자 입력을 공유하도록 기존 질문
   플래너에서 묶습니다. 답변 수정/삭제 때 파생 facts를 새로 만들며 별도로 영구 저장하지 않습니다.

한 write command에서 기본 최대 **8상품 / 48원문 / 50,000문자**를 분석합니다.
이번 호출 창은 `max(3K, 12)`의 **아직 읽지 않은** 도전 가능 후보이고, 이미 분석한
상품이 창을 계속 차지하지 않습니다. 창 밖 미분석 후보는 `pending_product_count`에
남기고 `COMPLETE`로 바꾸지 않습니다. 새 답변 또는 명시적 계속 명령으로 다음 batch를
처리합니다.
세션 내 source cache는 상품 버전, source metadata/hash, typed context, compiler/prompt/model/schema
식별자를 포함합니다. 사용자 정보는 compiler prompt/cache에 넣지 않습니다. 현재 MVP는
persistent cross-session cache와 비동기 worker를 포함하지 않습니다.

## 지원하는 조건과 보수적 처리

표현식은 ALL/ANY/NOT, 자녀 존재/인원 비교, 가입일 또는 기준일의 나이/출생 범위,
혼인일 비교, 가입자 본인의 현재 임신 여부, UNKNOWN으로 제한됩니다.
공통 사용자 입력은 CHILDREN, MARRIAGE_DATE, PREGNANT_SELF 세 종류입니다.
기존 다른 typed 조건(급여, 카드 등)은 원래 엔진/플래너에 남습니다.

- 자녀 수만 필요하면 생년월일을 묻지 않습니다.
- 출생연도만 받았는데 경계에 걸리면 정확한 날짜를 추가 질문합니다. 1월 1일로 추정하지 않습니다.
- 목록이 일부이면 전체 자녀가 없다고 판단하지 않습니다. `complete=true`는 명시적 전체 목록에만 사용합니다.
- “미성년”, “다자녀”의 정의가 원문에 없으면 임계값을 추정하지 않습니다.
- 임신 본인/배우자, 기존 자녀/향후 출산, 서류 제출/기관 승인을 서로 구분합니다.
- 기관 확인이 필요한 조건은 사용자 응답만으로 SATISFIED/confirmed가 되지 않습니다.
- 원문이 공급한 고정 ADD_RATE/BONUS_RATE의 percentage-point 보상만 지원합니다.
  모델은 보상 숫자, 사용자 facts, 상품 제외, 순위 또는 실행 코드를 만들 수 없습니다.
- 불명확한 논리 가지, 복잡한 거래/서류/승인/가입기간 중 사건과 미지원 보상은 UNKNOWN으로 유지합니다.
- `SOURCE_CLAUSE_GATE`는 원래 비교·보상·근거 정책을 유지한 채 guard를 추가합니다.
  같은 우대를 두 번 합산하거나 generic “예” 답변으로 미해석 조건을 충족시키지 않습니다.
  카드 의향 거절은 결제금액 미달을 입증하지 않고, 급여 의향 거절은 가맹점 입금 경로를
  지우지 않습니다. 알 수 없는 대체 경로를 정규식 목록에 없다고 해서 필수로 보지 않습니다.
- 자식이 12개를 넘는 ALL/ANY 식은 가지를 잘라 적용하지 않고 해당 절을 보류합니다.
- 일반 structured schema/숫자/인용문 검증만으로 자연어 해석의 정확성을 증명할 수는 없습니다.
  AI 해석이 사용된 추천은 실험적 잠정 결과로 표시합니다.

**전체 비정형 원문의 완전한 coverage를 보장하지 않습니다.** canonical preferential-policy 외의
standalone disclosure/custom 텍스트 intake, 새로운 ontology, 개인별 세금·복리, 영구 캐시,
동시 요청 직렬화는 별도 확장 대상입니다. `COMPLETE`는 도전 가능한 미분석 패킷이 더 없을 때입니다.
창 밖에 읽지 않은 후보가 있으면 `PENDING`/`PARTIAL`을 유지합니다. 상품의 모든 조건이나
금융기관 승인이 검증되었다는 뜻은 아닙니다.

## API / UI

목록 응답 `semantic_review`:

- `status`: DISABLED / DEFERRED / PENDING / COMPLETE / FAILED
- `pending_product_count`, `unresolved_clause_count`, `reviewed_product_ids`
- `ai_interpreted`, `calls_this_turn`, `error_code`

계속 분석 또는 실패 재시도는 **명시적 POST**입니다.

```text
POST /api/search-sessions/{id}/semantic-review
{"retry_failed": false}
```

실패 재시도는 JSON boolean `true`를 사용합니다. 문자열 `"false"`는 거부합니다.
Web 목록에 계속/재시도 버튼과 잠정 상태 설명이 추가됩니다.
GET state/recommendations/questions/detail/preview는 compiler를 호출하거나 캐시를 변경하지 않습니다.
기존 active-question 자연어 답변은 `/messages`의 엄격한 AnswerPlan을 사용합니다.
직접 `/answers`를 호출하는 클라이언트의 자녀 입력 예:

```json
{"answer": {"count": 2, "children": [{"birth_year": 2016}, {"birth_year": 2020}], "complete": true}}
```

미확인/응답 거절은 기존 unknown/skip으로 처리합니다. 자녀 0명과 같지 않습니다.
수정은 기존 answers PATCH 및 fact revision/clear 경로를 사용합니다.

## 검증

`tests/test_semantic_preferential_questions.py`는 실제 ApplicationService, 금융 엔진,
ranking/question planner, REST/TestClient를 사용합니다. 외부 LLM만 scripted adapter로 대체합니다.
실제 애(愛)랑해적금 source-clause gate도 활성 index에서 읽어 intake를 검증합니다.

검증 항목: typed+raw 공통 질문, 금리 재계산, 원본 카탈로그 불변, 나이 경계,
부분 자녀 목록, ALL/ANY의 UNKNOWN, 기관 근거 우선순위, compile 실패와 검증 거부,
batch 후순위 후보 유지/진행, source/model cache 변경, 답변 수정/삭제/rollback,
반복 GET 불변, structured message→엔진 연결, 기관 승인과 사용자 응답의 분리.
실제 외부 모델의 오해석률/지연/토큰 비용 및 실브라우저 E2E는 별도 검증이 필요합니다.
