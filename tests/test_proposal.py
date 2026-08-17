"""Unit tests for Official Proposal generation and HTML templating."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog import RecipeCatalog
from app.proposal import ProposalGenerator
from app.quote_generator import QuoteGenerator
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


class TestProposalGenerator:
    def test_build_proposal_from_resolved_quote(self, catalog, generator):
        extraction = build_full_nexus_brief_extraction()
        resolutions = ResolutionSet(
            resolutions=[
                build_led_resolution(LedScreenResolutionChoice.RATIO_8X4),
                build_uplighters_resolution(UplightersResolutionChoice.QTY_8),
                build_stage_resolution(StageResolutionChoice.DIM_12X4),
                build_hologram_resolution(HologramResolutionChoice.PASS_THROUGH),
            ]
        )
        quote = generator.generate(extraction, resolutions=resolutions)
        proposal_gen = ProposalGenerator(catalog)
        proposal = proposal_gen.build_proposal(
            quote=quote,
            extraction=extraction,
            client_name="Nexus Ventures",
            event_name="Annual Tech Forum 2026",
            venue="Grand Ballroom",
        )

        assert proposal.client_name == "Nexus Ventures"
        assert proposal.event_name == "Annual Tech Forum 2026"
        # The proposal and quote sum the same line totals but may differ by 1 ULP due to
        # Decimal arithmetic order. Round to 2 dp before comparing.
        two_dp = Decimal("0.01")
        assert proposal.subtotal_sar.quantize(two_dp) == quote.subtotal_sar.quantize(two_dp)
        assert proposal.subtotal_sar > Decimal("0")
        assert proposal.vat_amount_sar == (proposal.subtotal_sar * Decimal("0.15")).quantize(Decimal("0.01"))
        assert proposal.total_with_vat_sar == proposal.subtotal_sar + proposal.vat_amount_sar
        assert len(proposal.groups) > 0

        # Check HTML generation
        html = proposal_gen.generate_html(proposal)
        assert "<html" in html.lower()
        assert "Nexus Ventures" in html
        assert "Annual Tech Forum 2026" in html
        assert "SAR" in html
        assert "Commercial Terms & Conditions" in html
        assert "Golden Falcon" not in html
