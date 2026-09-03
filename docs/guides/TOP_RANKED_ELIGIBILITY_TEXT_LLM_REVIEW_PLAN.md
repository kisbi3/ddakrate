# 상위 추천상품 `eligibility_text` LLM 가입조건 검수 구현 계획

- 작성일: 2026-08-28
- 상태: 핵심 구현 완료·운영 배포 전 (2026-08-28)
- 범위: 예비 순위 3위 이내 상품의 가입조건 원문 검수, 기존 자격평가와의 결합, 추가 질문, 재정렬, 사용자 메시지
- 관련 설계: [`FLEXIBLE_CONVERSATION_CONTEXT_AND_LEDGER_DESIGN.md`](FLEXIBLE_CONVERSATION_CONTEXT_AND_LEDGER_DESIGN.md)

구현 기준 파일:

- `src/eligibility/schema/eligibility_text_review.py`: strict 입출력 계약
- `src/eligibility/eligibility_text_review.py`: packet, grounding 검증, 캐시 키, 보수적 결합
- `src/eligibility/application_service.py`: Top 3 반복 frontier와 질문·추천 상태 통합
- `src/eligibility/llm/system_prompts.py`: 원문 전체 검수 전용 정책
- `tests/test_top_ranked_eligibility_text_review.py`: 실제 Sh 상품, rollback, 재진입, round 제한 회귀

## 1. 목적

정규화 상품에는 구조화된 `eligibility_rule`과 사람이 읽는 `eligibility_text`가 함께 존재할 수 있다. 현재 엔진은 구조화 규칙만 실행하므로, `eligibility_text`에 필수 가입조건이 있어도 구조화 규칙이 없거나 불완전하면 상품을 잘못 `SATISFIED`로 평가할 수 있다.

대표 사례는 `Sh어촌청년을 응원海`다.

- 원문에는 60세 미만, 실명의 개인, 1인 1계좌, 어업 관계자 증빙 또는 수산계 재학생 증빙 조건이 있다.
- 현재 실행 규칙은 `구조화된 필수 가입조건 없음`으로 로드된다.
- 사용자 자격을 확인하지 않아도 상품이 `SATISFIED`가 되어 상위 추천에 들어갈 수 있다.

이 계획의 목적은 상위 추천상품에 대해서는 LLM이 `eligibility_text` 전체를 직접 읽고 현재 사용자 사실에 대한 가입조건 상태와 필요한 추가 질문을 반환하게 하는 것이다.

## 2. 확정 결정

다음 결정은 구현 시 다시 선택하지 않는다.

1. `PARTIALLY_COVERED`, `FULLY_COVERED` 같은 원문-구조화 커버리지 상태를 새 데이터로 만들지 않는다.
2. Backend는 `eligibility_text`와 구조화 규칙의 의미적 차이를 스스로 판단하지 않는다.
3. LLM은 커버리지 비교가 아니라 `eligibility_text` 전체를 현재 사용자 사실에 대입해 평가한다.
4. 새 자격 상태를 만들지 않고 기존 `EvaluationStatus`를 그대로 사용한다.
   - `SATISFIED`
   - `ACHIEVABLE`
   - `UNKNOWN`
   - `UNSATISFIABLE`
5. 기존 구조화 평가와 원문 LLM 평가를 보수적인 `AND`로 결합한다.
6. 일반 대화 계획 LLM은 계속 가입 가능 여부를 계산하지 않는다. 가입조건 원문 검수는 별도 LLM purpose와 strict schema로 분리한다.
7. 예비 순위의 숫자상 3위 이내 상품을 검수한다. 공동 순위가 있으면 `rank <= 3`인 상품을 모두 포함한다.
8. 검수 결과로 상품이 제외되거나 순위가 변해 새로운 상품이 3위 이내에 들어오면 새 진입 상품도 검수한다.
9. LLM은 상품별 검수 결과와 함께 사용자에게 보여줄 자연스러운 설명과 필요한 질문을 작성한다. Backend는 모든 성공·실패 경우의 사용자 문장을 템플릿으로 만들지 않는다.
10. Backend는 ID, enum, 근거, 권한, 상태 결합, 질문 대상, 원자성과 재시도 안전성만 검증한다.
11. 런타임 검수 결과로 normalized 상품 데이터를 자동 수정하지 않는다. 데이터 교정은 별도 발행 절차로 수행한다.
12. 구현과 필수 테스트에는 `gpt-5.6-luna`, reasoning effort `low` subagent를 활용하고 최종 통합과 전체 회귀는 메인 agent가 담당한다.

