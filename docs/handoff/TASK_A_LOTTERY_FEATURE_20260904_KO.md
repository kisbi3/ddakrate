# 작업 A — 추첨형 상품 기능(LOTTERY_BASED_BENEFIT) 복구

리포지토리: 이 문서가 있는 저장소 루트
Python: `.venv/bin/python`
테스트: `.venv/bin/python -m pytest -q`

**시작 전에 반드시:** `git switch -c fix/lottery-feature`

**병렬 작업 주의 — 다른 세션이 동시에 돌고 있다.**
그 세션은 `docs/handoff/TASK_B_CATALOG_DATA_GAPS_20260904_KO.md` 를 수행하며
`data/financial_products/**` 와 `scripts/**` 를 수정한다.
이 작업은 그 경로를 **읽기만** 하고 절대 쓰지 마라.

**사용자의 미커밋 작업물 보호:** 워킹 트리에 사용자가 직접 편집 중인 파일이 있다
(`data/product_catalog/products/*.json` 2개, `handoff/`, 추적되지 않은 `scripts/*.py` 다수).
`git checkout -- .`, `git stash`, `git reset --hard`, `git clean` 류의 명령을 절대 쓰지 마라.

---

## 문제

`src/eligibility/catalog/normalized_loader.py` 의 `_normalized_product_features()` (99~108행):

```python
def _normalized_product_features(product: dict[str, Any]) -> list[ProductFeature]:
    """Project explicitly supported normalized structures into typed features.

    This deliberately keys off the canonical preferential application mode;
    variable rates and free-form text are not evidence of a lottery benefit.
    """
    preferential = (product.get("return_policy") or {}).get("preferential_application") or {}
    if preferential.get("mode") == "CUMULATIVE_LOTTERY":
        return [ProductFeature(feature_id="LOTTERY_BASED_BENEFIT", present=True)]
    return []
```

이 함수는 **은퇴한 스키마**(`return_policy.preferential_application.mode == "CUMULATIVE_LOTTERY"`)만
본다. 발행된 카탈로그 4,306개 버전 중 이 값을 가진 상품은 **0개**다 (검증 완료).

결과: `LOTTERY_BASED_BENEFIT` 피처가 **어떤 상품에도 부여되지 않는다.**
`apply_feature_policy(products, "LOTTERY_BASED_BENEFIT", FeaturePolicy.EXCLUDE)` 가
실질적으로 무동작이며, "운에 좌우되는 상품은 빼줘"라는 요구가 조용히 무시된다.
예외가 나지 않으므로 런타임에서 드러나지 않는다.

## 실제 데이터가 있는 곳

데이터는 더 정밀한 `return_policy.preferential_policy.rules[].reward` 형식으로 이전했다.
현재 카탈로그에 추첨/랜덤 성격의 상품은 **정확히 2개**이며, **서로 다른 두 형식**을 쓴다.
하나만 잡으면 절반만 고치는 셈이다.

**(1) `INST-KR-000424-1-C3C58A5BBCE` — 우리 두근두근 행운적금**
rule `INST-KR-000424-1-C3C58A5BBCE-PREF-01`, title `"행운카드 당첨 우대"`:
```json
{"kind": "CUMULATIVE_RATE",
 "counter_fact_key": "PROMOTION.LUCK_CARD_WIN_COUNT",
 "count_outcome": "WIN",
 "value_per_occurrence": "2.00", "max_occurrences": 5, "cap_value": "10.00"}
```

**(2) `INST-KR-000865-1-0002` — 궁금한 적금 (K뱅크)**
rule `INST-KR-000865-1-0002-PREF-RANDOM-CUMULATIVE`, title `"입금 시 랜덤 우대금리 누적"`:
```json
{"kind": "RANDOM_REWARD", ...}
```
trigger 에 `"one_reward_draw_per_event": true` 가 있다.

카탈로그 전체 집계 (검증 완료):
- `reward.count_outcome` 값 분포: `{"WIN": 1}` — 즉 `count_outcome == "WIN"` 은 (1)만 잡는다
- `reward.kind` 분포:
  `ADD_RATE 1340, UNKNOWN_REWARD 81, COUPON_VARIABLE 78, TIERED_RATE 76,`
  `CUMULATIVE_RATE 29, SET_FINAL_RATE 13, EXTERNAL_POSTED_RATE 11, BONUS_RATE 3,`
  `REPEATABLE_ADD_RATE 2, RANDOM_REWARD 1`

