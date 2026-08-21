# DATA_QUALITY_REPORT

## 1. Quality policy

- **A** — official-source-backed + executable + material eligibility/preferential conditions sufficiently decomposed without unresolved resolver/service dependency.
- **B** — official-source-backed + executable, but resolver/service dependency or aggregate institution entitlement remains.
- **C** — material source gap remains.
- **D** — unsuitable/rejected.

Final 50 uses **A/B only** as requested. In this freeze the conservative grade is:

- A: **0**
- B: **50**
- C: **0**
- D: **0**

B는 validation 실패를 뜻하지 않는다. 모든 50개는 strict schema를 통과하고 deterministic하게 SATISFIED/ACHIEVABLE/UNSATISFIABLE/UNKNOWN을 생성할 수 있다. 다만 authoritative fact를 실제로 채우는 resolver/service가 남아 있으면 A로 과대평가하지 않았다.

## 2. Mechanical quality results

- ProductDefinition strict validation: **50/50 PASS**
- Catalog tests: **13/13 PASS**
- Full integrated engine regression: **382/382 PASS**
- metadata official source URL: **50/50**
- source manifest unique official URLs: **38**
- preferential rule source URL: **158/158**
- global guard source URL: **44/44**
- manifest reviewed: **50/50**
- source `retrieved_at`: **present for all manifest sources/products**
- warnings carried in sidecar: **12 products**

## 3. Metadata null policy

문서에 없는 값은 추측하지 않는다.

|Field|Non-null / 50|
|---|---:|
|target_customer_summary|49/50|
|one_account_per_person|31/50|
|sale_start|2/50|
|sale_end|4/50|
|quantity_limit|6/50|
|effective_from|30/50|
|effective_to|0/50|

낮은 non-null 비율은 자동으로 품질 결함을 의미하지 않는다. 공식 문서에 명시되지 않은 날짜/수량을 만들지 않은 결과다.

## 4. Provenance policy

- `ProductDefinition`에는 research-only field를 넣지 않았다.
- exact official wording을 확보하지 못한 generated source는 `source_text=None`으로 두었다.
- `normalized_source_note`는 source manifest의 조사 메모이며 법적/공식 quote로 취급하지 않는다.
- HANA pension coupon처럼 별도 이벤트 source가 필요한 rule은 상품 기본페이지와 이벤트 source를 각각 연결했다.
- Woori SUPER처럼 상품설명 source와 current rate source가 다른 경우 sidecar에 두 source를 모두 보존했다.

## 5. Source audit corrections

- 케이뱅크 **챌린지박스**: 적금이 아니라 입출금이 자유로운 예금 → final 50에서 제거.
- 우리은행 **우리의 소원 적금**: current sale evidence 부족 → final 50에서 제거.
- 우리은행 **장병내일준비적금**: monthly max를 공식 현행 페이지의 **300,000원**으로 수정.
- 하나은행 **연금하나 월복리 적금**: 2026-07-10 이벤트 +0.50%p를 별도 source/service fact로 연결, advertised max **4.65%**.

## 6. B-grade의 주요 유형

1. **Coarse eligibility resolver** — 46 data-driven products.
2. **Transaction/history resolver** — 급여, 자동이체, 카드, 계좌 보유, 가족관계 등.
3. **Institution service knowledge** — 쿠폰, 앱 회원/서비스, 빙고, event entitlement 등.
4. **Aggregate official entitlement** — 공식 current page에서 총 우대 cap은 확인되지만 모든 내부 reward allocation을 안전하게 추출하지 못한 일부 상품.

## 7. A-grade 승격 조건

A로 승격하려면 해당 상품에 대해:

- 가입대상 leaf AST 완전 분해,
- 우대조건별 공식 reward allocation 완전 분해,
- source page/PDF + section/page provenance 연결,
- 필요한 resolver/service 구현 또는 authoritative fact contract 확정,
- 재검증 tests PASS

를 충족하는 것을 권장한다.

## 8. Final data-quality judgment

**B-grade executable catalog, 50/50 validated.**  
현재 상태는 MVP search/question/UNKNOWN/ranking integration에 사용할 수 있으나, 공식 문서에서 leaf condition까지 닫힌 A-grade product corpus라고 표현하면 안 된다.