## 3. 기존 판정 체계 재사용

### 3.1 상태 의미

원문 검수는 기존 엔진과 동일한 의미를 사용한다.

| 상태 | 의미 |
|---|---|
| `SATISFIED` | 원문의 모든 필수 가입조건이 현재 확인된 사실로 충족된다. |
| `ACHIEVABLE` | 현재 미충족이지만 가입 전 수행 가능한 구체적 행동과 사용자 의향이 확인돼 있다. 기존 future capability 의미론을 만족할 때만 사용한다. |
| `UNKNOWN` | 판단에 필요한 사용자 사실이 없거나, 원문이 모호하거나, 근거만으로 확정할 수 없다. |
| `UNSATISFIABLE` | 현재 확인된 사용자 사실이 필수조건과 명백히 충돌하거나 사용자가 필수조건을 충족할 수 없다고 답했다. |

LLM은 단순한 가능성, 상식, 상품명 또는 통계적 추정으로 `SATISFIED`나 `ACHIEVABLE`을 반환할 수 없다. 필요한 사실이 하나라도 부족하면 `UNKNOWN`이다.

장병내일준비적금과 청년 정책계좌처럼 별도로 합의된 전역 불변조건은 원문 검수보다 우선하며 그대로 유지한다.

### 3.2 결합 원칙

구조화 평가를 `S`, 원문 LLM 평가를 `T`라고 하면 최종 가입조건 평가는 기존 `AND` 상태 결합 함수를 사용한다.

```text
FINAL_ELIGIBILITY = combine_and(S, T)
```

주요 결과는 다음과 같다.

| 구조화 평가 `S` | 원문 평가 `T` | 최종 결과 |
|---|---|---|
| `SATISFIED` | `SATISFIED` | `SATISFIED` |
| `SATISFIED` | `UNKNOWN` | `UNKNOWN` |
| `SATISFIED` | `UNSATISFIABLE` | `UNSATISFIABLE` |
| `UNKNOWN` | `SATISFIED` | `UNKNOWN` |
| `UNSATISFIABLE` | 모든 상태 | `UNSATISFIABLE` |

전체 조합의 실제 처리는 새 표 기반 로직을 만들지 않고 현재 `eligibility.engine.logical.combine_and`의 의미론을 재사용한다.

## 4. 책임 경계

### 4.1 가입조건 검수 LLM

- `eligibility_text` 전체에서 실제 가입대상과 필수 증빙·계좌 제한·가입 시점 조건을 읽는다.
- 우대금리, 적립방법, 이자지급, 중도해지와 가입 필수조건을 구분한다.
- 구조화 평가 결과와 현재 사용자 사실을 참고하되, 원문 전체를 독립적으로 평가한다.
- 현재 사실로 상태를 확정할 수 없으면 필요한 사용자 사실을 식별한다.
- 사용자가 이해할 수 있는 설명과 질문을 작성한다.
- 판정에 사용한 원문 구절을 `evidence_bindings`로 정확히 연결한다.
- 금리, 예상 이자 또는 순위를 계산하지 않는다.
- normalized 데이터를 수정하거나 실행 규칙을 영구 생성하지 않는다.

### 4.2 Backend

- 검수 대상 상품과 source/version/fingerprint를 결정한다.
- LLM 입력에 구조화 평가, 현재 사용자 사실, 전역 불변조건과 원문을 제공한다.
- strict schema와 allowlist를 검증한다.
- `product_id`, 상태 enum, 질문 대상, 근거 구절과 출처를 검증한다.
- LLM이 인용한 `source_text`가 제공한 `eligibility_text` 안에 실제로 존재하는지 확인한다.
- 구조화 평가와 원문 평가를 기존 `AND` 의미론으로 결합한다.
- missing fact와 질문을 기존 질문 lifecycle에 연결한다.
- 전체 batch 적용을 원자적으로 처리하고 실패 시 검수 상태와 사용자 메시지를 노출하지 않는다.
- 검수 이후 순위를 다시 계산하고 새로 3위 이내에 진입한 상품을 찾는다.

### 4.3 일반 대화 LLM

일반 `FlexibleConversationTurnPlan`의 역할은 유지한다.

