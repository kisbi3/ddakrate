# Top 3 조건 질문·추천 안정화 구현 설계

- 작성일: 2026-09-01
- 상태: **핵심 runtime 구현 완료 — 검수된 condition family의 단계적 활성화 대기**
- 범위: 적금·예금·파킹통장·CMA의 우대금리·가입조건 질문, 답변 상태, Top 3 추천 안정화
- 기준: 이 문서에서 `MUST`, `SHOULD`, `MAY`는 각각 필수, 권장, 선택 요구사항을 뜻한다.

> 이 문서는 현재 합의한 최종 구현 기준이다. 기존 [`PRE_SEARCH_QUESTION_DESIGN.md`](PRE_SEARCH_QUESTION_DESIGN.md)와 [`../PRODUCT_RECOMMENDATION_CONVERSATION_GENERALIZATION_DESIGN.md`](../PRODUCT_RECOMMENDATION_CONVERSATION_GENERALIZATION_DESIGN.md)의 질문 순서·후보 범위·모호한 답변 처리와 충돌하면 이 문서를 우선한다. 기존 원천 상품 데이터는 즉시 재작성하지 않고, 파생 조건 인덱스를 만들어 단계적으로 전환한다.

## 0. 2026-09-01 구현 결과

Phase 0~4의 핵심 runtime 계약과 Phase 5의 안전한 전환 기반을 구현했다. 상세 구현·검증 기록은 [`../reports/web/TOP3_CONDITION_IMPLEMENTATION_REPORT_2026-09-01.md`](../reports/web/TOP3_CONDITION_IMPLEMENTATION_REPORT_2026-09-01.md)에 정리했다.

| 항목 | 구현 상태 |
|---|---|
| 파생 `ConditionRequirement` compiler와 관측 지표 | 완료 |
| 세션별 사용자 조건 상태, 정정·무효화 | 완료 |
| 가능한 최고점수 순위와 실현 가능 예상값의 표시 역할 분리 | 완료 |
| Top 3 frontier 및 질문 family 선정 | 완료 |
| 질문 생성과 답변 해석 계약 분리 | 완료 |
| 허용 ID 검증과 원자적 multi-intent 반영 | 완료 |
| 제한된 read-only 상품 조회 계약 | 완료 |
| 목록·상세의 예상금리 상태 통일 | 완료 |
| 검수 완료 family의 실제 계산 활성화 | 데이터 검수 후 단계적 전환 |

현재 4,292개 상품에서 4,769개 requirement를 파생했다. 이 가운데 169개는 `PARTIAL`, 11개는 `UNAVAILABLE`, 480개는 canonical 사용자 변수가 아직 매핑되지 않았다. 명시적인 사람 검수가 끝난 requirement는 아직 0개이므로 전체 파생 인덱스는 shadow 상태다. `REVIEW_REQUIRED` 조건을 실제 금리로 적용하지 않는 것이 정상 동작이며, 검수 없이 자동 활성화하지 않는다.

## 1. 목표

사용자에게 현재 순위를 계속 보여주되, 실제 Top 3에 들어올 가능성이 있는 상품만 내부 후보군으로 관리하고 가장 많은 후보의 불확실성을 줄이는 질문을 하나씩 제시한다.

핵심 목표는 다음과 같다.

1. 아직 배제되지 않은 조건을 반영한 가능한 최고 순위와 현재 확인된 예상값을 분리한다.
2. 상품별 문장을 그대로 묻지 않고, 여러 상품에 재사용할 수 있는 **사용자 변수** 단위로 질문한다.
3. 가입조건, 우대금리, 단순 혜택을 서로 다른 의미로 처리한다.
4. LLM은 자연어 표현과 답변 해석을 담당하고, 후보 선정·금리·이자·순위·종료 판단은 backend가 결정한다.
5. 모호한 답변을 거절로 바꾸거나 우대금리 충족으로 과대 해석하지 않는다.
6. 더 물어도 Top 3가 바뀌지 않으면 명시적으로 탐색을 끝낸다.

## 2. 비목표

- LLM이 임의 SQL을 실행하거나 상품을 직접 삭제·정렬하게 하지 않는다.
- 광고 최고금리를 사용자의 예상금리로 표시하지 않는다.
- 모든 상품 원문을 한 번에 완전한 공통 스키마로 수작업 재작성하지 않는다.
- 수수료 면제와 부가 혜택을 기본 추천 점수나 선제 질문에 포함하지 않는다.
- 사용자 답변을 동의 없이 영구 사용자 프로필로 저장하지 않는다.
- AI가 만든 조건 분류를 검수 없이 금융 계산에 활성화하지 않는다.

## 3. 핵심 용어

