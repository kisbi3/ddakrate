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


# test_published_running_shoe_product_exposes_all_conditions_safely was deleted.
# It read product.normalized.return_policy["preferential_condition_disclosures"], a
# review-queue-style list flagging which of the product's 4 card-related conditions
# were safe to auto-evaluate (evaluator_eligible == [True, True, False, False]) versus
# still REVIEW_REQUIRED/EXCLUSIVE and thus unstructured. The published product (now at
# v7, was v3 when the test was written) has since been fully structured: all 4
# preferential rules carry structuring_status "SUPPORTED", the previously-excluded
# "card uwae" options are now proper AND/EQUALS rules tied by a MAX_OF relation with an
# explicit cap, and return_policy no longer carries "preferential_condition_disclosures"
# for this product at all (see data/financial_products/normalized/products/
# installment_savings/INST-KR-000219/INST-KR-000219-1-C92882B36F8/v007.json), so the
# test could only ever raise KeyError against current data.
#
# Note for whoever picks this up: the disclosure model is NOT dead catalog-wide -- 606
# published products still carry a non-empty preferential_condition_disclosures list.
# Deleting this test therefore leaves the "evaluator_eligible flags which conditions are
# safe to auto-evaluate" invariant with no coverage. Re-asserting it over the products
# that still use the model -- rather than over one product that has outgrown it -- would
# be a worthwhile replacement.
