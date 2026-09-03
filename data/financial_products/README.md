# 금융상품 데이터 저장·수집 계약

이 문서는 `data/financial_products/`의 공식 진입점이다. 새 금융상품을 수집하거나 기존 상품을 갱신할 때는 이 문서와 [`staging/README.md`](staging/README.md)를 먼저 따른다.

상세 필드의 설계 배경과 조건 모델은 [`docs/FINANCIAL_PRODUCT_DATA_MODEL.md`](../../docs/FINANCIAL_PRODUCT_DATA_MODEL.md)에 있다. 실제 서비스가 읽는 계약은 현재 발행 데이터와 `src/eligibility/catalog/normalized_loader.py`이며, 설계 문서와 충돌하면 발행 전에 둘을 함께 고쳐야 한다.

## 1. 가장 중요한 규칙

1. 수집 원문은 `raw/`, 검증 전 후보는 `staging/`, 서비스가 읽을 확정본은 `normalized/`에 둔다.
2. 수집기가 `normalized/`를 직접 수정하지 않는다. 같은 artifact revision의 deterministic 검사와 semantic 검사가 모두 통과한 뒤 발행 단계에서만 갱신한다.
3. 확인하지 못한 값은 추측하거나 `0`, 빈 문자열 같은 값으로 채우지 않는다. 적용되는 필드를 확인하지 못했다면 그 필드를 생략하고 `version_metadata.data_gaps`에 경로와 이유를 기록한다.
4. 값마다 `source_ref_ids`로 `EvidenceRef`를 연결한다. 비교 서비스 자료는 발견·보강용이며, 공식 사실로 사용할 때는 승인 상태와 공식 재확인 여부를 분리해 기록한다.
5. 기관은 이름으로 연결하지 않고 발행된 기관 master의 `institution_id`를 사용한다. 기관을 확정하지 못하면 상품 ID를 임의 발급하지 않는다.
6. 이미 발행한 `vNNN.json`은 덮어쓰지 않는다. 의미가 달라지면 `vNNN+1.json`을 만들고, 공식 효력일을 확인한 경우에만 `effective_from`과 이전 버전의 `effective_to`를 기록한다.
7. `normalized/index.json`은 마지막에 원자적으로 발행한다. index에 실린 파일 경로·identity·sha256은 실제 상품 및 manifest와 일치해야 한다.

## 2. 저장 구조와 데이터 흐름

```text
공식 원문 또는 발견용 비교 데이터
  → raw/{batch_id}/                 원문·출처·근거를 보존
  → staging/{batch_id}/             구조화 후보와 검사 결과
  → normalized/products/.../vNNN    검증 완료된 불변 상품 버전
  → normalized/manifests/{batch_id}.json
  → normalized/index.json           서비스의 유일한 발행 진입점
```

```text
data/financial_products/
├── raw/{batch_id}/
│   ├── source_documents.json
│   ├── evidence_refs.json
│   └── source_captures/
├── staging/{batch_id}/
│   ├── products/ 또는 discovery/
│   ├── raw/
│   ├── collection_artifacts.json 또는 .jsonl
│   ├── validation_reports.json 또는 .jsonl
│   ├── manifest.json
│   └── COLLECTION_REPORT.md
├── normalized/
│   ├── products/{family_dir}/{institution_id}/{product_code}/vNNN.json
│   ├── definitions/
│   ├── manifests/{batch_id}.json
│   ├── reports/{batch_id}/
│   └── index.json
├── work/                             재생성 가능한 중간 작업물
├── archive/                          이전 스냅샷·발행 전 백업
└── README.md
```

`batch_id`는 충돌하지 않는 날짜 기반 식별자를 사용한다. 현재 관례는 `YYYYMMDD-purpose-NN`이다. 예: `20260827-official-recheck-01`.

`family_dir` 매핑은 다음과 같다.

| `product_family` | 디렉터리 | 대표 자금 흐름 |
|---|---|---|
| `INSTALLMENT_SAVINGS` | `installment_savings` | 반복 납입 |
| `TIME_DEPOSIT` | `time_deposit` | 일시 예치 |
| `PARKING_ACCOUNT` | `parking_account` | 수시 입출금 |
| `CMA` | `cma` | 수시 입출금·환매 |

