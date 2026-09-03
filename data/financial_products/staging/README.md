# 금융상품 staging 배치

전체 저장 구조, 공통 필드, 출처·근거, 발행 순서와 검증 명령은 상위 [`README.md`](../README.md)를 먼저 따른다. 이 문서는 staging과 discovery에만 필요한 추가 규칙을 설명한다.

`staging/{batch_id}/`는 공식 출처에서 수집했지만 아직 상품별 상세 필드와 semantic validation이 완결되지 않은 후보를 보관한다.

```text
staging/{batch_id}/
├── products/{product_code}.json
├── raw/
│   ├── evidence_refs.json
│   └── source_captures/
├── collection_artifacts.json
├── custom_definitions.json
├── protection_schemes.json
├── sale_status_other.json
├── validation_reports.json
├── manifest.json
└── COLLECTION_REPORT.md
```

staging 상품은 `normalized/products/`에 직접 편입하지 않는다. 공식 원문 대조와 결정적·의미적 검증이 끝난 상품만 새 버전으로 정규화한다. 확인하지 못한 값은 추측하지 않고 `data_gap`으로 유지한다.

## discovery 전용 staging

네이버처럼 비교 서비스에서 수집한 구조화 후보는 canonical 상품으로 위장하지 않고 다음과 같이 별도 보관한다.

```text
staging/{batch_id}/
├── discovery/
│   ├── candidates.jsonl
│   ├── collection_artifacts.jsonl
│   ├── validation_reports.jsonl
│   └── official_recheck_queue.jsonl
├── raw/
│   ├── evidence_refs.json
│   └── source_documents.json
├── institution_resolution.json
├── manifest.json
├── validation_report.json
└── COLLECTION_REPORT.md
```

discovery 후보의 `artifact.target_type`은 `FINANCIAL_PRODUCT_CANDIDATE`, `promotion_status`는 `DISCOVERY_ONLY`, `publication_gate`는 `BLOCKED_UNTIL_OFFICIAL_RECHECK`로 유지한다. `institution_id`나 `selected_product_code`가 없는 후보에 내부 ID를 임의로 부여하지 않으며, 공식 금융사 원문·기관 identity·필드별 검증을 통과하기 전에는 `normalized/` 또는 기본 런타임 카탈로그에 추가하지 않는다.

## Naver 전체 discovery 배치

`20260826-naver-discovery-02`는 Naver full crawl의 4,233건을 모두 보관한다.

- `STAGING_READY`: 4,198건
- `FIELD_REVIEW_REQUIRED`: 6건 — `PARTIAL_DISCOVERY`, 금리 파싱 보완 전 금리순·추천 제외
- `RECOLLECTION_REQUIRED`: 29건 — CMA partial, 검색·탐색만 허용하고 금리순·추천·수익 계산 제외

부분 후보도 원문과 Naver 식별자를 보존하되, `source_data_readiness`, `partial_reasons`, `missing_fields`, `discovery_usage_policy`로 노출 제한을 명시한다. 구조적 등록 결과는 `PASS_WITH_WARNINGS`일 수 있으며, 이는 데이터 완결성 경고이지 canonical 승인이나 공식 사실값 승인을 의미하지 않는다.

## 사용자 승인 Naver 필드 정책

사용자가 Naver 상세 원문에 표시된 값을 데이터 보강에 사용하도록 승인한 경우, 최신 배치의 candidate에는 다음 필드 판정을 함께 기록한다.

- `data_approval_status=APPROVED_NAVER_FIELDS`: Naver 원문에 실제 값이 있는 구조화 필드
- `field_confirmation_status=NEEDS_CONFIRMATION_FOR_MISSING_FIELDS`: Naver 원문에 값이 없어 별도 확인이 필요한 필드
- `conflict_status=NOT_CHECKED_AGAINST_OFFICIAL`: 공식 금융사 원문과 아직 대조하지 않은 상태
- Naver 값의 승인과 기관 identity 확인, canonical normalized 승격은 별도 문제로 다룬다.
- `NO_MATCH`도 상품 데이터가 구조화되어 있으면 discovery 보강 데이터로 보존한다.

