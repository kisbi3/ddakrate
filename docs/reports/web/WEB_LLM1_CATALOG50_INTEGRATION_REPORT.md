# Financial Eligibility Engine v0.4.6-web-llm1 — Catalog50 Integration

**Integration status:** `PASS`  
**Catalog readiness inherited from catalog build:** `READY_WITH_LIMITATIONS`  
**Target runtime:** `financial-eligibility-engine-v0.4.6-web-llm1`

## 1. Scope

`financial-eligibility-engine-v0.4.6-web-llm1`의 OpenAI/LLM/Web 변경을 기준선으로 유지한 채, `financial-product-catalog-v0.1`의 50개 executable `ProductDefinition`을 기본 Web runtime 상품 집합으로 통합했다.

이번 작업은 catalog를 기존 `web-ux5`/`catalog50` 코드로 덮어쓰는 merge가 아니다. 최신 `web-llm1`의 LLM provider 처리, Responses API integration, Web runtime error handling을 보존하고 catalog 관련 파일과 최소 runtime wiring만 추가했다.

## 2. Added catalog assets

```text
data/product_catalog/
├─ products/                       # 50 strict ProductDefinition JSON
├─ manifests/
│  ├─ product_catalog_manifest.json
│  ├─ source_manifest.json
│  ├─ resolver_requirements.json
│  ├─ service_knowledge_requirements.json
│  ├─ schema_gap_backlog.json
│  └─ validation_results.json
├─ catalog_summary.csv
└─ README.md
```

또한 일반 wheel/install 후에도 Web runtime이 catalog를 잃지 않도록 동일 catalog를 `eligibility.catalog` package-data mirror로 포함한다.

## 3. Runtime integration

`src/eligibility/catalog/loader.py`가 strict `ProductDefinition.model_validate_json()`으로 catalog를 읽는다.

기본 Web runtime은 기존 4개 fixture 하드코딩 대신 다음을 사용한다.

```text
load_default_product_catalog()
→ 50 ProductDefinition
→ ApplicationService
→ Candidate Retrieval
→ FinancialEligibilityEngine × N
→ Ranking
→ Top 5
```

다른 catalog를 검증해야 할 때만 다음 environment override를 사용할 수 있다.

```text
ELIGIBILITY_PRODUCT_CATALOG_PATH=/path/to/product_catalog
```

## 4. Preserved semantics

- 기존 4개 Python golden fixture 삭제/대체 없음
- catalog의 동일 4개 상품과 semantic-equivalence regression 유지
- 카카오뱅크 26주적금 7주/26주 `CountConsecutiveRule.from_sequence=None` 유지
- resolver/service dependency를 `False`로 축약하지 않고 기존 `UNKNOWN` semantics 유지
- CandidateRetriever / RankingService / QuestionPlanner / ApplicationService에 `if product_id == ...` 분기 추가 없음
- 최신 `web-llm1`의 OpenAI Responses API / Structured Outputs / missing-key safety를 보존
- LLM은 intent/question/explanation interface이며 eligibility/rate/interest/ranking은 deterministic backend가 계산

## 5. Validation results

- Catalog ProductDefinition: **50/50 validation PASS**
- Catalog-specific tests: **16/16 PASS**
- Full integrated regression: **390/390 PASS**
- `compileall src`: **PASS**
- Default `build_web_runtime()`: **50 products**
- Default Web `/api/runtime`: **HTTP 200 / product_count=50**
- Default Web search-session create: **HTTP 201**
- Default Web recommendation request: **HTTP 200 / Top 5 returned**
- Wheel contents: **50 product JSON + 6 catalog manifests**
- Installed-wheel smoke outside source tree: **Web runtime product_count=50**

## 6. Data quality boundary

Catalog 자체의 품질 판정은 기존 build 결과를 그대로 보존한다.

```text
A: 0
B: 50
C: 0
D: 0
READY_WITH_LIMITATIONS
```

여기서 B는 schema/runtime failure가 아니라 authoritative Resolver / Institution Service Knowledge dependency가 남아 있음을 뜻한다. 따라서 Web runtime은 정보가 없는 조건을 임의로 미충족 처리하지 않고 `UNKNOWN`/verification-required 상태로 유지해야 한다.

## 7. Result

`financial-eligibility-engine-v0.4.6-web-llm1`은 이제 50개 Product Catalog를 기본 데이터셋으로 사용하며, LLM-enabled Web interaction과 deterministic 50-product search/ranking이 동일 `ApplicationService` 위에서 함께 동작한다.