## 3. 출처와 근거

### `raw/{batch_id}/source_documents.json`

문서 자체의 메타데이터를 한 번 저장한다.

| 필드 | 의미 |
|---|---|
| `source_id` | 배치와 문서 안에서 안정적인 출처 ID |
| `publisher` | 발행자 이름과 확인된 `institution_id` |
| `authority` | `OFFICIAL_INSTITUTION`, `REGULATORY_DISCLOSURE`, `OFFICIAL_ASSOCIATION`, `COMPARISON_SERVICE`, `SECONDARY` 등 |
| `document_type` | 상품 페이지, 약관, 금리표, 공시 등 문서 종류 |
| `title`, `url` | 사람이 다시 확인할 수 있는 제목과 URL |
| `version_date` | 문서가 명시한 기준일; 조회일과 혼동하지 않는다 |
| `retrieved_at` | 실제 수집 시각과 시간대 |
| `content_hash` | 보존한 원문의 sha256 |
| `capture_path` | 가능하면 `source_captures/` 아래의 로컬 원문 |

### `raw/{batch_id}/evidence_refs.json`

문서 중 어느 부분이 어느 필드를 뒷받침하는지 저장한다.

| 필드 | 의미 |
|---|---|
| `evidence_ref_id` | 상품 JSON의 `source_ref_ids`가 참조할 ID |
| `source_id` | `SourceDocument` FK |
| `locator` | 페이지, 섹션, 표 행, CSS 경로 등 재확인 위치 |
| `source_text` | 필요한 범위의 근거 원문 |
| `supports` | 예: `candidate.return_policy.rate_entries[0]` |

URL만 저장하고 근거 위치를 생략하지 않는다. 원문을 로컬에 보존할 수 없으면 그 이유와 캡처 상태를 source metadata에 기록한다.

## 4. 정규화 상품 한 건의 구조

경로는 다음 규칙을 따른다.

```text
normalized/products/{family_dir}/{institution_id}/{product_code}/v{version:03d}.json
```

최상위 필드는 다음과 같다.

| 필드 | 필수 | 의미 |
|---|---:|---|
| `product_code` | 예 | 기관·상품군 안에서 유지되는 canonical 상품 ID |
| `version` | 예 | 1부터 증가하는 불변 버전 번호 |
| `institution_id` | 예 | 기관 normalized snapshot의 FK |
| `name` | 예 | 출처에 표시된 상품명 |
| `product_family` | 예 | 네 상품군 enum 중 하나 |
| `product_subtype` | 아니요 | 확인된 subtype; 이름만 보고 추정하지 않음 |
| `classifications` | 예 | 검색용 분류. 문자열형 legacy와 근거 포함 object가 함께 존재할 수 있음 |
| `sale_policy` | 예 | 판매상태·기간·채널·수량 제한 |
| `eligibility_policy` | 예 | 가입 대상과 계좌 제한 |
| `term_policy` | 예 | 고정·선택·범위·무기한 기간 |
| `cash_flow_policy` | 예 | 예치/납입 방식, 통화, 금액 규칙 |
| `return_policy` | 예 | 금리·수익률·계산·지급·구간 정책 |
| `fee_policy` | 아니요 | 수수료. 미확인이면 내부 값을 만들지 않음 |
| `tax_policy` | 아니요 | 상품이 허용하는 세제 처리 |
| `liquidity_policy` | 아니요 | 수시입출금·중도해지·환매·자동연장 |
| `protection_policy` | 아니요 | 보호 여부와 protection scheme 참조 |
| `standard_conditions` | 예 | deterministic 가입·우대 조건 배열 |
| `custom_bindings` | 예 | 기관별 CustomDefinition 연결 배열 |
| `effective_from`, `effective_to` | 예 | 공식 효력 기간. 미확인은 `null` 허용 |
| `source_ref_ids` | 예 | 상품 전체를 뒷받침하는 EvidenceRef ID |
| `version_metadata` | 예 | 수집·검증·gap·fingerprint 메타데이터 |
| `raw_product` | 아니요 | 승인된 외부 구조 원본을 보존할 필요가 있을 때만 사용 |

최소 골격은 다음과 같다. 이 예시의 빈 policy를 그대로 발행하지 말고, 수집한 값과 근거를 채운 뒤 검사를 통과시킨다.