| 용어 | 의미 |
|---|---|
| 표시 순위 | 사용자 조건에서 아직 가능한 최고금리 또는 최고 예상이자 순위 |
| 실현 가능 점수 | 현재 세션에서 충족이 확인된 조건만 반영한 점수 |
| 낙관 상한 | 아직 부정되지 않은 우대조건을 모두 충족한다고 가정한 최대 가능 점수 |
| Top 3 | 가능한 최고점수 표시 순위의 상위 3개 상품. 동점 정책은 backend의 고정 정렬 규칙을 따른다. |
| 질문 후보군(frontier) | 현재 Top 3와, 낙관 상한으로 현재 3위에 진입할 수 있는 외부 상품의 합집합 |
| 사용자 변수 | 여러 상품 조건이 공통으로 참조할 수 있는 한 가지 사용자 사실 또는 선택 |
| 질문 가족(question family) | 동일한 사용자 변수를 묻는 질문들의 안정적인 식별 단위 |
| 확인 전 | 거절도 충족도 아니며, 현재 계산에 적용할 수 있을 만큼 구체적이지 않은 상태 |
| 잠정 결과 | 사용자가 추가 확인을 원하지 않거나 데이터가 불완전해 순위 변동 가능성이 남은 결과 |

## 4. 필수 불변조건

1. 표시 순위는 **사용자 조건에서 아직 가능한 최고점수**를 사용해야 한다.
2. 낙관 상한은 순위와 질문 후보군에 사용하되 현재 예상금리로 승격하면 안 된다.
3. 가입조건이 거짓이면 상품을 제외한다.
4. 우대금리 조건이 거짓이면 해당 우대분만 제외하고 상품 자체는 유지한다.
5. 정보성 조건은 질문·순위·금리 계산에 참여하지 않는다.
6. `PARTIAL` 또는 `UNAVAILABLE` 데이터를 조건 없음이나 조건 불충족으로 해석하면 안 된다.
7. 모호한 답변은 우대금리를 적용하지 않으며 거절로도 해석하지 않는다.
8. 같은 모호한 질문을 자동으로 반복하지 않는다.
9. LLM은 상품 ID, 조건 ID, 금리, 순위, 다음 질문을 새로 만들거나 결정할 수 없다.
10. 모든 수치 계산은 backend가 구조화된 입력과 조건 그래프로 재현 가능하게 수행해야 한다.

## 5. 현재 데이터 기준선

아래 수치는 2026-09-01 로컬 카탈로그 감사 스냅샷이다. 구현 전 migration 기준선이며, 데이터 갱신 후에는 자동 감사 보고서로 대체해야 한다.

### 5.1 상품과 정규화 조건

| 항목 | 수치 |
|---|---:|
| 전체 상품 | 4,292 |
| 적금 | 2,190 |
| 예금 | 1,825 |
| 파킹통장 | 219 |
| CMA | 58 |
| 정규화 우대조건 | 485개 상품, 1,430개 rule |
| `semantic_domain` 있음 | 1,134 rule |
| `semantic_domain` 없음 | 296 rule |
| 구조화 `SUPPORTED` | 1,280 rule |
| `REQUIRES_EXTERNAL_DEFINITION` | 145 rule |

상품군별 정규화 우대조건 분포는 적금 364개 상품/1,148개 rule, 파킹통장 120개/281개, CMA 1개/1개, 예금 0개다.

### 5.2 현재 구조의 중요한 한계

- `metadata.features`는 전체 상품에서 사실상 비어 있어 대화 로직의 신뢰 가능한 기준으로 사용할 수 없다.
- 기존 adapter가 만든 실행 가능 우대조건에도 정규화 질문과 근거는 있으나, `action_id`, future achievement, canonical action 연결이 거의 없어 여러 상품을 동일한 사용자 변수로 안정적으로 묶지 못한다.
- 조건 정보가 비어 있는 상품은 적금 1,925개, 예금 386개, 파킹통장 82개, CMA 36개다. 단, 모든 공백이 오류는 아니다. 예를 들어 일부 CMA는 광고 최고수익률을 의도적으로 제공하지 않는다.
- 현재 `semantic_domain` 분포에는 `CARD_ACTIVITY`, `PRODUCT_HOLDING`, `AUTOMATIC_TRANSFER`, `INCOME_CREDIT`, `MARKETING_CONSENT` 등이 있지만, 질문 단위로 쓰기에는 범위가 너무 넓은 값이 있다.

따라서 기존 데이터를 없애지 않고 **파생 조건 인덱스**를 추가하는 방식으로 전환해야 한다.

## 6. 목표 아키텍처

```text
원천 상품 데이터
  → Requirement Compiler
  → 검수된 파생 ConditionRequirement 인덱스
  → 사용자 입력/세션 상태
  → Eligibility + Rate Evaluator
  → 가능한 최고점수 표시 순위 + 현재 예상값
  → Top3 Frontier Planner
  → QuestionSpec
  → 질문 문구 렌더러(템플릿 또는 제한된 LLM)
  → 사용자 답변
  → Answer Interpreter(제한된 LLM)
  → backend 검증·원자적 반영
  → 재계산 또는 완료
```

추천 runtime의 권한 경계는 다음과 같다.

