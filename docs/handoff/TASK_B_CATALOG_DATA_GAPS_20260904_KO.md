# 작업 B — 발행 카탈로그의 데이터 구멍 조사·복구

리포지토리: 이 문서가 있는 저장소 루트
Python: `.venv/bin/python`
테스트: `.venv/bin/python -m pytest -q`

**시작 전에 반드시:** `git switch -c fix/catalog-data-gaps`

**병렬 작업 주의 — 다른 세션이 동시에 돌고 있다.**
그 세션은 `docs/handoff/TASK_A_LOTTERY_FEATURE_20260904_KO.md` 를 수행하며
`src/eligibility/catalog/normalized_loader.py` 와
`tests/test_normalized_feature_policy.py` 를 수정한다. **이 두 파일은 건드리지 마라.**
`src/**` 전체를 수정하지 말고, 수정이 필요하다고 판단되면 **보고만** 하라.

**사용자의 미커밋 작업물 보호:** 워킹 트리에 사용자가 직접 편집 중인 파일이 있다.
```
data/product_catalog/products/IBK_LOVE_SHARING_SAVINGS_20260805.json
data/product_catalog/products/TOSS_CHILD_SAVINGS_20260714.json
handoff/
추적되지 않은 scripts/*.py 다수
```
**이들을 수정·삭제·stash 하지 마라.** `git checkout -- .`, `git stash`, `git reset --hard`,
`git clean` 류의 명령을 절대 쓰지 마라.

---

## 배경 1: 카탈로그 구조 (2단계 조회에 주의)

- 진입점 `data/financial_products/normalized/index.json` → `products[]` 각 항목이
  `{product_code, version, product_family, institution_id, sale_status, path, sha256}`.
  총 4,306개 버전.
- 상품 JSON 은 `source_ref_ids: ["EVD-..."]` 를 가진다. 이건 **`source_documents.json` 을
  직접 가리키지 않는다.** 2단계다:
  ```
  product.source_ref_ids[]  →  evidence_refs.json 의 evidence_ref_id
                            →  그 항목의 source_id  →  source_documents.json
  ```
  URL 은 evidence 의 `locator.url` 에도, source 의 `url` 에도 있을 수 있다. 둘 다 봐야 한다.
- `index.json` 의 `sha256` 은 상품 파일 내용의 해시이며 로더가 **검증한다**
  (`normalized_loader._verify_sha256`). 상품 JSON 을 한 글자라도 고치면 반드시 해시를
  다시 계산해 `index.json` 에 반영하라. 안 하면 `NormalizedCatalogError` 로 전부 깨진다.
  `data/financial_products/normalized/manifests/` 의 배치 매니페스트 7개도 확인할 것.
  참고할 기존 발행 스크립트:
  `scripts/publish_naver_first_catalog_20260828.py`,
  `scripts/publish_official_rate_schedules_20260827.py`

## 배경 2: 이 프로젝트의 핵심 원칙

**모르는 값을 절대 지어내거나 0으로 채우지 않는다.**
확인되지 않은 금리·링크·출처는 비워두고 `version_metadata.data_gaps` 에 **신고**한다.
이 규칙이 프로젝트 신뢰성의 근간이고, 실제로 대부분의 구멍은 이미 정직하게 신고되어 있다.

따라서 이 작업의 성공 기준은 "구멍을 몇 개 메웠다"가 **아니다.**
**각 구멍이 (a) 실제 값으로 채워졌거나 (b) 명시적으로 신고되었거나 (c) 정상 모델링임이
확인되었다** — 셋 중 하나면 완료다. 정직한 신고도 완료로 친다.

---

## 구멍 1 — 웹 링크가 하나도 없는 상품 55개

증상: UI 의 "자세히 보기"로 보낼 곳이 없다.
정의: 그 상품의 모든 evidence/source 에서 `locator.url` 과 `url` 이 전부
`http(s)` 로 시작하지 않음.

기관별 분포 (검증 완료, 합계 55):
```
INST-KR-000408: 12   INST-KR-000259: 6    INST-KR-000830: 6    INST-KR-000796: 5
INST-KR-000296: 4    INST-KR-000055: 4    INST-KR-000459: 3    INST-KR-000028: 2
INST-KR-000076: 2    INST-KR-000148: 2    INST-KR-000175: 2    INST-KR-000739: 2
INST-KR-000084: 1    INST-KR-000092: 1    INST-KR-000244: 1    INST-KR-000289: 1
INST-KR-000424: 1
```

