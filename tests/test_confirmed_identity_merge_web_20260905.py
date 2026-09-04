import pytest

pytest.importorskip("fastapi")

from eligibility.application_service import ApplicationService
from eligibility.catalog.normalized_loader import load_normalized_product_catalog, load_product_aliases
from eligibility.web.app import create_app
from starlette.testclient import TestClient


def test_web_catalog_detail_accepts_historical_alias():
    alias, canonical = next(iter(load_product_aliases().items()))
    product = next(item for item in load_normalized_product_catalog() if item.product_id == canonical)
    service = ApplicationService([product], product_aliases={alias: canonical})
    response = TestClient(create_app(service=service)).get(f"/api/catalog/products/{alias}")
    assert response.status_code == 200
    assert response.json()["product_id"] == canonical