| 결정 | 담당 |
|---|---|
| 상품 검색, 기관 제외, 상품군 필터 | backend 조회 도구/정형 로직 |
| 가입 가능 여부 | backend condition evaluator |
| 예상금리·이자 | backend rate/cash-flow engine |
| 질문 후보군과 다음 사용자 변수 | backend planner |
| 질문 문장의 자연스러운 표현 | 고정 템플릿 우선, 필요 시 LLM |
| 자유발화의 구조화 | LLM + backend schema 검증 |
| 결과 설명 | backend가 제공한 계산 결과와 근거 범위 안에서 LLM 가능 |

### 6.1 LLM에 제공할 제한된 조회 도구

LLM에 상품별 전용 도구를 여러 개 주거나 raw SQL 실행권을 주지 않는다. SQL의 필터·정렬·projection 장점만 살린 read-only query contract를 제공한다.

```text
search_products(query_spec)
get_product_details(product_ids, field_groups)
resolve_institutions(names)
get_current_recommendation_state()
submit_answer_plan(answer_plan)
```

`query_spec`은 허용된 field, operator, order, limit만 받을 수 있어야 한다. 기관 제외, 상품군 제한, 카드 실적 제외 같은 deterministic 요청은 정형 filter로 실행한다. 예상금리·이자·Top 3·조건 충족 여부는 query 결과를 받은 LLM이 계산하지 않고 backend의 동일 평가기를 사용한다.

벡터 검색은 다음 보조 용도로만 MAY 사용한다.

- 사용자의 표현과 canonical 조건 family 후보 연결
- 구조화되지 않은 상품 원문에서 검토할 문단 탐색
- 상품명이나 혜택 표현의 discovery

벡터 유사도만으로 기관 제외, 가입 가능, 우대 충족, 금리 또는 순위를 확정하면 안 된다. 구조화 데이터가 이미 답을 제공하는 질의는 정형 조회가 우선이다.

## 7. 조건 모델

### 7.1 ConditionRequirement

```json
{
  "requirement_id": "req:product-123:rule-4",
  "product_id": "product-123",
  "scope": "RATE_BENEFIT",
  "variable_id": "CARD_MONTHLY_SPEND_LIMIT",
  "subject_scope": "USER",
  "institution_id": "shinhan-bank",
  "operator": "GTE",
  "threshold": {"amount": 300000, "currency": "KRW", "period": "MONTH"},
  "rate_effect": {"percentage_point": 1.5},
  "relationship": {"group_id": "bonus-group-2", "logic": "AND"},
  "evidence_refs": ["source:official:..."],
  "coverage_status": "COMPLETE",
  "review_status": "VERIFIED"
}
```

필수 필드는 다음 의미를 가져야 한다.

- `scope`: 조건이 상품 제외, 금리 변화, 정보 표시에 어떤 영향을 주는지 지정한다.
- `variable_id`: 질문과 답변을 여러 상품에 재사용하기 위한 canonical 사용자 변수다.
- `operator`와 `threshold`: backend가 판정할 수 있는 구조화 계약이다.
- `relationship`: AND, OR, 선택 그룹, 중복 불가, 우대 한도를 보존한다.
- `coverage_status`: 상품 데이터 완전성을 나타낸다.
- `review_status`: AI 초안이 금융 계산에 사용되는 것을 막는다.

### 7.2 조건 scope

| scope | 거짓일 때 | 질문 대상 | 순위 반영 |
|---|---|---|---|
| `ELIGIBILITY` | 상품 제외 | Top 3 또는 진입 후보에 영향을 줄 때 | 예 |
| `RATE_BENEFIT` | 해당 우대분만 미적용 | Top 3가 바뀔 수 있을 때 | 예 |
| `INFORMATION_ONLY` | 상품·금리 변화 없음 | 기본적으로 묻지 않음 | 아니요 |

수수료 면제, 경품, 단순 서비스 혜택은 기본적으로 `INFORMATION_ONLY`다. 사용자가 직접 수수료나 해당 혜택을 요청했을 때만 별도 조회·표시할 수 있다.

### 7.3 사용자 변수의 세분화

광범위한 단어를 하나의 변수로 사용하면 안 된다.

| 금지되는 과도한 묶음 | 최소 분리 단위 예시 |
|---|---|
| 카드 | `NEW_CARD_ISSUANCE`, `CARD_MONTHLY_SPEND_LIMIT`, `CARD_SETTLEMENT_ACCOUNT` |
| 새 계좌 | `NEW_INSTITUTION_PREFERENCE`, `FIRST_TRANSACTION_HISTORY`, `LINKED_ACCOUNT_OPENING` |
| 거래 가능 | `AUTO_TRANSFER`, `SALARY_ACCOUNT_CHANGE`, `MARKETING_CONSENT` 등 실제 행위별 변수 |
| 보유 이력 | `PRIOR_PRODUCT_INSTITUTIONS` + 상품군/조회기간 qualifier |

질문은 상품 문장이 아니라 사용자 변수를 물어야 한다. 예를 들어 카드 실적 20만·30만·50만원 조건이 섞여 있으면 상품별 세 질문 대신 `CARD_MONTHLY_SPEND_LIMIT` 하나를 묻는다.

