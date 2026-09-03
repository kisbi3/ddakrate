# 금융기관 데이터 저장 구조

금융기관 데이터는 금융상품 데이터와 독립된 기준 데이터로 관리한다. 상품은 `institution_id`로 기관을 참조하며, 이 값은 금융위원회 원천 데이터의 `fssCorpUnqNo`와 연결된다.

```text
data/institutions/
├── raw/{batch_id}/
│   └── fsc_institutions_{capture_id}.json
├── normalized/
│   ├── entities/{institution_id}/vNNN.json
│   ├── snapshots/{snapshot_file}.json
│   └── manifests/{batch_id}.json
└── README.md
```

`raw/`와 `normalized/snapshots/`는 수집 당시 전체 결과를 보존한다. `normalized/entities/{institution_id}/`에는 상품이 직접 참조할 기관별 정규화 레코드를 버전별로 둔다. 2026-08-26 `20260826-naver-accepted-01`에서 공식 identity가 확인된 신협 597개를 추가해 현재 기관 snapshot은 1,704개다.

기관 정보가 변경되면 기존 버전을 덮어쓰지 않고 새 버전을 추가한다. 변경 효력일은 공식 원천에서 확인된 경우에만 기록한다.
