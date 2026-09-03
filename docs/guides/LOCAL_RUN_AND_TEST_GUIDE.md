# Financial Eligibility Engine v0.4.6 Web LLM1 — 로컬 실행 및 테스트 가이드

이 문서는 `financial-eligibility-engine-v0.4.6-web-llm1.zip`을 로컬 PC에 내려받아 **실제 Web UI를 브라우저에서 확인하고, OpenAI LLM 연결까지 포함해 자연어 대화를 테스트**하기 위한 실행 가이드다.

현재 패키지는 별도의 Node.js / npm / React build가 필요하지 않다. Web UI 정적 파일은 Python 패키지에 포함되어 있고 FastAPI가 같은 origin에서 UI와 API를 함께 제공한다.

현재 기본 Product Catalog는 normalized 발행본의 **ON_SALE 155개 상품**이며, 저장된 샘플 사용자 금융데이터 없이 대화에서 조건을 확인한다. `ENDED` 2개는 데이터에 보존되지만 기본 검색·추천에서는 제외된다.

---

## 1. 필요한 환경

필수:

- Python **3.11 이상**
- `pip`
- 인터넷 연결: 최초 Python dependency 설치 시 필요
- 최신 Chrome / Edge / Safari / Firefox 중 하나

실제 자연어 LLM 테스트까지 하려면 추가로:

- OpenAI API key
- OpenAI API에 접속 가능한 네트워크

필요하지 않음:

- Node.js
- npm / yarn / pnpm
- 별도 frontend dev server
- 별도 DB
- 실제 MyData API

버전 확인:

```bash
python --version
```

macOS/Linux에서 `python` 명령이 없으면:

```bash
python3 --version
```

---

## 2. ZIP 압축 해제

다운로드한 파일:

```text
financial-eligibility-engine-v0.4.6-web-llm1.zip
```

압축을 풀면 다음 프로젝트 디렉토리가 생긴다.

```text
financial-eligibility-engine-v0.4.6-web-llm1/
```

이후 모든 명령은 이 디렉토리에서 실행한다.

---

## 3. 가상환경 생성 및 dependency 설치

### Windows PowerShell

```powershell
cd .\financial-eligibility-engine-v0.4.6-web-llm1

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[web,dev]"
```

PowerShell에서 스크립트 실행 정책 때문에 가상환경 활성화가 막히면 현재 PowerShell 세션에 한해서:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

`py -3.11`이 없고 `python`이 3.11 이상이면 다음을 사용해도 된다.

```powershell
python -m venv .venv
```

### macOS / Linux

```bash
cd financial-eligibility-engine-v0.4.6-web-llm1

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e '.[web,dev]'
```

설치 후 간단히 확인:

```bash
python -c "import eligibility; print('eligibility import OK')"
```

---

## 4. 먼저 MOCK 모드로 화면 확인

OpenAI key를 연결하기 전에 UI와 deterministic backend가 정상 동작하는지 먼저 확인하는 것을 권장한다.

### Windows PowerShell

```powershell
$env:LLM_PROVIDER="MOCK"
$env:WEB_HOST="127.0.0.1"
$env:WEB_PORT="8000"

eligibility-web
```

### macOS / Linux

```bash
export LLM_PROVIDER=MOCK
export WEB_HOST=127.0.0.1
export WEB_PORT=8000

eligibility-web
```

브라우저에서:

```text
http://127.0.0.1:8000
```

또는:

```text
http://localhost:8000
```

을 연다.

정상이라면 Web UI가 보이고 적금·예금·파킹통장·CMA 155개 판매 중 상품 Catalog를 기반으로 검색/추천 화면을 사용할 수 있다.

MOCK 모드에서 가능한 것:

- 화면 렌더링
- 자연어 입력 기반 초기 검색
- deterministic 질문과 선택형 응답
- 상품 후보 평가
- 계산 가능한 상품의 예상금리 / 세전이자 계산
- Ranking / Top 5
- 상품 상세 / 판정 근거

MOCK 모드에서 의도적으로 제한된 것:

- 자유 자연어 후속 발화의 의미 해석

즉 `"월 20만원으로 바꿀게"` 같은 자유 문장은 실제 LLM 연결 후 테스트한다.

서버 종료:

```text
Ctrl + C
```

---

## 5. OpenAI LLM 연결

### 중요: API key는 코드나 브라우저에 넣지 않는다

API key는 **서버 프로세스 환경변수**로만 주입한다.

