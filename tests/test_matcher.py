"""Tests for deterministic catalog matching."""

from pathlib import Path

from app.catalog import RecipeCatalog
from app.matcher import CatalogMatcher
from app.quote_models import MatchStatus
from tests.fixtures import build_full_nexus_brief_extraction, build_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def _matcher() -> CatalogMatcher:
    return CatalogMatcher(RecipeCatalog.load(CATALOG_CSV))


class TestCatalogMatcher:
    def test_led_screen_matches_suggestion(self):
        extraction = build_nexus_brief_extraction()
        matches = _matcher().match_extraction(extraction)
        led = next(m for m in matches if m.item_id == "item-main-led-screen")
        assert led.match_status == MatchStatus.MATCHED
        assert led.recipe_code == "LED-P39-IN"

    def test_hologram_has_no_match(self):
        extraction = build_nexus_brief_extraction()
        matches = _matcher().match_extraction(extraction)
        hologram = next(m for m in matches if m.item_id == "item-hologram-box")
        assert hologram.match_status == MatchStatus.NO_MATCH

    def test_cancelled_breakout_stage_skipped(self):
        extraction = build_full_nexus_brief_extraction()
        matches = _matcher().match_extraction(extraction)
        breakout = next(m for m in matches if m.item_id == "item-breakout-stage")
        assert breakout.match_status == MatchStatus.CANCELLED

    def test_guest_count_skipped(self):
        extraction = build_nexus_brief_extraction()
        matches = _matcher().match_extraction(extraction)
        guests = next(m for m in matches if m.item_id == "item-guest-count")
        assert guests.match_status == MatchStatus.SKIPPED

    def test_uplighters_match_battery_uplighter(self):
        extraction = build_nexus_brief_extraction()
        matches = _matcher().match_extraction(extraction)
        uplighters = next(m for m in matches if m.item_id == "item-uplighters")
        assert uplighters.match_status == MatchStatus.MATCHED
        assert uplighters.recipe_code == "LGT-UPL-BAT"

    def test_full_brief_matches_multiple_lines(self):
        extraction = build_full_nexus_brief_extraction()
        matches = _matcher().match_extraction(extraction)
        matched_codes = {
            m.recipe_code for m in matches if m.match_status == MatchStatus.MATCHED
        }
        assert "FRN-TBL-RND" in matched_codes
        assert "PWR-GEN-100" in matched_codes
        assert "PRN-FLR-VNL" in matched_codes
