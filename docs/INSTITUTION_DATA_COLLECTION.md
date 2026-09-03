# 금융기관 기본정보 수집

상품을 수집하기 전에 공식 기관 자료로 Institution 마스터를 먼저 구축한다.
`institution_id`는 외부 기관의 번호가 아니라 ddakrate가 발급하는 내부 PK다.
금융위원회 `fssCorpUnqNo`, 법인등록번호, 사업자등록번호, 행정표준기관코드는
`identifiers[]`에 서로 다른 namespace로 저장한다.

```json
{
  "institution_id": "INST-KR-000123",
  "official_name_ko": "<공식 기관명>",
  "identifiers": [
    {"system": "FSS_CORP_UNIQUE_NO", "value": "00685935", "country": "KR"},
    {"system": "CRNO", "value": "1101113892240", "country": "KR"},
    {"system": "BZNO", "value": "1078708658", "country": "KR"}
  ]
}
```

Product와 CustomDefinition은 외부 번호가 아니라 이 내부 ID를 참조한다.
내부 ID 매핑은 한 번 발급하면 재정렬하지 않으며, 새 기관은 다음 순번으로만
추가한다. 합병이나 상호변경은 공식 법적 관계를 확인한 뒤 동일 기관의 버전 또는
별도 기관과 관계로 처리하고 번호를 임의로 합치지 않는다.

## 키 설정

가장 편한 방법은 현재 셸에 키를 주입하는 것이다.

```sh
export FSC_OPENAPI_SERVICE_KEY='발급받은 키'
```

또는 저장소 루트의 무시되는 `.env`에 `FSC_OPENAPI_SERVICE_KEY=...`를
작성한다. 둘 다 없으면 실행 시 숨겨진 입력 프롬프트가 표시된다. 키를
명령행 인자로 받지 않으므로 셸 히스토리와 프로세스 목록에 키가 남지
않는다. 환경변수 → `.env` → 숨은 프롬프트 순으로 찾으며, data.go.kr에서
복사한 퍼센트 인코딩 키와 디코딩 키를 모두 받아 요청에는 한 번만
인코딩한다.

## 실행

```sh
PYTHONPATH=src python -m eligibility.ingestion.institution_collector \
  --output-dir data/institutions
```

필요하면 `--bas-dt YYYYMMDD`, `--crno`, `--fnco-nm`, `--page-size`로
조회 조건을 줄일 수 있다. 키가 없거나 API 요청이 실패하면 파일을 만들지
않으며, 수집된 기관을 꾸며내지 않는다.

통신은 HTTPS를 사용한다. 금융위원회 API 수집기는 공식 외부 식별자 공급원 중
하나다. API가 `fssCorpUnqNo`를 주지 않더라도 기관을 즉시 제외하지 않는다.
법인은 DART 법인등록번호와 기관 공식 사업자등록번호, 공공기관은
행정표준기관코드처럼 해당 기관 유형에 맞는 공식 식별자를 추가 확인한다.

확인하지 못한 FSS 번호를 DART 번호나 행정코드로 가장해 저장하지 않는다.
`identifiers[]`에는 실제 system을 표시하고, 조회했지만 제공되지 않은 식별자는
`identifier_coverage.status=NOT_PUBLISHED_BY_SOURCE` 등으로 별도 기록한다.

결과는 데이터 모델의 수집·발행 경계를 지키도록 분리한다.

- `data/institutions/raw/`: API 페이지 원문과 조회 메타데이터
- `data/institutions/normalized/`: 상품/카탈로그가 참조할 기관 레코드

정규화 레코드는 확인된 값만 포함하며 `institution_id`, `identifiers[]`,
`official_name_ko`, `official_name_en`, `entity_type`, `institution_categories`,
`sicCd`, `sicNm`, `source_ref_ids`, `identifier_coverage`를 사용한다.

현재 발행본은 다음 파일을 기준으로 한다.

- 내부 ID 마이그레이션: `scripts/migrate_institution_identity_20260825.py`
- 현재 기관 index: `data/institutions/normalized/index.json`
- 외부 ID→내부 ID 감사 매핑: `data/institutions/normalized/mappings/20260825-institution-identity-01.json`
- 현재 엔터티: `data/institutions/normalized/entities/{institution_id}/vNNN.json`