프로젝트의 `.gitignore`는 `.env` 계열 로컬 secret 파일을 무시하도록 되어 있다. 다만 현재 코드는 `.env` 파일을 자동으로 로드하지 않으므로, 가장 단순하고 확실한 방법은 아래처럼 PowerShell 또는 shell 환경변수를 직접 설정하는 것이다.

### Windows PowerShell

```powershell
$env:LLM_PROVIDER="OPENAI"
$env:LLM_BASE_URL="https://api.openai.com/v1"
$env:LLM_MODEL="gpt-5.6-terra"
$env:OPENAI_API_KEY="여기에_본인_API_KEY"
$env:LLM_REASONING_EFFORT="low"

$env:WEB_HOST="127.0.0.1"
$env:WEB_PORT="8000"

eligibility-web
```

### macOS / Linux

```bash
export LLM_PROVIDER=OPENAI
export LLM_BASE_URL=https://api.openai.com/v1
export LLM_MODEL=gpt-5.6-terra
export OPENAI_API_KEY='여기에_본인_API_KEY'
export LLM_REASONING_EFFORT=low

export WEB_HOST=127.0.0.1
export WEB_PORT=8000

eligibility-web
```

`LLM_API_KEY`를 사용해도 된다. 둘 다 설정되어 있으면 `LLM_API_KEY`가 우선한다.

```text
LLM_API_KEY
→ 없으면 OPENAI_API_KEY
```

### `.env.example` 사용 시 주의

`.env.example`은 **설정 예시 파일**이며 자동으로 읽히지 않는다.

또한 OpenAI를 쓸 때는 `LLM_PROVIDER`만 바꾸지 말고 아래 값을 함께 명시하는 것이 안전하다.

```text
LLM_PROVIDER=OPENAI
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-5.6-terra
OPENAI_API_KEY=...
LLM_REASONING_EFFORT=low
```

macOS/Linux에서 직접 만든 `.env`를 현재 shell에 로드하려면 예를 들어:

```bash
set -a
source .env
set +a
eligibility-web
```

API key가 들어간 `.env`는 Git에 commit하지 않는다.

---

## 6. LLM 연결 상태 확인

서버 실행 후 다음 주소를 확인한다.

### 전체 서버 health

```text
http://127.0.0.1:8000/healthz
```

예상:

```json
{
  "status": "ok",
  "backend_version": "0.4.6"
}
```

### Web runtime 정보

```text
http://127.0.0.1:8000/api/runtime
```

확인할 핵심 값:

```text
product_count = 50
llm_provider = OPENAI   # OpenAI 모드일 때
llm_api_family = RESPONSES
```

### 실제 LLM health

```text
http://127.0.0.1:8000/api/llm/health
```

OpenAI 연결이 정상이라면 핵심적으로:

```text
configured = true
healthy = true
```

여야 한다.

브라우저에서 확인해도 되고 terminal에서 확인해도 된다.

macOS/Linux:

```bash
curl http://127.0.0.1:8000/api/llm/health
```

Windows PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/llm/health
```

---

## 7. 브라우저에서 실제로 확인할 기본 흐름

1. `http://127.0.0.1:8000` 접속
2. 첫 화면이 정상 렌더링되는지 확인
3. 원하는 상품·기간·납입액을 자연어로 입력해 추천 검색 시작
4. 왼쪽 대화/질문 영역과 오른쪽 추천 영역이 동시에 보이는지 확인
5. 현재 AI가 이해한 검색 기준이 표시되는지 확인
6. 질문에 답하면 추천 결과가 재계산되는지 확인
7. 추천 상품 카드를 클릭
8. 상세 화면에서 다음 항목 확인
   - 내 예상금리
   - 현재 확인된 금리
   - 광고 최고금리
   - 예상 세전이자
   - 우대조건별 상태
   - 필요한 행동
   - 미확인 조건
   - 공식 근거
   - 판정 근거 tree/map

중요한 확인 기준:

```text
LLM = 자연어 의미 해석
Backend = 금융판정 / 금리 / 이자 / Ranking
```

화면의 숫자와 status는 LLM prose가 아니라 backend DTO에서 와야 한다.

---

## 8. 실제 자연어 Acceptance Test

OpenAI health가 `healthy=true`가 된 뒤 아래 순서를 그대로 테스트한다.

```text
월 20만원으로 바꿀게.
급여계좌는 바꿀 수 있어.
생각해보니 잘 모르겠어.
카카오는 3천원으로 할게.
카카오는 빼줘.
아까 카카오 뺀 건 취소.
질문은 그만하고 지금 결과 보여줘.
금리 높은 순으로 다시 보여줘.
```

각 turn마다 확인한다.

### 1. `월 20만원으로 바꿀게.`

