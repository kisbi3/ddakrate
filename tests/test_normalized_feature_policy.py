from eligibility.catalog.normalized_loader import load_normalized_product_catalog
from eligibility.search.feature_policy import FeaturePolicy, apply_feature_policy


def test_cumulative_lottery_projects_to_typed_feature_and_excludes_woori():
    products = load_normalized_product_catalog()
    lottery = [p for p in products if p.normalized and p.normalized.return_policy.get("preferential_application", {}).get("mode") == "CUMULATIVE_LOTTERY"]
    assert lottery
    assert any(p.institution_id == "INST-KR-000424" for p in lottery)
    assert all({f.feature_id for f in p.metadata.features} >= {"LOTTERY_BASED_BENEFIT"} for p in lottery)
    filtered = apply_feature_policy(products, "LOTTERY_BASED_BENEFIT", FeaturePolicy.EXCLUDE)
    assert not any(p in lottery for p in filtered)


def test_variable_rate_and_similar_name_do_not_trigger_lottery_feature():
    products = load_normalized_product_catalog()
    ordinary = [
        p
        for p in products
        if p.normalized
        and p.normalized.return_policy.get("variable_rate")
        and (p.normalized.return_policy.get("preferential_application") or {}).get("mode")
        != "CUMULATIVE_LOTTERY"
    ]
    assert ordinary
    assert all(not any(f.feature_id == "LOTTERY_BASED_BENEFIT" for f in p.metadata.features) for p in ordinary)
