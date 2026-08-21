# Financial Eligibility Engine v0.2 — Design Issues

이 문서는 v0.1에서 발견된 문제와 v0.2 Integration Sprint에서 새로 확인된 한계를 기록한다. 현재까지 아키텍처 전면 변경이 필요한 `HIGH` 이슈는 없다.

---

## DESIGN ISSUE 001 — Conditional upper rate와 광고 최고금리의 의미 충돌

**상태:** RESOLVED IN v0.2

**현재 Rule:**  
기존 `conditional_upper_rate`는 `UNKNOWN` branch가 유리하게 해소될 때의 개인별 상한이지만, 이름만 보면 상품의 광고 최고금리와 혼동될 수 있었다.

**문제:**  
특정 reward branch가 이미 `UNSATISFIABLE` 또는 명시적 `false`로 확정된 뒤에도 광고 최고금리를 그대로 조건부 상한으로 반환하면 개인별 판정 의미가 사라진다.

**최소 수정안:**  
직렬화 필드를 다음처럼 분리했다.

```text
advertised_max_rate
confirmed_rate
realizable_rate
user_specific_conditional_upper_rate
```

`user_specific_conditional_upper_rate`는 `SATISFIED / ACHIEVABLE / UNKNOWN` reward만 포함하고 `UNSATISFIABLE` reward는 제외한다. v0.1 코드 호출 호환을 위해 Python property `conditional_upper_rate`는 남겨두되, v0.2 JSON에는 명확한 필드명을 사용한다.

**기존 설계 영향:** LOW

---

## DESIGN ISSUE 002 — NOT_EXISTS에는 시간범위 coverage 증명이 필요함

**상태:** RESOLVED IN v0.2

**현재 Rule:**  
`NOT_EXISTS account WHERE ... OVERLAPS lookback_window`로 첫거래 같은 absence 조건을 평가한다.

**문제:**  
검색 결과가 0건이어도 MyData 또는 기관 이력이 required window 전체를 제공하지 않았다면 실제 부재를 확정할 수 없다.

**최소 수정안:**  
`DataCoverage`와 Rule의 `CoverageRequirement`를 추가했다.

```text
required window fully covered
AND
matching entity does not exist
```

일 때만 `SATISFIED`다. Coverage가 부족하면 `UNKNOWN / ABSENCE_QUERY_COVERAGE_INCOMPLETE`를 반환한다. 여러 coverage interval의 합집합이 required window를 연속적으로 덮는 경우도 지원한다. 기존 `coverage_fact_type` boolean 경로는 v0.1 fixture 호환용으로만 유지한다.

**기존 설계 영향:** LOW

---

## DESIGN ISSUE 003 — 진행 중인 schedule의 미도래 회차와 실패 회차 구분

**상태:** OPEN / DEFERRED MINOR CHANGE

**현재 Rule:**  
`COUNT_CONSECUTIVE`는 평가시점까지 관측된 `ScheduledOccurrence`를 순서대로 검사하고 첫 누락 또는 predicate 실패에서 중단한다.

**문제:**  
이번 카카오 fixture는 7회 또는 26회 평가시점 이후의 completed schedule이라 누락을 곧 실패로 볼 수 있다. 하지만 가입 직후 실시간 평가에서는 아직 도래하지 않은 회차가 없는 것이 정상이다. 이를 곧바로 `UNSATISFIABLE`로 처리하면 안 된다.

**실제 구현 불가능 이유:**  
`ScheduledOccurrence` 목록만으로는 “회차가 아직 예정됨”, “데이터 coverage가 없음”, “실제로 실패/누락됨”을 항상 구분할 수 없다.

**최소 수정안:**  
후속 버전에서 다음 중 하나를 추가한다.

- schedule definition의 `expected_occurrence_count`와 `observation_horizon`
- occurrence-domain `DataCoverage`
- `future_achievement` 또는 pending status semantics

현재 primitive는 completed/observed sequence 판정에 사용하도록 문서화한다.

**기존 설계 영향:** LOW

---

