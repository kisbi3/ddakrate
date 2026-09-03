from eligibility.conversation import ConversationOrchestrator
from eligibility.search.institution_names import resolve_institution_references


CATALOG = [
    {"institution_id": "SH_BANK", "institution_name": "SH수협은행"},
    {"institution_id": "SHINHAN_BANK", "institution_name": "(주)신한은행"},
    {"institution_id": "SHINHAN_SAVINGS", "institution_name": "신한저축은행"},
    {"institution_id": "KOREA_INVEST_SAVINGS", "institution_name": "한국투자저축은행"},
    {"institution_id": "KOREA_INVEST_SECURITIES", "institution_name": "한국투자증권"},
    {"institution_id": "POST", "institution_name": "우정사업본부"},
]


def test_short_brand_name_resolves_from_catalog_generated_alias():
    result = resolve_institution_references("수협 싫어", CATALOG)

    assert [item["institution_id"] for item in result.resolved] == ["SH_BANK"]
    assert result.ambiguous == ()


def test_exact_full_name_wins_over_ambiguous_shorter_alias():
    result = resolve_institution_references("신한은행 상품은 빼줘", CATALOG)

    assert [item["institution_id"] for item in result.resolved] == ["SHINHAN_BANK"]
    assert result.ambiguous == ()


def test_ambiguous_short_name_is_not_resolved_automatically():
    result = resolve_institution_references("신한 상품은 싫어", CATALOG)

    assert result.resolved == ()
    assert len(result.ambiguous) == 1
    assert {
        item["institution_id"] for item in result.ambiguous[0]["candidates"]
    } == {"SHINHAN_BANK", "SHINHAN_SAVINGS"}


def test_data_backed_exception_alias_resolves_post_office():
    result = resolve_institution_references("우체국 상품은 제외해줘", CATALOG)

    assert [item["institution_id"] for item in result.resolved] == ["POST"]


def test_projected_context_exposes_resolved_id_and_related_products_only():
    projected = ConversationOrchestrator._project_context(
        "수협 싫어",
        {
            "INSTITUTION_CATALOG_SUMMARY": CATALOG,
            "PRODUCT_CATALOG_SUMMARY": [
                "P-SH|SH_BANK|Sh매일받는통장",
                "P-OTHER|SHINHAN_BANK|신한 적금",
            ],
            "ALLOWED_OPERATIONS": {
                "institution_ids": ["SH_BANK", "SHINHAN_BANK", "POST"]
            },
            "MUTABLE_SEARCH_STATE": {
                "excluded_institution_ids": ["POST"]
            },
            "_PRODUCT_EVIDENCE_INDEX": {},
        },
    )

    assert projected["INSTITUTION_CATALOG_SUMMARY"][0]["institution_id"] == "SH_BANK"
    assert projected["PRODUCT_CATALOG_SUMMARY"] == [
        "P-SH|SH_BANK|Sh매일받는통장"
    ]
    assert projected["AMBIGUOUS_INSTITUTION_REFERENCES"] == []
    assert projected["ALLOWED_OPERATIONS"]["institution_ids"] == ["POST", "SH_BANK"]
