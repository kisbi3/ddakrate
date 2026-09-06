# 공모전 제출 자료

2026-09-07 기준 제품 설명과 기능 범위는 다음 두 개정 원고를 기준으로 한다.

- [기획서](2026_FINANCIAL_AI_CHALLENGE_PLAN_DRAFT.md)
- [기능 명세서](2026_FINANCIAL_AI_CHALLENGE_FUNCTION_SPEC_DRAFT.md)

이번 개정은 2026-08-25 초안(상품 209개)을 2026-09-05 발행 카탈로그(상품 4,295개, 판매 중 4,289개)에 맞추고, 화면 표기 **딱금리**를 반영한다. 기존 대화형 탐색·계산 기능과 기본 OFF인 우대조건 의미 분석 실험을 구분하며, 실제 API 실험에서 확인한 성과·검증 결함·미검증 범위를 적는다. 프롬프트 실험 라운드는 마무리했지만 모델 가중치 학습이나 운영 활성화를 완료했다는 뜻은 아니다.

## 파일 버전 관계

| 파일 | 용도와 상태 |
|---|---|
| 위 두 Markdown 원고 | 2026-09-07 개정본. 내용 검토의 기준 |
| `2026_금융_AI_Challenge_기획서_ddakrate_초안.hwpx` | 제출 양식. 2026-09-07 개정 문구를 Markdown과 동기화함. 페이지 배치·이미지는 제출 전 한글에서 확인 |
| `2026_금융_AI_Challenge_기능명세서_ddakrate_초안.hwpx` | 제출 양식 요약본. 기획서와 같은 팀명·딱금리 표기, 실제 화면(조건 카드·필터·Top 3) 기준으로 맞춤 |
| `*.backup_*.hwpx` | 2026-08-25 당시 편집 백업. 이번 개정 미반영 |
| [통합 제출 초안](CONTEST_SUBMISSION_DRAFT.md) | 이전 배경 자료. 현재 구현·검증 상태는 개정 원고를 우선 |
| [금융상품 데이터 관계 설계](FINANCIAL_PRODUCT_DATA_RELATIONSHIP.md) | 데이터 모델 설명. 구축 규모는 2026-09-05 발행 수치 |

HWPX는 Markdown 전체를 그대로 담은 파일이 아니라 제출 양식에 맞춘 요약·편집본이다. 제출할 때는 페이지 배치·이미지·표를 한글에서 확인하고, 팀명·구성원·배포 URL 등 미입력 항목을 실제 제출 정보로 채운다.

## 개정 근거

- 발행 카탈로그: `data/financial_products/normalized/index.json` (2026-09-05, `product_count` 4,295)
- [실제 원문 프롬프트 실험과 원응답](../../reports/semantic-prompt-experiment-20260907/README.md)
- [의미 분석 기능 설명](../semantic-preferential-questions.md)
- [구현 진행 기록](../handoff/SEMANTIC_PREFERENTIAL_PROGRESS_20260906_KO.md)

실험 결과는 제한된 원문과 반복 호출의 관찰이다. 카탈로그 전체 정확도, 금리 보장 또는 운영 준비 완료의 근거로 사용하지 않는다.