- 사용자 발화에서 프로필 답변과 검색조건 변경을 찾는다.
- 검수 LLM이 이미 생성하고 Backend가 승인한 pending 질문 중 표시할 질문을 선택한다.
- 사용자의 정정과 취소를 처리한다.
- 가입조건 원문을 직접 최종 평가하지 않는다.

단, 대화 문맥에는 승인된 원문 검수 상태와 상위 상품의 관련 근거를 제공해 자연스러운 후속 대화가 가능하게 한다.

## 5. 처리 흐름

```text
1. 기존 deterministic retrieval/evaluation/ranking 수행
2. 예비 순위에서 rank <= 3인 검수 frontier 계산
3. 아직 현재 상태로 검수되지 않은 frontier 상품을 한 batch로 LLM에 전달
4. LLM이 상품별 EvaluationStatus, 근거, missing fact, 질문, assistant message 반환
5. Backend가 batch 전체를 strict validation
6. 구조화 평가와 원문 평가를 combine_and
7. UNSATISFIABLE 제거, UNKNOWN/ACHIEVABLE 질문·잠정 상태 반영
8. ranking 재계산
9. 새로 rank <= 3에 진입한 미검수 상품이 있으면 2번부터 반복
10. frontier가 모두 현재 상태로 검수됐으면 결과와 승인된 LLM 메시지·질문 노출
```

### 5.1 반복 종료 조건

다음 조건을 모두 만족하면 검수를 종료한다.

- 현재 `rank <= 3` 상품에 유효한 원문 검수 결과가 있다.
- 검수 결과가 참조한 상품 version/fingerprint가 현재 발행본과 같다.
- 검수 결과가 참조한 사용자 사실 snapshot이 현재 상태와 같다.
- 마지막 검수 적용 뒤 3위 이내 구성원이 바뀌지 않았다.

무한 호출을 막기 위해 한 요청의 최대 batch round를 설정한다. 제한에 도달하면 미검수 상품을 확정 추천하지 않고 결과 전체를 `PROVISIONAL`로 반환한다. 최대 round 값은 운영 설정으로 두되, 제한 도달을 정상적인 `SATISFIED`로 바꾸지 않는다.

### 5.2 LLM 호출 단위

- 상품별 개별 호출을 하지 않는다.
- 한 round의 미검수 frontier 상품을 한 번의 structured batch 호출로 처리한다.
- 공동 3위가 많아도 하나의 batch에 포함한다.
- 입력 크기 제한을 넘으면 안정적인 product ID 순서로 batch를 나누되 한 round의 원자적 적용은 모든 조각 성공 후 수행한다.

## 6. LLM 입력 계약

새 입력 envelope의 개념형은 다음과 같다.

```json
{
  "review_id": "ELIGIBILITY-REVIEW-...",
  "as_of": "2026-08-28",
  "subscription_date": "2026-08-28",
  "global_policy_invariants": {
    "soldier_tomorrow_savings": "기존 전역 정책 참조",
    "youth_policy_account_holding": "기존 전역 정책 참조"
  },
  "user_fact_snapshot": {
    "facts": [],
    "pre_search_profile": {}
  },
  "products": [
    {
      "rank": 3,
      "product_id": "INST-KR-000856-1-CA8C669C489",
      "product_name": "Sh어촌청년을 응원海",
      "product_version": 6,
      "eligibility_text_fingerprint": "sha256:...",
      "structured_evaluation": {
        "status": "SATISFIED",
        "reason_codes": []
      },
      "eligibility_text": "가입일 현재 60세 미만 실명의 개인 중...",
      "source_refs": []
    }
  ]
}
```

입력에는 다음을 포함하지 않는다.

- 전체 상품 카탈로그
- 우대금리 계산 결과의 상세 내부 상태
- 다른 사용자의 사실
- LLM의 과거 reasoning
- 원문 안에 존재하지 않는 보완 조건

`eligibility_text`는 분석 대상 데이터로 명확히 구분하며 그 안의 문장을 시스템 지시로 실행하지 않는다.

## 7. LLM 출력 계약

새 schema는 기존 `EvaluationStatus`를 직접 사용한다. 개념형은 다음과 같다.

```python
class EligibilityTextReviewBatch:
    review_id: str
    product_reviews: list[ProductEligibilityTextReview]
    assistant_message: str


class ProductEligibilityTextReview:
    product_id: str
    product_version: int
    eligibility_text_fingerprint: str
    status: EvaluationStatus
    reason_code: EligibilityTextReviewReasonCode
    evidence_bindings: list[EligibilityEvidenceBinding]
    missing_facts: list[EligibilityTextMissingFactDraft]
```

