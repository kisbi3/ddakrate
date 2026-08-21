# Financial Eligibility Engine v0.3 구현 보고서

**버전:** 0.3.0  
**Sprint:** LLM Integration, Audit & Goal Tracking  
**기준 코드:** `financial-eligibility-engine-v0.2`  
**검증일:** 2026-08-19  
**최종 판정:** `READY_FOR_APPLICATION_LAYER`

---

## A. Executive Summary

`financial-eligibility-engine-v0.2`의 deterministic core를 유지하면서 다음 기능을 실제 코드와 테스트로 통합했다.

1. append-only `AuditEvent`와 correlation context
2. provider-independent `LLMClient` / `LLMGateway`
3. `MockLLMAdapter`와 local/external 공용 `OpenAICompatibleAdapter`
4. page/section-aware `RuleExtractor`
5. `ProductKnowledgeDraft`, schema/semantic validation, human review/activation boundary
6. `FactSemanticType`과 `FUTURE_INTENT`
7. 가입 후 조건의 deterministic future-capacity 평가
8. `GoalInstance`, 누적형 `GoalTracker`, deterministic `AlertEvent`
9. 문서 추출 → Fact → Rule → Rate → Goal → Alert의 end-to-end trace correlation
10. Audit replay CLI, Question Generator fallback, Golden structural diff, Mermaid 보강

핵심 아키텍처 경계는 유지됐다.

```text
LLM
= 문서/질문을 구조화하는 인터페이스

FinancialEligibilityEngine
= 가입조건, 집계, 시간, 상태, 금리, 이자, 미래 기회 판정

GoalTracker / AlertEngine
= progress, remaining, buffer, 위험도, alert trigger의 deterministic 계산
```

`FinancialEligibilityEngine` 내부에는 LLM HTTP 요청이나 특정 공급자 SDK 의존성이 없다. LLM 결과는 schema validation, semantic validation, human review를 통과하기 전에는 `ACTIVE` Rule로 사용할 수 없다.

### 최종 검증 결과

```text
v0.2 regression: 63 passed, 0 failed
v0.3 full suite: 116 passed, 0 failed
```

실제 외부 LLM API 설정은 실행 환경에 존재하지 않았으므로 외부 네트워크 smoke test는 수행하지 않았다. 대신 Mock provider와 주입 가능한 fake HTTP transport로 structured output, timeout, retry, 오류 정규화, local server 무-key 설정을 검증했다.

---

## B. v0.2 → v0.3 변경 파일

### B.1 수정 파일

```text
README.md
pyproject.toml
scripts/export_schemas.py

src/eligibility/__init__.py
src/eligibility/cli.py
src/eligibility/engine/evaluator.py
src/eligibility/engine/fact_resolver.py
src/eligibility/schema/__init__.py
src/eligibility/schema/enums.py
src/eligibility/schema/evaluation.py
src/eligibility/schema/rule.py
src/eligibility/schema/user_fact.py
src/eligibility/visualization/rule_to_mermaid.py
src/eligibility/visualization/user_facts_to_mermaid.py
```

### B.2 추가: Audit

```text
src/eligibility/audit/__init__.py
src/eligibility/audit/__main__.py
src/eligibility/audit/context.py
src/eligibility/audit/models.py
src/eligibility/audit/replay.py
src/eligibility/audit/sink.py
```

### B.3 추가: LLM Gateway

```text
src/eligibility/llm/__init__.py
src/eligibility/llm/client.py
src/eligibility/llm/gateway.py
src/eligibility/llm/mock_adapter.py
src/eligibility/llm/models.py
src/eligibility/llm/openai_compatible_adapter.py
src/eligibility/llm/profiles.py
src/eligibility/llm/transport.py
.env.example
```

### B.4 추가: Ingestion / Rule Extraction

```text
src/eligibility/ingestion/__init__.py
src/eligibility/ingestion/activation.py
src/eligibility/ingestion/document.py
src/eligibility/ingestion/golden_diff.py
src/eligibility/ingestion/knowledge_draft.py
src/eligibility/ingestion/review.py
src/eligibility/ingestion/rule_extractor.py
src/eligibility/ingestion/validators.py
```

### B.5 추가: Goal / Alert

```text
src/eligibility/goal/__init__.py
src/eligibility/goal/alert.py
src/eligibility/goal/factory.py
src/eligibility/goal/models.py
src/eligibility/goal/tracker.py
```

### B.6 추가: Application / Fixtures / Demo

```text
src/eligibility/application.py
src/eligibility/fixtures/extraction.py
src/eligibility/fixtures/future_goals.py
scripts/run_v03_demo.py
```

### B.7 추가 테스트

```text
tests/test_application_v03.py
tests/test_audit_v03.py
tests/test_future_intent_v03.py
tests/test_goal_tracker_v03.py
tests/test_llm_gateway_v03.py
tests/test_rule_extractor_v03.py
tests/test_v03_end_to_end.py
tests/test_visualization_v03.py
```

