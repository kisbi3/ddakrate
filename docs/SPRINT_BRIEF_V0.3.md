너는 **2026 금융 AI Challenge 출품작의 Main Track 개발자**다.

이번 작업은 새 프로젝트를 만드는 것이 아니다.

기존에 완성된:

`financial-eligibility-engine-v0.2.zip`

을 기준 코드로 사용하여,

**`Financial Eligibility Engine v0.3`** **— LLM Integration, Audit & Goal Tracking Sprint**

를 구현한다.

**Reasoning effort는 medium으로 진행하라.**

---

# 0. 반드시 먼저 읽어야 할 자료

프로젝트 Source의 다음 자료를 먼저 확인하라.

## 최우선

1. `Financial Eligibility Engine — LLM Integration, Audit & Goal Tracking Design v0.3.md`
2. `financial-eligibility-engine-v0.2.zip`

## 보조 자료

3. `금융상품 규칙 분석.txt`
4. `금융상품 기술검증.txt`
5. 필요시 공모전 기획서/기능명세서

---

# 1. Source of Truth 우선순위

구현상 충돌이 있으면 다음 순서로 판단한다.

```
1. Financial Eligibility Engine — LLM Integration, Audit & Goal Tracking Design v0.3.md
2. 실제 v0.2 코드와 테스트
3. 금융상품 규칙 분석.txt
4. 금융상품 기술검증.txt

```

단, v0.3 문서는 **Target Design**이다.

기존 v0.2의 deterministic core를 불필요하게 재작성하지 마라.

---

# 2. 이번 Sprint의 핵심 목표

이번 Sprint의 질문은 다음 하나다.

> **LLM이 공식 금융문서를 구조화하고 사용자와 자연어로 상호작용하되, 실제 금융조건 판정과 미래 목표 추적은 deterministic하게 수행되고, 모든 처리과정을 Audit Log로 재현할 수 있는가?**

이를 위해 다음을 구현한다.

```
1. Audit Event Log
2. Provider-independent LLM Gateway
3. Local / External API Adapter
4. Rule Extractor Pilot
5. ProductKnowledgeDraft + Validation
6. Future Intent Fact
7. 가입 후 Future Achievement 평가
8. GoalInstance / Goal Tracker
9. Alert Event
10. End-to-end trace correlation

```

---

# 3. 절대 변경하지 말아야 할 핵심 원칙

## 3.1 Financial Eligibility Engine은 LLM-independent deterministic core다

다음 기존 흐름을 유지한다.

```
ProductDefinition / Rule AST
+
UserFactStore
+
EvaluationContext
↓
FinancialEligibilityEngine
↓
RuleEvaluator
↓
RateEngine
↓
InterestEngine
↓
ProductEvaluation + Evaluation Trace

```

LLM이 다음을 직접 계산하거나 판정하면 안 된다.

```
가입 가능 여부
SATISFIED / ACHIEVABLE / UNSATISFIABLE / UNKNOWN
기간 계산
AND / OR / NOT
집계 결과
연속 성공
금리
이자
남은 기회
Goal 위험도
Alert trigger 여부

```

LLM은 자연어 ↔ 구조화된 데이터의 인터페이스다.

---

# 4. 이번 Sprint의 목표 아키텍처

개념적으로 다음 경계를 유지한다.

```
Official Document
    ↓
Document Parser
    ↓
LLM Rule Extractor
    ↓
ProductKnowledgeDraft
    ↓
Schema / Semantic Validation
    ↓
Human Review boundary
    ↓
Product Rule Store

User
    ↓
Application Layer
    ↓
LLM Gateway
    ↓
Structured Intent / Missing Fact Answer

Product Rule Store
Institution Service KG
User Fact Store
    ↓
Financial Eligibility Engine
    ↓
ProductEvaluation + Evaluation Trace
    ↓
Goal Tracker / Alert

```

Engine 자체에서 LLM HTTP request를 보내지 마라.

---

# 5. 가장 먼저 할 일 — v0.2 Baseline 확인

코드를 수정하기 전에:

1. v0.2 ZIP을 풀어 구조를 읽는다.
2. 기존 테스트를 전부 실행한다.
3. 테스트 개수를 기록한다.
4. 실패가 있으면 먼저 원인을 확인한다.
5. 기존 코드가 이미 제공하는 기능을 다시 구현하지 않는다.

이후 모든 구현에서 기존 regression test를 보존한다.

---

# 6. Phase 1 — Audit Event Log

이번 Sprint에서는 **Audit를 가장 먼저 구현한다.**

LLM을 붙인 뒤 디버깅하는 것이 아니라, LLM을 붙이기 전에 전체 처리과정을 기록할 기반을 만든다.

## 6.1 요구사항

Append-only event 구조를 만든다.

예:

```
AuditEvent
- event_id
- occurred_at

- request_id
- evaluation_id
- trace_id

- span_id
- parent_span_id

- component
- event_type

- entity_refs

- input_hash
- output_hash

- payload
- provenance

```

## 6.2 최소 Event Type

다음을 우선 구현하라.

```
REQUEST_RECEIVED

LLM_CALL_STARTED
LLM_CALL_COMPLETED
LLM_CALL_FAILED

SCHEMA_VALIDATION_COMPLETED
SEMANTIC_VALIDATION_COMPLETED

RULE_LOADED

DATE_EXPRESSION_RESOLVED

FACT_REQUESTED
FACT_CANDIDATES_FOUND
FACT_SOURCE_SELECTED
FACT_CONFLICT_DETECTED

DATA_COVERAGE_CHECKED

AGGREGATION_EXECUTED
COMPARISON_EXECUTED

FUTURE_CAPACITY_CHECKED

MISSING_FACT_CREATED
USER_FACT_RECEIVED

STATUS_DERIVED
REWARD_APPLIED
RATE_SUMMARY_CREATED

GOAL_CREATED
GOAL_PROGRESS_UPDATED
GOAL_FEASIBILITY_RECALCULATED

ALERT_TRIGGERED

```

기존 RuleEvaluator 전체를 과도하게 뜯어고쳐야 한다면 작은 instrumentation helper를 사용한다.

---

# 7. Correlation ID

모든 처리과정은 다음 ID로 연결한다.

```
request_id
evaluation_id
trace_id

```

필요하면 내부 span:

```
span_id
parent_span_id

```

를 사용한다.

예:

```
사용자 요청
request_id = REQ-001

상품 평가
evaluation_id = EVAL-001

사용자용 Trace
trace_id = TRACE-001

Rule 평가
span_id = RULE-12
parent_span_id = EVAL-001

```

LLM 호출, Rule 평가, Goal 생성, Alert 발생이 같은 request chain에서 추적 가능해야 한다.

---

# 8. Audit payload 보안

다음 정책을 구현한다.

```
LLM_LOG_PAYLOAD_MODE
=
NONE
HASHED
REDACTED
FULL_DEBUG

```

기본값은 `REDACTED` 또는 `HASHED`.

절대 로그에 남기지 않는다.

```
LLM_API_KEY
Authorization header
password
실제 인증 토큰

```

외부 API 사용 시 사용자 개인정보 raw prompt를 기본 저장하지 않는다.

---

# 9. Phase 2 — LLM Gateway

Provider-independent interface를 만든다.

개념적으로:

```
class LLMClient(Protocol):

    def generate_text(...):
        ...

    def generate_structured(...):
        ...

    def health_check(...):
        ...

```

## 최소 구현 Adapter

```
MockLLMAdapter
OpenAICompatibleAdapter

```

`OpenAICompatibleAdapter`는 반드시:

```
외부 API
또는
OpenAI-compatible local model server

```

모두 연결할 수 있도록 한다.

특정 공급자 SDK에 Engine 전체가 묶이지 않게 한다.

가능하면 HTTP client abstraction을 사용한다.

---

# 10. LLM Configuration

최소 다음 환경변수를 지원한다.

```
LLM_PROVIDER
LLM_BASE_URL
LLM_MODEL
LLM_API_KEY

LLM_TIMEOUT_SECONDS
LLM_MAX_RETRIES

LLM_TEMPERATURE
LLM_LOG_PAYLOAD_MODE

```