```json
{
  "product_code": "INST-KR-000123-1-0001",
  "version": 1,
  "institution_id": "INST-KR-000123",
  "name": "공식 상품명",
  "product_family": "INSTALLMENT_SAVINGS",
  "product_subtype": "FLEXIBLE_INSTALLMENT",
  "classifications": [],
  "sale_policy": {"status": "ON_SALE", "source_ref_ids": ["EVD-..."]},
  "eligibility_policy": {"source_ref_ids": ["EVD-..."]},
  "term_policy": {"kind": "FIXED", "unit": "MONTH", "fixed_value": 12, "source_ref_ids": ["EVD-..."]},
  "cash_flow_policy": {"funding_type": "RECURRING", "currency": "KRW", "source_ref_ids": ["EVD-..."]},
  "return_policy": {"return_kind": "INTEREST", "rate_entries": [], "source_ref_ids": ["EVD-..."]},
  "standard_conditions": [],
  "custom_bindings": [],
  "effective_from": null,
  "effective_to": null,
  "source_ref_ids": ["EVD-..."],
  "version_metadata": {
    "captured_at": "2026-08-27T12:00:00+09:00",
    "last_verified_at": "2026-08-27T12:00:00+09:00",
    "data_gaps": [],
    "content_fingerprint": "sha256:..."
  }
}
```

현행의 실제 예시는 발행 index에서 선택한다. 임의로 가장 최신처럼 보이는 파일을 고르지 않는다.

```bash
jq -r '.products[] | select(.product_family == "TIME_DEPOSIT") | .path' \
  data/financial_products/normalized/index.json | head
```

## 5. 핵심 policy 규칙

### 판매·기간·자금 흐름

- `sale_policy.status`: `UPCOMING`, `ON_SALE`, `SUSPENDED`, `ENDED`, `UNKNOWN` 중 근거가 있는 값만 사용한다.
- `term_policy.kind`: `FIXED`, `DISCRETE`, `RANGE`, `OPEN_ENDED` 중 하나다. kind에 해당하는 값만 둔다.
- 금액과 비율은 계산 오차를 막기 위해 decimal 문자열로 저장한다. 기간의 정수 값은 JSON integer를 사용한다.
- `cash_flow_policy.funding_type`은 현행 발행본의 값을 따른다. 새 enum을 만들거나 상품명에서 추정하지 않는다.

### 금리·수익

- 계산 가능한 금리·수익률은 `return_policy.rate_entries[]`에 둔다.
- `calculation.value`의 `PERCENT`와 우대폭의 `PERCENTAGE_POINT`를 구분한다.
- 기간·잔액별 적용 범위는 `applies_to[].basis`와 경계 포함 여부까지 기록한다.
- 공시수익률과 실적배당을 확정 금리로 바꾸지 않는다. 관측값에는 `as_of`를 붙인다.
- `advertised_max_rate`는 표시·합계 검증용이며 기본금리 대신 사용하지 않는다.
- `standard_conditions[].reward_refs`와 `custom_bindings[].reward_refs`는 같은 상품의 `rate_entries[].rate_id`만 참조해야 한다.

### 조건·보호·미확인 값

- 반복 사용되는 조건은 `standard_conditions`, 기관 고유 조건은 `custom_bindings`로 저장한다.
- 보호 여부는 기관군이나 CMA subtype에서 추론하지 않는다.
- 적용되지 않는 필드는 생략한다. 적용되지만 확인하지 못한 필드는 다음처럼 gap으로 남긴다.

```json
{
  "path": "liquidity_policy.early_termination_allowed",
  "reason": "현재 공식 상품 페이지와 약관에서 중도해지 가능 여부를 확인하지 못함",
  "source_ref_ids": ["EVD-..."]
}
```

## 6. 수집 배치 절차

