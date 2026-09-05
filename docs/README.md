# 문서 인덱스

프로젝트 실행과 현재 구현을 이해할 때는 아래 순서로 읽는다.

## 시작하기

- [로컬 실행 및 테스트 가이드](guides/LOCAL_RUN_AND_TEST_GUIDE.md)
- [Web handoff](handoff/FINANCIAL_ELIGIBILITY_ENGINE_V0_4_6_WEB_HANDOFF.md)
- [대화형 탐색 프로필 및 사전 질문 설계](guides/PRE_SEARCH_QUESTION_DESIGN.md)
- [Top 3 조건 질문·추천 안정화 구현 설계](guides/TOP3_CONDITION_QUESTION_IMPLEMENTATION_DESIGN.md)
- [유연한 대화 턴·문맥·Decision Ledger 설계](guides/FLEXIBLE_CONVERSATION_CONTEXT_AND_LEDGER_DESIGN.md)
- [상위 추천상품 eligibility_text LLM 가입조건 검수 구현 계획](guides/TOP_RANKED_ELIGIBILITY_TEXT_LLM_REVIEW_PLAN.md)

## 우대조건 의미 분석 (진행 중)

실험 기능이다. 스위치 기본값은 꺼짐이며, 아직 main에 넣지 않았다.

- [지금까지 한 일과 앞으로 할 일 (2026-09-06)](handoff/SEMANTIC_PREFERENTIAL_PROGRESS_20260906_KO.md) — 현황의 출발점
- 계획·1금융권 표본·코드 PR은 그 문서 8절의 GitHub 링크를 본다

## 릴리스 보고서

버전별 구현·강화·의미론 변경 기록은 [`reports/releases/`](reports/releases/)에 있다.

- v0.3 계열: `V0_3_*`
- v0.4 계열: `V0_4_*`

## Web 및 통합 보고서

UI, UX, LLM, Catalog 통합 기록은 [`reports/web/`](reports/web/)에 있다.

- [AI 대화 런타임 핵심 테스트 보고서 (2026-08-21)](reports/web/AI_CONVERSATION_RUNTIME_TEST_REPORT_2026-08-21.md)
- [AI 대화 런타임 개선 계획](reports/web/AI_CONVERSATION_RUNTIME_IMPROVEMENT_PLAN.md)
- [AI 대화 런타임 구현 및 검증 보고서 (2026-08-21)](reports/web/AI_CONVERSATION_RUNTIME_IMPLEMENTATION_REPORT_2026-08-21.md)
- `WEB_UI_IMPLEMENTATION_SPRINT_1_REPORT.md`
- `UX_5_ITERATION_REVIEW.md`
- `WEB_LLM_INTEGRATION_SPRINT_1_REPORT.md`
- `WEB_LLM1_CATALOG50_INTEGRATION_REPORT.md`

## 설계 및 변경 기록

현재 디렉터리의 나머지 문서는 초기 설계, schema freeze, sprint brief, changelog 기록이다.

## 공모전 제출 자료

- [금융상품 데이터 관계 설계](submission/FINANCIAL_PRODUCT_DATA_RELATIONSHIP.md)

## 실행 결과 원문

테스트·빌드·회귀 실행의 원문 로그는 프로젝트 루트의 [`reports/`](../reports/)에 보존한다. JSON 예제는 [`examples/`](../examples/), 내보낸 스키마는 [`schemas/`](../schemas/)에 있다.