API key가 없을 때 Mock Adapter 테스트가 가능해야 한다.

실제 API key를 코드에 hard-code하지 않는다.

`.env.example`을 제공해도 좋다.

---

# 11. LLM Profile

동일 모델을 사용하더라도 용도를 구분한다.

```
RULE_EXTRACTION
SERVICE_EXTRACTION
INTENT_PARSING
QUESTION_GENERATION
RESULT_EXPLANATION

```

각 Profile은 가능하면 다음 metadata를 가진다.

```
prompt_template_id
prompt_template_version
response_schema_version
temperature
timeout

```

---

# 12. 이번 Sprint에서 반드시 실제 구현할 LLM 기능

우선순위는:

```
1. RULE_EXTRACTION
2. QUESTION_GENERATION 또는 최소 deterministic fallback

```

`INTENT_PARSING`, `RESULT_EXPLANATION`은 SHOULD 수준이다.

Web chatbot 전체를 만들지 않는다.

---

# 13. Phase 3 — ProductKnowledgeDraft

LLM Rule Extractor의 출력은 단순 `Rule AST`가 아니다.

다음 구조를 만든다.

```
ProductKnowledgeDraft
├─ document_id
├─ product_metadata
├─ rules
├─ service_references
├─ required_facts
├─ unresolved_items
├─ warnings
└─ extraction_metadata

```

구체적인 schema는 v0.3 설계문서를 따른다.

---

# 14. Rule Extractor가 반드시 분리해야 할 것

예:

> “급여클럽 월급봉투를 6개월 이상 받은 경우”

를 다음처럼 만들면 안 된다.

```
HAS_SHINHAN_SALARY_ENVELOPE_6M

```

대신:

```
Rule AST
=
COUNT_DISTINCT_PERIODS(
    period=MONTH,
    fact_type=SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED
) >= 6

```

그리고 별도로:

```
Service Reference
institution = SHINHAN
service = 급여클럽
concept = 월급봉투

```

```
Required Fact
SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED

```

```
Unresolved
월급봉투 인정정의는 별도 Service Definition 필요

```

를 생성한다.

은행 고유 의미를 DSL operator로 만들지 않는다.

---

# 15. Rule Extractor Input

가능하면 page-aware section 단위 입력을 지원한다.

최소 interface:

```
DocumentSection
- document_id
- document_name
- page
- section
- text
- source_url optional

```

실제 PDF binary parser를 지나치게 복잡하게 만들 필요는 없지만, Rule Extractor가 page/section provenance를 잃지 않아야 한다.

테스트에서는 text fixture를 사용해도 된다.

---

# 16. Structured Output

`RULE_EXTRACTION`에서는 자유형 text 결과를 source of truth로 사용하지 않는다.

반드시:

```
LLM
↓
Structured JSON
↓
Pydantic / JSON Schema validation

```

형태로 처리한다.

Validation 실패 시:

```
INVALID_DRAFT

```

로 처리하고 Product Rule Store에 ACTIVE 상태로 저장하지 않는다.

---

# 17. Semantic Validator

최소한 다음 검사를 구현하라.

```
unknown DSL operator
invalid reward
invalid time expression
rule without provenance
empty rule
service-specific operator usage
missing fact reference
invalid achievement mode

```

가능하다면 추가:

```
NOT / absence rule warning
nested OR warning
external service dependency warning

```

---

# 18. Human Review boundary

실제 UI는 필요 없다.

하지만 상태 모델은 구현한다.

예:

```
DRAFT
SCHEMA_VALID
SEMANTIC_VALID
REVIEW_REQUIRED
APPROVED
REJECTED
ACTIVE

```

LLM 결과가 자동으로 ACTIVE가 되지 않게 한다.

CLI 또는 function 수준에서:

```
approve_rule_draft(...)

```

정도면 충분하다.

---

# 19. Phase 4 — FactSemanticType

기존 UserFact를 확장하여 최소 다음 의미를 구분한다.

```
OBSERVED_FACT
OBSERVED_EVENT
DERIVED_FACT
FUTURE_INTENT

```