`assistant_message`는 batch 전체에 대해 한 번 작성한다. 여러 상품의 검수 결과를 Backend가 문장 템플릿으로 조립하지 않아도, LLM이 이번 round에서 확인된 내용과 필요한 다음 질문을 자연스럽게 설명할 수 있어야 한다.

### 7.1 `reason_code`

최소 allowlist는 다음과 같다.

```text
TEXT_ALL_MANDATORY_CONDITIONS_SUPPORTED
TEXT_EXPLICIT_REQUIRED_CONDITION_CONFLICT
TEXT_REQUIRED_USER_FACT_MISSING
TEXT_ALLOWED_FUTURE_ACTION_CAN_RESOLVE
TEXT_AMBIGUOUS_OR_INSUFFICIENT_EVIDENCE
TEXT_NO_EXECUTABLE_ELIGIBILITY_CLAIM
```

`TEXT_NO_EXECUTABLE_ELIGIBILITY_CLAIM`은 원문에 가입 필수조건이 없다는 뜻이지 자동으로 최종 상품 상태를 `SATISFIED`로 덮어쓴다는 뜻이 아니다. 최종 상태는 구조화 평가와 결합해 결정한다.

### 7.2 근거 계약

각 평가에는 최소 하나의 근거 또는 명시적인 근거 없음 사유가 필요하다.

```json
{
  "source_ref_id": "EVD-...",
  "source_text": "가입일 현재 60세 미만 실명의 개인",
  "claim_role": "MANDATORY_ELIGIBILITY",
  "applied_user_fact_ids": ["PRESEARCH-..."]
}
```

Backend는 다음을 검증한다.

- `source_ref_id`가 입력 상품에 허용된 출처인지
- `source_text`가 입력 `eligibility_text`의 실제 부분문자열인지
- `applied_user_fact_ids`가 현재 snapshot의 fact인지
- 다른 상품의 근거를 참조하지 않았는지
- `SATISFIED`인데 missing fact가 남아 있지 않은지
- `UNKNOWN`인데 질문도 근거 있는 미해결 사유도 없는 빈 결과가 아닌지

### 7.3 missing fact와 질문

LLM은 정보가 부족할 때 상품별 missing fact draft와 사용자 질문을 함께 반환한다.

```json
{
  "fact_type": "ELIGIBILITY_TEXT::INST-KR-000856-1-CA8C669C489::QUALIFYING_STATUS",
  "semantic_type": "SELF_REPORTED_FACT",
  "answer_mode": "BINARY_OR_EXPLANATION",
  "question": "어업 관련 자격과 증빙이 있거나, 수산계 학교에 재학 중이며 재학 증빙이 가능하신가요?",
  "grounding_evidence_indexes": [0, 1]
}
```

Backend는 fact type namespace와 대상 product ID를 검증하고 기존 `MissingFactRequest` 및 `PlannedQuestion`으로 변환한다. 질문 문장은 LLM 출력에서 가져오며 Backend가 상품별 문장 경우의 수를 하드코딩하지 않는다.

한 원문에 `OR` 가입 경로가 있으면 질문과 답변 schema가 대체 경로를 보존해야 한다. LLM이 한 경로만 보고 사용자를 탈락시키지 않도록 prompt와 필수 테스트에 고정한다.

## 8. 일반 대화 문맥 연결

현재 `REFERENCED_PRODUCT_EVIDENCE`에는 typed feature와 우대 적용 방식만 들어간다. 다음 섹션을 추가한다.

```text
TOP_RANKED_ELIGIBILITY_EVIDENCE
```

각 항목에는 다음 정보만 제공한다.

- 현재 rank
- product ID와 이름
- `eligibility_text`
- 구조화 평가 상태
- 승인된 원문 검수 상태와 reason code
- 검수에서 생성된 pending question ID
- 근거 source/version/fingerprint

이 문맥은 일반 대화 LLM이 승인된 질문을 재표현하거나, 사용자가 `이 상품은 왜 가입 확인이 필요해?`라고 물을 때 답하기 위한 것이다. 일반 대화 LLM이 원문 검수 상태를 임의로 덮어쓰는 operation은 제공하지 않는다.

## 9. 상태 저장과 무효화

검수 결과는 세션 runtime에 다음 key로 저장한다.

