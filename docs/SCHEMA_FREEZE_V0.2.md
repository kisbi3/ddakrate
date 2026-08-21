# Schema Freeze Decision — Financial Eligibility Engine v0.2

## 판정

# FREEZE_WITH_MINOR_CHANGES

## 판단 근거

동일한 `FinancialEligibilityEngine`, `RuleEvaluator`, `RateEngine`, `FactResolver` 구조로 다음 네 가지 서로 다른 압력을 처리했다.

1. **신한은행 청년 처음적금**
   - lookback interval overlap
   - coverage-aware absence
   - recurring distinct months
   - Missing Fact와 네 상태
2. **카카오뱅크 26주적금**
   - ordered occurrence sequence
   - 연속 성공과 단순 성공 건수 분리
   - 실패 후 수동 보정 불인정
3. **IBK부모급여우대적금**
   - 가입자와 실적 주체 분리
   - 검증된 부모-자녀 관계 traversal
   - 기관 고유 가족등록정책의 Service Fact 분리
4. **하나은행 달려라 하나 적금**
   - 은행 고유 건강서비스를 DSL operator로 만들지 않음
   - Institution Service definition과 User Fact provenance chain 분리
   - 서비스가 확정한 누적거리 Fact를 generic comparison으로 평가

이 과정에서 필요한 핵심 변화는 두 generic primitive와 몇 개의 fact schema 추가였으며, 다음은 그대로 유지됐다.

- Rule AST가 실행 의미를 소유
- Resolver가 raw 데이터를 판정 가능한 Fact로 변환
- Institution Service가 금융회사 고유 의미와 lineage를 보유
- User Fact가 사람·시간·출처를 보유
- deterministic evaluator가 최종 판정
- `SATISFIED / ACHIEVABLE / UNSATISFIABLE / UNKNOWN`
- Rate/Interest/Evaluation Trace 분리

따라서 상품 추가가 기존 판정 구조 자체를 깨뜨렸다고 볼 근거는 없다.

## 왜 FREEZE_V0_2가 아닌가

아래 항목은 실제 다음 fixture에서 소규모 보완 가능성이 높다.

- 진행 중인 schedule의 미도래 회차와 실제 실패 구분
- 관계 방향/역할
- WEEK bucket anchor와 timezone
- Account lifecycle -> effective holding resolver
- FX normalization resolver

모두 아키텍처 재설계가 아니라 primitive option, schema field, resolver 추가 수준이다.

## Freeze 범위

다음은 v0.2 기준으로 동결한다.

```text
Rule DSL
= generic deterministic semantics

Resolver
= raw event/account/transaction/relationship normalization

Institution Service
= institution-specific service definition and provenance

User Fact
= subject-aware temporal fact with authority source

Visualization
= Evaluation Trace / Facts / Rule AST renderer only
```

다음은 아직 동결하지 않는다.

- 모든 상품에 공통인 최종 Product metadata schema
- Account lifecycle normalization 세부정책
- FX conversion basis taxonomy
- WEEK period semantics
- production DB 물리구조

## 다음 단계 진입 조건

PDF -> LLM Rule Extractor의 제한된 pilot로 넘어갈 수 있다. 단, extractor output은 곧바로 ACTIVE Rule이 아니라 다음 파이프라인을 따라야 한다.

```text
PDF / official page
-> LLM draft Rule AST
-> JSON Schema validation
-> semantic validation
-> existing golden fixture differential test
-> human review
-> ACTIVE RuleVersion
```

첫 pilot의 성공 기준은 “문장을 그럴듯하게 요약”하는 것이 아니라 다음이다.

- AND/OR scope 보존
- time anchor 보존
- aggregation target 보존
- absence/coverage 요구 표시
- Institution Service reference와 generic Rule 분리
- source page/section provenance 생성
- human-reviewed fixture와 구조적 diff가 설명 가능