## DESIGN ISSUE 004 — 관계 모델의 방향성과 역할 명시

**상태:** OPEN / DEFERRED MINOR CHANGE

**현재 Rule:**  
`PersonRelationship(person_a, person_b, relationship_type)`와 `RELATED_PERSON` selector를 사용한다.

**문제:**  
`PARENT_CHILD`는 이번 IBK 합산에서는 대칭적으로 탐색해도 충분하지만, 향후 “법정대리인”, “친권자”, “부양자”처럼 방향과 역할이 중요한 조건이 등장할 수 있다.

**최소 수정안:**  
실제 상품 fixture가 요구할 때 `person_a_role / person_b_role` 또는 directional relationship type을 추가한다. 은행 전용 가족등록정책은 계속 Institution Service Fact로 둔다.

**기존 설계 영향:** LOW

---

## DESIGN ISSUE 005 — WEEK period의 anchor/timezone semantics

**상태:** OPEN / DEFERRED MINOR CHANGE

**현재 Rule:**  
`COUNT_DISTINCT_PERIODS(period=WEEK)`는 ISO calendar week를 사용한다.

**문제:**  
상품에 따라 “가입일 기준 7일 bucket”, “월요일 시작 주”, “은행 영업주” 등 다른 week 정의가 필요할 수 있다. timestamp timezone도 명시돼야 날짜 경계가 안정적이다.

**최소 수정안:**  
실제 WEEK fixture가 생기면 `period_anchor`, `week_start`, `timezone` 필드를 추가한다. 현재 DAY/MONTH fixture와 카카오 sequence 판정에는 영향이 없다.

**기존 설계 영향:** LOW

---

## DESIGN ISSUE 006 — AccountLifecycleEvent는 schema만 있고 effective holding resolver는 미구현

**상태:** OPEN / SHOULD

**현재 Rule:**  
`OPENED / CANCELLED / WITHDRAWN / RENAMED / CLOSED`와 lineage용 필드를 schema에 추가했다.

**문제:**  
KB 상품처럼 취소계좌는 제외하고 철회계좌는 포함하며 명의변경 시 원 신규일을 사용하는 조건은 lifecycle event를 `AccountHoldingInterval`로 정규화하는 resolver가 있어야 실행된다.

**최소 수정안:**  
KB fixture를 추가할 때 `AccountLifecycleResolver -> effective AccountHoldingInterval`을 구현하고 취소/철회/명의변경 회귀테스트를 만든다. Rule DSL primitive는 추가하지 않는다.

**기존 설계 영향:** LOW

---

## DESIGN ISSUE 007 — NormalizedMonetaryAmount는 schema만 있고 FX resolver는 미구현

**상태:** OPEN / SHOULD

**현재 Rule:**  
원화·외화 원금과 환산값, 환산기준, 시점, source reference를 저장할 수 있다.

**문제:**  
해외송금 조건의 USD equivalent를 실제로 계산하려면 기관별 환율 기준과 환산시점이 필요하다.

**최소 수정안:**  
하나더이지 fixture에서 `FXNormalizationResolver`를 구현한다. 환율 변환을 DSL operator로 만들지 않고, provenance가 있는 normalized fact를 Rule이 비교하도록 유지한다.

**기존 설계 영향:** LOW

---

## DESIGN ISSUE 008 — Focused fixture와 상품 전체 금리모델의 구분

**상태:** DOCUMENTED SCOPE

**현재 Rule:**  
IBK와 하나 fixture는 각각 related-person, Institution Service provenance를 검증하는 branch만 모델링한다.

**문제:**  
`advertised_max_rate`는 상품 전체 metadata인데, fixture의 `confirmed_rate`는 모델링된 branch만 반영하므로 두 값의 차이를 “사용자가 받을 수 없는 금리”로 해석하면 안 된다.

**최소 수정안:**  
fixture와 결과 문서에 `focused regression slice`임을 명시한다. 전체 상품비교/ranking에 투입하기 전에는 공식 Rule 전체를 human-reviewed fixture로 완성한다.

**기존 설계 영향:** NONE