### 7.4 데이터 완전성과 검수

```text
coverage_status = COMPLETE | PARTIAL | UNAVAILABLE
review_status   = VERIFIED | REVIEW_REQUIRED | REJECTED
```

- AI 또는 규칙 기반 분류 결과는 처음에 반드시 `REVIEW_REQUIRED`다.
- `REVIEW_REQUIRED` 조건은 shadow 평가에는 사용할 수 있지만 사용자 금리와 순위를 바꾸면 안 된다.
- 기존 `semantic_domain`은 compiler의 seed로 사용할 수 있다.
- 매핑되지 않은 조건은 억지로 일반화하지 말고 review queue에 남긴다.
- 원문 조건, 파생 조건, 검수 이력과 compiler 버전을 모두 추적할 수 있어야 한다.

## 8. 사용자 조건 상태

### 8.1 상태 모델

사용자 조건은 기본적으로 검색 세션 안에서만 유지한다.

| 내부 상태 | 의미 | 금리 적용 | 재질문 |
|---|---|---:|---:|
| `NOT_ASKED` | 아직 질문하지 않음 | 아니요 | 필요 시 예 |
| `DECLINED` | 사용자가 하지 않겠다고 명시 | 아니요 | 아니요 |
| `DECLARED_FEASIBLE` | 금액·기간 등 판정 가능한 구체적 자기진술 | 조건 충족 시 예 | 아니요 |
| `WILLING_UNSPECIFIED` | 가능/의향만 말했고 임계값 판정 불가 | 아니요 | 자동 반복 금지 |
| `ACKNOWLEDGED_UNKNOWN` | 모르거나 확인할 수 없다고 응답 | 아니요 | 자동 반복 금지 |
| `VERIFIED` | 기관 또는 공식 근거로 확인 | 조건 충족 시 예 | 아니요 |

`WILLING_UNSPECIFIED`와 `ACKNOWLEDGED_UNKNOWN`은 false가 아니다. 아직 부정되지 않았으므로 낙관 상한에는 남을 수 있지만 표시 예상금리에는 반영하지 않는다.

### 8.2 사용자 표시 문구

| 표시 | 내부 상태 |
|---|---|
| 적용 예상 | `DECLARED_FEASIBLE`이고 조건 충족 |
| 확인 전 | `WILLING_UNSPECIFIED`, `ACKNOWLEDGED_UNKNOWN`, 데이터 `PARTIAL/UNAVAILABLE` |
| 적용 안 함 | `DECLINED` 또는 구체적 값으로 조건 미달 |
| 확인 완료 | `VERIFIED` |

### 8.3 모호한 답변 정책

예: 카드 실적 금액을 물었는데 사용자가 “카드는 가능해요”라고 답한다.

1. `CARD_MONTHLY_SPEND_LIMIT`은 `WILLING_UNSPECIFIED`로 저장한다.
2. 카드 우대금리를 표시 예상금리에 적용하지 않는다.
3. 관련 우대분은 낙관 상한에는 유지한다.
4. 해당 질문 가족을 이번 세션에서 `acknowledged` 처리해 같은 금액 질문을 반복하지 않는다.
5. 결과에 해당 조건을 `확인 전`으로 표시한다.
6. 사용자가 나중에 “월 30만원까지 가능”처럼 구체적으로 정정하면 상태를 갱신하고 영향을 받는 상품만 재계산한다.

## 9. 답변 적용 범위

모든 답변 patch는 적용 범위를 명시해야 한다.

```text
GLOBAL_ACTION_TYPE | INSTITUTION | PRODUCT | RULE
```

- “카드 실적은 싫어요”는 `GLOBAL_ACTION_TYPE` 범위로 모든 카드 실적 우대 경로에 적용할 수 있다.
- “수협은행은 싫어요”는 기관 resolver가 canonical institution ID로 변환하고 `INSTITUTION` 제외로 적용한다.
- 특정 상품을 지칭한 답변은 다른 기관의 유사 조건에 전파하지 않는다.
- 기관명 alias 연결은 코드와 검수된 registry로 deterministic하게 처리한다. LLM이 임의 기관 ID를 생성하면 안 된다.

## 10. 표시 순위와 질문 후보군

### 10.1 두 종류의 점수

상품 `p`에 대해 다음을 계산한다.

```text
realizable(p)
  = base rate
  + VERIFIED 또는 판정 가능한 DECLARED_FEASIBLE 우대금리

optimistic_upper(p)
  = realizable(p)
  + 아직 거절·불충족으로 확정되지 않은 검수 완료 우대금리의 최대 추가분
```

광고 최고금리는 원칙적으로 조건 그래프의 사용자별 상한을 대체하지 않는다. 다만 기간·금액 입력 전이라 시나리오 상한을 아직 계산할 수 없는 상품은 후보 누락을 막기 위해 공시 최고금리를 임시 상한으로 사용한다. 이 값은 순위·후보 탐색에만 사용하고 사용자 예상금리로 쓰지 않는다.