중요:

```
USER_DECLARED FUTURE_INTENT

```

를

```
OBSERVED_FACT

```

로 변환하지 않는다.

---

# 20. ACHIEVABLE 정의

이번 Sprint에서 반드시 다음 의미로 고정한다.

> 현재 확인된 정보, 남은 기간, 선행조건 및 사용자의 명시적 수행 의향을 기준으로 상품 규칙상 달성경로가 열려 있음.

`ACHIEVABLE`은:

```
실제 달성 예측
확정금리
보장

```

이 아니다.

---

# 21. Evaluation Phase

Rule에 다음 개념을 추가한다.

```
PRE_SUBSCRIPTION
POST_SUBSCRIPTION
BOTH

```

가능하면 기존 schema와 backward compatible하게 설계한다.

---

# 22. Achievement Mode

최소:

```
ACCUMULATIVE
CONSECUTIVE
MAINTAIN_UNTIL_DEADLINE
ONE_TIME_BEFORE_DEADLINE

```

를 표현할 수 있게 한다.

---

# 23. FutureAchievementSpec

v0.2의 Future Achievement 구조가 있다면 최대한 재사용·일반화한다.

최소 다음 정보를 표현한다.

```
intent_fact_type
missing_fact specification
capability_rule optional
achievement_mode
max_qualifying_units_per_opportunity
action
goal_template

```

---

# 24. Golden Flow 1 — 월급봉투 6개월

이 Flow는 반드시 구현하고 테스트한다.

상품 조건:

> 가입 후 인정기간 중 급여클럽 월급봉투 인정월 6개월 이상.

가입 전 실제 월급봉투 과거실적을 요구하는 조건으로 잘못 처리하지 마라.

## 가입 전

먼저 deterministic하게:

```
전체 가능한 인정월 수 >= 6 ?

```

를 계산한다.

불가능:

```
UNSATISFIABLE

```

가능하지만 사용자 의향 Fact 없음:

```
UNKNOWN
+
MissingFactRequest
+
resolution_strategy = ASK_USER

```

사용자에게 묻는다.

예:

> 이 상품의 +1.0%p 우대를 받으려면 가입 후 월급봉투 인정조건을 6개월 이상 달성해야 합니다. 이 조건을 목표로 관리할까요?

사용자가 YES:

```
FUTURE_INTENT = true
source = USER_DECLARED

```

재평가:

```
ACHIEVABLE
progress = 0 / 6 MONTH

```

---

# 25. User NO 처리

사용자가 미래조건 수행을 거부하면 MVP에서는:

```
status = UNSATISFIABLE
reason_code = USER_DECLINED

```

로 처리하여 realizable rate에서 제외한다.

단 UI-facing metadata나 reason은 **선택하지 않음**이라는 의미를 보존한다.

장기적으로 Status/Disposition 분리가 가능하도록 과도하게 결합하지 않는다.

---

# 26. Golden Flow 2 — 하루 30,000보 이상 300일

이 조건은 실제 금융상품 fact가 아니라 **synthetic stress test**다.

상품조건:

```
가입 후 평가기간 동안
하루 30,000보 이상인 날
300일 이상

```

Rule:

```
COUNT_DISTINCT_PERIODS(
    period=DAY,
    fact_type=QUALIFIED_DAILY_STEP_METRIC,
    where=DAILY_STEPS >= 30000,
    window=[subscription_date, maturity_date]
) >= 300

```

가입 전:

```
available_days
required_days

```

를 계산한다.

```
available_days < 300
→ UNSATISFIABLE

```

```
available_days >= 300
+
사용자 Intent UNKNOWN
→ UNKNOWN / ASK_USER

```

```
available_days >= 300
+
사용자 YES
→ ACHIEVABLE
progress = 0 / 300 DAY

```

LLM이 사용자의 성공확률을 추측하지 않는다.

---

# 27. Phase 5 — GoalInstance

가입 후 추적 가능한 조건에 Goal을 생성한다.

최소:

```
goal_id

user_id
product_id
subscription_id
rule_id

tracking_mode

metric
required_target
current_progress

window_start
deadline

remaining_required
remaining_opportunities
buffer

rate_reward_pp

status

created_from_evaluation_id

```

Goal이 Rule과 EvaluationResult에서 추적 가능해야 한다.

---

# 28. Goal Status

Rule Evaluation Status와 Goal Status를 섞지 않는다.

Goal Status:

```
ACTIVE
AT_RISK
COMPLETED
FAILED
PAUSED

```

---

# 29. Goal 생성 조건

최소:

```
Rule phase = POST_SUBSCRIPTION or BOTH
Rule Evaluation = ACHIEVABLE
User tracking intent = YES
Subscription confirmed or MVP user-confirmed
Goal template exists

```

이때 Goal을 생성한다.

---

# 30. Goal Tracker — ACCUMULATIVE

이번 Sprint에서는 반드시 누적형 Goal을 구현한다.

예:

```
월급봉투 6개월
3만보 300일

```

계산:

```
remaining_required
=
required_target - current_progress

```

```
buffer
=
remaining_opportunities - remaining_required

```

---

# 31. Goal 위험도

최소 deterministic logic:

```
buffer < 0
→ FAILED

buffer == 0
→ CRITICAL / AT_RISK

0 < buffer <= safety_threshold
→ AT_RISK

그 외
→ ACTIVE

```

Goal status enum과 Alert severity를 적절히 분리해도 된다.

---

# 32. Alert Event

실제 Push Notification infrastructure는 만들지 않는다.

다만 최소:

```
AlertEvent
- alert_id
- user_id
- product_id
- goal_id

- alert_type
- severity
- trigger_reason

- deterministic_payload
- created_at

```

를 구현한다.

예:

```
AT_RISK
CRITICAL
COMPLETED
FAILED
ACTION_REQUIRED
DATA_SYNC_REQUIRED

```

---

# 33. Alert Trigger는 deterministic

LLM이:

> 이 사용자는 위험해 보인다.

라고 결정해서는 안 된다.

코드가:

```
remaining_required = 51
remaining_opportunities = 50

```

를 보고:

```
FAILED

```

를 결정해야 한다.

LLM은 선택적으로 문장만 자연스럽게 만들 수 있다.

---

# 34. Consecutive Goal

SHOULD 수준이다.

시간이 충분하면 v0.2의 카카오 26주 `ScheduledOccurrence + COUNT_CONSECUTIVE`를 Goal Tracker와 연결한다.

예:

```
다음 자동이체 4회차
현재 1~3회 성공

```

Alert:

> 다음 자동이체가 실패하면 연속 성공조건을 더 이상 달성할 수 없습니다.

단, 실제 알림 문구는 deterministic fallback을 먼저 제공해도 된다.

---

# 35. LLM Question Generator

가능하면 구현한다.

입력:

```
MissingFactRequest

```

출력:

```
사용자 친화적 질문

```

다만 LLM이 새로운 조건·금리효과를 만들면 안 된다.

금리 영향 등 숫자는 MissingFactRequest의 값을 그대로 사용한다.

LLM 호출 실패 시 deterministic template fallback을 제공하면 좋다.

---

# 36. Rule Extraction Golden Set

`금융상품 규칙 분석.txt`와 기존 fixture를 Golden Set으로 사용한다.

최소한 다음 네 상품을 평가 가능하도록 구조를 만든다.

```
신한 청년 처음적금
카카오뱅크 26주적금
IBK 부모급여우대적금
하나 달려라 하나 적금

```

이번 Sprint에서 실제 LLM 결과가 Golden AST와 100% 일치할 필요는 없다.

대신 비교 가능한 evaluation infrastructure를 만든다.

---

# 37. Golden Diff Metrics

가능하면 다음을 측정한다.

```
rule recall
hallucinated rule count

logical structure mismatch
temporal mismatch
aggregation mismatch
reward mismatch

service reference recall
required fact recall

provenance match
unresolved item recall

```

정교한 semantic AST equivalence가 어렵다면 MVP에서는 canonical JSON / normalized structural diff로 시작해도 된다.

---

