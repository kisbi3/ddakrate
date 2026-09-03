# 유연한 대화 턴·문맥·Decision Ledger 설계

- 작성일: 2026-08-28
- 상태: 1차 구현 완료 (2026-08-28)
- 범위: 사전질문과 상품별 질문 도중 들어오는 자유발화, 전역 조건, 질문 재노출, 정정·취소, 사용자 응답 생성
- 기준: 이 문서는 구현 목표 계약이다. 현재 런타임의 동작을 설명하는 문서는 [`PRE_SEARCH_QUESTION_DESIGN.md`](PRE_SEARCH_QUESTION_DESIGN.md)다.

구현 위치:

- 단일 턴 schema: `src/eligibility/schema/conversation.py`
- 단일 LLM 호출과 context projection: `src/eligibility/conversation.py`
- 원자적 실행·질문 표시·feature policy 통합: `src/eligibility/application_service.py`
- append-only ledger: `src/eligibility/conversation_ledger.py`
- 정규화 feature 파생: `src/eligibility/catalog/normalized_loader.py`
- feature filtering helper: `src/eligibility/search/feature_policy.py`

후속 구현 계획:

- 상위 추천상품의 비구조화 가입조건 원문을 별도 LLM purpose로 검수하고 기존 `EvaluationStatus`에 결합하는 계획은 [`TOP_RANKED_ELIGIBILITY_TEXT_LLM_REVIEW_PLAN.md`](TOP_RANKED_ELIGIBILITY_TEXT_LLM_REVIEW_PLAN.md)를 따른다.

## 1. 목적

사용자는 현재 표시된 질문에만 답하지 않는다. 질문을 무시하고 검색조건을 바꾸거나, 다른 전역 자격정보를 먼저 말하거나, 특정 상품을 제외하거나, 이전 결정을 정정할 수 있다.

이 설계의 목표는 한 자연어 발화에 포함된 모든 명시적 의도를 한 턴에서 처리하는 것이다.

```text
현재 질문에 대한 답변
+ 다른 사전질문에 대한 선제 답변
+ 전역 상품 특성 선호·제외
+ 특정 상품 제외·복원
+ 금액·기간·기관·정렬 조건 변경
+ 과거 결정의 정정·취소
+ 이번 응답에서 질문을 다시 표시할지 여부
+ 자연스러운 사용자 응답 문장
```

`NOT_AN_ANSWER`는 "현재 질문에 답하지 않았다"는 뜻이다. 사용자 발화 전체를 무시하거나 `NO_OP`으로 만들라는 뜻이 아니다.

## 2. 최종 합의

다음 결정은 구현 시 다시 선택하지 않고 이 문서의 계약으로 사용한다.

1. LLM은 한 번 호출한다.
2. 한 번의 LLM 출력에 상태 변경 계획, 자연어 응답, 이번에 표시할 질문을 함께 담는다.
3. LLM은 현재 질문을 다시 물을지, 미룰지, 다른 pending 질문을 제시할지, 관련 상품을 제외하고 더 묻지 않을지 자율적으로 선택한다.
4. LLM은 사용자 표현을 특정 상품에만 적용할지, 같은 특성을 가진 상품 전체에 적용할지 자율적으로 선택한다.
5. 같은 발화 안에서 의미가 충돌해 보여도 backend가 별도 자연어 규칙으로 재해석하지 않는다. LLM이 적용안을 선택하거나 필요한 경우 다시 묻는다.
6. Backend는 사용자용 성공 메시지를 결정론적인 문장 템플릿으로 만들지 않는다.
7. Backend는 구조, 대상 ID, 권한, 금융 불변조건과 원자성만 검증한다.
8. 과거 LLM의 원출력이나 reasoning은 다음 턴의 권위 있는 상태가 아니다. Backend 검증과 실행을 통과한 결정만 Decision Ledger에 남긴다.
9. 현재 상태는 매 턴 backend가 구조화된 snapshot으로 다시 만든다.
10. 과거는 처음부터 누적된 검증 완료 Decision Ledger로 제공한다.
11. 최근 자연어 대화와 사용자가 실제로 본 질문·상품은 별도의 visible context로 제공한다.
12. 미해결 필수 질문이 있어도 잠정 후보는 볼 수 있다. 확정 추천과 잠정 후보를 구분한다.
13. 구현과 필수 테스트에는 `gpt-5.6-luna`, reasoning effort `low` subagent들을 병렬 활용한다. 최종 통합과 전체 회귀는 메인 agent가 책임진다.