기대:

- 현재 납입계획이 20만원으로 수정
- 상품별 예상 원금/세전이자 재계산
- Top 5가 필요하면 재정렬

### 2. `급여계좌는 바꿀 수 있어.`

기대:

- 관련 Capability가 `CAN`
- 해당 ActionPath만 다시 평가
- LLM이 직접 금리를 만들어내지 않음

### 3. `생각해보니 잘 모르겠어.`

기대:

- 직전의 mutable user-declared 상태를 `UNKNOWN`으로 되돌릴 수 있음
- favorable한 값으로 임의 유지하지 않음

### 4. `카카오는 3천원으로 할게.`

기대:

- 카카오 26주적금 product-specific contribution choice 설정
- 해당 실제 cashflow schedule로 affordability/이자 재계산

### 5. `카카오는 빼줘.`

기대:

- 카카오 상품 exclusion 활성화
- 재검색 후 목록에서 제외

### 6. `아까 카카오 뺀 건 취소.`

기대:

- exclusion 제거
- 카카오 상품 다시 후보에 포함 가능

### 7. `질문은 그만하고 지금 결과 보여줘.`

기대:

- `UNKNOWN`은 그대로 유지
- active question을 금융적으로 충족된 것으로 처리하지 않음
- 현재 deterministic 결과를 즉시 표시

### 8. `금리 높은 순으로 다시 보여줘.`

기대:

- Ranking objective가 `MAX_REALIZABLE_RATE`로 변경
- LLM이 직접 순서를 정하지 않고 backend Ranking이 재계산

---

## 9. 전체 regression test 실행

개발 상태까지 확인하려면 프로젝트 루트에서:

```bash
python -m pytest
```

테스트 개수는 구현에 따라 증가하므로 고정 숫자보다 실행 결과의 전체 통과 여부를 확인한다.

빠른 Python compile 확인:

```bash
python -m compileall -q src
```

Web JavaScript syntax까지 확인하려면 Node.js가 설치되어 있을 때만 선택적으로:

```bash
node --check src/eligibility/web/static/app.js
```

Node.js는 **서비스 실행 자체에는 필요하지 않다.**

---

## 10. API를 직접 보고 싶을 때

FastAPI 문서:

```text
http://127.0.0.1:8000/api/docs
```

주요 Web flow:

```text
POST   /search-sessions
POST   /search-sessions/{id}/messages
GET    /search-sessions/{id}/recommendations
GET    /search-sessions/{id}/recommendations/{product_id}
GET    /search-sessions/{id}/questions/next
GET    /search-sessions/{id}/state
DELETE /search-sessions/{id}
```

브라우저 UI의 자연어 conversational entrypoint는 기본적으로:

```text
POST /search-sessions/{id}/messages
```

를 사용한다.

---

## 11. 포트 충돌 해결

8000번 포트를 다른 프로그램이 사용하고 있으면 포트를 바꾼다.

### Windows PowerShell

```powershell
$env:WEB_PORT="8001"
eligibility-web
```

### macOS / Linux

```bash
export WEB_PORT=8001
eligibility-web
```

그 뒤:

```text
http://127.0.0.1:8001
```

으로 접속한다.

참고로 시스템에 `PORT` 환경변수가 이미 설정되어 있다면 `PORT`가 `WEB_PORT`보다 우선한다.

---

## 12. `eligibility-web` 명령을 찾지 못할 때

먼저 가상환경이 활성화되어 있는지 확인한다.

그래도 안 되면 프로젝트 루트에서 설치를 다시 실행한다.

```bash
python -m pip install -e '.[web,dev]'
```

Windows PowerShell에서는:

```powershell
python -m pip install -e ".[web,dev]"
```

console script 대신 다음 방식으로도 실행할 수 있다.

```bash
python -m uvicorn eligibility.web.app:app --host 127.0.0.1 --port 8000
```

---

## 13. 자주 발생할 수 있는 문제

### A. 화면은 뜨는데 자연어 수정이 안 됨

확인:

```text
GET /api/llm/health
```

`LLM_PROVIDER=MOCK`이면 자유 자연어 후속 해석은 의도적으로 비활성화되어 있다.

OpenAI 모드인지 확인:

```text
LLM_PROVIDER=OPENAI
```

### B. `/api/llm/health`에서 configured=false

주요 원인:

- `OPENAI_API_KEY` / `LLM_API_KEY` 미설정
- 환경변수를 설정한 terminal과 서버를 실행한 terminal이 다름
- 서버를 환경변수 설정 전에 띄우고 재시작하지 않음