```text
search_session_id
+ product_id
+ product_version
+ eligibility_text_fingerprint
+ relevant_user_fact_snapshot_hash
+ global_policy_version
```

다음 변화가 있으면 해당 상품 검수 결과를 무효화한다.

- 사용자 답변 추가·수정·취소
- 관련 pre-search 전역 사실 변경
- authoritative fact 변경
- 상품 version 또는 `eligibility_text` fingerprint 변경
- 가입 예정일 또는 기준일 변경
- 원문 검수 prompt/policy version 변경

사용자 상태와 무관한 상품 원문 해석을 별도 장기 cache로 저장하는 것은 이 계획의 필수 범위가 아니다. 그렇게 저장하면 사실상 구조화 데이터 발행 단계가 되므로 별도 데이터 파이프라인으로 다룬다.

## 10. 질문과 사용자 응답

### 10.1 질문 우선순위

기존 전역 필수 질문은 계속 전역적으로 동작한다.

- 청년 정책계좌 중복가입 여부
- 장병내일준비적금 공통 복무 자격

원문 검수 질문은 상품별 질문으로 추가한다. 질문 표시 순서는 현재 pending workflow와 결과 영향도를 LLM에 제공하고, 유연 대화 계획기가 이번에 표시할 질문을 선택하게 한다.

### 10.2 사용자 메시지

- 검수 LLM은 상품별 상태를 바탕으로 batch 단위의 `assistant_message`를 함께 반환한다.
- Backend는 batch 검증과 상태 적용이 성공한 뒤에만 메시지를 노출한다.
- 적용 실패 또는 rollback 시 성공한 것처럼 말하는 LLM 메시지를 노출하지 않는다.
- LLM 호출 자체가 불가능하면 기존 시스템 오류 UX를 사용하되 미검수 상품을 확정 추천으로 바꾸지 않는다.

## 11. 랭킹과 추천 상태

- `UNSATISFIABLE`은 기존과 같이 최종 순위에서 제외한다.
- `UNKNOWN`은 현재의 보수적 정렬 및 provisional 의미론을 유지한다.
- `ACHIEVABLE`은 기존 future capability 의미론을 만족하는 경우에만 유지한다.
- 3위 이내에 현재 상태 기준 원문 검수가 끝나지 않은 상품이 있으면 추천 결과는 `PROVISIONAL`이다.
- 미검수 상품을 `SATISFIED`로 가정해 `CONFIRMED` Top 3에 표시하지 않는다.
- UI에는 원문 검수로 인해 필요한 질문 수와 `가입조건 확인 필요` 상태를 노출할 수 있다.

상위 5개를 화면에 표시하더라도 이번 강제 검수 경계는 숫자상 3위 이내다. 4위 이하 상품은 기존 구조화 평가를 따르며, 이후 3위 이내로 진입하는 순간 검수 대상이 된다.

## 12. 실패와 충돌 처리

### 12.1 LLM 출력 검증 실패

```text
batch validation 실패
→ 원문 검수 상태 적용 없음
→ ranking 변경 없음
→ assistant_message 노출 없음
→ 해당 frontier는 미검수 상태 유지
→ 추천은 CONFIRMED가 아닌 PROVISIONAL
```

### 12.2 구조화 평가와 원문 평가 충돌

두 평가 중 하나를 자동으로 삭제하거나 normalized 데이터를 수정하지 않는다. 기존 `AND` 결합으로 사용자 추천은 보수적으로 처리하고 다음을 audit에 남긴다.

- product ID/version
- 구조화 상태와 reason code
- 원문 상태와 reason code
- 사용된 사용자 fact IDs
- 원문 근거 구절
- 최종 결합 상태

충돌 빈도가 높은 상품은 별도 데이터 정밀화 큐로 보낼 수 있지만, 런타임이 원본 상품 파일을 직접 수정하지 않는다.

### 12.3 원문 없음 또는 데이터 gap

- `eligibility_text`가 없고 구조화 규칙이 있으면 기존 구조화 평가만 사용한다.
- `eligibility_text`도 구조화 규칙도 없지만 발행 metadata가 eligibility data gap을 선언하면 `UNKNOWN`을 유지한다.
- 명시적인 data gap 없이 두 정보가 모두 없는 상품은 데이터 품질 audit 대상이며 자동으로 필수조건이 없다고 단정하지 않는다.

## 13. Audit·Debug 기록

다음 event를 추가한다.

