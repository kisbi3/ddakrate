# Top 3 조건 질문·추천 안정화 구현 보고서

- 구현일: 2026-09-01
- 기준 설계: [`../../guides/TOP3_CONDITION_QUESTION_IMPLEMENTATION_DESIGN.md`](../../guides/TOP3_CONDITION_QUESTION_IMPLEMENTATION_DESIGN.md)
- 배포 대상: `http://165.246.151.191:57949/`
- 상태: 핵심 runtime 배포 완료, 검수된 condition family 활성화 대기

## 1. 구현 결과

추천 순위는 사용자 조건에서 아직 배제되지 않은 `user_specific_conditional_upper_rate`를 기준으로 정렬한다. 확인된 조건으로 계산한 `realizable_rate`는 예상금리로 별도 표시하며, 미확인 우대를 합친 값은 예상금리로 승격하지 않고 `조건 충족 시 최고`로 분리한다. 목록과 상세 화면은 같은 계산 상태를 사용한다. 기간·금액 입력 전이라 사용자별 상한을 계산할 수 없는 경우에는 공시 최고금리를 후보 탐색용 임시 상한으로만 사용한다.

backend는 표시 Top 3와 낙관 상한으로 3위에 진입할 수 있는 challenger를 합쳐 frontier를 만든다. 질문은 frontier 안에서 동일 사용자 변수에 묶인 조건을 family로 합친 뒤, 영향을 받는 상품 범위와 실제 순위 변동 가능성을 기준으로 선택한다. 질문할 사항이 사라지면 고정 질문 수가 아니라 `completion_reason`으로 종료한다.

자유 답변은 일반 대화 계획과 분리된 `AnswerPlan`으로 해석한다. LLM은 현재 질문에 허용된 변수·requirement·기관 ID만 반환할 수 있고, backend가 전체 plan을 검증한 뒤 한 번에 반영하고 한 번만 재계산한다. 하나라도 허용되지 않은 ID가 있으면 해당 턴 전체를 반영하지 않는다.

## 2. 주요 구현 경계

| 책임 | 구현 위치 |
|---|---|
| 조건 파생 compiler | `src/eligibility/catalog/requirement_compiler.py` |
| requirement·상태·질문·완료 schema | `src/eligibility/schema/condition_requirement.py` |
| AnswerPlan과 정량 답변 schema | `src/eligibility/schema/conversation.py` |
| 답변 전용 LLM prompt와 해석 | `src/eligibility/llm/system_prompts.py`, `src/eligibility/conversation.py` |
| frontier와 질문 family | `src/eligibility/search/questions.py` |
| 질문 표현 정책 | `src/eligibility/question_policy.py` |
| 순위와 상세 projection | `src/eligibility/search/ranking.py`, `src/eligibility/search/recommendation.py` |
| 원자적 적용·정정·audit | `src/eligibility/application_service.py` |
| 제한된 조회 도구 | `src/eligibility/search/query_tools.py` |
| 화면 표시 | `src/eligibility/web/static/app.js` |

## 3. 제한된 조회 도구

LLM에 raw SQL 권한을 주지 않고 SQL의 필터·정렬·projection 장점만 제공하는 read-only contract를 추가했다.

- allowlist에 있는 상품 필드만 조회할 수 있다.
- 연산자와 정렬 필드를 검증하고 한 번에 최대 100건만 반환한다.
- 원본 저장 구조나 임의 표현식, 쓰기·삭제 명령은 받지 않는다.
- 상품 상세, 파생 requirement, 세션 조건 상태는 각각 제한된 projection으로 읽는다.
- 세션 상태는 조회 시점 snapshot이며 변경은 application service의 검증된 답변 경로로만 가능하다.

현재 대화 runtime은 이 경계를 backend의 공식 조회 facade로 사용한다. LLM이 임의 쿼리를 실행하는 function-calling loop는 추가하지 않았다. 후보 선정·계산·정렬은 계속 deterministic backend가 담당한다.

## 4. 데이터 compiler 결과

| 항목 | 수치 |
|---|---:|
| 상품 | 4,292 |
| 원천 조건 | 4,769 |
| 파생 requirement | 4,769 |
| `COMPLETE` | 4,589 |
| `PARTIAL` | 169 |
| `UNAVAILABLE` | 11 |
| canonical 변수 미매핑 | 480 |
| 사람 검수 완료 | 0 |
| 검수 필요 | 4,769 |

compiler가 만든 분류는 기존 원천 데이터를 수정하지 않는다. 명시적으로 `VERIFIED`된 구조만 실제 조건 그래프에 활성화할 수 있으며, 현재 데이터는 모두 `REVIEW_REQUIRED`로 유지한다. 따라서 이번 배포는 검수되지 않은 AI 분류가 사용자 금리나 순위를 올리는 일을 방지한다.

## 5. 답변과 상태 정책

- “카드 가능”처럼 금액이 없는 답은 `WILLING_UNSPECIFIED`로 남고 우대금리를 적용하지 않는다.
- 월 카드 실적 20만·30만·50만원 조건이 동시에 있으면 최대 가능 금액을 한 번 묻는다. 30만원 답변은 20만·30만원 조건만 충족한다.
- `RATE_BENEFIT=false`는 우대분만 제거하고 상품은 남긴다.
- `ELIGIBILITY=false`는 상품을 제외한다.
- 수수료 면제와 경품은 `INFORMATION_ONLY`로 분류해 선제 질문과 금리 순위에서 제외한다.
- 사용자가 이전 답을 정정하거나 지우면 연결 상태를 무효화하고 질문 가능 상태로 되돌린다.
- 확률형 조건도 특정 상품명이 아니라 조건 family 전체에 적용되며, frontier에서 Top 3 진입 가능성이 있을 때만 일반 조건 흐름 안에서 질문 후보가 된다.

## 6. UI 결과

- 예상금리: `realizable_rate`
- 예상이자: backend의 `estimated_pre_tax_interest`
- 표시 순위: 사용자별 가능한 최고금리(`user_specific_conditional_upper_rate`), 계산 전에는 공시 최고금리 fallback
- 낙관 가능분: `조건 충족 시 최고`로 별도 표시
- 상세 우대 상태: `적용 예상`, `확인 전`, `적용 안 함`, `확인 완료`
- 완료 문구: 안정 완료, 잠정 완료, 데이터 불완전, 후보 부족을 구분

## 7. 검증

핵심 계약 테스트 88개가 통과했다.

- requirement compiler와 read-only query contract
- AnswerPlan 허용 ID 및 원자성
- 카드 금액 threshold 통합과 모호한 답변
- Top 3 frontier, 완료 이유, audit 관측성
- 질문 문구 정책
- 목록·상세 UI 계산 상태 일치
- 기존 v0.4 순위·상세 회귀

추가로 Python compile, JavaScript syntax, schema export, `git diff --check`를 통과했다. 배포 서버에서 4,292개 상품, OpenAI `gpt-5.6-luna`, 실제 자연어 적금 세션 생성과 다음 backend 질문 반환을 확인했다.

## 8. 남은 운영 작업

코드 구현과 데이터 활성화는 분리한다. 다음 단계는 480개 미매핑 requirement를 family별로 정리하고, 근거를 사람이 검수한 family만 `VERIFIED`로 승격한 뒤 shadow 지표와 회귀 테스트를 통과시켜 점진적으로 활성화하는 일이다. 이 검수 작업 전까지 legacy 계산 경로를 안전 경로로 유지한다.
