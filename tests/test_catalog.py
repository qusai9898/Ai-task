"""Tests for recipe catalog loading and cost calculations."""

from decimal import Decimal
from pathlib import Path

from app.catalog import RecipeCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def test_catalog_loads_all_recipes():
    catalog = RecipeCatalog.load(CATALOG_CSV)
    assert len(catalog.all()) == 28
    assert "LED-P39-IN" in catalog.codes()


def test_recipe_unit_cost_calculation():
    catalog = RecipeCatalog.load(CATALOG_CSV)
    recipe = catalog.get("LED-P39-IN")
    assert recipe is not None

    labour = recipe.labour_hours_per_unit * recipe.labour_rate_sar_per_hr
    expected_cost = recipe.material_cost_sar + labour + recipe.equipment_cost_sar
    assert recipe.unit_cost_sar == expected_cost


def test_recipe_unit_price_uses_margin():
    catalog = RecipeCatalog.load(CATALOG_CSV)
    recipe = catalog.get("STG-RAMP-STD")
    assert recipe is not None

    margin_fraction = recipe.standard_margin_pct / Decimal("100")
    expected_price = recipe.unit_cost_sar / (Decimal("1") - margin_fraction)
    assert recipe.unit_price_sar == expected_price
    assert recipe.unit_price_sar > recipe.unit_cost_sar
