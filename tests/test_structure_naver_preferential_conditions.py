from __future__ import annotations

from eligibility.catalog.normalized_loader import load_normalized_product_catalog
from scripts.structure_naver_preferential_conditions_20260827 import detached_patch, parse


SHINHAN_RUNNING_SHOE_TEXT = """조건별
다음의 우대조건 충족 시 최고 연 5.0% 우대이자율이 적용됩니다.
1
건강 플랫폼 우대 : 연 1.0%
만기 전전영업일까지 신한 20+ 뛰어요 또는 신한 50+ 걸어요 서비스 가입을 완료한 경우
2
첫 적금 우대 : 연 1.0%
신규 직전 6개월간 정기예금, 정기적금을 보유하지 않은 경우
3
카드 우대 : 다음 각 요건을 충족하는 경우 최고 연 3.0% (중복 적용 불가)
① 결제 실적이 3개월 이상인 경우 연 1.0%
② 신용카드를 신규하고 결제 실적이 3개월 이상인 경우 연 3.0%
유형 고정금리
"""


def test_parser_excludes_summary_and_marks_exclusive_card_options_for_review() -> None:
    rows = parse(SHINHAN_RUNNING_SHOE_TEXT)

    assert [(row.value, row.confidence) for row in rows] == [
        ("1", "AUTO_READY"),
        ("1", "AUTO_READY"),
        ("1", "REVIEW_REQUIRED"),
        ("3", "REVIEW_REQUIRED"),
    ]
    assert rows[0].condition_type == "BANK_APP_USAGE"
    assert rows[1].condition_type == "FIRST_TRANSACTION"
    assert all("exclusive_capped_or_choice_semantics" in row.review_reasons for row in rows[2:])


def test_detached_patch_promotes_only_safe_rows_and_preserves_pending_provenance() -> None:
    product = {
        "product_code": "TEST-1",
        "version": 1,
        "product_family": "INSTALLMENT_SAVINGS",
        "institution_id": "INST-1",
        "source_ref_ids": ["EVD-NAVER-1"],
        "return_policy": {"rate_entries": []},
        "standard_conditions": [],
        "custom_bindings": [],
        "version_metadata": {"data_gaps": []},
    }

    patched = detached_patch(product, parse(SHINHAN_RUNNING_SHOE_TEXT))

    preferential = [row for row in patched["return_policy"]["rate_entries"] if row["role"] == "PREFERENTIAL"]
    assert [row["calculation"]["value"] for row in preferential] == ["1", "1"]
    assert [row["title"] for row in patched["standard_conditions"]] == ["건강 플랫폼 우대", "첫 적금 우대"]
    assert all(row["source_ref_ids"] == ["EVD-NAVER-1"] for row in preferential)
    metadata = patched["version_metadata"]["naver_preferential_structure_candidate"]
    assert metadata["status"] == "REVIEW_REQUIRED"
    assert metadata["auto_promoted_count"] == 2
    assert len(metadata["review_candidates"]) == 2
    disclosures = patched["return_policy"]["preferential_condition_disclosures"]
    assert len(disclosures) == 4
    assert [row["evaluator_eligible"] for row in disclosures] == [True, True, False, False]
    assert disclosures[-1]["verification_status"] == "PENDING_OFFICIAL_VERIFICATION"
    assert disclosures[-1]["relationship_hints"] == ["EXCLUSIVE_OR_CAPPED_GROUP"]
    assert any(row["path"] == "standard_conditions/custom_bindings" for row in patched["version_metadata"]["data_gaps"])


def test_published_running_shoe_product_exposes_all_conditions_safely() -> None:
    product = next(
        item
        for item in load_normalized_product_catalog()
        if item.product_id == "INST-KR-000219-1-C92882B36F8"
    )

    assert product.normalized is not None
    assert product.normalized.version >= 3
    disclosures = product.normalized.return_policy["preferential_condition_disclosures"]
    assert len(disclosures) == 4
    assert [item["evaluator_eligible"] for item in disclosures] == [True, True, False, False]
    if product.normalized.version >= 4:
        assert disclosures[0]["presentation"]["summary_text"].startswith("만기 전전영업일까지")
        assert disclosures[2]["presentation"]["relation"]["operator"] == "EXCLUSIVE"
    assert len(product.preferential_rules) == 2