## 3. 책임 경계

### 3.1 LLM이 담당하는 것

- 한 발화에서 복수 의도를 찾는다.
- 현재 질문에 대한 답과 다른 정보를 분리한다.
- 질문 순서와 무관하게 사용자가 자발적으로 말한 전역정보를 구조화한다.
- 특정 상품, 같은 특성의 상품군, 검색 세션 전체 중 적용 범위를 선택한다.
- `이거`, `그런 거`, `아까`, `처음 조건` 같은 문맥 표현을 해석한다.
- 같은 발화의 충돌을 해석하거나 clarification을 선택한다.
- 현재 질문을 다시 표시할지, 미룰지, 다른 질문을 표시할지 선택한다.
- 실행 예정인 변경을 근거로 자연스러운 사용자 응답을 작성한다.

### 3.2 Backend가 담당하는 것

- strict schema를 검증한다.
- operation, feature ID, product ID, question ID와 decision ID를 검증한다.
- authoritative fact 변경을 차단한다.
- 가입 자격, 금리, 이자, 순위와 결과 안정성을 계산한다.
- 모든 상태 변경을 한 changeset으로 원자적으로 적용한다.
- 하나라도 실패하면 전체 상태와 ledger 기록을 롤백한다.
- 실제 실행이 성공한 경우에만 LLM이 작성한 성공 메시지를 노출한다.
- 현재 snapshot과 검증 완료 Decision Ledger를 만든다.

### 3.3 Backend가 하지 않는 것

- 사용자 문장의 자연어 의미를 정규식 목록으로 재해석하지 않는다.
- 모든 경우의 사용자 응답 문장을 하드코딩하지 않는다.
- `NOT_AN_ANSWER`만으로 사용자 발화를 버리지 않는다.
- 과거의 취소된 값이 현재 snapshot을 덮어쓰게 하지 않는다.
- LLM이 질문을 이번에 표시하지 않았다는 이유로 질문을 해결 완료 처리하지 않는다.

## 4. 한 턴의 처리 흐름

```text
1. Backend가 ConversationContextEnvelope 생성
2. LLM이 FlexibleConversationTurnPlan 한 번 반환
3. Backend가 전체 plan을 dry validation
4. Backend가 profile update와 action을 하나의 changeset으로 적용
5. 전역 fact materialize
6. 영향받는 후보를 재평가·재정렬
7. Decision Ledger에 검증 완료 changeset append
8. LLM의 assistant_message와 선택된 question을 응답으로 노출
```

실패 흐름은 다음과 같다.

```text
plan validation 실패 또는 action 실행 실패
→ business state 변경 없음
→ Decision Ledger append 없음
→ LLM이 작성한 성공 메시지 노출 없음
→ 재시도 가능한 시스템 오류
```

## 5. 단일 LLM 출력 계약

목표 스키마의 개념형은 다음과 같다.

```python
class FlexibleConversationTurnPlan:
    profile_updates: list[PreSearchProfileUpdate]
    actions: list[ConversationAction]
    assistant_message: str
```

### 5.1 `profile_updates`

현재 active question에 한정하지 않는다. 사용자가 명시적으로 말한 사전 프로필 사실을 질문 순서와 무관하게 여러 개 담을 수 있다.

```json
{
  "profile_updates": [
    {
      "question_key": "YOUTH_POLICY_ACCOUNT_HOLDING",
      "resolution": "ANSWER",
      "youth_policy_account_held": false
    },
    {
      "question_key": "SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY",
      "resolution": "ANSWER",
      "soldier_tomorrow_savings_eligible": true
    }
  ]
}
```