신규 테스트는 53개이며, v0.2 회귀 63개와 합쳐 총 116개다.

### B.8 추가 Schema / 예제 / 보고자료

```text
schemas/audit_event.v0.3.schema.json
schemas/goal_instance.v0.3.schema.json
schemas/institution_service.v0.3.schema.json
schemas/product_evaluation.v0.3.schema.json
schemas/product_knowledge_draft.v0.3.schema.json
schemas/rule_ast.v0.3.schema.json
schemas/user_fact_store.v0.3.schema.json

examples/v0.3/*
reports/*-v0.3.*
docs/TARGET_DESIGN_V0.3.md
docs/SPRINT_BRIEF_V0.3.md
```

전체 변경 manifest는 `reports/changed-files-v0.3.txt`에 저장했다.

---

## C. 기존 Regression 결과

### C.1 수정 전 baseline

v0.2 ZIP을 풀고 코드를 수정하기 전에 실행한 결과:

```text
63 passed
```

### C.2 v0.3 코드 위에서의 v0.2 regression

v0.3 전용 테스트 파일을 제외하고 기존 19개 테스트 모듈을 다시 실행했다.

```text
63 passed in 0.16s
0 failed
```

결과 파일:

```text
reports/regression-v0.2-on-v0.3.txt
```

보존된 주요 동작:

- 신한은행 청년 처음적금
- 카카오뱅크 26주적금
- IBK 부모급여우대적금
- 하나은행 달려라 하나 적금
- `COUNT_CONSECUTIVE`
- `COUNT_DISTINCT_PERIODS(DAY|WEEK|MONTH)`
- coverage-aware absence
- related-person resolution
- 금리/이자/Evaluation Trace/Mermaid

---

## D. Audit Architecture

### D.1 Event model

`AuditEvent`는 Pydantic `frozen=True` immutable model이다.

```text
AuditEvent
- event_id
- occurred_at
- request_id
- evaluation_id optional
- trace_id
- span_id optional
- parent_span_id optional
- component
- event_type
- entity_refs
- input_hash
- output_hash
- payload
- provenance
```

입력·출력 hash는 canonical JSON 기반 SHA-256이다. 이벤트 자체는 생성 후 수정할 수 없다.

### D.2 Sink

두 가지 append-only sink를 구현했다.

```text
InMemoryAuditSink
- thread-safe RLock
- append only
- read 결과는 immutable tuple snapshot

JsonLinesAuditSink
- 파일을 항상 append mode로 기록
- line-by-line AuditEvent schema validation
- trace/request/evaluation filter 지원
```

Sink contract에는 update/delete 메서드가 없다.

### D.3 Correlation

`AuditSession`이 다음 ID를 유지한다.

```text
request_id: 하나의 사용자/application request chain
trace_id: 문서/LLM/판정/Goal/Alert를 묶는 trace
 evaluation_id: deterministic input snapshot마다 재계산되는 평가 ID
```

동일 request/trace 안에서 사용자 답변 전·후 평가가 다른 `evaluation_id`를 갖는다. `bind_evaluation_id()`는 이후 이벤트에 새 평가 ID를 연결하되 `request_id`와 `trace_id`는 유지한다.

중첩 처리에는 다음을 사용한다.

```text
span_id
parent_span_id
```

### D.4 Instrumentation 위치

```text
DOCUMENT_PARSER
LLM_GATEWAY
SCHEMA_VALIDATOR
SEMANTIC_VALIDATOR
HUMAN_REVIEW
FINANCIAL_ELIGIBILITY_ENGINE
RULE_EVALUATOR
TEMPORAL_ENGINE
FACT_RESOLVER
AGGREGATION_ENGINE
RATE_ENGINE
APPLICATION
GOAL_FACTORY
GOAL_TRACKER
ALERT_ENGINE
```

Engine에 optional `AuditSession`을 주입하는 방식이라 Audit가 없어도 기존 deterministic API는 그대로 동작한다.

### D.5 구현 Event Type

```text
REQUEST_RECEIVED
DOCUMENT_PARSED
LLM_CALL_STARTED
LLM_CALL_COMPLETED
LLM_CALL_FAILED
SCHEMA_VALIDATION_COMPLETED
SEMANTIC_VALIDATION_COMPLETED
HUMAN_REVIEW_RECORDED
RULE_LOADED
DATE_EXPRESSION_RESOLVED
FACT_REQUESTED
FACT_CANDIDATES_FOUND
FACT_SOURCE_SELECTED
FACT_CONFLICT_DETECTED
SERVICE_RESOLVER_CALLED
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
RESULT_EXPLAINED
```

### D.6 보안 정책

```text
LLM_LOG_PAYLOAD_MODE = NONE | HASHED | REDACTED | FULL_DEBUG
```

