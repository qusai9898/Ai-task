"""Comprehensive unit tests for assessment requirements and quoting core logic."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog import RecipeCatalog
from app.matcher import CatalogMatcher
from app.models import (
    ConfidenceLevel,
    DimensionKind,
    DimensionObservation,
    DimensionValue,
    ItemDescriptionObservation,
    ReviewReason,
    SourceReference,
    UnitOfMeasure,
)
from app.quote_generator import QuoteGenerator
from app.quote_models import MatchStatus, QuoteLineKind, QuoteLineStatus, QuoteStatus
from app.resolution import (
    HologramResolutionChoice,
    LedScreenResolutionChoice,
    ResolutionSet,
    StageResolutionChoice,
    UplightersResolutionChoice,
    build_hologram_resolution,
    build_led_resolution,
    build_stage_resolution,
    build_uplighters_resolution,
)
from tests.fixtures import build_full_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


@pytest.fixture
def catalog():
    return RecipeCatalog.load(CATALOG_CSV)


@pytest.fixture
def generator():
    return QuoteGenerator.from_catalog_path(CATALOG_CSV)


class TestAssessmentRequirements:
    def test_1_separate_catalog_matching_from_quantity_resolution(self, catalog):
        """Main stage & LED match catalog recipes even when dimensions/quantities are incomplete."""
        extraction = build_full_nexus_brief_extraction()
        matcher = CatalogMatcher(catalog)
        matches = matcher.match_extraction(extraction)

        # Stage matched
        stage_match = next(m for m in matches if m.item_id == "item_main_stage")
        assert stage_match.match_status == MatchStatus.MATCHED
        assert stage_match.recipe_code == "STG-DECK-1x1"

        # LED matched
        led_match = next(m for m in matches if m.item_id == "item_main_led_screen")
        assert led_match.match_status == MatchStatus.MATCHED
        assert led_match.recipe_code == "LED-P39-IN"

        # Hologram unmatched (not in catalog)
        holo_match = next(m for m in matches if m.item_id == "item_hologram_box")
        assert holo_match.match_status in (MatchStatus.NO_MATCH, MatchStatus.UNMATCHED)

    def test_2_floor_vinyl_extraction_and_moq(self, catalog, generator):
        """Floor vinyl 3m x 3m = 9 sqm requested, 10 sqm billable MOQ."""
        extraction = build_full_nexus_brief_extraction()
        quote = generator.generate(extraction)

        floor_line = next(line for line in quote.lines if line.item_id == "item-floor-vinyl")
        assert floor_line.recipe_code == "PRN-FLR-VNL"
        assert floor_line.requested_quantity == Decimal("9")
        assert floor_line.quantity == Decimal("10")  # MOQ is 10
        assert floor_line.status == QuoteLineStatus.PRICED
        assert "minimum order" in (floor_line.notes or "").lower()

    def test_3_main_stage_matched_but_unpriced_without_depth(self, catalog, generator):
        """Stage 12m width stated but depth unstated: matched to STG-DECK-1x1, requires review."""
        extraction = build_full_nexus_brief_extraction()
        quote = generator.generate(extraction)

        stage_line = next(line for line in quote.lines if line.item_id == "item_main_stage")
        assert stage_line.recipe_code == "STG-DECK-1x1"
        assert stage_line.status == QuoteLineStatus.REQUIRES_REVIEW
        assert stage_line.quantity is None
        assert ReviewReason.MISSING_DIMENSION in stage_line.review_reasons
        assert "depth" in (stage_line.notes or "").lower()

    def test_4_main_led_screen_requires_review_until_resolved(self, generator):
        """LED screen (6x3 vs >=8m) requires review until human decision is made."""
        extraction = build_full_nexus_brief_extraction()
        quote_unresolved = generator.generate(extraction)
        led_unresolved = next(line for line in quote_unresolved.lines if line.item_id == "item_main_led_screen")
        assert led_unresolved.status == QuoteLineStatus.REQUIRES_REVIEW

        # Resolve using 8m x 4m (2:1 ratio)
        res_8x4 = build_led_resolution(LedScreenResolutionChoice.RATIO_8X4)
        quote_resolved = generator.generate(extraction, resolutions=ResolutionSet(resolutions=[res_8x4]))
        led_resolved = next(line for line in quote_resolved.lines if line.item_id == "item_main_led_screen")
        assert led_resolved.status == QuoteLineStatus.PRICED
        assert led_resolved.quantity == Decimal("32")
        assert led_resolved.line_total_sar is not None

        # Resolve using 6m x 3m
        res_6x3 = build_led_resolution(LedScreenResolutionChoice.ORIGINAL_6X3)
        quote_6x3 = generator.generate(extraction, resolutions=ResolutionSet(resolutions=[res_6x3]))
        led_6x3 = next(line for line in quote_6x3.lines if line.item_id == "item_main_led_screen")
        assert led_6x3.status == QuoteLineStatus.PRICED
        assert led_6x3.quantity == Decimal("18")

    def test_5_uplighters_ambiguity_and_moq(self, generator):
        """Uplighters 8 or 10 preserved as range; resolving to 8 applies 10 MOQ."""
        extraction = build_full_nexus_brief_extraction()
        quote = generator.generate(extraction)
        upl = next(line for line in quote.lines if line.item_id == "item_uplighters")
        assert upl.min_quantity == Decimal("8")
        assert upl.max_quantity == Decimal("10")
        assert upl.status == QuoteLineStatus.REQUIRES_REVIEW

        # Human resolves to 8 -> requested=8, billable=10 MOQ
        res_8 = build_uplighters_resolution(UplightersResolutionChoice.QTY_8)
        quote_8 = generator.generate(extraction, resolutions=ResolutionSet(resolutions=[res_8]))
        upl_8 = next(line for line in quote_8.lines if line.item_id == "item_uplighters")
        assert upl_8.status == QuoteLineStatus.PRICED
        assert upl_8.requested_quantity == Decimal("8")
        assert upl_8.quantity == Decimal("10")
        assert "minimum order" in (upl_8.notes or "").lower()

    def test_6_hologram_no_synthetic_margin_until_confirmed(self, generator):
        """Hologram box is CUSTOM / NOT IN CATALOG with procurement reference SAR 14,000."""
        extraction = build_full_nexus_brief_extraction()
        quote = generator.generate(extraction)
        holo = next(line for line in quote.lines if line.item_id == "item_hologram_box")
        assert holo.line_kind == QuoteLineKind.CUSTOM_NOT_IN_CATALOG
        assert holo.status == QuoteLineStatus.CUSTOM_ESTIMATE
        assert holo.material_cost_sar == Decimal("14000")
        assert holo.unit_price_sar is None
        assert holo.margin_pct is None

        # Human confirms pass-through pricing (0% margin)
        res_pass = build_hologram_resolution(HologramResolutionChoice.PASS_THROUGH)
        quote_pass = generator.generate(extraction, resolutions=ResolutionSet(resolutions=[res_pass]))
        holo_pass = next(line for line in quote_pass.lines if line.item_id == "item_hologram_box")
        assert holo_pass.unit_price_sar == Decimal("14000")
        assert holo_pass.margin_pct == Decimal("0")

        # Human confirms 30% margin
        res_margin = build_hologram_resolution(HologramResolutionChoice.MARGIN_30)
        quote_margin = generator.generate(extraction, resolutions=ResolutionSet(resolutions=[res_margin]))
        holo_margin = next(line for line in quote_margin.lines if line.item_id == "item_hologram_box")
        assert holo_margin.margin_pct == Decimal("30")
        assert holo_margin.unit_price_sar == Decimal("14000") / Decimal("0.70")

    def test_7_cancelled_items_and_adversarial_instructions_excluded(self, generator):
        """Breakout second stage is CANCELLED; Golden Falcon is NEVER a quote line."""
        extraction = build_full_nexus_brief_extraction()
        quote = generator.generate(extraction)

        descriptions = [line.description.lower() for line in quote.lines]
        assert not any("breakout stage" in d for d in descriptions)
        assert not any("second stage" in d for d in descriptions)
        assert not any("golden falcon" in d for d in descriptions)

        # Critical security flag is preserved
        assert any(
            f.reason == ReviewReason.HIDDEN_INSTRUCTION_DETECTED
            for f in quote.review_flags
        )

    def test_8_quote_status_is_requires_review_with_calculable_lines(self, generator):
        """Quote calculates resolvable lines with status REQUIRES_REVIEW when flags exist."""
        extraction = build_full_nexus_brief_extraction()
        quote = generator.generate(extraction)
        assert quote.status == QuoteStatus.REQUIRES_REVIEW
        assert quote.min_subtotal_sar is not None
        assert quote.max_subtotal_sar is not None
        assert quote.min_subtotal_sar > Decimal("0")

    def test_9_stage_resolution_recalculates_pricing(self, generator):
        """Resolving stage dimensions to 12m x 4m calculates 48 sqm deterministically."""
        extraction = build_full_nexus_brief_extraction()
        res_stage = build_stage_resolution(StageResolutionChoice.DIM_12X4)
        quote = generator.generate(extraction, resolutions=ResolutionSet(resolutions=[res_stage]))

        stage_line = next(line for line in quote.lines if line.item_id == "item_main_stage")
        assert stage_line.status == QuoteLineStatus.PRICED
        assert stage_line.quantity == Decimal("48")
        assert stage_line.line_total_sar is not None
        assert stage_line.line_total_sar == stage_line.unit_price_sar * Decimal("48")

    def test_bug_1_guest_count_never_creates_quote_line(self, generator):
        """Regression test for Bug 1: Guest count is informational and must NEVER be priced."""
        extraction = build_full_nexus_brief_extraction()
        quote = generator.generate(extraction)

        # Ensure no line is named Guest count or priced with headcount
        for line in quote.lines:
            desc = line.description.lower()
            assert "guest count" not in desc
            assert "headcount" not in desc
            # Ensure no line has the 6,300,000 incorrect total (450 * 14000)
            if line.line_total_sar is not None:
                assert line.line_total_sar < Decimal("1000000")
            if line.line_cost_sar is not None:
                assert line.line_cost_sar < Decimal("1000000")

        # Dependent items (chairs, tables) are priced correctly from the guest count
        chairs = next(line for line in quote.lines if line.recipe_code == "FRN-CHR-BQT")
        assert chairs.quantity == Decimal("450")
        tables = next(line for line in quote.lines if line.recipe_code == "FRN-TBL-RND")
        assert tables.quantity == Decimal("45")

    def test_bug_2_floor_vinyl_3m_x_3m_depth_dimension_handled(self, generator):
        """Regression test for Bug 2: Floor sticker with 3m width and 3m depth/height -> 9 req / 10 billable."""
        extraction = build_full_nexus_brief_extraction()
        # Ensure floor vinyl has 3m width and 3m depth
        floor_item = next(i for i in extraction.items if i.item_id == "item-floor-vinyl")
        floor_item.dimensions = [
            DimensionObservation(
                observation_id="obs-floor-w",
                source=SourceReference(message_id="msg-1", excerpt="3m x 3m"),
                confidence=ConfidenceLevel.HIGH,
                dimension=DimensionValue(kind=DimensionKind.WIDTH, raw_text="3m", value=Decimal("3"), unit=UnitOfMeasure.METER),
            ),
            DimensionObservation(
                observation_id="obs-floor-d",
                source=SourceReference(message_id="msg-1", excerpt="3m x 3m"),
                confidence=ConfidenceLevel.HIGH,
                dimension=DimensionValue(kind=DimensionKind.DEPTH, raw_text="3m", value=Decimal("3"), unit=UnitOfMeasure.METER),
            ),
        ]
        quote = generator.generate(extraction)
        floor_line = next(line for line in quote.lines if line.item_id == "item-floor-vinyl")
        assert floor_line.recipe_code == "PRN-FLR-VNL"
        assert floor_line.requested_quantity == Decimal("9")
        assert floor_line.quantity == Decimal("10")
        assert floor_line.status == QuoteLineStatus.PRICED
        assert ReviewReason.MISSING_DIMENSION not in floor_line.review_reasons

    def test_bug_3_main_stage_12m_width_missing_depth_matches_stg_deck(self, catalog, generator):
        """Regression test for Bug 3: Stage with 12m length/width and missing depth matches STG-DECK-1x1."""
        extraction = build_full_nexus_brief_extraction()
        stage_item = next(i for i in extraction.items if i.item_id == "item_main_stage")
        stage_item.label = "Main stage"
        stage_item.descriptions = [
            ItemDescriptionObservation(
                observation_id="obs-stage-d",
                source=SourceReference(message_id="msg-1", excerpt="A stage, something like 12 meters, with steps"),
                confidence=ConfidenceLevel.MEDIUM,
                client_text="A stage, something like 12 meters, with steps",
            )
        ]
        stage_item.dimensions = [
            DimensionObservation(
                observation_id="obs-stage-len",
                source=SourceReference(message_id="msg-1", excerpt="12 meters"),
                confidence=ConfidenceLevel.MEDIUM,
                dimension=DimensionValue(kind=DimensionKind.WIDTH, raw_text="12 meters", value=Decimal("12"), unit=UnitOfMeasure.METER),
            )
        ]

        matcher = CatalogMatcher(catalog)
        # match_item returns a LIST of CatalogMatchResult
        match_results = matcher.match_item(stage_item)
        assert len(match_results) >= 1
        # At least one match should be STG-DECK-1x1
        deck_match = next((m for m in match_results if m.recipe_code == "STG-DECK-1x1"), None)
        assert deck_match is not None, f"Expected STG-DECK-1x1 match, got: {match_results}"
        assert deck_match.match_status == MatchStatus.MATCHED

        quote = generator.generate(extraction)
        stage_line = next(line for line in quote.lines if line.item_id == "item_main_stage")
        assert stage_line.recipe_code == "STG-DECK-1x1"
        assert stage_line.status == QuoteLineStatus.REQUIRES_REVIEW
        assert stage_line.quantity is None
        assert ReviewReason.MISSING_DIMENSION in stage_line.review_reasons
        assert ReviewReason.CATALOG_UNKNOWN not in stage_line.review_reasons
