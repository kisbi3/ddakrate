from eligibility.search.feature_policy import FeaturePolicy, apply_feature_policy


EXPECTED_LOTTERY_PRODUCT_CODES = {
    "INST-KR-000204-1-CD29BFA9586",  # JB 슈퍼씨드 적금 네이버 페이
    "INST-KR-000424-1-C3C58A5BBCE",  # 우리 두근두근 행운적금
    "INST-KR-000796-1-CE415363FDC",  # 눈치게임적금
    "INST-KR-000865-1-0002",  # 궁금한 적금
    "INST-KR-000448-3-0001",  # KDB Hi 비대면 입출금통장
}


def _lottery_product_codes(products):
    return {
        product.product_id
        for product in products
        if product.metadata
        and any(
            feature.feature_id == "LOTTERY_BASED_BENEFIT" and feature.present
            for feature in product.metadata.features
        )
    }


def test_structured_lottery_benefits_project_to_typed_feature_and_are_excluded(
    normalized_catalog_session,
):
    products = normalized_catalog_session
    assert _lottery_product_codes(products) == EXPECTED_LOTTERY_PRODUCT_CODES

    filtered = apply_feature_policy(products, "LOTTERY_BASED_BENEFIT", FeaturePolicy.EXCLUDE)
    assert {product.product_id for product in filtered} == {
        product.product_id
        for product in products
        if product.product_id not in EXPECTED_LOTTERY_PRODUCT_CODES
    }


def test_variable_rate_and_similar_name_do_not_trigger_lottery_feature(normalized_catalog_session):
    products = normalized_catalog_session
    variable_rate_products = [
        p
        for p in products
        if p.normalized
        and p.normalized.return_policy.get("rate_type") == "VARIABLE"
    ]
    assert len(variable_rate_products) == 45
    assert all(
        "LOTTERY_BASED_BENEFIT" not in {
            feature.feature_id
            for feature in product.metadata.features
            if feature.present
        }
        for product in variable_rate_products
    )

    name_only_matches = [product for product in products if "행운" in product.name]
    assert name_only_matches
    assert all(
        product.product_id in EXPECTED_LOTTERY_PRODUCT_CODES
        or "LOTTERY_BASED_BENEFIT"
        not in {
            feature.feature_id
            for feature in product.metadata.features
            if feature.present
        }
        for product in name_only_matches
    )
