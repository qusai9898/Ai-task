"""Tests for deterministic human resolution logic."""

from decimal import Decimal
from pathlib import Path

from app.quote_generator import QuoteGenerator
from app.quote_models import QuoteLineStatus, QuoteStatus
from app.resolution import (
    LED_SCREEN_ITEM_ID,
    LedScreenResolutionChoice,
    ResolutionSet,
    build_led_resolution,
    led_contradiction_pending,
)
from app.models import ReviewReason
from tests.fixtures import build_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def _generator() -> QuoteGenerator:
    return QuoteGenerator.from_catalog_path(CATALOG_CSV)


class TestBuildLedResolution:
    def test_original_6x3(self):
        res = build_led_resolution(LedScreenResolutionChoice.ORIGINAL_6X3)
        assert res.width_m == Decimal("6")
        assert res.height_m == Decimal("3")
        assert not res.excluded

    def test_ratio_8x4(self):
        res = build_led_resolution(LedScreenResolutionChoice.RATIO_8X4)
        assert res.width_m == Decimal("8")
        assert res.height_m == Decimal("4")

    def test_exclude(self):
        res = build_led_resolution(LedScreenResolutionChoice.EXCLUDE)
        assert res.excluded

    def test_custom_dimensions(self):
        res = build_led_resolution(
            LedScreenResolutionChoice.CUSTOM,
            custom_width_m=Decimal("10"),
            custom_height_m=Decimal("5"),
        )
        assert res.width_m == Decimal("10")
        assert res.height_m == Decimal("5")


class TestLedResolutionQuoteRecalculation:
    def test_original_6x3_prices_led_at_18_sqm(self):
        extraction = build_nexus_brief_extraction()
        generator = _generator()
        before = generator.generate(extraction)
        led_before = next(
            line for line in before.lines if line.item_id == LED_SCREEN_ITEM_ID
        )
        assert led_before.status == QuoteLineStatus.REQUIRES_REVIEW

        resolution = build_led_resolution(LedScreenResolutionChoice.ORIGINAL_6X3)
        after = generator.generate(
            extraction,
            resolutions=ResolutionSet(resolutions=[resolution]),
        )
        led_after = next(
            line for line in after.lines if line.item_id == LED_SCREEN_ITEM_ID
        )
        assert led_after.status == QuoteLineStatus.PRICED
        assert led_after.quantity == Decimal("18")
        assert led_after.line_total_sar is not None
        assert led_after.line_total_sar > Decimal("0")

    def test_ratio_8x4_prices_led_at_32_sqm(self):
        extraction = build_nexus_brief_extraction()
        resolution = build_led_resolution(LedScreenResolutionChoice.RATIO_8X4)
        quote = _generator().generate(
            extraction,
            resolutions=ResolutionSet(resolutions=[resolution]),
        )
        led = next(line for line in quote.lines if line.item_id == LED_SCREEN_ITEM_ID)
        assert led.quantity == Decimal("32")
        assert led.line_total_sar is not None

    def test_exclude_removes_led_pricing(self):
        extraction = build_nexus_brief_extraction()
        resolution = build_led_resolution(LedScreenResolutionChoice.EXCLUDE)
        quote = _generator().generate(
            extraction,
            resolutions=ResolutionSet(resolutions=[resolution]),
        )
        led_lines = [
            line
            for line in quote.lines
            if line.item_id == LED_SCREEN_ITEM_ID
            and line.status != QuoteLineStatus.EXCLUDED
        ]
        assert not led_lines
        excluded = next(
            line
            for line in quote.lines
            if line.item_id == LED_SCREEN_ITEM_ID
            and line.status == QuoteLineStatus.EXCLUDED
        )
        assert excluded.quantity == Decimal("0")

    def test_resolution_clears_led_contradiction_flags(self):
        extraction = build_nexus_brief_extraction()
        resolution = build_led_resolution(LedScreenResolutionChoice.ORIGINAL_6X3)
        quote = _generator().generate(
            extraction,
            resolutions=ResolutionSet(resolutions=[resolution]),
        )
        led_flags = [
            f
            for f in quote.review_flags
            if f.reason == ReviewReason.CONTRADICTORY_INSTRUCTIONS
            and LED_SCREEN_ITEM_ID in f.related_item_ids
        ]
        assert not led_flags

    def test_critical_hidden_flag_remains_after_led_resolution(self):
        extraction = build_nexus_brief_extraction()
        resolution = build_led_resolution(LedScreenResolutionChoice.RATIO_8X4)
        quote = _generator().generate(
            extraction,
            resolutions=ResolutionSet(resolutions=[resolution]),
        )
        assert quote.status == QuoteStatus.BLOCKED
        assert any(
            f.reason == ReviewReason.HIDDEN_INSTRUCTION_DETECTED
            for f in quote.review_flags
        )

    def test_no_golden_falcon_line_after_resolution(self):
        extraction = build_nexus_brief_extraction()
        resolution = build_led_resolution(LedScreenResolutionChoice.ORIGINAL_6X3)
        quote = _generator().generate(
            extraction,
            resolutions=ResolutionSet(resolutions=[resolution]),
        )
        assert not any(
            "golden falcon" in line.description.lower() for line in quote.lines
        )

    def test_led_contradiction_pending_helper(self):
        extraction = build_nexus_brief_extraction()
        assert led_contradiction_pending(extraction)

    def test_priced_line_has_cost_breakdown(self):
        extraction = build_nexus_brief_extraction()
        resolution = build_led_resolution(LedScreenResolutionChoice.ORIGINAL_6X3)
        quote = _generator().generate(
            extraction,
            resolutions=ResolutionSet(resolutions=[resolution]),
        )
        led = next(line for line in quote.lines if line.item_id == LED_SCREEN_ITEM_ID)
        assert led.material_cost_sar is not None
        assert led.labour_cost_sar is not None
        assert led.equipment_cost_sar is not None
        assert led.margin_amount_sar is not None
