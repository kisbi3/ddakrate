# Web UI UX Review — 5 Iterations

Baseline: `financial-eligibility-engine-v0.4.6-web-sprint1`

목표: 금융상품 추천 Web UI를 실제 사용자/심사자 관점에서 headless Chromium으로 렌더링하고, 검색 시작 → 추천 목록 → 추가 질문 → 답변 반영 → 상품 상세 흐름을 반복 검토하여 5회 개선.

> 실행환경의 Chromium은 localhost URL navigation을 관리자 정책으로 차단해, 동일 HTML/CSS/JS를 Chromium에 로드하고 `fetch`만 로컬 FastAPI runtime에 bridge하는 방식으로 검증했다. 화면 렌더링, 프론트 JS, API 응답은 실제 코드/실제 runtime을 사용했다.

## Round 1 — 첫인상과 신뢰

### 발견
- `LLM 미연결`, `fixture` 같은 개발자 언어가 사용자 첫인상에 노출됨.
- 서비스 가치보다 기술 상태가 먼저 보임.
- `PERSONALIZED TOP 5` 등 일부 카피가 MVP 내부 용어처럼 보임.

### 변경
- 서비스 카피를 `내 금리 찾기` 중심으로 변경.
- `광고 최고금리 대신, 내가 실제 받을 금리와 세후이자`라는 핵심 가치 명시.
- `LLM 미연결` → `데모 모드 · 선택형 입력`, `실상품 fixture` → `검증 상품`으로 사용자 언어화.
- 검색 기준 영역을 `내가 이해한 조건`으로 단순화.

Screenshot: `ux-review/iter1_search.png`

## Round 2 — Ranking 시각 위계 수정

### 발견
- 기본 Ranking은 예상 세후이자인데 카드에서는 금리가 더 크게 보여, 사용자가 “왜 1위인지”를 직관적으로 이해하기 어려움.

### 변경
- 카드의 1차 숫자를 `내 예상 세후이자`로 변경.
- `내 예상금리`는 2차 지표로 축소.
- 정렬 문구를 `예상 세후이자 높은 순`으로 명확화.
- 카카오처럼 납입계획이 미정인 상품은 `시작금액 선택 필요`로 직접 표시.

Screenshot: `ux-review/iter2_search.png`

## Round 3 — 질문/상태 투명성

### 발견
- 질문 블록이 다소 기술적인 `추천 정확도 향상` 메시지로 보였음.
- 어떤 값이 사용자의 응답인지 구분이 약함.

### 변경
- 질문을 `다음 질문`으로 단순화하고 `잘 모르겠으면 건너뛰어도 돼요`를 명시.
- 답변이 관련 상품의 예상이자/순위 재계산에 사용된다는 설명 추가.
- 현재 상태의 user-declared 값에 `사용자 응답` provenance 표시.

Screenshot: `ux-review/iter3_search.png`

## Round 4 — 상세 화면 금융 의미 강화

### 발견
- `confirmed / realizable / advertised` 세 금리가 사용자에게 혼동될 수 있음.
- 세전이자와 상품 납입한도가 상세 요약에서 빠져 있었음.
- Rule status가 내부 상태에 가까운 형태로 보임.

### 변경
- 금리를 `내 예상금리 / 현재 확인된 금리 / 광고 최고금리`로 명확히 구분하고 각각 의미 설명 추가.
- `상품 납입한도`, `예상 세전이자`, `예상 세후이자` 추가.
- 금리 구성에 `✓ 이미 충족 / → 계획대로 달성 가능 / × 받을 수 없음 / ? 추가 확인` 범례 추가.
- `branch` 같은 내부 표현을 화면에서 `조건`으로 사용자화.
- `AI 설명` → `AI 요약`, `GROUNDED SUMMARY` → `구조화된 판정 결과 기준`.

Screenshot: `ux-review/iter4_detail.png`

## Round 5 — 대화 중복, 응답 문구, 접근성, 모바일

### 발견
- 추가 질문이 채팅 bubble과 질문 dock에 중복 노출됨.
- `특별금리 쿠폰을 보유하고 있나요?`에 `네, 가능해요`는 부자연스러움.
- 추천 카드가 클릭 가능하다는 affordance가 약함.

### 변경
- 다음 질문을 채팅에 다시 복제하지 않고 질문 dock에서만 보여줌.
- 사실 확인 질문은 `네, 있어요 / 아니요, 없어요`, 미래 행동 질문은 `네, 할 수 있어요 / 아니요, 어려워요`로 semantic-aware 버튼 적용.
- 카드에 `상세 금리·근거 보기 →` 추가.
- keyboard focus style, Enter 상세 열기, Escape 닫기 검증.
- reduced-motion 대응과 모바일 레이아웃 조정.

Screenshots:
- `ux-review/iter5_after_answer.png`
- `ux-review/iter5_detail.png`
- `ux-review/iter5_mobile.png`

## 최종 검증

- `pytest`: **369 passed**
- `python -m compileall`: **PASS**
- `node --check app.js`: **PASS**
- FastAPI `/api/runtime`: **PASS**
- root static title: **PASS**
- keyboard recommendation open / Escape close: **PASS**
- 390px mobile render: **PASS**

## 남은 blocker

UI/금융판정 correctness blocker는 이번 검토에서 새로 발견되지 않았다.

실제 심사 데모의 다음 핵심 작업은 **실제 LLM provider 연결 후 자연어 수정 adversarial QA**다. 현재 MOCK runtime은 선택형 질문 흐름을 검증하기 위한 데모 모드이며, 자유로운 후속 자연어 수정은 의도적으로 비활성 상태다.
