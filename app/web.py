"""Minimal Streamlit UI for human review and quote resolution."""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal
from pathlib import Path

import streamlit as st

from app.models import ReviewReason, ReviewSeverity
from app.pipeline import QuotingPipeline
from app.quote_generator import QuoteGenerator
from app.quote_models import QuoteLineKind, QuoteLineStatus, QuoteStatus
from app.resolution import (
    LED_SCREEN_ITEM_ID,
    LedScreenResolutionChoice,
    ResolutionSet,
    build_led_resolution,
    led_contradiction_pending,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = PROJECT_ROOT / "data" / "client_brief.pdf"
CATALOG_PATH = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def _format_money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _format_quantity(line) -> str:
    if line.quantity is not None:
        return str(line.quantity)
    if line.min_quantity is not None and line.max_quantity is not None:
        return f"{line.min_quantity} – {line.max_quantity}"
    return "—"


def _line_kind_label(line) -> str:
    if line.line_kind == QuoteLineKind.CUSTOM_NOT_IN_CATALOG:
        return "CUSTOM / NOT IN CATALOG"
    if line.line_kind == QuoteLineKind.UNRESOLVED:
        return "UNRESOLVED / REVIEW"
    if line.status == QuoteLineStatus.EXCLUDED:
        return "EXCLUDED"
    return "CATALOG"


def _severity_emoji(severity: ReviewSeverity) -> str:
    if severity == ReviewSeverity.CRITICAL:
        return "🔴"
    if severity == ReviewSeverity.WARNING:
        return "🟠"
    return "🔵"


def _init_session_state() -> None:
    defaults = {
        "extraction": None,
        "quote": None,
        "resolutions": None,
        "source_label": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _regenerate_quote() -> None:
    if st.session_state.extraction is None:
        return
    generator = QuoteGenerator.from_catalog_path(CATALOG_PATH)
    st.session_state.quote = generator.generate(
        st.session_state.extraction,
        quote_id=st.session_state.quote.quote_id if st.session_state.quote else None,
        resolutions=st.session_state.resolutions,
    )


def _run_pipeline_on_pdf(pdf_path: Path, source_label: str) -> None:
    pipeline = QuotingPipeline(catalog_path=CATALOG_PATH)
    extraction, quote = pipeline.generate_quote_from_pdf(
        pdf_path=pdf_path,
        extraction_id="extract-web-ui",
        quote_id="quote-web-ui",
    )
    st.session_state.extraction = extraction
    st.session_state.quote = quote
    st.session_state.resolutions = None
    st.session_state.source_label = source_label


def _load_sample_brief() -> None:
    from tests.fixtures import build_full_nexus_brief_extraction

    extraction = build_full_nexus_brief_extraction()
    generator = QuoteGenerator.from_catalog_path(CATALOG_PATH)
    quote = generator.generate(extraction, quote_id="quote-sample")
    st.session_state.extraction = extraction
    st.session_state.quote = quote
    st.session_state.resolutions = None
    st.session_state.source_label = "Sample Nexus brief (fixture, no OpenAI call)"


def _display_review_flags(quote) -> None:
    st.subheader("Human review flags")
    if not quote.review_flags:
        st.success("No review flags.")
        return

    for flag in quote.review_flags:
        st.markdown(
            f"{_severity_emoji(flag.severity)} **{flag.reason.value}** — {flag.message}"
        )
        if flag.related_item_ids:
            st.caption(f"Items: {', '.join(flag.related_item_ids)}")


def _display_led_resolution_panel() -> None:
    extraction = st.session_state.extraction
    quote = st.session_state.quote
    if extraction is None or quote is None:
        return

    led_line = next(
        (line for line in quote.lines if line.item_id == LED_SCREEN_ITEM_ID),
        None,
    )
    pending = led_contradiction_pending(extraction)
    already_resolved = (
        st.session_state.resolutions
        and st.session_state.resolutions.get(LED_SCREEN_ITEM_ID) is not None
    )

    if not pending and not (led_line and led_line.status == QuoteLineStatus.REQUIRES_REVIEW):
        if already_resolved:
            resolution = st.session_state.resolutions.get(LED_SCREEN_ITEM_ID)
            st.success(
                f"LED screen resolved: {resolution.choice.value}"
                + (f" ({resolution.note})" if resolution.note else "")
            )
        return

    st.subheader("Resolve LED screen contradiction")
    st.markdown(
        """
**Original (Fahad):** 6m × 3m  
**Later update (Khalid):** at least 8m wide, height not stated  
**Internal note:** earlier 6×3 ratio suggested as a judgment call
        """
    )

    choice = st.radio(
        "Choose resolution",
        options=[
            LedScreenResolutionChoice.ORIGINAL_6X3,
            LedScreenResolutionChoice.RATIO_8X4,
            LedScreenResolutionChoice.EXCLUDE,
            LedScreenResolutionChoice.CUSTOM,
        ],
        format_func=lambda c: {
            LedScreenResolutionChoice.ORIGINAL_6X3: "Use original 6m × 3m",
            LedScreenResolutionChoice.RATIO_8X4: "Use 8m × 4m (original 2:1 ratio)",
            LedScreenResolutionChoice.EXCLUDE: "Exclude LED line from quote",
            LedScreenResolutionChoice.CUSTOM: "Custom dimensions",
        }[c],
        key="led_resolution_choice",
    )

    custom_width = None
    custom_height = None
    if choice == LedScreenResolutionChoice.CUSTOM:
        custom_width = st.number_input("Width (m)", min_value=0.1, value=8.0, step=0.5)
        custom_height = st.number_input("Height (m)", min_value=0.1, value=3.0, step=0.5)

    if st.button("Apply LED resolution and recalculate quote", type="primary"):
        try:
            resolution = build_led_resolution(
                choice=choice,
                custom_width_m=Decimal(str(custom_width)) if custom_width else None,
                custom_height_m=Decimal(str(custom_height)) if custom_height else None,
            )
            st.session_state.resolutions = ResolutionSet(resolutions=[resolution])
            _regenerate_quote()
            st.success("Quote recalculated deterministically (no LLM).")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def _display_quote_lines(quote) -> None:
    st.subheader("Quote lines")

    rows = []
    for line in quote.lines:
        if "golden falcon" in line.description.lower():
            continue

        rows.append(
            {
                "Type": _line_kind_label(line),
                "Item": line.description,
                "Recipe": line.recipe_code or "—",
                "Qty": _format_quantity(line),
                "Unit": line.unit,
                "Material": _format_money(line.material_cost_sar),
                "Labour": _format_money(line.labour_cost_sar),
                "Equipment": _format_money(line.equipment_cost_sar),
                "Margin %": (
                    f"{line.margin_pct}%" if line.margin_pct is not None else "—"
                ),
                "Margin SAR": _format_money(line.margin_amount_sar),
                "Unit sell": _format_money(line.unit_price_sar),
                "Line total": (
                    _format_money(line.line_total_sar)
                    if line.line_total_sar is not None
                    else (
                        f"{_format_money(line.min_line_total_sar)} – "
                        f"{_format_money(line.max_line_total_sar)}"
                    )
                ),
                "Status": line.status.value,
                "Notes": line.notes or "",
            }
        )

    st.dataframe(rows, use_container_width=True)

    for line in quote.lines:
        if line.line_kind == QuoteLineKind.CUSTOM_NOT_IN_CATALOG:
            st.info(
                f"**{line.description}** — CUSTOM / NOT IN CATALOG. "
                f"{line.notes or 'No catalog recipe; estimate based on procurement reference.'}"
            )


def _display_totals(quote) -> None:
    st.subheader("Quote totals")
    st.metric("Status", quote.status.value)
    if quote.subtotal_sar is not None:
        st.metric("Subtotal (SAR)", _format_money(quote.subtotal_sar))
    elif quote.min_subtotal_sar is not None and quote.max_subtotal_sar is not None:
        st.metric(
            "Subtotal range (SAR)",
            f"{_format_money(quote.min_subtotal_sar)} – "
            f"{_format_money(quote.max_subtotal_sar)}",
        )
    if quote.notes:
        st.caption(quote.notes)


def main() -> None:
    st.set_page_config(
        page_title="AI Quoting Tool — Human Review",
        page_icon="📋",
        layout="wide",
    )
    _init_session_state()

    st.title("AI Quoting Tool — Human Review")
    st.caption(
        "Upload the client brief PDF, generate a quote, resolve review flags, "
        "and recalculate deterministically."
    )

    with st.sidebar:
        st.header("Brief input")
        uploaded = st.file_uploader("Client brief PDF", type=["pdf"])
        if st.button("Generate quote from uploaded PDF", use_container_width=True):
            if uploaded is None:
                st.warning("Upload a PDF first.")
            else:
                with st.spinner("Extracting brief and generating quote…"):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(uploaded.getvalue())
                        tmp_path = Path(tmp.name)
                    try:
                        _run_pipeline_on_pdf(tmp_path, uploaded.name)
                    finally:
                        tmp_path.unlink(missing_ok=True)
                st.success("Quote generated.")

        if st.button("Use bundled sample brief (no OpenAI)", use_container_width=True):
            _load_sample_brief()
            st.success("Sample brief loaded.")

        if DEFAULT_PDF.is_file() and os.getenv("OPENAI_API_KEY"):
            if st.button("Run bundled PDF with OpenAI", use_container_width=True):
                with st.spinner("Calling OpenAI extraction…"):
                    _run_pipeline_on_pdf(DEFAULT_PDF, str(DEFAULT_PDF))
                st.success("Quote generated from bundled PDF.")

        if not os.getenv("OPENAI_API_KEY"):
            st.info("Set OPENAI_API_KEY to extract live PDFs via OpenAI.")

        if st.session_state.source_label:
            st.caption(f"Source: {st.session_state.source_label}")

    if st.session_state.quote is None:
        st.info("Upload a brief or load the sample to begin.")
        return

    quote = st.session_state.quote
    _display_totals(quote)
    _display_review_flags(quote)
    _display_led_resolution_panel()
    _display_quote_lines(quote)

    hidden_flags = [
        f for f in quote.review_flags
        if f.reason == ReviewReason.HIDDEN_INSTRUCTION_DETECTED
    ]
    if hidden_flags:
        st.error(
            "Critical: adversarial instruction detected in brief "
            "(Golden Falcon Welcome Arch). It must NOT appear as a quote line."
        )


if __name__ == "__main__":
    main()
