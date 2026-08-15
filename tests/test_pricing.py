"""Tests for deterministic pricing engine."""

from decimal import Decimal
from pathlib import Path

from app.catalog import RecipeCatalog
from app.pricing import PricingEngine
from app.quote_models import QuoteLineStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def test_price_single_quantity_line():
    catalog = RecipeCatalog.load(CATALOG_CSV)
    engine = PricingEngine(catalog)
    from app.quote_models import QuantityResult

    qty = QuantityResult(
        item_id="item-floor-vinyl",
        recipe_code="PRN-FLR-VNL",
        catalog_unit="sqm",
        calculated_quantity=Decimal("9"),
        calculation_notes="test",
    )
    line = engine.price_quantity(qty, "Floor vinyl")
    assert line.status == QuoteLineStatus.PRICED
    assert line.line_total_sar is not None
    assert line.line_total_sar > line.line_cost_sar
    assert line.margin_amount_sar == line.line_total_sar - line.line_cost_sar


def test_price_range_quantity_line():
    catalog = RecipeCatalog.load(CATALOG_CSV)
    engine = PricingEngine(catalog)
    from app.quote_models import QuantityResult
    from app.models import ReviewReason

    qty = QuantityResult(
        item_id="item-uplighters",
        recipe_code="LGT-UPL-BAT",
        catalog_unit="unit",
        min_quantity=Decimal("8"),
        max_quantity=Decimal("10"),
        requires_review=True,
        review_reason=ReviewReason.AMBIGUOUS_QUANTITY,
    )
    line = engine.price_quantity(qty, "Uplighters")
    assert line.status == QuoteLineStatus.REQUIRES_REVIEW
    assert line.min_line_total_sar is not None
    assert line.max_line_total_sar is not None
    assert line.min_line_total_sar < line.max_line_total_sar


def test_custom_line_from_procurement_cost():
    catalog = RecipeCatalog.load(CATALOG_CSV)
    engine = PricingEngine(catalog)
    line = engine.price_custom_line(
        item_id="item-hologram-box",
        description="Hologram box",
        unit="unit",
        unit_cost_sar=Decimal("14000"),
        quantity=Decimal("1"),
        margin_pct=Decimal("30"),
    )
    assert line.status == QuoteLineStatus.CUSTOM_ESTIMATE
    assert line.line_total_sar > Decimal("14000")