- 기본값: `REDACTED`
- `NONE`: safe call metadata만 기록, request/output payload 미기록
- `HASHED`: payload hash만 기록
- `REDACTED`: hash와 message length/type만 기록
- `FULL_DEBUG`: synthetic fixture 용도로 raw 구조 기록 가능
- 모든 mode에서 API key, Authorization, password, token, secret 계열 key를 재귀적으로 redaction
- `Bearer ...` 문자열 redaction
- base URL은 raw 값 대신 식별 hash를 기록

`FULL_DEBUG`는 자유 텍스트 내부의 임의 개인정보를 DLP 방식으로 탐지하지 않으므로 synthetic/non-sensitive 환경에서만 사용하도록 명시했다.

### D.7 Evaluation Trace와 분리

Evaluation Trace에는 금융판정에 필요한 rule status/evidence/provenance만 들어간다. LLM raw response, retry count, 내부 span, API metadata는 Audit Log에만 존재한다.

---

## E. LLM Gateway

### E.1 Interface

```python
class LLMClient(Protocol):
    def generate_text(...): ...
    def generate_structured(...): ...
    def health_check(...): ...
```

Gateway는 purpose profile, request schema, audit metadata를 구성하고 adapter에 위임한다.

### E.2 Adapter

#### MockLLMAdapter

- API key·network 없이 동작
- purpose별 text/structured fixture 주입
- Pydantic model 또는 JSON-like payload 지원
- 전체 Rule Extractor pipeline test에 사용

#### OpenAICompatibleAdapter

- standard-library `urllib` 기반 transport abstraction
- `POST {base_url}/chat/completions`
- `GET {base_url}/models` health check
- local server와 external API 공용
- optional API key
- `response_format.type=json_schema`, strict schema 요청
- JSON code fence normalization
- Pydantic validation
- timeout/retry/error normalization
- 429/5xx retry
- timeout retry

### E.3 환경변수

```text
LLM_PROVIDER
LLM_BASE_URL
LLM_MODEL
LLM_API_KEY
LLM_TIMEOUT_SECONDS
LLM_MAX_RETRIES
LLM_TEMPERATURE
LLM_LOG_PAYLOAD_MODE
```

`.env.example`을 제공한다.

### E.4 Profile

```text
RULE_EXTRACTION
SERVICE_EXTRACTION
INTENT_PARSING
QUESTION_GENERATION
RESULT_EXPLANATION
```

각 profile은 다음 metadata를 가진다.

```text
prompt_template_id
prompt_template_version
response_schema_version
temperature
timeout_seconds
```

이번 Sprint에서 실제 end-to-end로 사용한 profile은 `RULE_EXTRACTION`과 `QUESTION_GENERATION`이다. Question Generator는 LLM 실패 또는 숫자 hallucination 시 deterministic fallback으로 전환한다.

### E.5 로컬 서버 설정

```bash
export LLM_PROVIDER=OPENAI_COMPATIBLE
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=qwen-local
export LLM_API_KEY=
export LLM_TIMEOUT_SECONDS=30
export LLM_MAX_RETRIES=2
export LLM_TEMPERATURE=0
export LLM_LOG_PAYLOAD_MODE=REDACTED

PYTHONPATH=src python -m eligibility.cli llm-health
```

### E.6 외부 API 설정

```bash
export LLM_PROVIDER=OPENAI_COMPATIBLE
export LLM_BASE_URL=https://provider.example/v1
export LLM_MODEL=provider-model
export LLM_API_KEY='secret-from-runtime-environment'

PYTHONPATH=src python -m eligibility.cli llm-health
```

코드에 key를 hard-code하지 않는다.

### E.7 실제 실행 상태

현재 실행 환경에는 `LLM_*` 외부 API 설정이 없었다. 따라서 실제 provider network smoke test는 실행하지 않았다.

실행한 health check:

```json
{
  "healthy": true,
  "provider": "MOCK",
  "model": "mock-model",
  "detail": "Mock adapter is available",
  "latency_ms": 0
}
```

OpenAI-compatible 동작은 fake transport를 사용하여 request URL, header 정책, timeout, retry, structured output failure, local server no-key 설정을 단위 테스트했다.

---

## F. Rule Extractor

### F.1 입력

```text
DocumentSection
- document_id
- document_name
- page optional
- section optional
- text
- source_url optional
```

한 extraction call은 하나의 `document_id`만 허용하며, page/section/source provenance를 prompt와 output contract에 유지한다.

실제 PDF binary parser를 복잡하게 만들지 않고 text fixture 기반 page-aware interface를 구현했다.

### F.2 Structured output

```text
DocumentSection[]
→ LLMGateway.generate_structured(RULE_EXTRACTION)
→ ProductKnowledgeDraft
```

`ProductKnowledgeDraft`:

```text
document_id
product_metadata
rules
service_references
required_facts
unresolved_items
warnings
extraction_metadata
```

### F.3 LLM metadata 신뢰 경계

LLM이 반환한 provider/model/document hash를 그대로 신뢰하지 않는다. Gateway response와 실제 prompt payload로 다음 값을 다시 덮어쓴다.