예상이자 정렬을 사용하는 화면에서는 동일한 원리로 backend cash-flow 계산 결과의 `realizable_interest`와 `optimistic_interest_upper`를 사용한다.

### 10.2 frontier 정의

```text
visible_top3 = top 3 by optimistic_upper
cutoff       = realizable score of visible rank 3

frontier = visible_top3
         ∪ { p outside top3 | optimistic_upper(p) >= cutoff }
```

- 표시 순위는 가능한 최고점수로 정렬하지만, 카드의 예상금리·예상이자는 실현 가능 값으로 별도 표시한다.
- 후보군은 표시 Top 3뿐 아니라 현재 3위가 조건을 충족하지 못할 경우 진입할 수 있는 모든 외부 challenger까지 포함한다.
- 답변을 반영할 때마다 전체 관련 상품의 실현 가능 점수, 상한, cutoff, frontier를 다시 계산한다.
- 성능상 한 턴에 외부 challenger 하나만 질문 대상으로 가져올 수 있지만, 전체 진입 가능 상품을 계산에서 누락하면 안 된다.
- 가입 불가가 확정된 상품은 frontier에서 제거한다.

## 11. 다음 질문 선정 알고리즘

### 11.1 질문 단위

planner는 개별 상품 조건이 아니라 `QuestionSpec`으로 묶인 사용자 변수를 평가한다. 같은 변수라도 subject, 기관, 시간창, 값 스키마가 다르면 별도 질문이어야 한다.

```json
{
  "question_id": "q:CARD_MONTHLY_SPEND_LIMIT:GLOBAL:v1",
  "family_id": "CARD_MONTHLY_SPEND_LIMIT",
  "variable_id": "CARD_MONTHLY_SPEND_LIMIT",
  "value_schema": {"type": "money", "currency": "KRW", "period": "MONTH"},
  "scope": {"type": "GLOBAL_ACTION_TYPE", "id": "CARD_SPEND"},
  "bound_requirement_ids": ["req:a:1", "req:b:3", "req:c:2"],
  "affected_product_ids": ["a", "b", "c"],
  "options": [0, 200000, 300000, 500000],
  "prompt_template_id": "monthly-card-spend-limit.v1"
}
```

### 11.2 우선순위

가중합 하나로 섞지 않고 다음 사전식 우선순위를 MUST 사용한다.

1. 현재 Top 3의 미확인 `ELIGIBILITY` gate를 닫는가
2. 현재 Top 3와 frontier에서 영향을 받는 상품 수가 많은가
3. Top 3 진입·이탈·순서 변경 가능성을 얼마나 줄이는가
4. 답변 재사용 범위가 넓고 사용자 부담이 낮은가
5. 안정적인 deterministic tie-breaker 순서

상품 수만 많고 Top 3 결과를 바꾸지 못하는 질문은 선택하면 안 된다.

### 11.3 정량 질문 우선

같은 변수를 임계값별 예/아니요로 여러 번 묻는 대신 가능한 경우 하나의 정량 질문으로 묶는다.

```text
월 카드 실적은 최대 얼마까지 가능하신가요?
[사용 안 함] [20만원] [30만원] [50만원] [직접 입력]
```

정량화할 수 없는 `NEW_CARD_ISSUANCE`, `MARKETING_CONSENT`, `SALARY_ACCOUNT_CHANGE` 등은 선택형 또는 이진 질문을 사용한다.

### 11.4 기본 질문과 동적 조건 질문의 순서

상품 비교 자체에 필요한 기본 입력은 동적 우대조건 질문보다 먼저 확보한다.

1. 사용 목적과 상품군
2. **얼마씩, 어느 주기로, 얼마나 모을지**와 최대 부담 금액·필수 기간
3. 명시한 기관 제외, 상품군 제한 등 검색 범위
4. frontier에 실제 영향을 주는 가입조건과 일반 우대조건
5. 과거 예금·적금·청약 보유 기관처럼 여러 상품에 재사용되는 이력 질문
6. 확률형 조건을 포함한 상품별 특수조건

위 순서는 모든 질문을 무조건 묻는 고정 설문을 뜻하지 않는다. 3~6은 현재 frontier와 Top 3에 영향을 줄 때만 묻는다. 특히 확률형 조건은 낙관 상한 기준으로 Top 3에 들어올 수 있을 때만 대상이 되며, 기본 입력과 더 넓게 재사용되는 일반 조건보다 먼저 묻지 않는다.

### 11.5 의사코드

```python
def select_next_question(products, session):
    evaluated = evaluate_realizable_and_upper(products, session)
    visible_top3 = top_k(evaluated, k=3, key="realizable")
    cutoff = visible_top3[-1].realizable if len(visible_top3) == 3 else MIN_SCORE
    frontier = visible_top3 + [
        p for p in evaluated
        if p not in visible_top3
        and p.optimistic_upper >= cutoff
        and not p.eligibility_false
    ]

    specs = group_unresolved_requirements_by_user_variable(frontier)
    specs = [q for q in specs if not session.is_acknowledged(q.family_id, q.scope)]
    specs = [q for q in specs if can_change_top3(q, evaluated, frontier)]

    if not specs:
        return CompletionResult(...)

    return min(specs, key=lexicographic_priority_key)
```