# 38. Rule Extractor Test 전략

실제 외부 API가 없어도 테스트가 가능해야 한다.

따라서:

```
MockLLMAdapter

```

에 정답 JSON을 넣고 전체 파이프라인:

```
DocumentSection
→ LLM Gateway
→ ProductKnowledgeDraft
→ Schema Validator
→ Semantic Validator
→ Review State

```

를 테스트한다.

실제 API integration test는 환경변수가 있을 때만 선택적으로 수행한다.

---

# 39. LLM Audit

모든 LLM call은 최소 다음 정보를 Audit Event에 기록한다.

```
purpose

provider
model
model_version optional

prompt_template_id
prompt_template_version

response_schema_version

input_document_id
input_document_hash
input_context_hash

output_hash

latency_ms
token_usage optional
retry_count

schema_validation_result
semantic_validation_result

error_code

```

Raw API key는 절대 기록하지 않는다.

---

# 40. 기존 Evaluation Trace와 Audit Log를 섞지 마라

`Evaluation Trace`는:

```
사용자에게 보여줄 금융판정 근거

```

다.

`Audit Log`는:

```
개발자 / 디버깅 / 재현용 내부 처리 기록

```

이다.

Evaluation Trace에 LLM raw response, internal span, retry count 등을 넣지 않는다.

---

# 41. Replay

가능하면 SHOULD 수준으로 간단한 Replay 또는 Audit dump CLI를 구현한다.

예:

```
python -m eligibility.audit show --trace-id TRACE-001

```

출력 예:

```
REQUEST_RECEIVED
LLM_CALL_STARTED
LLM_CALL_COMPLETED
SCHEMA_VALIDATION_COMPLETED
RULE_LOADED
FACT_REQUESTED
FUTURE_CAPACITY_CHECKED
STATUS_DERIVED
RATE_SUMMARY_CREATED
GOAL_CREATED

```

실제 command 구조는 기존 repo에 맞게 조정한다.

---

# 42. 이번 Sprint에서 하지 말아야 할 것

이번 작업 중 다음으로 새지 마라.

```
전체 Web UI
서비스 브랜딩

실제 Push Notification 서비스
실제 MyData API

모든 금융상품 크롤링
전체 상품 DB 구축

Neo4j 도입 자체를 목표로 삼기
Vector DB
Graph RAG

LLM Planner

LLM이 직접 금융판정

Net Benefit 고도화

Production authentication

```

특히 **LLM Agent를 만드는 Sprint가 아니다.**

---

# 43. 권장 코드 구조

현재 repo 구조를 먼저 존중하되 개념적으로는 다음 모듈이 필요하다.

```
src/eligibility/

    audit/
        models.*
        sink.*
        context.*
        instrumentation.*
        replay.*

    llm/
        client.*
        models.*
        profiles.*
        gateway.*
        mock_adapter.*
        openai_compatible_adapter.*

    ingestion/
        document.*
        rule_extractor.*
        knowledge_draft.*
        validators.*
        review.*

    schema/
        ...
        fact_semantics.*
        future_achievement.*

    goal/
        models.*
        factory.*
        tracker.*
        alert.*

    engine/
        기존 v0.2 코드 유지

tests/
    test_audit_*.*
    test_llm_gateway_*.*
    test_rule_extractor_*.*
    test_future_intent_*.*
    test_goal_tracker_*.*
    test_alert_*.*

```

파일명과 구조는 기존 프로젝트에 맞춰 조정 가능하다.

---

# 44. 반드시 추가할 테스트

## 기존

v0.2 기존 테스트 전부 유지.

## Audit

```
test_audit_events_are_append_only
test_trace_ids_propagate
test_fact_source_selection_logged
test_aggregation_execution_logged
test_sensitive_credentials_not_logged

```

## LLM Gateway

```
test_mock_structured_generation
test_openai_compatible_configuration
test_timeout_normalization
test_retry_behavior
test_invalid_structured_output

```

## Rule Extractor

```
test_rule_extractor_returns_knowledge_draft
test_service_reference_is_not_dsl_operator
test_required_facts_extracted
test_provenance_preserved
test_schema_failure_blocks_activation

```

