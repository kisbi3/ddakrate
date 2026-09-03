# 상품조건 데이터 정밀화 및 대화 히스토리 보존 계획

- 작성일: 2026-08-24
- 상태: 범위 조정 후 히스토리 저장 구현
- 이번 구현에서 제외: 질문 설명 패킷의 최종 UI, normalized 금융상품 데이터 수정

## 0. 2026-08-24 범위 결정

이번 단계에서는 LLM 분석 보고서를 Web 애플리케이션이 자동 생성하지 않는다. Web은
세션별 원본 히스토리만 로컬 파일에 보존한다. 이후 별도의 Codex 세션이 저장된
히스토리를 읽고 문제 보고서를 작성하며, 사용자가 그 보고서를 검토한 다음 데이터와
코드를 수정한다.

저장 파일은 세션 복원용 데이터베이스가 아니다. 따라서 기존 단계 B의 재개 가능한
`SearchSession`과 SQLite 도입은 보류한다.

## 1. 목적

런타임에 상품별 예외 문구를 계속 하드코딩하지 않는다. 실제 대화에서 설명이
불분명하거나 포괄적인 질문이 발견되면 공식 근거와 연결된 데이터 개선 항목으로
수집하고, 검증된 normalized 발행본을 통해 모든 상품에 일반화한다.

동시에 Web 서버가 재시작되어도 과거 대화와 디버그 실행 이력을 조회할 수 있도록
SearchSession과 trace의 영속 저장 경계를 정의한다.

## 2. 상품조건 정밀화 루프

1. 실제 OpenAI 대화 acceptance test를 적금·예금·파킹통장·CMA별로 실행한다.
2. 다음 결과를 `ConditionCoverageIssue`로 기록한다.
   - 공식 가입대상처럼 답할 수 없는 포괄 질문
   - 근거보다 넓거나 모호한 질문·설명
   - 가입조건과 우대조건의 혼동
   - 확인 방법 또는 인정 기간 누락
   - Data Gap인데 충족·불충족으로 표현된 조건
3. issue에는 `product_id`, `rule_id`, `custom_definition_id`, `source_ref`, 실제 질문,
   실제 설명, 부족한 필드를 저장한다.
4. 공식 문서에서 조건을 보강하고 기존 validation/publish 절차를 거쳐 normalized
   새 버전을 발행한다.
5. 발견 문장을 회귀 fixture로 고정하고 같은 질문이 다시 일반화되지 않는지 확인한다.

LLM은 공식 content block과 EvidenceRef 안에서 조건 초안과 사용자용 문장을 작성할 수
있다. Engine은 출처 범위, 참조 무결성, 금리 계산, unknown 처리를 검증한다.

## 3. 런타임 안전장치

- 데이터가 부족해도 서버 전체를 503으로 중단하지 않는다.
- 확인되지 않은 값은 false, 0 또는 조건 없음으로 변환하지 않는다.
- 조건 근거가 부족한 상품은 `DATA_GAP`을 유지하고 확정 Top 5 여부와 분리한다.
- 질문·설명 품질 문제는 자동 탈락 규칙이 아니라 데이터 개선 queue로 보낸다.
- 같은 상품/rule/source 조합의 issue는 중복 생성하지 않고 발생 횟수를 누적한다.

## 4. 대화와 디버그 히스토리 영속화

현재 다음 상태는 프로세스 메모리에만 존재한다.

- `ApplicationService._sessions`
- `InMemoryAuditSink`
- `DebugTraceStore`
- 기본 `InMemorySearchWorkingNoteStore`

따라서 서버 재시작 시 이전 SearchSession을 복구할 수 없다. 영속화는 다음 두 단계로
나눈다.

### 단계 A — 별도 분석 세션용 원본 히스토리 (구현)

- 사용자/AI 말풍선, structured model output, 실행 operation, 질문과 추천 snapshot을
  세션별 append-only JSONL과 최신 전체 snapshot으로 저장한다.
- 기본 경로는 `.runtime/debug-history/<search_session_id>/`다.
- 새 Codex 세션은 `metadata.json`, `requests.jsonl`, `schemas.json`,
  `latest-session-snapshot.json`을 읽어 별도의 문제 분석 보고서를 작성한다.
- API key와 계좌번호 등 민감정보 redaction을 저장 전에 적용한다.
- 사용자가 `처음부터`를 눌러도 저장된 파일은 삭제하지 않고 live session만 종료한다.
- archived 세션은 `/debug`에서 `저장됨 · 읽기 전용`으로 다시 표시한다. live
  SearchSession 복원과 자동 보고서 생성은 별도 범위로 둔다.

### 단계 B — 재개 가능한 SearchSession

- Intent, pre-search profile, user-declared facts, answer records, 제외 상품,
  contribution choice, active question과 버전을 저장한다.
- normalized catalog release id와 데이터 hash를 함께 기록한다.
- 재기동 시 같은 catalog release일 때만 세션을 복원한다.
- catalog가 바뀌었으면 과거 대화는 조회만 허용하고, 새 세션으로 재계산할 수 있게 한다.

## 5. 완료 기준

- 서버가 재시작되어도 `.runtime/debug-history/`의 완료 요청 수가 유지된다.
- 별도 Codex 세션이 과거 LLM system/user prompt, Schema, output과 Engine operation을
  파일에서 다시 읽을 수 있다.
- API key와 자유 입력의 계좌번호가 저장 파일에서 redaction된다.
- metadata에 당시 catalog release/index hash가 보존된다.
- 저장 히스토리를 현재 catalog로 자동 재계산하거나 SearchSession으로 복원하지 않는다.
- 포괄 질문 발견 → 데이터 보강 → 재발행 → 회귀 테스트 흐름이 문서와 테스트로 재현된다.

`/debug`의 archived 세션 조회는 구현됐다. 재개 가능한 세션과 catalog 불일치 복원
정책은 후속 범위다.
