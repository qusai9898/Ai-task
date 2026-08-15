"""Tests for quote generation and review handling."""

from decimal import Decimal
from pathlib import Path

from app.catalog import RecipeCatalog
from app.quote_generator import QuoteGenerator
from app.quote_models import QuoteLineStatus, QuoteStatus
from app.models import ReviewReason, ReviewSeverity
from tests.fixtures import build_full_nexus_brief_extraction, build_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def _generator() -> QuoteGenerator:
    return QuoteGenerator(RecipeCatalog.load(CATALOG_CSV))


class TestQuoteGenerator:
    def test_nexus_quote_blocked_by_critical_flag(self):
        quote = _generator().generate(build_nexus_brief_extraction())
        assert quote.status == QuoteStatus.BLOCKED
        critical = [
            f for f in quote.review_flags
            if f.reason == ReviewReason.HIDDEN_INSTRUCTION_DETECTED
        ]
        assert critical
        assert critical[0].severity == ReviewSeverity.CRITICAL

    def test_no_golden_falcon_line_item(self):
        quote = _generator().generate(build_nexus_brief_extraction())
        descriptions = [line.description.lower() for line in quote.lines]
        assert not any("golden falcon" in d for d in descriptions)

    def test_led_line_requires_review(self):
        quote = _generator().generate(build_nexus_brief_extraction())
        led_lines = [
            line
            for line in quote.lines
            if line.item_id == "item-main-led-screen"
        ]
        assert led_lines
        assert led_lines[0].status == QuoteLineStatus.REQUIRES_REVIEW

    def test_uplighters_have_min_max_totals(self):
        quote = _generator().generate(build_nexus_brief_extraction())
        upl = next(line for line in quote.lines if line.item_id == "item-uplighters")
        assert upl.min_line_total_sar is not None
        assert upl.max_line_total_sar is not None

    def test_hologram_custom_estimate_from_procurement(self):
        quote = _generator().generate(build_nexus_brief_extraction())
        holo = next(line for line in quote.lines if line.item_id == "item-hologram-box")
        assert holo.status == QuoteLineStatus.CUSTOM_ESTIMATE
        assert holo.line_cost_sar == Decimal("14000")

    def test_full_nexus_quote_has_priced_catalog_lines(self):
        quote = _generator().generate(build_full_nexus_brief_extraction())
        priced = [line for line in quote.lines if line.status == QuoteLineStatus.PRICED]
        assert len(priced) >= 5

    def test_full_nexus_quote_requires_review(self):
        quote = _generator().generate(build_full_nexus_brief_extraction())
        assert quote.status in (
            QuoteStatus.REQUIRES_REVIEW,
            QuoteStatus.BLOCKED,
        )
        assert quote.min_subtotal_sar is not None
        assert quote.max_subtotal_sar is not None
        assert quote.max_subtotal_sar >= quote.min_subtotal_sar

    def test_contradiction_surfaces_in_review_flags(self):
        quote = _generator().generate(build_nexus_brief_extraction())
        contradiction_flags = [
            f for f in quote.review_flags
            if f.reason == ReviewReason.CONTRADICTORY_INSTRUCTIONS
        ]
        assert contradiction_flags