```text
extractor_profile
prompt_template_id/version
response_schema_version
provider
model/model_version
document_hash
```

### F.4 Schema validation

Pydantic structured validation 실패 시:

```text
state = INVALID_DRAFT
activation = blocked
```

### F.5 Semantic validation

오류로 차단:

```text
unknown DSL operator
institution/service-specific DSL operator
invalid Rule AST / date expression
invalid or missing reward
rule without provenance
empty draft/rule
missing required fact declaration
invalid achievement mode/evaluation phase combination
```

Review warning:

```text
NOT / absence rule
nested OR
external service dependency
```

### F.6 Human review boundary

상태 모델:

```text
DRAFT
→ SCHEMA_VALID
→ SEMANTIC_VALID
→ REVIEW_REQUIRED
→ APPROVED | REJECTED
→ ACTIVE
```

- `REVIEW_REQUIRED` 상태는 자동 activation 불가
- semantic error가 있으면 approval 불가
- reviewer ID 필수
- validation 시 저장한 `draft_hash`와 approval/activation 시점 hash를 비교
- validation 이후 draft 변조 시 activation 차단

### F.7 Golden diff

구현 지표:

```text
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

MVP는 canonical JSON/normalized structural diff 방식이다.

---

## G. 실제 Extraction Demo

### G.1 입력

Mock LLM에 정답 JSON을 주입했지만, 다음 전체 pipeline은 실제 구현을 사용했다.

```text
DocumentSection
→ LLM Gateway
→ structured ProductKnowledgeDraft
→ schema validation
→ semantic validation
→ REVIEW_REQUIRED
→ human approval
→ ACTIVE RuleVersion
→ ProductDefinition activation
```

입력 section:

```text
document_id: DOC-SHINHAN-YOUTH-20260722
page: 2
section: [1] 주거래 우대
text: 상품 신규 후 인정기간 중 급여클럽 월급봉투를 6개월 이상 받은 경우 연 1.0%p 우대
```

### G.2 Rule AST

```json
{
  "type": "COUNT_DISTINCT_PERIODS",
  "period": "MONTH",
  "fact_type": "SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED",
  "operator": "GTE",
  "expected": 6,
  "evaluation_phase": "POST_SUBSCRIPTION"
}
```

기관 전용 operator인 `HAS_SHINHAN_SALARY_ENVELOPE_6M`를 만들지 않았다.

### G.3 Service Reference

```json
{
  "institution_id": "SHINHAN_BANK",
  "service_name": "급여클럽",
  "concept_name": "월급봉투",
  "reference_type": "REQUIRES_SERVICE_DEFINITION",
  "used_by_rule_ids": ["RATE_SALARY_ENVELOPE_6M"]
}
```

### G.4 Required Facts

```text
SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED / OBSERVED_EVENT / INSTITUTION_SERVICE
WILL_TRACK_SHINHAN_SALARY_ENVELOPE_6M / FUTURE_INTENT / USER_DECLARED
```

### G.5 Provenance

```text
page = 2
section = [1] 주거래 우대
source_text = 월급봉투 6개월 조건 원문
```

### G.6 Unresolved

```text
EXTERNAL_DEFINITION_REQUIRED
월급봉투 인정정의는 급여클럽 Service Definition이 필요함
```

### G.7 Review / diff 결과

```text
review_state = ACTIVE
human reviewer = HUMAN-DEMO-REVIEWER
```

Mock golden fixture와 비교한 결과:

```json
{
  "rule_recall": 1.0,
  "hallucinated_rule_count": 0,
  "logical_structure_mismatch": 0,
  "temporal_mismatch": 0,
  "aggregation_mismatch": 0,
  "reward_mismatch": 0,
  "service_reference_recall": 1.0,
  "required_fact_recall": 1.0,
  "provenance_match": 1.0,
  "unresolved_item_recall": 1.0
}
```

이는 실제 모델 정확도 평가 결과가 아니라, Mock structured-output pipeline과 diff infrastructure가 정확히 연결됐음을 확인하는 golden fixture 결과다.

관련 파일:

```text
examples/v0.3/salary_document_sections.json
examples/v0.3/salary_extraction_draft.json
examples/v0.3/salary_extraction_review.json
examples/v0.3/salary_active_rule_version.json
examples/v0.3/salary_golden_diff_metrics.json
```

---

## H. Future Intent Flow

### H.1 Fact semantic type

```text
OBSERVED_FACT
OBSERVED_EVENT
DERIVED_FACT
FUTURE_INTENT
```

`FUTURE_INTENT`는 `source_type=USER_DECLARED`만 허용한다.

같은 `fact_type`의 `OBSERVED_FACT`가 존재해도 `required_semantic_type=FUTURE_INTENT` 조회에는 사용되지 않는다.

### H.2 Evaluation phase

```text
PRE_SUBSCRIPTION
POST_SUBSCRIPTION
BOTH
```

기존 Rule에는 기본값 `PRE_SUBSCRIPTION`을 적용하여 backward compatibility를 유지했다.

### H.3 Achievement mode

```text
ACCUMULATIVE
CONSECUTIVE
MAINTAIN_UNTIL_DEADLINE
ONE_TIME_BEFORE_DEADLINE
```

이번 Goal Tracker의 실제 구현 범위는 `ACCUMULATIVE`다. 나머지는 schema와 future design contract에 포함되며 추후 tracker 확장 대상이다.

### H.4 결정 순서

```text
1. 현재 progress 계산
2. 남은 opportunity와 capacity 계산
3. capacity < remaining_required이면 UNSATISFIABLE
4. capability rule이 있으면 deterministic 평가
5. FUTURE_INTENT가 없으면 UNKNOWN + ASK_USER
6. USER_DECLARED YES이면 ACHIEVABLE
7. USER_DECLARED NO이면 UNSATISFIABLE / USER_DECLINED
```

중요하게, 사용자에게 묻기 전에 시간상 불가능 여부를 코드가 먼저 계산한다.

### H.5 상태 전이

```text
UNKNOWN
  reason = FUTURE_INTENT_REQUIRED
  MissingFactRequest.resolution_strategy = ASK_USER