```text
ELIGIBILITY_TEXT_REVIEW_REQUESTED
ELIGIBILITY_TEXT_REVIEW_COMPLETED
ELIGIBILITY_TEXT_REVIEW_REJECTED
ELIGIBILITY_TEXT_REVIEW_INVALIDATED
ELIGIBILITY_TEXT_REVIEW_APPLIED
ELIGIBILITY_REVIEW_FRONTIER_CHANGED
```

Debug history에는 다음 연결이 보여야 한다.

```text
예비 rank
→ 검수 frontier
→ LLM 입력 source/version/fingerprint
→ structured output
→ validation 결과
→ 구조화 상태와 원문 상태의 결합
→ 생성된 질문
→ 재평가 rank
```

LLM reasoning은 저장 계약이 아니다. strict output, 입력 hash, 근거 binding과 적용 결과만 보존한다.

## 14. 구현 단계와 예상 변경 위치

### 단계 1. Schema와 LLM purpose

- `src/eligibility/schema/eligibility_text_review.py` 추가
  - batch input/output DTO
  - product review DTO
  - evidence binding
  - missing fact draft
  - reason code
- `src/eligibility/llm/models.py`
  - `ELIGIBILITY_TEXT_REVIEW` purpose 추가
- `src/eligibility/llm/system_prompts.py`
  - 원문 전체 평가 전용 system prompt 추가

### 단계 2. 검수 packet과 frontier

- `src/eligibility/eligibility_text_review.py` 추가
  - `rank <= 3` frontier 계산
  - product/version/fingerprint packet 생성
  - 관련 사용자 사실 snapshot 생성
  - batch 검증
- normalized loader가 `eligibility_text`와 source ref를 실행 객체에서 안전하게 조회할 수 있도록 read-only metadata projection 추가

### 단계 3. ApplicationService 통합

- `src/eligibility/application_service.py`
  - 예비 ranking 이후 원문 검수 round 실행
  - 결과 원자적 적용
  - 기존 상태와 `combine_and`
  - 검수 무효화와 재실행
  - 재진입 frontier 반복
  - assistant message 성공 후 노출

### 단계 4. 질문 lifecycle와 대화 문맥

- 원문 검수 missing fact를 기존 `MissingFactRequest`와 `PlannedQuestion`에 연결
- `_conversation_context`에 `TOP_RANKED_ELIGIBILITY_EVIDENCE` 추가
- 유연 대화 계획기가 승인된 원문 질문을 선택·재표현할 수 있도록 pending allowlist 확장

### 단계 5. Web/API·Debug

- 추천 DTO에 원문 검수 상태와 provisional reason을 필요한 최소 범위로 추가
- `/state`, debug history와 audit timeline에 검수 round 표시
- UI에서 질문과 LLM 설명을 기존 chat 흐름으로 표시
- 미검수 상태를 확정 가입 가능처럼 표시하지 않음

### 단계 6. 운영 보호장치

- feature flag: `ELIGIBILITY_TEXT_TOP_RANK_REVIEW_ENABLED`
- 최대 batch round 설정
- LLM timeout/retry와 batch 크기 제한
- product/fact/policy hash 기반 무효화
- shadow mode에서 기존 순위와 검수 적용 예상 결과 비교

## 15. 필수 테스트 계획

구현 테스트는 `gpt-5.6-luna`, reasoning effort `low` subagent들에게 독립 영역으로 나누어 병렬 작성하게 한다. 메인 agent는 테스트가 실제 구현 계약을 검증하는지 재검토한다.

### 15.1 Schema와 grounding

1. 허용되지 않은 product ID를 거부한다.
2. 입력 원문에 없는 `source_text`를 거부한다.
3. 다른 상품 source ref를 사용하면 거부한다.
4. `SATISFIED`와 missing fact가 동시에 있으면 거부한다.
5. batch 일부만 잘못돼도 전체 적용을 rollback한다.
6. rollback된 LLM assistant message가 사용자에게 노출되지 않는다.

### 15.2 상태 의미론

1. 구조화 `SATISFIED` + 원문 `UNKNOWN`은 최종 `UNKNOWN`이다.
2. 구조화 `SATISFIED` + 원문 `UNSATISFIABLE`은 최종 `UNSATISFIABLE`이다.
3. 구조화 `UNSATISFIABLE`은 원문 결과와 무관하게 최종 `UNSATISFIABLE`이다.
4. 사용자 사실이 부족한데 `SATISFIED`로 만드는 출력을 prompt fixture와 validation에서 차단한다.
5. `ACHIEVABLE`은 구체적인 future capability와 의향이 연결된 경우에만 허용한다.