환경변수 설정 후 서버를 `Ctrl+C`로 종료하고 다시 실행한다.

### C. configured=true인데 healthy=false

응답의 `detail`을 먼저 확인한다.

가능한 범주:

- provider 연결 실패
- 잘못된 credential
- 모델 또는 endpoint 설정 문제
- 네트워크 문제
- provider 측 요청 제한/오류

### D. Web 메시지 요청이 503

외부 LLM provider/runtime 오류는 Web API에서 `503 RUNTIME_UNAVAILABLE`로 반환될 수 있다.

먼저:

```text
/api/llm/health
```

를 확인한다.

### E. Product Catalog를 못 찾는 오류

기본 전달본에는 50개 Catalog가 source tree와 package data에 모두 포함되어 있으므로 일반 실행에서는 별도 설정이 필요 없다.

다른 Catalog를 테스트할 때만:

```text
ELIGIBILITY_PRODUCT_CATALOG_PATH=/absolute/path/to/product_catalog
```

를 지정한다.

---

## 14. 로컬 실행 시 권장 보안 설정

로컬 테스트에서는 다음을 권장한다.

```text
WEB_HOST=127.0.0.1
```

기본 설정의 `0.0.0.0`은 같은 네트워크의 다른 장치에서도 접근 가능한 형태로 bind될 수 있기 때문이다.

또한:

- API key를 source code에 쓰지 않기
- API key를 browser console/localStorage에 넣지 않기
- `.env`를 Git에 commit하지 않기
- screenshot/화면 공유 시 credential이 보이지 않는지 확인하기

OpenAI direct request는 현재 adapter에서 `store=false`로 전송하도록 구현되어 있다.

---

## 15. 가장 짧은 실행 순서

### Windows PowerShell — OpenAI 연결까지

```powershell
cd .\financial-eligibility-engine-v0.4.6-web-llm1
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[web,dev]"

$env:LLM_PROVIDER="OPENAI"
$env:LLM_BASE_URL="https://api.openai.com/v1"
$env:LLM_MODEL="gpt-5.6-terra"
$env:OPENAI_API_KEY="본인_API_KEY"
$env:LLM_REASONING_EFFORT="low"
$env:WEB_HOST="127.0.0.1"

eligibility-web
```

접속:

```text
http://127.0.0.1:8000
```

### macOS / Linux — OpenAI 연결까지

```bash
cd financial-eligibility-engine-v0.4.6-web-llm1
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[web,dev]'

export LLM_PROVIDER=OPENAI
export LLM_BASE_URL=https://api.openai.com/v1
export LLM_MODEL=gpt-5.6-terra
export OPENAI_API_KEY='본인_API_KEY'
export LLM_REASONING_EFFORT=low
export WEB_HOST=127.0.0.1

eligibility-web
```

접속:

```text
http://127.0.0.1:8000
```

---

## 16. 이번 로컬 테스트의 합격 기준

최소 다음이 모두 되면 로컬 환경 연결은 정상으로 본다.

```text
[ ] http://127.0.0.1:8000 접속 성공
[ ] /healthz = ok
[ ] /api/runtime product_count = 50
[ ] /api/llm/health configured=true, healthy=true
[ ] 초기 추천 결과 표시
[ ] 상품 상세 열기 성공
[ ] 자연어로 월 납입금 수정 성공
[ ] Capability CAN → UNKNOWN revision 성공
[ ] 카카오 시작금액 설정 성공
[ ] 상품 제외 → 재포함 성공
[ ] 질문 중단 후 현재 결과 표시 성공
[ ] 금리순 Ranking objective 변경 성공
[ ] 화면 숫자/status가 backend 재계산 결과와 함께 갱신
```

이 체크리스트까지 통과하면 다음 단계는 UI 미세조정보다 **실제 자연어 adversarial QA와 배포환경 검증**이다.

---

## 17. 실제 OpenAI Acceptance 자동 검증

OpenAI 모드 Web 서버가 실행 중일 때 다음 명령으로 월 30만원·12개월 계산, 세전이자 정렬, active question 유지, LLM 미호출 정렬, 실제 transport schema와 debug bundle 크기를 한 번에 확인할 수 있다.

```bash
.venv/bin/python scripts/acceptance_test_web_openai.py \
  --base-url http://127.0.0.1:57949
```

성공하면 `"status": "PASS"`와 세션 id, 지연시간, debug bundle byte 수가 출력된다. `/debug`의 기본 bundle에는 요약만 포함되며, 전체 audit payload와 상품별 전체 평가는 해당 탭이나 상품을 선택할 때 별도로 불러온다.