사용자 YES
  semantic_type = FUTURE_INTENT
  source_type = USER_DECLARED

재평가
  ACHIEVABLE
  reason = FUTURE_INTENT_CONFIRMED_AND_TIME_FEASIBLE
```

`ACHIEVABLE`은 성공 예측이나 확정금리가 아니라 수행 경로가 열려 있다는 뜻이다.

### H.6 User NO

```text
status = UNSATISFIABLE
reason_code = USER_DECLINED
evidence.ui_disposition = NOT_SELECTED
```

Rate Engine은 해당 reward를 realizable rate에서 제외한다.

---

## I. 월급봉투 6개월 Golden Flow

### I.1 가입 전, 사용자 답변 전

```text
status = UNKNOWN
reason_code = FUTURE_INTENT_REQUIRED
progress = 0 / 6 MONTH
missing fact = WILL_TRACK_SHINHAN_SALARY_ENVELOPE_6M
resolution = ASK_USER
```

금리:

```text
confirmed_rate = 3.05%
realizable_rate = 3.05%
user_specific_conditional_upper_rate = 4.05%
```

질문:

> 이 상품의 +1.0%p 우대를 받으려면 가입 후 월급봉투 인정조건을 6개월 이상 달성해야 합니다. 이 조건을 목표로 관리할까요?

### I.2 사용자 YES Fact

```json
{
  "fact_type": "WILL_TRACK_SHINHAN_SALARY_ENVELOPE_6M",
  "semantic_type": "FUTURE_INTENT",
  "value": true,
  "source_type": "USER_DECLARED"
}
```

### I.3 재평가

```text
status = ACHIEVABLE
reason_code = FUTURE_INTENT_CONFIRMED_AND_TIME_FEASIBLE
progress = 0 / 6 MONTH
```

금리:

```text
confirmed_rate = 3.05%
realizable_rate = 4.05%
rate impact = +1.0%p
```

### I.4 가입 확인 후 Goal

생성 조건:

```text
rule phase = POST_SUBSCRIPTION
evaluation = ACHIEVABLE
subscription_confirmed = true
user_tracking_intent = true
goal_template exists
```

생성 결과:

```text
required_target = 6
current_progress = 0
remaining_opportunities = 12
buffer = 6
status = ACTIVE
```

### I.5 실제 progress 4/6

```text
current_progress = 4
remaining_required = 2
remaining_opportunities = 2
buffer = 0
GoalStatus = AT_RISK
AlertType = CRITICAL
```

---

## J. 30,000보 × 300일 Stress Test

이 조건은 실제 상품 Fact가 아닌 synthetic stress test다.

Rule:

```text
COUNT_DISTINCT_PERIODS(
  period=DAY,
  fact_type=QUALIFIED_DAILY_STEP_METRIC,
  where=DAILY_STEPS >= 30000,
  window=[subscription_date, maturity_date]
) >= 300
```

### J.1 Case A

```text
available_days = 365
intent = UNKNOWN

status = UNKNOWN
reason = FUTURE_INTENT_REQUIRED
progress = 0/300 DAY
ASK_USER
```

### J.2 Case B

```text
available_days = 365
intent = YES

status = ACHIEVABLE
progress = 0/300 DAY
confirmed_rate = 2.0%
realizable_rate = 4.0%
```

### J.3 Case C

```text
current = 170
remaining_required = 130
remaining_opportunities = 150
buffer = 20

GoalStatus = ACTIVE
Alert = none
```

### J.4 Case D

```text
current = 249
remaining_required = 51
remaining_opportunities = 50
buffer = -1

GoalStatus = FAILED
AlertType = FAILED
reason = INSUFFICIENT_REMAINING_OPPORTUNITIES
```

### J.5 시간상 불가능

```text
available_days = 299
required_days = 300

