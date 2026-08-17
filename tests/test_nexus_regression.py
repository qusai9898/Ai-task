"""
Regression tests for the post-fix Nexus Ventures quote run.

Covers all 13 requirements from the task description:
1.  Venue confirmation is not a quote line.
2.  Finance margin requirement is not a quote line.
3.  Quote deadline is not a quote line.
4.  Backup power remains a quoteable (priced) requirement.
5.  Breakout projector does not become quantity 2 without evidence.
6.  High tables for 80 people are REQUIRES_REVIEW (catalog has no capacity-per-table).
7.  Microphone requirement is preserved as UNMATCHED/REQUIRES_REVIEW (not dropped).
8.  Breakout panel 5-speaker constraint is preserved in extraction.
9.  Approximate sofa quantity remains review-required.
10. Cancelled breakout stage remains excluded.
11. LED contradiction remains review-required.
12. Stage missing depth remains review-required.
13. Golden Falcon remains excluded.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog import RecipeCatalog
from app.models import RequirementType, ReviewReason
from app.quote_generator import QuoteGenerator, QUOTEABLE_REQUIREMENT_TYPES
from app.quote_models import MatchStatus, QuoteLineStatus
from tests.fixtures import build_full_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


@pytest.fixture
def generator():
    return QuoteGenerator.from_catalog_path(CATALOG_CSV)


@pytest.fixture
def extraction():
    return build_full_nexus_brief_extraction()


@pytest.fixture
def quote(generator, extraction):
    return generator.generate(extraction)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _line_descriptions(quote_obj):
    return [line.description.lower() for line in quote_obj.lines]


# ===========================================================================
# 1–3. Non-quoteable global requirements must NEVER produce quote lines
# ===========================================================================

class TestNonQuoteableRequirements:
    def test_1_venue_is_not_a_quote_line(self, quote):
        """Venue confirmation ('King Abdullah Financial District...') is metadata."""
        for line in quote.lines:
            desc = (line.description or "").lower()
            assert "king abdullah" not in desc, f"Venue confirmation appeared as line: {line.description}"
            assert "kafd" not in desc, f"KAFD appeared as line: {line.description}"

    def test_2_finance_margin_requirement_is_not_a_quote_line(self, quote):
        """'Finance requires margins broken out per line item' is an output/formatting note."""
        for line in quote.lines:
            desc = (line.description or "").lower()
            assert "finance" not in desc, f"Finance note appeared as line: {line.description}"
            assert "margin" not in desc or line.item_id is not None, (
                "A global 'margin' line appeared without an item_id — likely a metadata leak"
            )

    def test_3_quote_deadline_is_not_a_quote_line(self, quote):
        """'Quote required this week; event in 6 weeks' is timeline metadata."""
        for line in quote.lines:
            desc = (line.description or "").lower()
            assert "quote required" not in desc, f"Quote deadline appeared as line: {line.description}"
            assert "6 weeks" not in desc, f"Timeline appeared as line: {line.description}"

    def test_quoteable_requirement_types_allowlist(self):
        """Only POWER, ACCESSIBILITY, SAFETY, OPERATIONAL can produce quote lines."""
        assert RequirementType.POWER in QUOTEABLE_REQUIREMENT_TYPES
        assert RequirementType.ACCESSIBILITY in QUOTEABLE_REQUIREMENT_TYPES
        assert RequirementType.SAFETY in QUOTEABLE_REQUIREMENT_TYPES
        assert RequirementType.OPERATIONAL in QUOTEABLE_REQUIREMENT_TYPES
        # These must NOT be in the allowlist
        assert RequirementType.TIMELINE not in QUOTEABLE_REQUIREMENT_TYPES
        assert RequirementType.BUDGET_EXPECTATION not in QUOTEABLE_REQUIREMENT_TYPES
        assert RequirementType.BRAND not in QUOTEABLE_REQUIREMENT_TYPES
        assert RequirementType.OTHER not in QUOTEABLE_REQUIREMENT_TYPES
        assert RequirementType.PROCUREMENT_REFERENCE not in QUOTEABLE_REQUIREMENT_TYPES


# ===========================================================================
# 4. Backup power remains a quoteable priced requirement
# ===========================================================================

class TestBackupPower:
    def test_4_backup_power_is_a_priced_line(self, quote):
        """Generator 100 kVA must be priced (not dropped, not duplicated)."""
        gen_lines = [
            line for line in quote.lines
            if line.recipe_code == "PWR-GEN-100"
        ]
        assert len(gen_lines) >= 1, "Backup power (PWR-GEN-100) must appear in the quote"
        # Must NOT be duplicated (one from item + one from global req)
        assert len(gen_lines) == 1, (
            f"Backup power must appear exactly once, found {len(gen_lines)} lines"
        )
        assert gen_lines[0].status == QuoteLineStatus.PRICED
        assert gen_lines[0].line_total_sar is not None

    def test_4b_backup_power_quantity_is_not_zero(self, quote):
        gen_line = next(
            line for line in quote.lines if line.recipe_code == "PWR-GEN-100"
        )
        assert gen_line.quantity is not None
        assert gen_line.quantity >= Decimal("1")


# ===========================================================================
# 5. Breakout projector quantity
# ===========================================================================

class TestBreakoutProjector:
    def test_5_projector_quantity_is_1(self, quote):
        """AV-PROJ-15K is 1 unit; brief says 'Projector and screen is fine here' — no qty=2."""
        proj_lines = [
            line for line in quote.lines
            if line.recipe_code == "AV-PROJ-15K"
        ]
        assert len(proj_lines) >= 1, "Projector must appear in the quote"
        for line in proj_lines:
            assert line.quantity == Decimal("1"), (
                f"Projector must be qty=1 (brief has no quantity ≥2); got {line.quantity}"
            )
            assert line.status == QuoteLineStatus.PRICED


# ===========================================================================
# 6. Cocktail high tables: 80-pax capacity spec → REQUIRES_REVIEW
# ===========================================================================

class TestCocktailHighTables:
    def test_6_high_tables_pax_spec_requires_review(self, quote):
        """
        'For around 80 people' is a capacity spec, not a table count.
        Catalog FRN-CTL-HIGH has no seats-per-table figure.
        Must be REQUIRES_REVIEW, not silently priced at 80 units.
        """
        ctl_lines = [
            line for line in quote.lines
            if line.recipe_code == "FRN-CTL-HIGH"
        ]
        assert len(ctl_lines) >= 1, "Cocktail high tables must appear in the quote"
        ctl = ctl_lines[0]
        assert ctl.status == QuoteLineStatus.REQUIRES_REVIEW, (
            f"Cocktail tables must be REQUIRES_REVIEW when only a pax spec is given; got {ctl.status}"
        )
        # Must not silently become 80 units or 1 unit
        assert ctl.quantity is None, (
            f"Cocktail table quantity must be None (needs review); got {ctl.quantity}"
        )
        assert ReviewReason.JUDGMENT_REQUIRED in ctl.review_reasons

    def test_6b_explicit_table_count_is_priced_directly(self, generator):
        """When the brief gives an explicit table count (not pax), it is priced."""
        from tests.fixtures import build_full_nexus_brief_extraction
        from app.models import QuantityObservation, QuantityValue, UnitOfMeasure
        extraction = build_full_nexus_brief_extraction()
        cocktail_item = next(
            i for i in extraction.items if i.item_id == "item-breakout-cocktail"
        )
        # Replace the PAX observation with an explicit table count
        cocktail_item.quantities = [
            QuantityObservation(
                observation_id="obs-cocktail-explicit",
                source=cocktail_item.quantities[0].source,
                confidence=cocktail_item.quantities[0].confidence,
                quantity=QuantityValue(
                    raw_text="10 tables",
                    value=Decimal("10"),
                    unit=UnitOfMeasure.UNIT,
                ),
            )
        ]
        quote = generator.generate(extraction)
        ctl = next(line for line in quote.lines if line.recipe_code == "FRN-CTL-HIGH")
        assert ctl.status == QuoteLineStatus.PRICED
        assert ctl.quantity == Decimal("10")


# ===========================================================================
# 7. Microphone requirement preserved
# ===========================================================================

class TestMicrophoneRequirement:
    def test_7_microphone_item_is_in_extraction(self, extraction):
        """Microphone item must be present in the extraction."""
        mic_item = next(
            (i for i in extraction.items if i.item_id == "item-breakout-mics"),
            None,
        )
        assert mic_item is not None, "Microphone item must be extracted"

    def test_7b_microphone_appears_in_quote_as_unmatched_or_review(self, quote):
        """
        Microphones have no dedicated catalog item — must appear as
        UNMATCHED or REQUIRES_REVIEW, never silently dropped.
        """
        mic_lines = [
            line for line in quote.lines
            if line.item_id == "item-breakout-mics"
        ]
        assert len(mic_lines) >= 1, (
            "Microphone item must produce at least one quote line "
            "(even if unmatched) so it is never silently dropped"
        )
        for line in mic_lines:
            assert line.status in (
                QuoteLineStatus.REQUIRES_REVIEW,
                QuoteLineStatus.UNMATCHED,
                QuoteLineStatus.CUSTOM_ESTIMATE,
            ), f"Microphone line must not be priced without a catalog match; got {line.status}"

    def test_7c_microphone_not_silently_priced(self, quote):
        """No catalog item exists for standalone mics; must not be PRICED."""
        for line in quote.lines:
            if line.item_id == "item-breakout-mics":
                assert line.status != QuoteLineStatus.PRICED, (
                    "Microphones must not be silently priced — no standalone catalog item exists"
                )


# ===========================================================================
# 8. Breakout panel: 5-speaker constraint preserved
# ===========================================================================

class TestBreakoutPanelConstraints:
    def test_8_five_speaker_requirement_on_lounge_item(self, extraction):
        """The breakout lounge item must carry the 5-speaker requirement."""
        lounge = next(
            (i for i in extraction.items if i.item_id == "item-breakout-lounge"),
            None,
        )
        assert lounge is not None, "Breakout lounge item must exist"
        speaker_reqs = [
            r for r in lounge.requirements
            if "5 speaker" in r.description.lower() or "speaker" in r.description.lower()
        ]
        assert len(speaker_reqs) >= 1, (
            "5-speaker constraint must be preserved as a requirement on the breakout lounge item"
        )


# ===========================================================================
# 9. Approximate sofa quantity remains review-required
# ===========================================================================

class TestSofaQuantity:
    def test_9_approximate_sofa_requires_review(self, quote):
        """'Maybe 2 of those sofa sets' is approximate — must remain REQUIRES_REVIEW."""
        sofa_lines = [
            line for line in quote.lines
            if line.recipe_code == "FRN-SOFA-LNG"
        ]
        assert len(sofa_lines) >= 1, "Sofa line must appear in quote"
        sofa = sofa_lines[0]
        assert sofa.status == QuoteLineStatus.REQUIRES_REVIEW, (
            f"Approximate sofa quantity must be REQUIRES_REVIEW; got {sofa.status}"
        )


# ===========================================================================
# 10. Cancelled breakout stage excluded
# ===========================================================================

class TestCancelledStage:
    def test_10_cancelled_breakout_stage_excluded(self, quote):
        """item-breakout-stage is cancelled in brief — must not appear as a priced line."""
        for line in quote.lines:
            desc = (line.description or "").lower()
            assert "breakout stage" not in desc, f"Cancelled stage appeared: {line.description}"
            assert "second stage" not in desc, f"Cancelled stage appeared: {line.description}"
        # Verify it's NOT priced
        breakout_stage_lines = [
            line for line in quote.lines
            if line.item_id == "item-breakout-stage"
        ]
        for line in breakout_stage_lines:
            assert line.status != QuoteLineStatus.PRICED


# ===========================================================================
# 11. LED contradiction remains review-required
# ===========================================================================

class TestLEDContradiction:
    def test_11_led_screen_requires_review_without_resolution(self, quote):
        """LED 6m×3m vs ≥8m wide — no resolution provided → REQUIRES_REVIEW."""
        led_lines = [
            line for line in quote.lines
            if line.item_id == "item_main_led_screen"
        ]
        assert len(led_lines) >= 1, "LED screen must appear in quote"
        led = led_lines[0]
        assert led.status == QuoteLineStatus.REQUIRES_REVIEW
        assert led.quantity is None

    def test_11b_led_contradiction_flag_present(self, quote):
        flag_reasons = [f.reason for f in quote.review_flags]
        assert ReviewReason.CONTRADICTORY_INSTRUCTIONS in flag_reasons


# ===========================================================================
# 12. Main stage missing depth remains review-required
# ===========================================================================

class TestStageMissingDepth:
    def test_12_stage_requires_review_for_missing_depth(self, quote):
        """Stage 12m stated but depth unknown → REQUIRES_REVIEW."""
        stage_lines = [
            line for line in quote.lines
            if line.item_id == "item_main_stage"
        ]
        assert len(stage_lines) >= 1, "Main stage must appear in quote"
        stage = stage_lines[0]
        assert stage.status == QuoteLineStatus.REQUIRES_REVIEW
        assert stage.quantity is None
        assert ReviewReason.MISSING_DIMENSION in stage.review_reasons


# ===========================================================================
# 13. Golden Falcon excluded
# ===========================================================================

class TestGoldenFalcon:
    def test_13_golden_falcon_excluded_from_quote(self, quote):
        """Adversarial 'Golden Falcon Welcome Arch' must not appear in any line."""
        for line in quote.lines:
            desc = (line.description or "").lower()
            assert "golden falcon" not in desc

    def test_13b_golden_falcon_hidden_instruction_flag_preserved(self, quote):
        """The HIDDEN_INSTRUCTION_DETECTED critical flag must be preserved."""
        assert any(
            f.reason == ReviewReason.HIDDEN_INSTRUCTION_DETECTED
            for f in quote.review_flags
        ), "Critical hidden-instruction flag must remain in review_flags"