`20260826-naver-discovery-05`는 이 정책을 적용한 배치다. 네이버 표시 필드는 승인 상태로 기록했으며, 기존 normalized 런타임 카탈로그는 변경하지 않았다.

`20260826-naver-discovery-06`는 위 정책을 유지하면서 Luna 병렬 보강 결과를 상품별 `luna_field_fill`에 보존한 배치다. `filled_fields`는 네이버 원문에서 구조화한 값이고, `still_missing_fields`는 네이버 원문에 없어 확인이 필요한 값이다. 네이버 원문 내부 표기가 서로 충돌한 6건은 `conflict_status=CONFLICT`로 분리했다. 공식 재확인 결과는 `data/financial_products/work/20260826/luna_field_fill/official_conflicts_*.jsonl`에 별도 보존한다.

`20260826-naver-discovery-07`은 사용자가 제공한 4건의 공식 원문 확인 내용과 공식 재확인으로 해소된 2건을 반영한 후속 배치다. 충돌 해소 근거는 각 candidate의 `official_recheck_resolution`에 보존하며, 네이버에 없는 수수료·기관 identity 등은 계속 별도 확인 대상으로 남긴다.

`20260826-naver-discovery-08`과 `20260826-naver-discovery-09`는 Luna 병렬 기관 identity 조사 결과를 반영한 후속 배치다. 공식 식별자와 현재 기관 master가 일치하는 21개 기관은 `institution_id`에 연결했으며, 동명 master 중복은 임의 선택하지 않았다. 기관별 조사 원문과 판단은 `institution_link_review` 및 `data/financial_products/work/20260826/institution_link_agents/*.jsonl`에 보존한다.

신협중앙회 공식 디렉터리에서 identity가 확인됐지만 현재 master에 없던 591개 조합은 `data/institutions/staging/20260826-naver-institution-expansion-01`에 신규 기관 레코드로 staging했다. 이 확장 배치는 `INST-KR-001108`부터 순차 내부 ID를 발급했으며, 기존 기관 master·상품 normalized·런타임 카탈로그는 변경하지 않았다.

`20260826-naver-discovery-10`은 위 기관 확장 staging을 참조해 공식 확인된 신협 상품까지 `institution_id`를 연결한 최신 상품 staging이다. 4,231개 후보 중 3,972개가 현재 master 또는 기관 확장 staging에 연결되며, 259개는 master 중복 또는 공식 identity 확인 필요로 null을 유지한다. canonical 승격 전에는 `20260826-naver-institution-expansion-01`을 기관 master의 새 snapshot으로 승격하고, 남은 259개 기관을 해소해야 한다.

`20260826-naver-institution-expansion-02`는 네이버 지점 주소·전화와 신협중앙회 결산공시를 추가 대조해 동부·청주·중앙·행복신협 4개를 더 확인한 delta staging이다. `20260826-naver-discovery-11`은 확장 01·02를 함께 참조하며 기관 ID 연결 상품을 3,999건으로 늘렸다. 우리·제일신협은 조합코드는 보였지만 주소·전화까지 단일 공식 원문으로 확인되지 않아 계속 보류한다.

`20260826-naver-institution-expansion-03`은 후속 공식 조합 상세 원문에서 우리·제일신협의 이름·주소·전화·조합코드를 함께 확인한 delta staging이다. `20260826-naver-discovery-12`는 확장 01·02·03과 최종 기관 identity 재확인 결과를 반영해 4,231개 후보 전부에 기관 `institution_id`를 연결했다. 기관 연결 기준의 확인 필요·충돌 상품은 0건이며, normalized 기관·상품 master와 런타임 카탈로그는 여전히 변경하지 않았다.

## 사용자 승인 Naver 정식 편입

`20260826-naver-accepted-01`은 사용자가 승인한 네이버 표시 필드를 정식 normalized 데이터로 변환한 release다. 4,231개 상품과 공식 identity가 확인된 신협 597개를 함께 편입했으며, 기존 파일은 덮어쓰지 않고 새 snapshot·manifest·상품 코드로 보존했다. 네이버에 표시되지 않은 필드는 `data_gaps`로 남겼고 판매상태는 `UNKNOWN`으로 두어 기본 `ON_SALE` 런타임에서 자동 노출되지 않도록 했다.