status = UNSATISFIABLE
reason = INSUFFICIENT_FUTURE_OPPORTUNITIES
intent 질문 생성 안 함
```

---

## K. Goal Tracker

### K.1 GoalInstance

```text
goal_id
user_id
product_id
subscription_id
rule_id
tracking_mode
metric
opportunity_unit
required_target
current_progress
window_start
deadline
remaining_required
remaining_opportunities
buffer
rate_reward_pp
status
safety_threshold
created_from_evaluation_id
created_at
next_check_at
alert_policy
```

모델 validator가 다음 불변식을 검사한다.

```text
window_start <= deadline
remaining_required = max(0, required_target - current_progress)
buffer = remaining_opportunities - remaining_required
```

### K.2 Rule status와 분리

```text
Rule Evaluation Status:
SATISFIED / ACHIEVABLE / UNSATISFIABLE / UNKNOWN

Goal Status:
ACTIVE / AT_RISK / COMPLETED / FAILED / PAUSED
```

### K.3 누적형 계산

```text
remaining_required = max(0, required_target - current_progress)
buffer = remaining_opportunities - remaining_required
```

### K.4 위험도

```text
current_progress >= target  -> COMPLETED
buffer < 0                   -> FAILED
buffer == 0                  -> AT_RISK + CRITICAL
0 < buffer <= threshold      -> AT_RISK
otherwise                    -> ACTIVE
```

Goal progress는 감소할 수 없도록 검사한다.

---

## L. Alert 결과

월급봉투 4/6, 남은 기회 2의 AlertEvent:

```json
{
  "alert_type": "CRITICAL",
  "severity": "CRITICAL",
  "trigger_reason": "NO_REMAINING_BUFFER",
  "deterministic_payload": {
    "required_target": 6,
    "current_progress": 4,
    "remaining_required": 2,
    "remaining_opportunities": 2,
    "buffer": 0,
    "rate_reward_pp": "1.0",
    "goal_status": "AT_RISK"
  }
}
```

30,000보 조건 실패 AlertEvent:

```json
{
  "alert_type": "FAILED",
  "severity": "CRITICAL",
  "trigger_reason": "INSUFFICIENT_REMAINING_OPPORTUNITIES",
  "deterministic_payload": {
    "remaining_required": 51,
    "remaining_opportunities": 50,
    "buffer": -1,
    "goal_status": "FAILED"
  }
}
```

LLM은 이 event의 발생 여부, severity, trigger reason을 결정하지 않는다. 선택적으로 문장 표현만 담당할 수 있다.

---

## M. Audit Log 예시

`TRACE-SALARY-DEMO-001` 하나로 다음 단계가 연결된다.

```text
DOCUMENT_PARSED
LLM_CALL_STARTED
LLM_CALL_COMPLETED
SCHEMA_VALIDATION_COMPLETED
SEMANTIC_VALIDATION_COMPLETED
HUMAN_REVIEW_RECORDED

REQUEST_RECEIVED                       # 사용자 답변 전 평가
RULE_LOADED                            # eligibility
FACT_REQUESTED
FACT_CANDIDATES_FOUND
FACT_SOURCE_SELECTED
COMPARISON_EXECUTED
STATUS_DERIVED
RULE_LOADED                            # 월급봉투 6개월
DATE_EXPRESSION_RESOLVED
AGGREGATION_EXECUTED
COMPARISON_EXECUTED
FUTURE_CAPACITY_CHECKED
FACT_REQUESTED                         # FUTURE_INTENT
FACT_CANDIDATES_FOUND
MISSING_FACT_CREATED
STATUS_DERIVED                         # UNKNOWN
REWARD_APPLIED
RATE_SUMMARY_CREATED

USER_FACT_RECEIVED                     # 사용자 YES

REQUEST_RECEIVED                       # 재평가, 새 evaluation_id
RULE_LOADED
FACT_REQUESTED
FACT_CANDIDATES_FOUND
FACT_SOURCE_SELECTED
COMPARISON_EXECUTED
STATUS_DERIVED
RULE_LOADED
DATE_EXPRESSION_RESOLVED
AGGREGATION_EXECUTED
COMPARISON_EXECUTED
FUTURE_CAPACITY_CHECKED
FACT_REQUESTED                         # FUTURE_INTENT
FACT_CANDIDATES_FOUND
FACT_SOURCE_SELECTED
STATUS_DERIVED                         # ACHIEVABLE
REWARD_APPLIED
RATE_SUMMARY_CREATED

