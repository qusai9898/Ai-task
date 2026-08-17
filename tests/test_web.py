"""Tests for Streamlit web UI helper functions and resolution workflows."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.catalog import RecipeCatalog
from app.models import ReviewSeverity
from app.quote_generator import QuoteGenerator
from app.quote_models import PricedLine, QuoteLineKind, QuoteLineStatus
from app.resolution import (
    HOLOGRAM_ITEM_ID,
    LED_SCREEN_ITEM_ID,
    MAIN_STAGE_ITEM_ID,
    UPLIGHTERS_ITEM_ID,
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
from app.web import (
    _display_client_proposal_tab,
    _format_money,
    _format_quantity,
    _format_requested_quantity,
    _line_kind_label,
    _severity_emoji,
)
from tests.fixtures import build_full_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_ROOT / "data" / "recipe_catalog.csv"


class TestWebUiHelpers:
    def test_format_money(self):
        assert _format_money(None) == "—"
        assert _format_money(Decimal("14000.5")) == "14,000.50"
        assert _format_money(Decimal("0")) == "0.00"

    def test_format_quantity(self):
        line_exact = PricedLine(description="Item", quantity=Decimal("10"), unit="unit", status=QuoteLineStatus.PRICED)
        assert _format_quantity(line_exact) == "10"

        line_range = PricedLine(
            description="Item",
            min_quantity=Decimal("8"),
            max_quantity=Decimal("10"),
            unit="unit",
            status=QuoteLineStatus.REQUIRES_REVIEW,
        )
        assert _format_quantity(line_range) == "8 – 10"

        line_none = PricedLine(description="Item", unit="unit", status=QuoteLineStatus.REQUIRES_REVIEW)
        assert _format_quantity(line_none) == "—"

    def test_format_requested_quantity(self):
        line = PricedLine(description="Item", requested_quantity=Decimal("9"), quantity=Decimal("10"), unit="sqm", status=QuoteLineStatus.PRICED)
        assert _format_requested_quantity(line) == "9"

    def test_line_kind_label(self):
        custom = PricedLine(description="Item", unit="unit", line_kind=QuoteLineKind.CUSTOM_NOT_IN_CATALOG, status=QuoteLineStatus.CUSTOM_ESTIMATE)
        assert _line_kind_label(custom) == "CUSTOM / NOT IN CATALOG"

        unres = PricedLine(description="Item", unit="unit", line_kind=QuoteLineKind.UNRESOLVED, status=QuoteLineStatus.REQUIRES_REVIEW)
        assert _line_kind_label(unres) == "UNRESOLVED / REVIEW"

        excluded = PricedLine(description="Item", unit="unit", status=QuoteLineStatus.EXCLUDED)
        assert _line_kind_label(excluded) == "EXCLUDED"

        catalog = PricedLine(description="Item", unit="unit", line_kind=QuoteLineKind.CATALOG, status=QuoteLineStatus.PRICED)
        assert _line_kind_label(catalog) == "CATALOG"

    def test_severity_emoji(self):
        assert _severity_emoji(ReviewSeverity.CRITICAL) == "🔴"
        assert _severity_emoji(ReviewSeverity.WARNING) == "🟠"
        assert _severity_emoji(ReviewSeverity.INFO) == "🔵"


class TestWebResolutionWorkflow:
    def test_all_resolutions_build_and_apply_cleanly(self):
        res_led = build_led_resolution(LedScreenResolutionChoice.RATIO_8X4)
        res_upl = build_uplighters_resolution(UplightersResolutionChoice.QTY_8)
        res_stg = build_stage_resolution(StageResolutionChoice.DIM_12X4)
        res_holo = build_hologram_resolution(HologramResolutionChoice.PASS_THROUGH)

        res_set = ResolutionSet(resolutions=[res_led, res_upl, res_stg, res_holo])
        assert len(res_set.resolutions) == 4
        assert res_set.get(LED_SCREEN_ITEM_ID) is not None
        assert res_set.get(UPLIGHTERS_ITEM_ID) is not None
        assert res_set.get(MAIN_STAGE_ITEM_ID) is not None
        assert res_set.get(HOLOGRAM_ITEM_ID) is not None

    @patch("streamlit.columns")
    @patch("streamlit.markdown")
    @patch("streamlit.expander")
    @patch("streamlit.download_button")
    @patch("streamlit.components.v1.html")
    @patch("streamlit.session_state", {"client_name": "Nexus", "event_name": "Forum", "venue": "Hall", "extraction": None})
    def test_display_client_proposal_tab_executes_without_error(
        self, mock_html, mock_dl, mock_expander, mock_md, mock_cols
    ):
        mock_cols.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        mock_expander.return_value.__enter__.return_value = MagicMock()
        mock_expander.return_value.__exit__.return_value = None

        catalog = RecipeCatalog.load(CATALOG_PATH)
        generator = QuoteGenerator(catalog)
        extraction = build_full_nexus_brief_extraction()
        quote = generator.generate(extraction)

        _display_client_proposal_tab(quote)
        assert mock_html.called
        assert mock_dl.called