### 11.6 보유 기관 선택 UI

`PRIOR_PRODUCT_INSTITUTIONS`는 자연어 입력과 선택 UI를 함께 지원한다.

- 은행은 기본 화면에 로고가 포함된 다중 선택 toggle로 보여준다.
- 저축은행은 기본 화면에 개별 기관을 모두 펼치지 않고 `저축은행` 버튼 하나를 보여준다.
- `저축은행` 버튼을 누르면 상품 목록 아래가 아니라 viewport 위의 작은 modal/popover에서 저축은행 선택지를 연다.
- 은행과 저축은행은 각각 가나다순으로 정렬한다.
- 저축은행도 로고가 있으면 표시하고, 없으면 일관된 fallback mark를 쓴다.
- 중복 선택을 허용한다.
- 선택을 확정하면 popover를 닫고, 질문 본문에는 선택한 기관 chip만 축약해 보여준다. 전체 목록은 다시 열 때만 노출한다.
- `해당 없음`은 명시적인 선택 상태로 저장하며 기관 목록과 동시에 선택할 수 없다.
- UI가 보낸 문자열이 아니라 canonical institution ID를 세션 상태에 저장한다.

## 12. 질문 생성 AI와 답변 해석 AI

같은 모델을 사용해도 되지만 prompt와 output schema는 반드시 분리한다.

### 12.1 질문 렌더러

입력은 backend가 선택한 `QuestionSpec` 하나뿐이다. 알려진 질문 가족은 deterministic 템플릿을 우선한다.

허용:

- 짧고 자연스러운 한국어 문장 생성
- backend가 제공한 선택지와 단위 표현

금지:

- 다른 조건이나 상품을 새로 선택
- 금리, 상품 사실, 가입 가능성을 추론 또는 추가
- 질문 순서를 바꾸거나 두 질문을 임의로 합침

출력 예:

```json
{
  "question_id": "q:CARD_MONTHLY_SPEND_LIMIT:GLOBAL:v1",
  "text": "카드 실적은 한 달에 최대 얼마까지 가능하신가요?",
  "option_labels": ["사용하지 않음", "20만원", "30만원", "50만원", "직접 입력"]
}
```

### 12.2 답변 해석기

입력은 현재 `QuestionSpec`, 사용자 발화, 허용된 canonical ID 목록이다. 출력은 `AnswerPlan`이다.

```json
{
  "active_question_answer": {
    "question_id": "q:CARD_MONTHLY_SPEND_LIMIT:GLOBAL:v1",
    "state": "DECLARED_FEASIBLE",
    "value": {"amount": 300000, "currency": "KRW", "period": "MONTH"}
  },
  "additional_updates": [
    {"kind": "INSTITUTION_EXCLUSION", "institution_id": "suhyup-bank"},
    {"kind": "USER_CONDITION", "variable_id": "AUTO_TRANSFER", "state": "DECLARED_FEASIBLE", "value": true}
  ],
  "unresolved_fragments": []
}
```

해석기는 다음을 하면 안 된다.

- 금리나 이자를 계산한다.
- 다음 질문을 고른다.
- 허용 목록에 없는 상품·기관·조건 ID를 만든다.
- “가능”을 임의의 금액 이상 충족으로 바꾼다.
- 답변하지 않은 조건을 거절 또는 동의로 채운다.

backend는 plan 전체를 검증한 뒤 한 transaction으로 반영하고 한 번만 재계산한다. 예를 들어 “카드는 싫고 자동이체는 가능하고 수협은 빼줘”를 한 턴에서 처리할 수 있어야 한다.

## 13. 완료 조건

고정 질문 개수나 시간 예산으로 완료를 결정하지 않는다.

다음 조건을 모두 만족하면 질문을 종료한다.

1. 현재 Top 3의 미확인 hard eligibility gate가 없다.
2. 아직 묻지 않았고 acknowledged되지 않은 사용자 변수 중 Top 3 진입·이탈·순서를 바꿀 수 있는 것이 없다.
3. 현재 입력으로 예상금리 또는 예상이자를 계산하는 데 필요한 기본 금액·기간·현금흐름 정보가 있다.

완료 결과는 다음 중 하나를 명시한다.

```text
STABLE_TOP3
PROVISIONAL_USER_STOPPED
PROVISIONAL_DATA_INCOMPLETE
INSUFFICIENT_ELIGIBLE_PRODUCTS
```

- 사용자가 구체화를 원하지 않아 질문 가족을 acknowledged 처리한 경우 질문은 끝내되 결과를 잠정으로 표시한다.
- frontier에 3위 진입 가능한 상품이 더 없으면 “현재 조건에서 순위를 바꿀 추가 확인 사항이 없습니다”에 해당하는 완료 응답을 보낸다.
- 사용자는 완료 뒤에도 사실을 정정할 수 있다. 이때 dependency graph로 영향을 받는 requirement만 무효화하고 재계산한다.