GOAL_CREATED
GOAL_PROGRESS_UPDATED
GOAL_FEASIBILITY_RECALCULATED
ALERT_TRIGGERED
```

이 chain에서 다음을 구분할 수 있다.

```text
문서 section 전달 문제        -> DOCUMENT_PARSED input/output hash
LLM provider/output 문제       -> LLM_CALL_* metadata/error
구조 오류                     -> SCHEMA_VALIDATION_COMPLETED
DSL/의미 오류                 -> SEMANTIC_VALIDATION_COMPLETED
승인 경계 문제                -> HUMAN_REVIEW_RECORDED
Fact 부재/충돌/선택 문제       -> FACT_* events
날짜/집계 문제                -> DATE_EXPRESSION_RESOLVED / AGGREGATION_EXECUTED
상태 도출 문제                -> STATUS_DERIVED
금리 반영 문제                -> REWARD_APPLIED / RATE_SUMMARY_CREATED
Goal 계산 문제                -> GOAL_* events
알림 문제                     -> ALERT_TRIGGERED
```

Replay:

```bash
PYTHONPATH=src python -m eligibility.audit show \
  --file examples/v0.3/audit_trace.jsonl \
  --trace-id TRACE-SALARY-DEMO-001
```

전체 sequence는 다음 파일에 있다.

```text
examples/v0.3/audit_sequence_salary.txt
reports/audit-replay-salary-v0.3.txt
```

`REDACTED/HASHED` 모드에서는 raw prompt 없이도 단계, provider/model, template/schema version, input/output hash, retry/error, Fact/Rule/Goal entity reference로 실패 구간을 식별할 수 있다. 정확한 원문 재현은 log의 document/fact reference와 hash에 대응하는 원본 snapshot 보관이 전제다.

---

## N. 전체 테스트 결과

실행 명령:

```bash
PYTHONPATH=src pytest -p no:cacheprovider
```

결과:

```text
116 passed in 0.34s
0 failed
```

구성:

```text
v0.2 regression tests = 63
v0.3 new tests        = 53
total                 = 116
```

### N.1 Audit

```text
test_audit_events_are_append_only
test_trace_ids_propagate
test_fact_source_selection_logged
test_aggregation_execution_logged
test_sensitive_credentials_not_logged
test_audit_replay_sequence_is_human_readable
```

### N.2 LLM Gateway

```text
test_mock_structured_generation
test_openai_compatible_configuration
test_timeout_normalization
test_retry_behavior
test_invalid_structured_output
test_llm_audit_does_not_capture_api_key
test_gateway_from_settings_applies_global_timeout_temperature_and_payload_mode
test_openai_compatible_adapter_allows_local_server_without_api_key
test_llm_payload_mode_none_keeps_safe_metadata_but_no_raw_payload
test_llm_payload_mode_full_debug_still_redacts_credentials
```

### N.3 Rule Extractor

```text
test_rule_extractor_returns_knowledge_draft
test_service_reference_is_not_dsl_operator
test_required_facts_extracted
test_provenance_preserved
test_schema_failure_blocks_activation
test_human_review_boundary_blocks_automatic_activation
test_approved_draft_builds_product_definition
test_service_specific_operator_is_semantic_error
test_missing_required_fact_is_semantic_error
test_golden_diff_metrics_are_computable
test_extraction_metadata_is_gateway_generated_not_llm_trusted
test_draft_hash_integrity_blocks_post_validation_mutation
```

### N.4 Future Intent / Golden Flow

```text
test_future_intent_is_distinct_from_observed_fact
test_post_subscription_missing_intent_returns_unknown
test_user_yes_and_time_feasible_returns_achievable
test_user_no_excludes_reward
test_insufficient_future_opportunities_unsatisfiable
test_salary_envelope_6m_unknown_to_achievable
test_step_30000_300d_time_feasible
test_step_30000_300d_time_infeasible
test_step_30000_300d_user_intent
```

### N.5 Goal / Alert

```text
test_accumulative_goal_progress
test_goal_remaining_opportunities
test_goal_buffer
test_goal_at_risk
test_goal_failed_when_opportunities_insufficient
test_goal_completed
test_goal_creation_requires_subscription_confirmation_and_tracking_intent
test_goal_and_alert_audit_events
```

### N.6 End-to-end

```text
test_end_to_end_trace_correlates_document_fact_rule_rate_goal_alert
```

결과 파일:

```text
reports/test-results-v0.3.txt
```

추가 validation:

```text
compileall = PASS
v0.3 JSON Schema export = PASS
Mock LLM health check = PASS
Demo generation = PASS
Audit replay = PASS
Python wheel build = PASS
```

---

## O. 새로 발견된 Design Issue

### O.1 Blocker

**없음.**

MUST 범위를 막는 구조적 문제는 발견되지 않았다. v0.2 deterministic core를 재작성하지 않고 optional instrumentation과 신규 계층으로 확장할 수 있었다.

### O.2 Minor

#### 1. OpenAI-compatible strict JSON Schema 편차

Adapter는 `response_format=json_schema`를 사용한다. OpenAI-compatible이라고 표방하는 일부 로컬 서버가 strict JSON Schema contract를 완전히 지원하지 않을 수 있다.

현재 처리:

- provider error normalization
- invalid structured output 차단
- Mock/fake transport 검증

향후:

- provider capability probe
- `json_schema` → `json_object` fallback policy를 adapter profile별로 명시

#### 2. Page-aware parser interface만 구현

`DocumentSection`과 provenance pipeline은 구현했지만, PDF binary → page/section text 변환기는 이번 범위에서 구현하지 않았다.

향후 Application/Ingestion Layer에서 공식 PDF parser를 붙이면 현재 contract를 그대로 사용할 수 있다.

#### 3. Audit storage는 MVP 수준

현재 sink는 in-memory와 JSONL이다. 단일 프로세스 demo/replay에는 충분하지만 production에서는 다음이 필요하다.

- durable append-only storage
- retention/partition 정책
- multi-process write coordination
- access control
- document/fact snapshot lifecycle

#### 4. FULL_DEBUG 자유 텍스트 DLP

key/token redaction은 구현했지만 자유 텍스트 안에 포함된 임의 개인정보를 완전 탐지하는 DLP는 구현하지 않았다. 따라서 `FULL_DEBUG`는 synthetic fixture 전용이다.

#### 5. 사용자 거부 상태 결합

MVP 요구대로 `USER_DECLINED`를 `UNSATISFIABLE`에 매핑했다. UI disposition `NOT_SELECTED`을 보존했지만 장기적으로는 Rule Status와 Plan Disposition을 분리하는 것이 더 명확하다.

### O.3 Deferred

```text
실제 PDF binary parser
실제 external LLM smoke/integration test
CONSECUTIVE Goal Tracker
MAINTAIN_UNTIL_DEADLINE Goal Tracker
ONE_TIME_BEFORE_DEADLINE Goal Tracker
Result Explainer production pipeline
Intent Parser production pipeline
실제 Institution Service Resolver
실제 MyData/API integration
persistent Product Rule Store
persistent Goal Store
actual push infrastructure
Web/Application UI
```

v0.2의 `COUNT_CONSECUTIVE` deterministic Rule 평가 기능은 계속 유지되지만, 이를 Goal Tracker와 연결하는 것은 deferred다.

---

## P. 다음 단계 판단

# `READY_FOR_APPLICATION_LAYER`

### 이유

1. deterministic core의 기존 63개 regression이 모두 유지됐다.
2. Engine은 LLM 없이 독립적으로 동작한다.
3. Local/external LLM을 설정만으로 교체할 수 있다.
4. LLM structured output이 곧바로 ACTIVE가 되는 경로를 차단했다.
5. `FUTURE_INTENT`와 observed fact를 구조적으로 분리했다.
6. 월급봉투 6개월이 `UNKNOWN → ASK_USER → ACHIEVABLE → Goal → Alert`로 동작한다.
7. 30,000보 × 300일 조건에서 기간상 가능 여부, intent, 가입 후 buffer를 각각 deterministic하게 처리한다.
8. 하나의 trace로 document extraction, validation, fact resolution, rule evaluation, rate, goal, alert를 연결한다.
9. 문제 발생 단계를 Audit replay로 식별할 수 있다.
10. 116개 테스트가 모두 통과한다.

### 다음 구현 우선순위

```text
1. Application Tool/API contract 구현
2. 실제 PDF parser 연결
3. 실제 local/external LLM integration marker test
4. persistent Rule/Audit/Goal storage
5. 심사용 Web UI
6. CONSECUTIVE 및 MAINTAIN_UNTIL_DEADLINE Goal 확장
```

---

# 최종 질문에 대한 답

> 로컬 또는 외부 LLM이 금융상품 문서를 구조화하고 사용자에게 필요한 질문을 생성하더라도, 금융조건 판정·금리·미래목표 추적은 기존 deterministic engine에 남아 있으며, 문제가 발생했을 때 문서 추출 → Fact → Rule → Rate → Goal → Alert 중 어느 단계에서 문제가 생겼는지 Audit Log만으로 식별할 수 있는가?

## 답: **예. 구현된 v0.3 범위에서는 가능하다.**

- LLM은 `ProductKnowledgeDraft`와 질문 문구만 생성한다.
- Rule activation은 validation과 human review를 통과해야 한다.
- 가입조건, 상태, 기간, 집계, 금리, 이자, future capacity는 `FinancialEligibilityEngine`이 계산한다.
- progress, remaining, buffer, Goal status, Alert trigger는 `GoalTracker/AlertEngine`이 계산한다.
- 동일 `trace_id`에서 문서/LLM/validation/fact/rule/rate/goal/alert event를 조회할 수 있다.
- raw prompt를 기록하지 않는 기본 보안 모드에서도 hash, schema/template version, entity refs, status/result metadata로 실패 단계를 식별할 수 있다.

단, production 수준의 완전한 재현에는 Audit의 document/fact hash가 가리키는 원본 snapshot을 durable storage에 함께 보존해야 한다. 이번 Sprint는 해당 storage 자체가 아니라 재현 가능한 event contract와 correlation을 구현한 것이다.
