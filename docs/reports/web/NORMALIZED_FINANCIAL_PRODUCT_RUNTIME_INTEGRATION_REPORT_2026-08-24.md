# Normalized 금융상품 Web runtime 통합 보고서

- 완료일: 2026-08-24
- 기본 진입점: `data/financial_products/normalized/index.json`
- 기본 후보 정책: `sale_policy.status == ON_SALE`
- 결과: 확정 155개 로드·검증, Web 기본 후보 153개

## 구현 결과

### 발행 데이터 로더

- index의 155개 `(product_code, version)`과 각 product path를 읽는다.
- index와 각 상품의 identity를 대조하고 product sha256을 검증한다.
- 발행 manifest의 파일 sha256과 product/family/status/institution count를 검증한다.
- Institution index가 가리키는 현재 1,103개 snapshot을 읽고 Product 26개 기관을 `institution_id`로 조인한다.
- CustomDefinition 33개, 전체 발행 CustomBinding 35개, StandardCondition과 모든 `reward_refs`의 참조를 검증한다.
- Product와 binding된 CustomDefinition의 기관이 다르면 기동을 실패시킨다.
- 기본 로더는 ON_SALE 153개만 반환한다. 명시적으로 `include_sale_statuses={"ON_SALE", "ENDED"}`를 사용하면 155개를 모두 읽는다.

기존 `data/product_catalog/products/` 50개는 삭제하지 않았다. `ELIGIBILITY_CATALOG_MODE=LEGACY` 또는 기존 디렉터리형 `ELIGIBILITY_PRODUCT_CATALOG_PATH`에서 회귀용으로 사용할 수 있다. 정상 기본 모드는 `NORMALIZED`다.

### 손실 없는 runtime envelope와 legacy adapter

기존 평가기는 적금 중심 `ProductDefinition`을 요구하므로 다음 두 층으로 분리했다.

1. `NormalizedProductData`가 새 Product의 policy, rate entries, StandardCondition, CustomBinding, CustomDefinition, Data Gap, 공식 출처와 원본 JSON을 보존한다.
2. 기존 평가기가 안전하게 이해할 수 있는 기간·납입·공식 숫자 금리만 adapter가 변환한다.

변환할 수 없는 값을 0이나 false로 채우지 않는다. `base_rate`, `advertised_max_rate`, `preferential_rate_cap`과 추천 DTO의 금리는 nullable이다. RateSummary는 `CALCULATED`, `UNKNOWN`, `UNSUPPORTED`를 구분한다.

### 금리·현금흐름

- 적금·예금은 선택한 계약기간에 적용되는 BASE RateEntry를 deterministic하게 선택한다.
- 예금의 일시예치는 반복 납입으로 변환하지 않고 원금 1회 cashflow로 계산한다.
- 파킹통장의 `MARGINAL` 잔액구간은 사용자 비교 잔액으로 가중 유효금리를 계산한다. 최고 구간 금리를 전체 잔액에 적용하지 않는다.
- `POSTED_YIELD`는 기준일이 있는 공시수익률로 표시하며 확정 보장 문구를 사용하지 않는다.
- `PERFORMANCE_LINKED` CMA는 금리·확정이자 계산을 하지 않고 `UNSUPPORTED / PERFORMANCE_LINKED_RETURN_IS_NOT_A_GUARANTEED_RATE`로 노출한다.
- 광고 최고금리 필드가 없으면 `null`로 유지한다.
- 중도해지·만기후 금리는 canonical RateEntry의 role과 calculation을 그대로 상세 API/UI에 전달한다.

현재 ON_SALE 153개 중 runtime 대표 시나리오에서 적용 BASE/공시수익률을 바로 고를 수 있는 상품은 97개다. 나머지 56개에는 실적배당 24개와 잔액·기간·공식 수치 입력이 더 필요한 상품이 포함되며 0%로 순위화하지 않는다.

### 조건과 질문