대부분 `document_type` 이 `INTERNAL_ORIGINAL_TEXT` (내부 원문) 하나뿐이다.
**먼저 손댈 곳:** 공식 출처를 이미 갖고 있는데도 URL 이 없는 것들. 수집 누락일 가능성이 높다.
```
INST-KR-000259-4-0004 / -0005 / -0006   document_type: [INTERNAL_ORIGINAL_TEXT, OFFICIAL_RATE_NOTICE]
INST-KR-000296-4-0003 / -0004 / -0005 / -0006   [INTERNAL_ORIGINAL_TEXT, OFFICIAL_WEBPAGE]
INST-KR-000289-4-0002                    [INTERNAL_ORIGINAL_TEXT, OFFICIAL_WEBPAGE]
```

참고 — 이 프로젝트에는 **의도적인 링크 억제 정책**이 따로 있다:
`src/eligibility/application_service.py:3875` 부근에서 `pay.naver.com/` 이 포함된 URL 은
PRODUCT_CONDITION 링크로 노출하지 않는다 (사용자 확인 완료된 의도적 정책).
따라서 "링크가 안 보인다"와 "링크 데이터가 없다"는 다른 문제다. 혼동하지 마라.

## 구멍 2 — 금리 정보가 전혀 없는 상품 (실제 문제는 3개)

`return_policy.rate_entries` 가 빈 상품은 67개지만 거의 전부 정상이다. 분류 (검증 완료):

| 분류 | 개수 | 판정 |
|---|---|---|
| A. `performance_observations` 보유 (CMA) | 11 | **정상** — 확정금리가 아니라 실적수익률이 맞는 모델 |
| B. `ranking_eligible: false` 명시 (파킹) | 3 | **정상** — 랭킹 제외가 명시됨 |
| C. `data_gaps` 에 금리/실적 누락을 신고함 | 50 | **정상** — 정직하게 신고됨 |
| D. **아무 표시 없이 조용히 금리가 없음** | **3** | **← 진짜 문제** |

D 목록 3개 전부:
```
INST-KR-000092-4-0004        | CMA             | CMA-MMW(약정형)    | data_gaps 없음
INST-KR-000736-3-C1C2DCAD7BA | PARKING_ACCOUNT | 기업자유예금플러스  | data_gaps 없음
INST-KR-000830-2-0001        | TIME_DEPOSIT    | 정기예금           | 'source','official_research' 만 신고
```
금리를 확보할 수 있으면 채우고, 없으면 `data_gaps` 에 명시적으로 신고하라.

## 구멍 3 — `data_gaps` 스키마가 통일돼 있지 않다 (조사 우선)

`version_metadata.data_gaps` 항목의 키 조합이 카탈로그 전체에서 **14가지**다 (검증 완료):
```
5537  (path, reason)                    ← 주류
  96  (execution_policy, path, product_code, product_name, reason, status)
  87  (field, message, reason_code)     ← path 대신 field 를 쓴다
  64  (code, path, reason, status)
  62  (input_line, path, product_code, product_name, reason, source_priority,
       source_ref_ids, status, usage_impact)
  25  (code, path, reason)
  19  (code, field, message, reason_code, source_ref_ids)
   8  (execution_policy, path, reason, status)
   7  (gap_id, path)
   5  (message, path, reason_code)
   2  (field, message, reason_code, source_ref_ids)
   1  (path, reason, source_ref_ids, status, usage_impact)
   1  (input_line, path, product_code, reason, source_ref_ids, status, usage_impact)
   1  (classification, does_not_affect_reward_value, path, reason)
```

`path` 계열과 `field` 계열이 공존해서, `g["path"]` 만 읽는 코드는 **108개 항목을 조용히 놓친다.**
이건 가설이 아니다 — 이 감사를 처음 돌렸을 때 위 구멍 2의 D 분류가 3개가 아니라 **15개로
잘못 나왔다.** 그 오차의 원인이 정확히 이것이다.

할 일: `src/**` 와 `scripts/**` 에서 `data_gaps` 를 읽는 곳을 전부 찾아 어느 형식을 가정하는지
조사하라. 실제로 항목을 놓치는 코드가 있으면 **어디서 무엇을 놓치는지 구체적으로 보고**하라.
`src/` 수정이 필요하면 직접 고치지 말고 보고만 할 것 (다른 세션이 `src/` 를 만지고 있다).
`scripts/` 는 수정해도 된다.

## 구멍 4 — 모든 출처에 `document_type` 이 없는 상품 28개

기관별 (검증 완료, 합계 28):
```
INST-KR-000424: 6   INST-KR-000223: 5   INST-KR-000796: 5   INST-KR-000219: 3
INST-KR-000252: 3   INST-KR-000739: 3   INST-KR-000216: 2   INST-KR-000516: 1
```
추적성 라벨 누락이며 사용자에게 직접 보이지 않는다. **우선순위 최하.**
허용값은 `source_documents.json` 의 기존 값 분포에서 확인하라 (예: `COMPARISON_SERVICE`,
`OFFICIAL_WEBPAGE`, `OFFICIAL_RATE_NOTICE`, `INTERNAL_ORIGINAL_TEXT`,
`INTERNAL_ORIGINAL_FALLBACK`). **출처의 실제 성격을 확인한 뒤에만** 라벨을 붙여라.