### 15.3 실제 상품 회귀

1. `Sh어촌청년을 응원海`에서 연령·어업·수산계 재학 사실이 없으면 `UNKNOWN`과 질문이 생성된다.
2. 65세 사용자는 원문의 60세 미만 조건으로 `UNSATISFIABLE`이다.
3. 30세라는 사실만으로는 어업·수산계 조건이 남아 있으므로 `SATISFIED`가 아니다.
4. 어업 관계자이며 허용된 증빙이 가능하거나, 수산계 재학생이며 증빙 가능한 사용자는 `SATISFIED`가 될 수 있다.
5. 우대금리의 자동이체·비대면 조건을 가입 필수조건으로 오해하지 않는다.
6. `민간인임`, `군인 아니라고`는 모든 장병내일준비적금 표기와 금융기관에 전역 `UNSATISFIABLE`로 적용된다.
7. `장병내일준비 적금`과 `장병내일준비적금`이 동일한 상품군 정책을 사용한다.

### 15.4 랭킹 frontier

1. 공동 3위를 전부 검수한다.
2. 1위 상품이 `UNSATISFIABLE`로 빠지면 새로 3위 이내에 진입한 상품을 검수한다.
3. 여러 round 뒤 frontier가 안정되면 종료한다.
4. 최대 round 도달 시 미검수 상품이 있어도 확정 추천으로 반환하지 않는다.
5. 사용자 사실 수정으로 기존 검수 결과가 무효화되고 재검수된다.
6. 상품 version/fingerprint 변경으로 기존 검수 결과가 재사용되지 않는다.

### 15.5 장애와 회귀

1. LLM timeout 시 기존 구조화 평가를 파괴하지 않고 결과를 provisional로 유지한다.
2. 검수 feature flag가 꺼지면 기존 동작이 유지된다.
3. 기존 청년·장병 전역 질문, 유연 대화, Decision Ledger, feature policy 테스트가 모두 통과한다.
4. 기존 금리·이자 계산과 순위 정렬 값은 가입조건 상태 변경 외에는 바뀌지 않는다.
5. 실제 provider smoke test에서 상위 상품 batch가 strict schema로 응답하는지 확인한다.

## 16. 단계적 배포

### 16.1 Shadow

- LLM 검수는 실행하지만 사용자 상태와 순위에는 반영하지 않는다.
- 현재 구조화 상태와 LLM 원문 상태의 차이, 호출 시간, 질문 생성률을 기록한다.
- `Sh어촌청년을 응원海`와 장병 상품군을 필수 관찰 대상으로 둔다.

### 16.2 Provisional enforcement

- LLM 검수에서 `UNKNOWN` 또는 충돌이 발견되면 확정 추천만 차단한다.
- `UNSATISFIABLE` 제거 적용 여부와 false positive를 debug history로 검토한다.

### 16.3 Full enforcement

- 검수 결과를 최종 자격 상태와 순위에 결합한다.
- 재진입 frontier 반복과 질문 lifecycle을 활성화한다.
- 운영 지표와 오류율이 기준을 충족하면 feature flag를 기본 활성화한다.

## 17. 완료 조건

다음을 모두 만족하면 구현 완료로 본다.

1. 숫자상 3위 이내 모든 상품이 현재 product version과 사용자 fact snapshot 기준의 원문 검수 결과를 가진다.
2. `eligibility_text`에 미확인 필수조건이 있는 상품이 `SATISFIED` 확정 추천으로 노출되지 않는다.
3. LLM 원문 검수는 기존 `EvaluationStatus`만 사용한다.
4. Backend는 원문 의미를 정규식이나 문장별 하드코딩으로 재판정하지 않는다.
5. 질문과 사용자 설명은 LLM이 작성하고, Backend 적용 성공 후에만 노출된다.
6. 검수 결과로 순위가 바뀌면 새 진입 상품까지 검수가 반복된다.
7. `Sh어촌청년을 응원海`와 모든 장병내일준비적금 회귀가 통과한다.
8. LLM 장애·잘못된 근거·stale cache가 가입 가능 확정으로 fail-open되지 않는다.
9. 전체 기존 회귀에서 가입조건 검수와 무관한 계산·대화 기능이 유지된다.
