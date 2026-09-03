# scripts/migrations

이 디렉터리는 이미 실행이 끝난 1회성 데이터 빌드/보정 스크립트를 보관한다.

- `_row*_build.py`, `_row*_publish.py`, `_row*_correct_v004.py`, 그리고 날짜(`YYYYMMDD`)가 붙은
  `apply_*`, `build_*`, `publish_*`, `correct_*` 류 스크립트들이다.
- 특정 시점의 특정 상품/기관 batch를 대상으로 한 번 실행하고 끝난 작업 기록이며,
  결과는 이미 `data/financial_products/normalized/` 등 발행 데이터에 반영되어 있다.
- **재실행 금지.** 같은 스크립트를 다시 돌리면 이미 발행된 데이터와 어긋나거나 중복 반영될 수 있다.
- 코드 자체는 provenance(무엇을, 언제, 어떻게 반영했는지) 기록 목적으로만 남겨둔다.
- 이 디렉터리는 `.gitignore`에 의해 git 추적 대상에서 제외된다(이 README만 예외).

재사용 가능한 도구는 `scripts/` 최상위에 그대로 있다. 새 스크립트가 반복 실행되는 범용
도구라면 이 디렉터리가 아니라 `scripts/` 최상위에 작성한다.