## Future Intent

```
test_future_intent_is_distinct_from_observed_fact
test_post_subscription_missing_intent_returns_unknown
test_user_yes_and_time_feasible_returns_achievable
test_user_no_excludes_reward
test_insufficient_future_opportunities_unsatisfiable

```

## Goal

```
test_accumulative_goal_progress
test_goal_remaining_opportunities
test_goal_buffer
test_goal_at_risk
test_goal_failed_when_opportunities_insufficient

```

## Golden Flows

```
test_salary_envelope_6m_unknown_to_achievable

test_step_30000_300d_time_feasible
test_step_30000_300d_time_infeasible
test_step_30000_300d_user_intent

```

---

# 45. 반드시 보여줘야 할 End-to-End Demo 1

## 월급봉투 6개월

실행 전:

```
intent fact 없음

```

Engine:

```
UNKNOWN
MissingFactRequest = ASK_USER

```

사용자 YES를 구조화된 Fact로 추가:

```
semantic_type = FUTURE_INTENT
value = true
source = USER_DECLARED

```

재평가:

```
ACHIEVABLE
progress = 0/6 MONTH

```

가입 확인 후:

```
GoalInstance 생성

```

예시 실제 progress:

```
4/6
remaining_required = 2
remaining_opportunities = 2

```

결과:

```
AT_RISK / CRITICAL
+
AlertEvent

```

Audit Log로 전체 과정이 이어져야 한다.

---

# 46. 반드시 보여줘야 할 End-to-End Demo 2

## 30,000보 × 300일 synthetic condition

Case A:

```
available_days = 365
intent = UNKNOWN

```

결과:

```
UNKNOWN / ASK_USER

```

Case B:

```
available_days = 365
intent = YES

```

결과:

```
ACHIEVABLE
0/300

```

Case C:

가입 후:

```
current = 170
remaining_required = 130
remaining_opportunities = 150
buffer = 20

```

결과:

```
ACTIVE

```

Case D:

```
remaining_required = 51
remaining_opportunities = 50

```

결과:

```
FAILED

```

---

# 47. 실제 LLM API가 사용 가능한 경우

환경변수에 API 설정이 존재하면 optional smoke test를 실행해도 된다.

하지만:

- API key를 출력하지 않는다.
- 테스트 실패 때문에 전체 deterministic test가 실패하게 하지 않는다.
- network test는 integration marker 등으로 분리한다.

API 설정이 없으면 Mock Adapter로 완료한다.

---

# 48. 로컬 LLM 서버 지원

로컬 모델도:

```
LLM_BASE_URL=http://...

```

설정만 바꾸어 연결할 수 있어야 한다.

OpenAI-compatible API를 제공하는 로컬 서버라면 동일 Adapter로 연결한다.

별도 provider-specific code를 Engine 안에 작성하지 않는다.

---

# 49. 구현 중 새로운 특수조건을 발견하면

새 operator를 바로 추가하지 않는다.

다음 순서로 판단한다.

```
1. 기존 Rule DSL로 표현 가능한가?
2. generic resolver로 해결 가능한가?
3. Institution Service Fact로 해결 가능한가?
4. Future Intent로 해결 가능한가?
5. 그래도 불가능한가?

```

5번일 때만 범용 DSL extension을 검토한다.

은행 전용 operator를 만들지 않는다.

---

# 50. 완료 기준

## MUST

- v0.2 regression tests 전부 통과
- Audit Event model + append-only sink
- request/evaluation/trace correlation
- Mock LLM Provider
- OpenAI-compatible Adapter
- Local/External API 설정 지원
- structured RULE\_EXTRACTION
- ProductKnowledgeDraft
- schema validator
- 최소 semantic validator
- FactSemanticType
- Future Intent flow
- 월급봉투 6개월 Golden Flow
- 30,000보 × 300일 stress test
- GoalInstance
- accumulative Goal Tracker
- deterministic risk calculation
- AlertEvent
- 핵심 Audit instrumentation

## SHOULD

