from __future__ import annotations

import os
import json
from pathlib import Path

from eligibility.schema.product import ProductDefinition
from eligibility.catalog.normalized_loader import load_normalized_product_catalog


CATALOG_PATH_ENV = "ELIGIBILITY_PRODUCT_CATALOG_PATH"
CATALOG_MODE_ENV = "ELIGIBILITY_CATALOG_MODE"


def load_product_definition(path: str | Path) -> ProductDefinition:
    p = Path(path)
    return ProductDefinition.model_validate_json(p.read_text(encoding="utf-8"))


def load_product_catalog(root: str | Path) -> list[ProductDefinition]:
    root = Path(root)
    products_dir = root / "products" if (root / "products").is_dir() else root
    paths = sorted(products_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No ProductDefinition JSON files found under {products_dir}")
    return [load_product_definition(p) for p in paths]


def default_product_catalog_root() -> Path:
    """Resolve the catalog shipped with the source tree or installed package.

    The source-tree copy remains the human-reviewable canonical catalog.  A
    package-data mirror is included so the Web runtime also works after a normal
    wheel/install step.  Deployments may override either location with
    ELIGIBILITY_PRODUCT_CATALOG_PATH.
    """

    configured = os.environ.get(CATALOG_PATH_ENV)
    if configured:
        root = Path(configured).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(
                f"{CATALOG_PATH_ENV} points to a missing path: {root}"
            )
        return root

    source_tree_root = Path(__file__).resolve().parents[3] / "data" / "product_catalog"
    if source_tree_root.is_dir():
        return source_tree_root

    packaged_root = Path(__file__).resolve().parent / "data" / "product_catalog"
    if packaged_root.is_dir():
        return packaged_root

    raise FileNotFoundError(
        "Bundled product catalog not found. Set "
        f"{CATALOG_PATH_ENV} to a product catalog directory."
    )


def load_default_product_catalog() -> list[ProductDefinition]:
    configured = os.environ.get(CATALOG_PATH_ENV)
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"{CATALOG_PATH_ENV} points to a missing path: {path}")
        index_path = path / "index.json" if path.is_dir() else path
        if index_path.is_file():
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid catalog JSON: {index_path}") from exc
            if isinstance(payload, dict) and isinstance(payload.get("products"), list):
                return load_normalized_product_catalog(index_path)
            if path.is_file():
                raise ValueError(
                    f"{CATALOG_PATH_ENV} file is not a normalized published index: {path}"
                )
        # Historical directory overrides remain explicit legacy mode. Strict
        # ProductDefinition validation prevents a normalized schema directory
        # from being silently interpreted as the old flat catalog.
        return load_product_catalog(path)

    mode = os.environ.get(CATALOG_MODE_ENV, "NORMALIZED").strip().upper()
    if mode == "NORMALIZED":
        return load_normalized_product_catalog()
    if mode == "LEGACY":
        return load_product_catalog(default_product_catalog_root())
    raise ValueError(
        f"Unsupported {CATALOG_MODE_ENV}={mode!r}; expected NORMALIZED or LEGACY"
    )