동일한 `question_key`를 한 턴에서 상충하는 값으로 두 번 반환하면 plan validation 실패로 처리한다. 자연어 의미 충돌 자체는 LLM이 해석하거나 질문해야 하지만, 최종 structured plan은 모순이 없어야 한다.

### 5.2 `actions`

기존 좁은 domain operation을 유지하면서 다음 operation을 추가한다.

```text
SET_PRODUCT_FEATURE_POLICY
CLEAR_PRODUCT_FEATURE_POLICY
REVERT_DECISIONS
```

LLM이 자유로운 필드명이나 임의 DSL을 만들도록 허용하지 않는다. 조절 가능한 feature ID와 operation은 context의 allowlist에 있는 값만 사용한다.

### 5.3 `assistant_message`

LLM이 plan과 같은 호출에서 작성한다. Backend는 이 문장을 실행 성공 후에만 반환한다.

다음 원칙을 prompt에 고정한다.

- 자신이 반환한 update와 action만 반영됐다고 말한다.
- 가입 가능성, 금리, 이자, 순위를 새로 계산하지 않는다.
- 제공되지 않은 상품이나 조건을 언급하지 않는다.
- deterministic workflow의 질문 ID나 문구를 생성하지 않는다.
- 다음 질문의 선택과 표시는 Backend가 담당한다.

## 6. 질문 lifecycle

현재 질문에 답하지 않은 경우에도 질문 lifecycle은 Backend가 관리한다.

```text
REASK_NOW
SHOW_CURRENT_RESULTS
SKIP_ACTIVE_QUESTION
```

표시 예외는 명시적 action으로만 표현한다. 금융 의미는 다음 불변조건을 따른다.

- `SHOW_CURRENT_RESULTS`로 이번 응답에서 질문을 표시하지 않아도 질문은 pending으로 유지한다.
- 필수 질문을 답하지 않았다고 가입 가능으로 간주하지 않는다.
- 미해결 사실이 결과에 영향을 주면 해당 상품은 잠정 후보 또는 확인 필요 상태다.
- 관련 상품을 확정 추천에서 제외하는 action을 실행하면 질문을 더 표시하지 않을 수 있다.
- 질문을 건너뛰려면 `SKIP_ACTIVE_QUESTION`을 명시적으로 실행한다.

### 6.1 청년 정책계좌 질문

미응답이면 청년미래적금 관련 상품을 잠정 목록에서 `확인 필요`로 표시할 수 있다. 확정 추천에는 넣지 않는다.

### 6.2 장병내일준비적금 질문

다음 전역 불변조건은 대화 자율성보다 우선한다.

```text
현역병, 상근예비역, 의무경찰, 대체복무요원, 사회복무요원 중 하나
→ true

부정, 모름, 확인 불가
→ false
→ 모든 장병내일준비적금 가입 자체 UNSATISFIABLE
```

질문을 무시하고 다른 말만 한 것은 `모름` 답변이 아니다. 이 경우 자격 fact는 미해결로 유지하고 LLM이 질문 재노출 시점을 결정한다.

## 7. Conversation context

LLM에는 원문 전체를 무제한 누적하지 않고 다음 envelope를 제공한다.

```text
SESSION_ORIGIN
CURRENT_STATE_SNAPSHOT
DECISION_LEDGER
PENDING_WORKFLOW
VISIBLE_DIALOGUE
REFERENCED_PRODUCT_EVIDENCE
ALLOWED_OPERATIONS
```

### 7.1 `SESSION_ORIGIN`

세션 최초 입력과 그 출처를 보존한다.

```json
{
  "original_utterance": "매달 30만원씩 넣을 적금 찾아줘",
  "initial_product_types": ["INSTALLMENT_SAVINGS"],
  "subscription_date": "2026-08-28",
  "initial_contribution_plan": {
    "desired_periodic_amount": 300000,
    "frequency": "MONTHLY"
  }
}
```

각 값은 다음 authority 중 하나를 가진다.

```text
USER_EXPLICIT
LLM_INFERRED
BACKEND_DERIVED
AUTHORITATIVE
```