## 14. 상세 화면 계약

모든 상품 상세 화면은 동일한 계산 상태를 사용해야 한다. 목록과 상세가 서로 다른 최고금리·예상금리를 계산하면 안 된다.

상세 화면은 최소한 다음을 표시한다.

1. 현재 적용 가능한 예상금리와 계산 기준
2. 사용자의 금액·기간에 따른 예상이자
3. 우대금리별 상태: `적용 예상`, `확인 전`, `적용 안 함`, `확인 완료`
4. 아직 확인 전인 조건을 충족할 경우의 추가 가능 수익률. 단, 현재 예상금리와 시각적으로 분리한다.
5. 가입조건과 그 판정 상태
6. 데이터 기준일과 공식 원문 링크
7. 작은 회색 포괄 면책 문구

수수료 면제 등 `INFORMATION_ONLY` 항목은 기본 핵심정보 영역에 넣지 않고, 사용자가 요청했을 때 부가정보로 보여준다.

## 15. 저장과 수정 정책

- 조건 답변, 제외 기관, 질문 acknowledge 상태는 검색 세션 scope가 기본이다.
- 영구 저장은 별도 동의와 개인정보 정책이 마련된 뒤에만 추가한다.
- 각 상태에는 `source_turn_id`, `question_id`, timestamp, interpreter version을 저장한다.
- 사용자가 값을 수정하면 해당 변수에 의존하는 eligibility/rate node만 invalidation한다.
- 기관 alias registry 변경은 기존 답변의 canonical ID를 마이그레이션할 수 있어야 한다.

## 16. 단계별 구현 계획

### Phase 0 — schema와 관측성

- 위 모델의 enum과 JSON schema를 먼저 고정한다.
- 현재 planner와 목표 planner의 frontier, 선택 질문, 완료 이유를 함께 기록하는 shadow trace를 추가한다.
- 상품군별 데이터 coverage와 unmapped condition 지표를 만든다.

### Phase 1 — 파생 Requirement Compiler

- 기존 `semantic_domain`, normalized preferential rule, 원문 근거에서 `ConditionRequirement`를 생성한다.
- AI 분류는 `REVIEW_REQUIRED`로만 저장한다.
- 카드·신규 계좌·보유 이력 등 우선순위 높은 family부터 human verification한다.
- 기존 `metadata.features` 기반 대화 분기를 제거하기 전에 shadow 비교한다.

### Phase 2 — 세션 상태와 Top 3 planner

- `UserConditionState`, 질문 가족 acknowledge, answer scope를 구현한다.
- 실현 가능 점수와 낙관 상한을 분리한다.
- frontier와 사전식 질문 선택기를 구현한다.
- 완료 이유를 API 응답에 포함한다.

### Phase 3 — LLM 계약 분리

- 질문 renderer와 answer interpreter의 prompt/schema를 분리한다.
- 허용 ID 검증, 원자적 multi-intent patch, hallucinated ID 거부를 구현한다.
- 알려진 질문 가족에는 템플릿을 두고 LLM 장애 시 fallback한다.

### Phase 4 — UI

- 목록은 실현 가능 점수만 표시한다.
- 상세 화면에 조건 상태와 낙관 가능분을 분리해 표시한다.
- 사용자가 조건 답변을 수정할 수 있는 진입점을 제공한다.
- 잠정 결과와 데이터 불완전 상태를 과장 없이 표현한다.

### Phase 5 — 데이터 확장과 전환

- 상품군과 condition family 단위로 활성화한다.
- 검수되지 않은 family는 기존 안전 경로를 유지한다.
- shadow 지표와 회귀 테스트가 기준을 통과한 family만 실제 순위 계산에 활성화한다.
- 충분히 전환된 뒤 중복 feature policy와 상품별 하드코딩을 제거한다.

## 17. 모듈 책임 제안

현재 구조를 기준으로 책임을 다음처럼 분리한다. 파일명은 구현 중 조정할 수 있으나 경계는 유지해야 한다.

| 책임 | 제안 위치 |
|---|---|
| 원천 조건 → requirement compile | `src/eligibility/catalog/requirement_compiler.py` |
| requirement/user state schema | `src/eligibility/schema/condition_requirement.py` |
| 실현 가능 점수·상한 계산 | `src/eligibility/search/rate_strategy.py` 또는 별도 evaluator |
| Top 3 frontier와 질문 선택 | `src/eligibility/search/questions.py` |
| 질문 템플릿/표현 | `src/eligibility/question_policy.py` |
| AnswerPlan schema/검증 | `src/eligibility/schema/conversation.py` |
| 세션 ledger와 invalidation | `src/eligibility/conversation_ledger.py` |
| 기관 canonical resolver | `src/eligibility/search/institution_names.py` |
| 상세 표시용 projection | `src/eligibility/search/recommendation.py` 및 web adapter |

`application_service.py`는 orchestration만 맡고 상품별 조건 문구나 특정 상품 ID 분기를 추가하지 않는 것을 목표로 한다.

## 18. 필수 테스트

### 18.1 단위 및 속성 테스트