**함정:** `kind == "CUMULATIVE_RATE"` 만으로 판정하면 추첨과 무관한 **29개가 오탐**된다.
`CUMULATIVE_RATE` 는 `count_outcome == "WIN"` 과 **함께** 볼 때만 추첨 신호다.

## 할 일

1. `_normalized_product_features()` 가 새 형식을 인식하도록 수정하라. 판정 규칙은 **좁게** 정의할 것.
   권장: rule 의 reward 가 `kind == "RANDOM_REWARD"` **이거나** `count_outcome == "WIN"` 인 경우.
   docstring 을 실제 동작에 맞게 갱신하고, 왜 좁게 잡는지(변동금리·자유 텍스트는 근거가 아님)를
   유지하라.

2. `tests/test_normalized_feature_policy.py` 를 고쳐라. 이 파일에는 함정이 있다:
   - 첫 번째 테스트에 `@pytest.mark.xfail(..., strict=True)` 와 그 위에 25줄짜리 주석 블록이
     붙어 있다. 그 주석은 "지금은 못 고친다"는 기록이므로 마커와 함께 **삭제**하라.
   - **중요:** xfail 만 떼면 여전히 실패한다. 테스트 본문이 아직 **은퇴한 형식**으로 상품을 고른다:
     ```python
     p.normalized.return_policy.get("preferential_application", {}).get("mode") == "CUMULATIVE_LOTTERY"
     ```
     새 형식 기준으로 다시 써야 한다. 기대 집합은 위 2개 상품이며, **`product_code` 를 명시해
     정확히 그 2개임을 단언**하라. 개수만 세지 말 것.
   - 두 번째 테스트 `test_variable_rate_and_similar_name_do_not_trigger_lottery_feature` 는
     "변동금리 상품이 추첨으로 오인되지 않는다"는 오탐 가드다. 이것도 은퇴한 형식을 참조하니
     갱신하되 **가드로서의 효력을 반드시 유지**하라
     (`return_policy.rate_type == "VARIABLE"` 인 45개가 추첨으로 잡히면 안 된다).

3. `apply_feature_policy(..., FeaturePolicy.EXCLUDE)` 가 실제로 이 2개를 걸러내는지 확인하라.
   피처가 `metadata.features` 까지 흘러가는 경로를 끝까지 따라갈 것.

## 지켜야 할 선

- `data/**`, `scripts/**` 수정 금지. 파괴적 git 명령 금지.
- **단언을 느슨하게 만들어 통과시키지 마라.** `== 1` → `>= 1`, 정확한 문자열 → 부분 문자열,
  `== [...]` → `is not None` 같은 변경은 전부 금지다.
  테스트가 데이터와 안 맞으면 **데이터 현실에 맞게 정확히 다시 쓰되 검증력은 유지**한다.
- 예상과 다른 사실을 발견하면 추측으로 덮지 말고 **그 사실을 보고**하라.
  (이 지시서의 숫자도 틀렸을 수 있다. 틀렸으면 거기서 멈추고 보고할 것.)
- 커밋 메시지 끝에:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

## 완료 조건

- `.venv/bin/python -m pytest -q tests/test_normalized_feature_policy.py` — 2개 모두 통과,
  xfail/xpass 없음
- `.venv/bin/python -m pytest -q` 전체 — **작업 시작 시점의 실패 수를 먼저 기록해두고**,
  그 수가 늘지 않았음을 보일 것. xfail 은 이 작업으로 1개 줄어드는 것이 정상이다.
  (참고: 워킹 트리에는 사용자의 미커밋 편집 때문에 실패하는 테스트가 1개 있다.
  `test_web_mvp_runtime.py::test_shared_toss_auto_transfer_question_names_the_twelve_month_period`
  — 이 작업과 무관하니 고치려 하지 마라.)
- 최종 보고에 다음을 포함:
  (a) 새 판정 규칙이 잡는 상품 목록 **전체**
  (b) 그 규칙이 과다탐지하지 않는다는 근거 (집계 수치)
  (c) 전체 테스트 실행 결과 (작업 전 / 작업 후)