### 7.2 `CURRENT_STATE_SNAPSHOT`

현재 적용 중인 유일한 권위 있는 mutable state다.

```text
intent
pre_search_profile
global_facts
feature_policies
excluded_product_ids
product_contribution_choices
pending_questions
current_candidates
result_stability
```

충돌 시 우선순위는 다음과 같다.

```text
CURRENT_STATE_SNAPSHOT
> ACTIVE Decision Ledger entries
> 과거 visible dialogue
> working note
```

### 7.3 `DECISION_LEDGER`

과거 LLM의 원출력이 아니라 backend 검증과 실제 적용을 통과한 결정만 처음부터 기록한다.

```json
{
  "decision_id": "DEC-00042",
  "changeset_id": "CHG-00012",
  "turn_id": 4,
  "operation": "SET_PRODUCT_FEATURE_POLICY",
  "target": "LOTTERY_BASED_BENEFIT",
  "before": null,
  "after": "EXCLUDE",
  "record_status": "ACTIVE",
  "reversible": true,
  "source_text": "확률적인 거 싫어"
}
```

다음 변경을 ledger에 남긴다.

- 전역질문 답변과 정정
- 검색조건 추가·변경·제거
- 상품 제외·복원
- feature policy 추가·변경·제거
- 상품별 납입 선택과 정정
- 질문 표시·연기·거절
- clarification 결과

금리 계산 중간값, 전체 카탈로그 평가 trace처럼 대화 해석에 불필요한 내부 이벤트는 context ledger에서 제외한다.

### 7.4 `PENDING_WORKFLOW`

한 개의 active question뿐 아니라 미해결 질문 후보를 제공한다.

```json
{
  "question_id": "PRESEARCH-YOUTH_POLICY_ACCOUNT_HOLDING",
  "status": "PENDING",
  "mandatory": true,
  "last_presented_turn": 3,
  "deferred_count": 1,
  "affected_scope": "YOUTH_FUTURE_SAVINGS"
}
```

LLM은 제공된 질문 중 하나를 선택하거나 이번에는 아무것도 표시하지 않을 수 있다.

### 7.5 `VISIBLE_DIALOGUE`

사용자가 실제로 본 최근 3~5턴을 제공한다.

```text
recent user messages
recent assistant messages
last displayed question
last displayed products
```

Backend가 내부에서 평가했지만 사용자에게 보여주지 않은 상품을 `이 상품`, `이거`의 지시 대상으로 사용하지 않는다.

### 7.6 `REFERENCED_PRODUCT_EVIDENCE`

다음 상품만 상세 근거를 제공한다.

- 사용자가 실제로 본 상품
- 표시된 질문이 직접 가리키는 상품
- 사용자가 이름으로 명시한 상품
- 직전 응답에서 설명한 상품

전체 카탈로그와 전체 evaluation trace를 매 턴 보내지 않는다.

### 7.7 context 크기 관리

```text
현재 snapshot: 항상 전체 유지
최근 visible dialogue: 기본 5턴
ACTIVE ledger entry: 항상 유지
SUPERSEDED entry: decision reference 중심으로 압축
pending 질문 관련 이력: 해결될 때까지 유지
과거 원문: 명시적인 과거 참조가 있을 때 선택적으로 확장
```

권위 있는 ledger를 LLM 자연어 요약에 맡기지 않는다. Backend가 타입별로 결정론적으로 압축한다.

## 8. 전역 상품 feature policy

첫 지원 feature는 다음과 같다.

```text
LOTTERY_BASED_BENEFIT
```

우리 두근두근 행운적금의 정규화 데이터는 다음 구조를 가진다.

```text
return_policy.preferential_application.mode = CUMULATIVE_LOTTERY
```

Runtime loader는 이 공식 구조를 다음 typed feature로 투영한다.

```text
ProductFeature(
  feature_id="LOTTERY_BASED_BENEFIT",
  present=true
)
```

`variable_rate=true`만으로 추첨형 상품을 판정하지 않는다. 시장금리 연동 변동금리와 확률형 우대조건을 혼동할 수 있기 때문이다.