- Question Generator
- Result Explainer
- Consecutive Goal
- Audit Replay CLI
- Provider Health Check
- Golden Rule Diff metrics

## NOT REQUIRED

- Web UI
- actual Push
- actual MyData
- Graph DB
- Vector DB
- LLM Planner

---

# 51. 이번 Sprint의 최종 산출물

작업 완료 후:

```
financial-eligibility-engine-v0.3.zip

```

으로 패키징하라.

가능하다면 다음 문서도 repo 안에 생성한다.

```
V0_3_IMPLEMENTATION_REPORT.md

```

---

# 52. 최종 보고 형식

작업을 마친 후 다음 순서로 보고하라.

## A. Executive Summary

v0.3에서 실제 구현한 기능.

## B. v0.2 → v0.3 변경 파일

추가/수정 파일 목록.

## C. 기존 Regression 결과

기존 테스트 개수와 결과.

## D. Audit Architecture

- event model
- sink
- correlation
- instrumentation 위치

## E. LLM Gateway

- interface
- Mock Provider
- OpenAI-compatible Adapter
- Local server 설정 방법
- External API 설정 방법

## F. Rule Extractor

- 입력
- structured output
- ProductKnowledgeDraft
- validation
- review state

## G. 실제 Extraction Demo

Mock 또는 실제 API로 하나 이상의 입력을 보여준다.

출력에서:

```
Rule AST
Service Reference
Required Facts
Provenance
Unresolved

```

를 확인시킨다.

## H. Future Intent Flow

`UNKNOWN → 사용자 답변 → ACHIEVABLE`을 보여준다.

## I. 월급봉투 6개월 Golden Flow

상태, progress, rate impact, Goal 결과를 보여준다.

## J. 30,000보 × 300일 Stress Test

가입 전과 가입 후 상태를 보여준다.

## K. Goal Tracker

progress / remaining / buffer / status 결과.

## L. Alert 결과

실제 AlertEvent 예시.

## M. Audit Log 예시

하나의 `trace_id`에 대해 처음부터 끝까지 event sequence를 보여준다.

예:

```
REQUEST_RECEIVED
...
MISSING_FACT_CREATED
USER_FACT_RECEIVED
...
STATUS_DERIVED
RATE_SUMMARY_CREATED
GOAL_CREATED
ALERT_TRIGGERED

```

## N. 전체 테스트 결과

```
N passed
M failed

```

명확히 보고.

## O. 새로 발견된 Design Issue

- Blocker
- Minor
- Deferred

로 분류.

## P. 다음 단계 판단

다음 중 하나로 판정한다.

```
READY_FOR_APPLICATION_LAYER
READY_WITH_MINOR_FIXES
CORE_DESIGN_REVISIT_REQUIRED

```

그리고 그 이유를 설명한다.

---

# 53. 작업 방식

- 질문하지 말고 먼저 Source와 v0.2 코드를 읽고 시작한다.
- 기존 코드를 불필요하게 재작성하지 않는다.
- 작은 단위로 구현하고 매 단계 테스트한다.
- 테스트 없는 핵심 로직을 만들지 않는다.
- LLM 결과를 production truth처럼 신뢰하지 않는다.
- API 호출 없이도 전체 테스트가 가능하게 한다.
- 실제 API가 없어도 Mock Adapter로 Sprint를 완료할 수 있어야 한다.
- 금융판정의 결과는 반드시 deterministic core에서 나온다.
- 모든 핵심 처리과정은 Audit 가능해야 한다.

---

# 54. 이번 Sprint의 최종 질문

모든 구현이 끝난 뒤 다음 질문에 답하라.

> **“로컬 또는 외부 LLM이 금융상품 문서를 구조화하고 사용자에게 필요한 질문을 생성하더라도, 금융조건 판정·금리·미래목표 추적은 기존 deterministic engine에 남아 있으며, 문제가 발생했을 때 문서 추출 → Fact → Rule → Rate → Goal → Alert 중 어느 단계에서 문제가 생겼는지 Audit Log만으로 식별할 수 있는가?”**

이 질문에 코드와 테스트로 답하는 것이 이번 Sprint의 목적이다.