- StandardCondition은 condition type과 criteria fingerprint가 같은 상품끼리 사용자 fact를 공유한다.
- CustomBinding은 `(custom_code, custom_version)` fact로 연결하고 공식 title/content/evidence를 보존한다.
- 기존 평가기로 자동 판정할 수 없는 조건은 자동 충족·자동 탈락시키지 않고 사용자 확인 질문으로 연결한다.
- 공통 사전 질문의 적용 여부도 legacy feature 문자열뿐 아니라 normalized StandardCondition과 CustomDefinition semantic tags를 사용한다.
- CustomDefinition의 `evaluation_mode=LLM`인 조건도 현재 단계에서는 공식 content/evidence를 벗어난 자동 추론을 하지 않는다.

### Web/API/UI

추천 목록과 상세에 공식 Institution 이름을 사용한다. 상세 응답과 drawer에서 다음을 확인할 수 있다.

- 상품군/subtype, 판매 상태, 가입 대상
- 기간 policy, 납입·예치 policy와 한도
- 적용 가능 금리, 광고 최고금리, return kind, calculation method, 기준일
- 전체 BASE/PREFERENTIAL/EARLY_TERMINATION/POST_MATURITY RateEntry
- Standard/Custom 우대조건
- 예금자보호 상태
- Data Gap과 확인되지 않은 항목
- 공식 출처 URL

추가 read-only API:

- `GET /api/catalog/products`
- `GET /api/catalog/products?institution_id=...&product_family=...`
- `GET /api/catalog/products/{product_id}`
- `GET /api/catalog/institutions/{institution_id}/products`

기관별 API는 별도 역방향 파일을 만들지 않고 현재 runtime 상품의 `institution_id`를 필터링한다.

## 검증 결과

실행 명령:

```bash
PYTHONPATH=src LLM_PROVIDER=MOCK .venv/bin/pytest -o addopts='' -q
PYTHONPATH=src .venv/bin/python -m compileall -q src tests
node --check src/eligibility/web/static/app.js
git diff --check
```

결과:

- 전체 테스트: `448 passed in 10.40s`
- Python compile: PASS
- JavaScript syntax: PASS
- diff whitespace 검사: PASS
- normalized 전체 로드: 155
- 기본 ON_SALE runtime: 153
- ENDED 기본 제외: 2
- 연결 기관: 26
- CustomDefinition: 33
- 전체 발행 CustomBinding: 35
- product/manifest sha256: PASS
- MOCK TCP server `/healthz`: 200
- MOCK TCP server `/api/runtime.product_count`: 153
- MOCK 검색 session 생성: 201

지속 실행 중인 LaunchAgent도 새 코드로 재시작했다. 현재 `http://127.0.0.1:57949`의 `/healthz`는 정상이며 `/api/runtime.product_count`는 153이다.

## 남은 통합 제약

1. 기존 evaluator가 canonical StandardCondition criteria의 모든 EVENT_COUNT, 기간 window, 기관 데이터 조회를 직접 계산하는 것은 아니다. 현재는 안전한 공통 fact 또는 사용자 확인으로 연결한다.
2. `evaluation_mode=LLM` CustomDefinition의 source-bounded 자동 의미 판정기는 아직 별도 구현 대상이다. 현 단계에서는 공식 content를 보여주고 사용자 응답을 받으며 임의 판정을 금지한다.
3. 기간/잔액 적용 범위가 구조화되지 않고 공식 `condition_text`에만 있는 일부 상품은 제한된 deterministic 기간 parser가 처리한다. 단일 적용 금리를 확정할 수 없으면 `UNKNOWN`으로 남긴다.
4. 실적배당 CMA의 예상 수익 비교는 공식 성과 측정값과 별도 계산 모델이 추가되기 전까지 지원하지 않는다.
5. source-tree/현재 editable 설치는 normalized data를 직접 사용한다. 독립 wheel에 155개 발행 데이터를 포함해 배포하려면 별도 package-data 배포 정책이 필요하다.

이 제약들은 데이터를 꾸며내지 않기 위한 의도적인 경계이며, 서버 중단 원인이 되지 않는다.