LLM이 선택할 수 있는 정책은 다음과 같다.

```text
EXCLUDE
PREFER_ABSENT
ALLOW
```

사용자 표현별 scope와 정책은 LLM이 visible context와 발화 전체를 보고 선택한다. Backend는 이를 한국어 키워드 규칙으로 덮어쓰지 않는다.

## 9. 정정과 취소

LLM 오해 전용 명령이나 모호한 `UNDO_LAST_ACTION`을 만들지 않는다. 검증 완료 ledger의 정확한 결정을 대상으로 하는 범용 operation을 사용한다.

```json
{
  "operation": "REVERT_DECISIONS",
  "decision_ids": ["DEC-00042"]
}
```

Backend는 다음을 검증한다.

- context에 제공된 decision ID인가
- `reversible=true`인가
- authoritative decision이 아닌가
- 이미 superseded 또는 reverted되지 않았는가
- 현재 상태에서 before 값 복원이 유효한가

Revert는 기존 ledger row를 삭제하지 않는다. 새 reversal decision을 append하고 이전 row를 superseded 상태로 연결한다.

예:

```text
사용자: 아니, 확률형 전체를 빼라는 게 아니라 우리 두근두근만 빼달라는 거야.

LLM plan:
1. REVERT_DECISIONS(전역 추첨형 제외 decision)
2. SET_PRODUCT_EXCLUSION(우리 두근두근 행운적금)
```

단순히 새 값으로 바꾸는 발화는 revert보다 직접 update/revise operation을 우선한다.

## 10. 결과 표시

미해결 질문이 있어도 후보를 볼 수 있다.

- 가입 불가능이 확정된 상품은 제외한다.
- 미해결 필수 사실이 영향을 주는 상품은 `확인 필요` 또는 잠정 후보로 표시한다.
- 확정 Top K와 잠정 후보를 구분한다.
- 사용자가 결과를 먼저 요청하면 질문 상태는 pending으로 유지한다.
- `SHOW_CURRENT_RESULTS` 응답에서 질문을 표시하지 않더라도 결과 안정성 계산은 바뀌지 않는다.

## 11. 감사와 원자성

한 자연어 턴은 하나의 `changeset_id`를 가진다.

```text
LLM plan received
→ plan validated
→ mutations staged
→ pipeline recalculated
→ changeset committed
→ response exposed
```

Audit에는 최소한 다음을 남긴다.

- message hash와 turn ID
- context schema version과 context hash
- structured plan
- 검증 결과
- 실행된 decision ID 목록
- supersede/revert 연결
- pipeline run ID
- 표시한 question ID 또는 `null`
- business state commit 여부

## 12. 구현 작업 분해

### 12.1 메인 agent 작업

1. conversation context와 ledger schema 확정
2. 단일 LLM structured output 계약 구현
3. turn changeset validation과 원자적 실행 구현
4. pending state와 question presentation 분리
5. 전역 profile update의 순서 독립 적용
6. feature policy와 revert operation 통합
7. 전체 회귀와 최종 코드 리뷰

### 12.2 Luna subagent 활용 계약

구현 시 다음 세 작업을 `gpt-5.6-luna`, reasoning effort `low`로 병렬 위임한다. 각 subagent는 서로 다른 테스트 파일 또는 명확히 분리된 영역만 수정한다.

#### Luna A: 상품 feature와 필터

- `CUMULATIVE_LOTTERY → LOTTERY_BASED_BENEFIT`
- 우리 두근두근 행운적금 feature 확인
- 전역 `EXCLUDE`, soft preference, 취소 후 복원
- 일반 적금 오탐 방지

#### Luna B: 자유발화와 질문 lifecycle

- 질문에 답하지 않고 다른 조건 변경
- 질문 답변과 다른 action 동시 적용
- 다른 전역질문 선제 답변
- `SHOW_CURRENT_RESULTS`와 pending 유지
- deterministic 질문 자동 재노출

#### Luna C: ledger·정정·안전성