1. 하나의 정량 사용자 변수가 연결된 모든 requirement를 동시에 판정한다.
2. 모호한 “가능” 답변은 우대금리를 적용하지 않고 같은 질문을 반복하지 않는다.
3. `RATE_BENEFIT=false`는 상품을 제거하지 않고 해당 우대분만 제거한다.
4. `ELIGIBILITY=false`는 상품을 제외한다.
5. `INFORMATION_ONLY`는 질문·금리·순위에 영향을 주지 않는다.
6. AND/OR/선택 그룹/우대 한도가 정확히 적용된다.
7. 외부 상품은 `optimistic_upper >= third.realizable`일 때만 frontier에 들어온다.
8. 순위를 바꿀 challenger가 없으면 완료한다.
9. 사용자 정정은 해당 변수 의존 node만 무효화한다.
10. `PARTIAL/UNAVAILABLE`을 false 또는 조건 없음으로 해석하지 않는다.
11. `REVIEW_REQUIRED` 조건은 실제 사용자 금리와 순위를 바꾸지 않는다.
12. 광고 최고금리는 표시 예상금리로 승격되지 않는다.
13. LLM이 만든 미등록 ID, 금리, ranking patch를 거부한다.
14. multi-intent 답변은 전부 검증된 경우에만 원자적으로 반영한다.

### 18.2 대표 시나리오

#### 카드 임계값 통합

- A는 월 20만원, B는 30만원, C는 50만원 실적을 요구한다.
- 질문은 세 번의 예/아니요가 아니라 한 번의 최대 가능 금액 질문이어야 한다.
- 30만원 답변은 A/B만 충족시키고 C는 미충족으로 판정해야 한다.

#### 모호한 답변

- 사용자가 “카드 가능”이라고 답한다.
- 카드 우대는 표시 예상금리에 적용하지 않는다.
- 카드 조건은 상세에 `확인 전`으로 표시한다.
- 동일 세션에서 카드 금액을 다시 자동 질문하지 않는다.

#### 확률형 상품

- 검수된 확률형 우대가 낙관 상한 기준으로 Top 3에 들어올 수 있을 때만 질문 후보가 된다.
- 기본 금액·기간 및 일반 가입조건보다 무조건 먼저 묻지 않는다.
- 확률형 최고 우대는 광고 최고금리와 현재 예상금리를 분리해 표시한다.

#### 기관 제외 자유발화

- 사용자가 “수협은행 싫어”라고 말하면 resolver로 해당 기관을 canonicalize한다.
- 해당 기관 상품을 검색 세션에서 제외하고 한 번 재계산한다.
- LLM이 문자열 유사도만으로 다른 기관까지 제외하면 안 된다.

## 19. 관측 지표와 출시 기준

최소한 다음을 상품군·condition family별로 측정한다.

- requirement compile coverage와 human verified 비율
- unmapped/review-required rule 수
- 현재 planner와 새 planner의 Top 3 불일치율
- 질문 수, 반복 질문률, 모호 답변률
- 질문 하나가 닫은 requirement/product 수
- 완료 후 사용자 정정으로 Top 3가 바뀐 비율
- 표시 예상금리가 검증된 조건 그래프로 재현되지 않는 건수
- 목록과 상세 화면의 예상금리 불일치 건수

출시 family는 필수 회귀 테스트를 통과하고, 검수되지 않은 조건이 순위에 반영되지 않으며, 목록/상세 계산 결과가 동일해야 한다.

## 20. 현재 코드와 목표의 차이

| 영역 | 현재 기준선 | 목표 |
|---|---|---|
| 후보 범위 | ranking-aware 질문기는 있으나 condition grouping이 제한적 | 표시 Top 3와 낙관 진입 frontier를 명시적으로 분리 |
| 질문 단위 | fact type/문구 또는 상품별 missing request 중심 | qualifier가 포함된 canonical 사용자 변수 |
| 조건 데이터 | normalized rule과 legacy feature가 혼재 | 검수된 파생 `ConditionRequirement`가 대화 기준 |
| 모호한 답변 | 질문 skip/unknown 처리가 여러 경로에 분산 | `WILLING_UNSPECIFIED`/`ACKNOWLEDGED_UNKNOWN` + family acknowledge |
| LLM 역할 | intent·답변·일부 질문 표현의 경계가 혼재 | renderer와 interpreter schema 분리, 계산 권한 없음 |
| 수수료 혜택 | 조건 데이터에 섞일 수 있음 | `INFORMATION_ONLY`, 기본 질문·순위 제외 |
| 완료 | 질문 흐름과 skip 상태 중심 | Top 3 변경 가능성이 사라졌는지로 종료 |
| 상세 화면 | 상품군별 projection 차이 가능 | 목록과 동일 계산 상태 및 조건별 상태 표시 |

이 차이표는 “현재 구현 완료 목록”이 아니라 migration 범위를 정의한다. 각 phase가 끝날 때 구현 보고서에서 실제 반영 파일, schema version, 테스트 결과를 별도로 기록해야 한다.