---

## 작업 순서

1. **먼저 감사 스크립트를 만들어 위 숫자를 재현하라**
   (`scripts/audit_catalog_gaps_20260904.py` 같은 새 파일).
   목표: 링크 없음 55, 금리 D분류 3, document_type 없음 28, data_gaps 스키마 14종.
   **재현되지 않으면 거기서 멈추고 보고하라.** 내 숫자가 틀렸을 수 있다.
   (특히 `data_gaps` 를 읽을 때 `g.get("path") or g.get("field")` 로 두 형식을 모두 다룰 것.
   `g["path"]` 만 쓰면 나와 같은 실수를 반복하게 된다.)
2. 우선순위: 구멍 3 조사 → 구멍 2 의 3건 → 구멍 1 의 "공식 출처 있는데 URL 만 없는" 8건
   → 구멍 1 나머지 → 구멍 4.
3. 데이터를 고칠 때마다 `index.json` 의 `sha256` 재계산을 반드시 동반하고,
   매 단계 후 `.venv/bin/python -m pytest -q` 로 카탈로그가 여전히 로드되는지 확인하라.
4. **회귀 방지 테스트를 추가하라.** 지금 이 구멍들은 어떤 테스트도 잡지 못한다.
   이 프로젝트가 이미 쓰는 "알려진 예외 목록" 패턴을 따를 것 — 남은 것을 명시적으로 열거하고
   집합이 **정확히 일치**하는지 단언한다:
   ```python
   KNOWN_PRODUCTS_WITHOUT_A_WEB_LINK = {...}   # 남은 것을 전부 나열
   assert missing_web_link == KNOWN_PRODUCTS_WITHOUT_A_WEB_LINK
   ```
   이러면 새 구멍이 생기는 순간 테스트가 깨진다.
   `>= 0`, `len(x) < 100`, `is not None` 같은 무의미한 단언은 쓰지 마라.

## 테스트 데이터에 관한 주의 (최근에 이것 때문에 CI 가 27개 깨졌다)

`.gitignore` 가 `data/` 아래 여러 빌드 샤드를 제외한다. 그런데 일부 테스트가 그 샤드를 읽어서,
로컬에서는 통과하고 CI(클린 체크아웃)에서는 `FileNotFoundError` 로 죽는 사고가 있었다.
지금은 세 테스트 파일이 **발행 카탈로그를 읽도록** 재조준되었고, 대응물이 없는 빌드 원장
몇 개만 `git add -f` 로 추적된다. `.gitignore` 상단 주석에 그 목록이 있다.

**그러므로:** 테스트가 새로 읽을 데이터 파일을 추가할 때는
`git check-ignore -v <경로>` 로 무시되는지 먼저 확인하고, 무시된다면
(a) 발행 카탈로그의 대응물을 읽도록 바꾸거나 (b) `git add -f` + `.gitignore` 주석 추가
중 하나를 하라. 로컬 통과만 보고 끝내지 마라.

## 지켜야 할 선

- **없는 값을 지어내지 마라.** 확인 못 하면 `data_gaps` 에 신고하고 넘어가라.
- `src/**` 수정 금지 (보고만). `tests/test_normalized_feature_policy.py` 수정 금지.
- 위에 적은 사용자 미커밋 파일 보호. 파괴적 git 명령 금지.
- 예상과 다른 사실을 발견하면 추측으로 덮지 말고 **그 사실을 보고**하라.
- 커밋 메시지 끝에:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

## 완료 조건 / 최종 보고에 반드시 포함할 것

- 구멍 1·2·4 각각: 착수 시점 개수 → 종료 시점 개수, 그리고 그 차이가
  **(a) 실제 값 확보 / (b) `data_gaps` 신고 / (c) 정상 모델링으로 판명** 중 무엇으로
  줄었는지 분해할 것
- 실제 값을 채운 항목은 **출처 URL 을 함께** 제시
- 구멍 3: `data_gaps` 를 읽는 코드 목록과, 각각이 108개 `field` 계열 항목을 놓치는지 여부
- 데이터를 수정했다면 `index.json` 해시 재계산이 되었음을 확인한 근거
- `.venv/bin/python -m pytest -q` 결과 (작업 전 / 작업 후).
  참고: 워킹 트리에는 사용자의 미커밋 편집 때문에 실패하는 테스트가 1개 있다
  (`test_web_mvp_runtime.py::test_shared_toss_auto_transfer_question_names_the_twelve_month_period`).
  이 작업과 무관하니 고치려 하지 마라.
