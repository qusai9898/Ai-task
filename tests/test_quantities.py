"""Tests for deterministic quantity calculation."""

from decimal import Decimal
from pathlib import Path

from app.catalog import RecipeCatalog
from app.matcher import CatalogMatcher
from app.quantities import QuantityCalculator
from tests.fixtures import build_full_nexus_brief_extraction, build_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def _quantities(extraction):
    catalog = RecipeCatalog.load(CATALOG_CSV)
    matches = CatalogMatcher(catalog).match_extraction(extraction)
    return QuantityCalculator(catalog).calculate_all(extraction, matches)


class TestQuantityCalculator:
    def test_led_screen_blocked_by_contradiction(self):
        extraction = build_nexus_brief_extraction()
        quantities = _quantities(extraction)
        led = next(q for q in quantities if q.item_id == "item_main_led_screen")
        assert led.requires_review
        assert led.calculated_quantity is None

    def test_uplighters_preserve_range(self):
        extraction = build_nexus_brief_extraction()
        quantities = _quantities(extraction)
        upl = next(q for q in quantities if q.item_id == "item_uplighters")
        assert upl.min_quantity == Decimal("8")
        assert upl.max_quantity == Decimal("10")
        assert upl.requires_review

    def test_backdrop_sqm_from_dimensions(self):
        extraction = build_full_nexus_brief_extraction()
        quantities = _quantities(extraction)
        backdrop = next(q for q in quantities if q.item_id == "item-backdrop")
        assert backdrop.calculated_quantity == Decimal("15")

    def test_floor_vinyl_sqm(self):
        extraction = build_full_nexus_brief_extraction()
        quantities = _quantities(extraction)
        floor = next(q for q in quantities if q.item_id == "item-floor-vinyl")
        assert floor.calculated_quantity == Decimal("10")

    def test_round_tables_from_guest_count(self):
        extraction = build_full_nexus_brief_extraction()
        quantities = _quantities(extraction)
        tables = next(q for q in quantities if q.item_id == "item-round-tables")
        assert tables.calculated_quantity == Decimal("45")

    def test_chairs_from_guest_count(self):
        extraction = build_full_nexus_brief_extraction()
        quantities = _quantities(extraction)
        # item-chairs can produce multiple quantity results (one per matched recipe).
        # We want the one for banquet chairs (FRN-CHR-BQT).
        chairs = next(
            q for q in quantities
            if q.item_id == "item-chairs" and q.recipe_code == "FRN-CHR-BQT"
        )
        assert chairs.calculated_quantity == Decimal("450")