- `REVERT_DECISIONS`
- 여러 action의 원자적 rollback
- 허용되지 않은 feature/product/question/decision ID 거부
- 장병 `모름 → false → UNSATISFIABLE`
- 과거 ledger가 current snapshot을 덮어쓰지 않음
- 긴 세션 ledger 압축 전후 동일 의미

Subagent 결과는 메인 agent가 다음 기준으로 통합한다.

- 테스트가 구현 불변조건을 검증하는지 확인
- 중복 또는 상충 테스트 정리
- 전체 suite 실행
- 실제 prompt eval과 deterministic stub 테스트를 구분해 보고

## 13. 필수 테스트 행렬

### 13.1 자연어와 scope

- `나 확률적인 거 싫어`
- `이 상품만 빼줘`
- `이런 상품은 전부 빼줘`
- `가능하면 추첨형 말고`
- `확률형 제외한 거 취소`
- `처음 조건으로 돌아가자`

### 13.2 전역질문과 복합발화

- 청년 질문 중 추첨형 제외만 말함
- 청년 답변과 추첨형 제외를 함께 말함
- 생년월일 질문 중 장병 자격을 먼저 말함
- 장병 질문에 모름이라고 말하면서 다른 조건도 변경함
- 답하지 않은 질문은 pending으로 남음

### 13.3 표시와 결과

- Backend가 이전 질문을 즉시 재표시
- `SHOW_CURRENT_RESULTS`일 때만 이번 응답의 질문 표시 생략
- LLM이 pending 질문을 선택하거나 재정렬하지 못함
- pending이 있어도 잠정 결과 표시
- pending 관련 상품이 확정 추천에 잘못 포함되지 않음

### 13.4 정정·오류·원자성

- 전역 제외를 특정 상품 제외로 정정
- 여러 결정 중 하나만 revert
- 이미 reverted된 decision 재사용 거부
- 하나의 invalid action 때문에 전체 changeset rollback
- 실패한 plan의 assistant message 미노출
- 한 턴에서 pipeline 한 번만 실행

### 13.5 context 안정성

- 화면에 보이지 않은 상품을 `이거`로 연결하지 않음
- 최신 snapshot이 과거 발화를 이김
- superseded ledger가 active state로 해석되지 않음
- 최근 원문이 잘려도 `처음 조건으로`가 Session Origin으로 복원됨
- context compaction 전후 structured plan 의미가 동일함

## 14. 구현 완료 기준

다음 조건을 모두 만족해야 구현 완료다.

1. 사용자가 현재 질문에 답하지 않아도 다른 명시적 요청이 사라지지 않는다.
2. 한 발화의 여러 전역·상품별 변경이 한 changeset으로 반영된다.
3. 질문을 이번에 표시하지 않아도 pending 상태가 유지된다.
4. LLM이 질문의 재노출, 적용 scope, clarification과 응답 문장을 자율적으로 선택한다.
5. Backend는 허용된 구조와 금융 불변조건만 검증한다.
6. 과거 문맥은 검증 완료 ledger와 현재 snapshot을 통해 제공된다.
7. 잘못된 action이 포함되면 상태, ledger, 성공 메시지가 모두 커밋되지 않는다.
8. 장병 자격의 부정·모름·확인 불가는 모든 장병내일준비적금에 `UNSATISFIABLE`로 적용된다.
9. 청년 정책계좌와 장병 자격 답변은 관련 상품 전체가 공유한다.
10. Luna low-effort subagent 테스트와 메인 전체 회귀가 통과한다.

## 15. 비목표

- LLM에게 임의의 금융 규칙이나 DSL을 생성하게 하는 것
- 과거 LLM reasoning을 저장하거나 재사용하는 것
- 전체 상품 원문과 전체 evaluation trace를 매 턴 전송하는 것
- Backend가 모든 사용자 응답 문장을 템플릿으로 작성하는 것
- 미확인 사실을 LLM의 자신감으로 참·거짓 판정하는 것
- 확률형 우대의 기대금리를 임의로 계산해 확정금리처럼 순위화하는 것