1. `batch_id`를 정하고 `raw/{batch_id}/`와 `staging/{batch_id}/`를 만든다.
2. 원문을 `source_captures/`에 보존하고 `source_documents.json`에 hash·URL·수집시각을 등록한다.
3. 필드별 `EvidenceRef`를 만들고 후보 값에 `source_ref_ids`를 연결한다.
4. 기관 master에서 `institution_id`를 확인한다. 불일치·동명이면 기관 staging에서 먼저 해결한다.
5. 후보를 `CollectionArtifact`로 저장한다. 수정 시 기존 artifact를 덮어쓰지 않고 `revision+1`을 만든다.
6. deterministic 검사와 독립 semantic 검사를 같은 revision에 실행한다.
7. `FAIL` 또는 `NEEDS_REVIEW`가 있으면 발행하지 않고 새 artifact revision으로 되돌아간다.
8. 두 검사가 모두 `PASS`이면 기존 현재 버전과 의미 fingerprint를 비교한다.
9. 의미가 같으면 새 상품 버전을 만들지 않는다. 의미가 다르면 다음 `vNNN.json`을 추가한다.
10. CustomDefinition 등 참조 대상을 먼저 발행하고 상품, manifest, index 순으로 발행한다.
11. 아래 검증을 모두 통과한 뒤 배치를 완료한다.

발견용 비교 서비스 수집은 canonical 수집과 gate가 다르다. 자세한 상태와 파일 배치는 [`staging/README.md`](staging/README.md)의 “discovery 전용 staging”을 따른다.

## 7. 발행물의 역할

### `normalized/manifests/{batch_id}.json`

발행 배치의 감사 기록이다. 최소한 다음 관계가 맞아야 한다.

- `publication_status=PUBLISHED`
- `counts.total_products == index.product_count`
- `institution_ids`가 index의 기관 집합과 동일
- source/evidence/custom definition/validation/report 경로가 실제 파일을 가리킴
- `files[]`의 모든 `path`, `sha256`, `size_bytes`가 실제 파일과 일치
- `previous_manifest`로 이전 발행본을 추적 가능

### `normalized/index.json`

서비스가 읽는 유일한 current pointer다. 각 `products[]` 행은 다음을 가진다.

```text
product_code / version / product_family / institution_id / sale_status / path / sha256
```

기본 런타임은 `sale_status=ON_SALE`만 로드한다. 다른 상태를 보존하는 것과 서비스 기본 후보에 노출하는 것은 별개다.

현재 건수는 문서에 복사해 두지 않고 발행 파일에서 확인한다.

```bash
jq '{product_count, manifest_batch, publication_status}' \
  data/financial_products/normalized/index.json
jq '.counts' \
  "data/financial_products/normalized/manifests/$(jq -r '.manifest_batch' data/financial_products/normalized/index.json).json"
```

## 8. 검증 명령

프로젝트 가상환경에 개발·Web 의존성을 한 번 설치한다. `test_normalized_runtime_catalog.py`는 Web endpoint도 검사하므로 `fastapi`가 필요하다.

```bash
python -m pip install -e ".[web,dev]"
```

JSON 문법과 단일 파일 내용은 먼저 확인한다.

```bash
python -m json.tool path/to/file.json >/dev/null
```

현재 index, manifest, 기관 FK, source/evidence/custom 참조, 파일 sha256, 기본 판매상태 필터는 loader로 검증한다.

```bash
PYTHONPATH=src python -c \
  'from eligibility.catalog.normalized_loader import load_normalized_product_catalog; print(len(load_normalized_product_catalog(verify_hashes=True)))'
```

정규화 카탈로그의 데이터·런타임 회귀 검사를 실행한다.

```bash
PYTHONPATH=src python -m pytest -q tests/test_normalized_runtime_catalog.py
```

상품군 또는 필드 정책을 바꿨다면 전체 회귀 검사도 실행한다.

```bash
PYTHONPATH=src python -m pytest -q
```

## 9. 관련 문서와 코드

- [`staging/README.md`](staging/README.md): staging 및 discovery 배치 규칙
- [`docs/FINANCIAL_PRODUCT_DATA_MODEL.md`](../../docs/FINANCIAL_PRODUCT_DATA_MODEL.md): 필드와 조건 모델의 상세 설계
- [`data/institutions/README.md`](../institutions/README.md): 기관 식별자 수집·정규화 규칙
- [`src/eligibility/catalog/normalized_loader.py`](../../src/eligibility/catalog/normalized_loader.py): 서비스 로딩·무결성 검증의 실행 기준
- [`tests/test_normalized_runtime_catalog.py`](../../tests/test_normalized_runtime_catalog.py): 발행 데이터 회귀 기준
