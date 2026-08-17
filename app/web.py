"""Streamlit Web UI for AI Quoting Tool — human review, pricing engine & official proposal generator."""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from app.catalog import RecipeCatalog
from app.extractor import (
    DEFAULT_GEMINI_MODEL,
    BriefExtractor,
    ExtractionError,
)
from app.models import ReviewReason, ReviewSeverity
from app.pipeline import QuotingPipeline
from app.proposal import OfficialProposal, ProposalGenerator
from app.quote_generator import QuoteGenerator
from app.quote_models import QuoteLineKind, QuoteLineStatus, QuoteStatus
from app.resolution import (
    ItemResolution,
    ResolutionSet,
    build_generic_dimension_resolution,
    build_generic_exclusion,
    build_generic_pricing_resolution,
    build_generic_quantity_resolution,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = PROJECT_ROOT / "data" / "client_brief.pdf"
CATALOG_PATH = PROJECT_ROOT / "data" / "recipe_catalog.csv"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_HEADER_PATH = ASSETS_DIR / "logo_header.png"
LOGO_WATERMARK_PATH = ASSETS_DIR / "logo_watermark.png"


def _image_to_base64(path: Path) -> str | None:
    if not path.is_file():
        return None
    import base64
    return base64.b64encode(path.read_bytes()).decode("ascii")


CUSTOM_CSS = """
<style>
    /* Executive modern aesthetic */
    .main-header {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #1e293b 0%, #4f46e5 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .metric-card {
        background: #ffffff;
        border-radius: 10px;
        padding: 16px 20px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.03);
    }
    .status-badge-ready {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        background: #dcfce7;
        color: #166534;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .status-badge-review {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        background: #fef3c7;
        color: #92400e;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        font-size: 1.05rem;
        font-weight: 600;
        border-radius: 6px 6px 0 0;
        padding: 10px 20px;
    }
    .proposal-frame {
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        background: white;
        padding: 8px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }
    .app-watermark {
        position: fixed;
        bottom: 14px;
        right: 18px;
        width: 42px;
        height: auto;
        opacity: 0.18;
        pointer-events: none;
        z-index: 9999;
    }
    .logo-card {
        display: inline-block;
        background: #ffffff;
        border-radius: 10px;
        padding: 8px 14px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.12);
    }
</style>
"""


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


def _format_requested_quantity(line) -> str:
    if line.requested_quantity is not None:
        return str(line.requested_quantity)
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
        "client_name": "",
        "event_name": "",
        "venue": "",
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


def _apply_resolution(resolution: ItemResolution) -> None:
    current = st.session_state.resolutions or ResolutionSet()
    updated = [
        r for r in current.resolutions
        if not (r.item_id == resolution.item_id and r.recipe_code == resolution.recipe_code)
    ]
    updated.append(resolution)
    st.session_state.resolutions = ResolutionSet(resolutions=updated)
    _regenerate_quote()


def _clear_all_resolutions() -> None:
    st.session_state.resolutions = None
    _regenerate_quote()


def _display_review_flags(quote) -> None:
    st.subheader("Human review flags")
    if not quote.review_flags:
        st.success("No review flags.")
        return
    for flag in quote.review_flags:
        emoji = _severity_emoji(flag.severity)
        st.markdown(f"{emoji} **{flag.reason.value}** — {flag.message}")
        if flag.related_item_ids:
            st.caption(f"Items: {', '.join(flag.related_item_ids)}")


def _set_proposal_defaults_from_extraction(extraction) -> None:
    """Pre-fill client/event/venue from what the LLM actually found in the
    brief text (client_organization/event_name/venue on the extraction).
    Falls back to an empty string -- never a stale value from a
    previous, different brief -- when the brief genuinely didn't state
    one. The reviewer can always edit these before sending the proposal."""
    st.session_state.client_name = extraction.client_organization or ""
    st.session_state.event_name = extraction.event_name or ""
    st.session_state.venue = extraction.venue or ""
    # A new brief means any cached resolution-panel snapshots (types,
    # original min/max, dimension presets...) belong to a DIFFERENT
    # brief's items and must not leak into this one.
    st.session_state["_panel_meta_cache"] = {}


def _run_pipeline_on_pdf(
    pdf_path: Path,
    source_label: str,
    provider: str = "gemini",
    model: str | None = None,
    api_key: str | None = None,
) -> bool:
    try:
        resolved_key = api_key.strip() if api_key and api_key.strip() else (os.getenv("GEMINI_API_KEY") or None)
        extractor = BriefExtractor(
            provider=provider,
            model=model,
            api_key=resolved_key,
            catalog_path=CATALOG_PATH,
        )
        pipeline = QuotingPipeline(catalog_path=CATALOG_PATH, extractor=extractor)
        extraction, quote = pipeline.generate_quote_from_pdf(
            pdf_path=pdf_path,
            extraction_id="extract-web-ui",
            quote_id="quote-web-ui",
        )
        st.session_state.extraction = extraction
        st.session_state.quote = quote
        st.session_state.resolutions = None
        st.session_state.source_label = source_label
        _set_proposal_defaults_from_extraction(extraction)
        return True
    except ExtractionError as exc:
        st.error(f"Extraction failed: {exc}")
        return False
    except Exception as exc:
        st.error(f"Unexpected error during quote generation: {exc}")
        return False


def _run_pipeline_on_text(
    brief_text: str,
    source_label: str = "Pasted Brief Text",
    provider: str = "gemini",
    model: str | None = None,
    api_key: str | None = None,
) -> bool:
    try:
        resolved_key = api_key.strip() if api_key and api_key.strip() else (os.getenv("GEMINI_API_KEY") or None)
        extractor = BriefExtractor(
            provider=provider,
            model=model,
            api_key=resolved_key,
            catalog_path=CATALOG_PATH,
        )
        pipeline = QuotingPipeline(catalog_path=CATALOG_PATH, extractor=extractor)
        extraction, quote = pipeline.generate_quote_from_text(
            brief_text=brief_text,
            source_document=source_label,
            extraction_id="extract-pasted-text",
            quote_id="quote-pasted-text",
        )
        st.session_state.extraction = extraction
        st.session_state.quote = quote
        st.session_state.resolutions = None
        st.session_state.source_label = source_label
        _set_proposal_defaults_from_extraction(extraction)
        return True
    except ExtractionError as exc:
        st.error(f"Extraction failed: {exc}")
        return False
    except Exception as exc:
        st.error(f"Unexpected error during quote generation: {exc}")
        return False


def _load_sample_brief() -> None:
    from tests.fixtures import build_nexus_brief_extraction

    extraction = build_nexus_brief_extraction()
    generator = QuoteGenerator.from_catalog_path(CATALOG_PATH)
    quote = generator.generate(extraction, quote_id="quote-sample")
    st.session_state.extraction = extraction
    st.session_state.quote = quote
    st.session_state.resolutions = None
    st.session_state.source_label = "Bundled Sample Brief (no LLM)"
    _set_proposal_defaults_from_extraction(extraction)


def _find_item(extraction, item_id: str | None):
    if extraction is None or item_id is None:
        return None
    for item in extraction.items:
        if item.item_id == item_id:
            return item
    return None


def _dimension_presets(item) -> list[dict]:
    """Build smart dimension presets from what the brief actually stated
    for this item -- never inventing a number. If an original width x
    height pair exists (even if later superseded by a width-only update),
    offer both 'keep the original pair' and 'apply the original ratio to
    the latest width' as presets, exactly like the old Nexus-specific LED
    panel did, but generically for any item on any brief."""
    from app.models import DimensionKind, ObservationStatus

    active_widths = [
        d.dimension.value for d in item.dimensions
        if d.dimension.kind == DimensionKind.WIDTH
        and d.dimension.value is not None
        and d.status != ObservationStatus.CANCELLED
    ]
    all_heights = [
        d.dimension.value for d in item.dimensions
        if d.dimension.kind in (DimensionKind.HEIGHT, DimensionKind.DEPTH)
        and d.dimension.value is not None
        and d.status != ObservationStatus.CANCELLED
    ]
    all_widths = [
        d.dimension.value for d in item.dimensions
        if d.dimension.kind == DimensionKind.WIDTH
        and d.dimension.value is not None
        and d.status != ObservationStatus.CANCELLED
    ]

    presets: list[dict] = []
    latest_width = active_widths[-1] if active_widths else None

    if all_widths and all_heights:
        original_w, original_h = all_widths[0], all_heights[0]
        if original_w and original_h:
            if latest_width and latest_width != original_w:
                ratio = original_h / original_w
                scaled_h = (latest_width * ratio).quantize(Decimal("0.01"))
                presets.append({
                    "key": "ratio",
                    "label": f"Use {latest_width}m × {scaled_h}m (original {original_w}:{original_h} ratio)",
                    "width": latest_width,
                    "height": scaled_h,
                })
            presets.append({
                "key": "original",
                "label": f"Use original {original_w}m × {original_h}m",
                "width": original_w,
                "height": original_h,
            })

    return presets


def _on_generic_dim_preset_change(
    item_id: str, recipe_code: str | None, choice_key: str, w_key: str, h_key: str,
    presets_by_key: dict,
) -> None:
    choice = st.session_state.get(choice_key)
    if choice == "custom":
        w = st.session_state.get(w_key)
        h = st.session_state.get(h_key)
        if not w or not h:
            return
        res = build_generic_dimension_resolution(item_id, recipe_code, Decimal(str(w)), Decimal(str(h)))
    else:
        preset = presets_by_key.get(choice)
        if not preset:
            return
        res = build_generic_dimension_resolution(
            item_id, recipe_code, Decimal(str(preset["width"])), Decimal(str(preset["height"])),
            note=preset["label"],
        )
    _apply_resolution(res)


def _on_generic_qty_range_change(
    item_id: str, recipe_code: str | None, choice_key: str, custom_key: str,
    min_val: Decimal, max_val: Decimal,
) -> None:
    choice = st.session_state.get(choice_key)
    if choice == "use_min":
        q = min_val
    elif choice == "use_max":
        q = max_val
    elif choice == "custom":
        c = st.session_state.get(custom_key)
        if not c:
            return
        q = Decimal(str(c))
    else:
        return
    res = build_generic_quantity_resolution(item_id, recipe_code, q)
    _apply_resolution(res)


def _on_generic_qty_approx_change(
    item_id: str, recipe_code: str | None, choice_key: str, custom_key: str, stated_val: Decimal,
) -> None:
    choice = st.session_state.get(choice_key)
    if choice == "confirm":
        q = stated_val
    elif choice == "custom":
        c = st.session_state.get(custom_key)
        if not c:
            return
        q = Decimal(str(c))
    else:
        return
    res = build_generic_quantity_resolution(item_id, recipe_code, q)
    _apply_resolution(res)


def _on_generic_price_change(
    item_id: str, recipe_code: str | None, choice_key: str, custom_key: str, cost_basis: Decimal | None,
) -> None:
    choice = st.session_state.get(choice_key)
    if choice == "pass_through":
        if cost_basis is None:
            return
        res = build_generic_pricing_resolution(
            item_id, recipe_code, unit_price_sar=cost_basis, margin_pct=Decimal("0"),
            note=f"Reviewer approved pass-through pricing at cost: SAR {cost_basis:,.2f} (0% margin).",
        )
    elif choice == "margin_30":
        if cost_basis is None:
            return
        sell = cost_basis / Decimal("0.70")
        res = build_generic_pricing_resolution(
            item_id, recipe_code, unit_price_sar=sell, margin_pct=Decimal("30"),
            note=f"Reviewer confirmed 30% margin on SAR {cost_basis:,.2f} cost basis (Sell: SAR {sell:,.2f}).",
        )
    elif choice == "custom":
        p = st.session_state.get(custom_key)
        if not p:
            return
        res = build_generic_pricing_resolution(item_id, recipe_code, unit_price_sar=Decimal(str(p)))
    else:
        return
    _apply_resolution(res)


def _on_generic_exclude(item_id: str, recipe_code: str | None) -> None:
    _apply_resolution(build_generic_exclusion(item_id, recipe_code))


def _display_resolution_panels() -> None:
    """
    Dynamically render a resolution panel for every item that ever needed
    (or still needs) human input -- for ANY brief. Panels never disappear
    once resolved: instead they keep showing (with a green "Confirmed"
    badge and the current choice pre-selected) so the reviewer can come
    back and change their mind at any time, exactly like the built-in
    LED/stage/uplighters/hologram panels always did.

    A resolved dimension/quantity line loses its original min/max,
    requested_quantity, and review_reasons once priced (there is nothing
    left to signal "this needed a decision"), so we snapshot each item's
    panel type and original options the FIRST time it is seen pending,
    into st.session_state, and keep using that snapshot afterwards
    regardless of what the live PricedLine looks like post-resolution.
    """
    extraction = st.session_state.extraction
    quote = st.session_state.quote
    if extraction is None or quote is None:
        return

    resolutions = st.session_state.resolutions or ResolutionSet()
    cache = st.session_state.setdefault("_panel_meta_cache", {})

    def _snapshot(key, line, item):
        if key in cache:
            return cache[key]
        reasons = set(line.review_reasons)
        if line.unit == "sqm" and (
            ReviewReason.MISSING_DIMENSION in reasons
            or ReviewReason.CONTRADICTORY_INSTRUCTIONS in reasons
        ):
            meta = {
                "type": "dimension",
                "label": item.label if item else (line.description or "Item"),
                "notes": line.notes,
                "presets": _dimension_presets(item) if item else [],
            }
        elif line.min_quantity is not None and line.max_quantity is not None:
            meta = {
                "type": "range",
                "label": item.label if item else (line.description or "Item"),
                "notes": line.notes,
                "min": line.min_quantity,
                "max": line.max_quantity,
            }
        elif ReviewReason.APPROXIMATE_VALUE in reasons and line.requested_quantity is not None:
            meta = {
                "type": "approx",
                "label": item.label if item else (line.description or "Item"),
                "notes": line.notes,
                "stated": line.requested_quantity,
            }
        elif line.status == QuoteLineStatus.CUSTOM_ESTIMATE:
            meta = {
                "type": "pricing",
                "label": item.label if item else (line.description or "Item"),
                "notes": line.notes,
                "cost_basis": line.material_cost_sar or line.line_cost_sar,
            }
        elif line.status == QuoteLineStatus.UNMATCHED and not line.recipe_code:
            meta = {
                "type": "hard_unmatched",
                "label": item.label if item else (line.description or "Item"),
                "notes": line.notes,
            }
        else:
            meta = {
                "type": "fallback",
                "label": item.label if item else (line.description or "Item"),
                "notes": line.notes,
            }
        cache[key] = meta
        return meta

    resolved_pairs = {(r.item_id, r.recipe_code) for r in resolutions.resolutions}

    relevant_lines: list = []
    seen_keys: set = set()
    for line in quote.lines:
        key = (line.item_id, line.recipe_code)
        is_pending = line.status in (
            QuoteLineStatus.REQUIRES_REVIEW,
            QuoteLineStatus.UNMATCHED,
            QuoteLineStatus.CUSTOM_ESTIMATE,
        )
        is_resolved = key in resolved_pairs
        if (is_pending or is_resolved) and key not in seen_keys:
            relevant_lines.append(line)
            seen_keys.add(key)
        if is_pending and key not in cache:
            _snapshot(key, line, _find_item(extraction, line.item_id))

    st.subheader("Human Review & Item Resolution")

    if not relevant_lines:
        st.success("No items currently require human review.")
        return

    for idx, line in enumerate(relevant_lines):
        key = (line.item_id, line.recipe_code)
        item = _find_item(extraction, line.item_id)
        meta = cache.get(key) or _snapshot(key, line, item)
        label = meta["label"]
        res = resolutions.get_for_recipe(line.item_id, line.recipe_code) if line.item_id else None
        badge = f" [Resolved: {res.choice}]" if res else ""
        key_base = f"{line.item_id or 'noid'}_{line.recipe_code or 'norecipe'}_{idx}"

        with st.expander(f"{label}{badge}", expanded=(res is None)):
            if res:
                st.success(f"✅ Confirmed — {res.note or res.choice}")
            if meta.get("notes"):
                st.caption(meta["notes"])

            ptype = meta["type"]

            if ptype == "dimension":
                presets = meta.get("presets") or []
                presets_by_key = {p["key"]: p for p in presets}
                choice_key = f"gdc_{key_base}"
                w_key, h_key = f"gw_{key_base}", f"gh_{key_base}"

                options = [p["key"] for p in presets] + ["custom"]
                labels = {p["key"]: p["label"] for p in presets}
                labels["custom"] = "Custom dimensions"

                choice = st.radio(
                    "Dimension Option",
                    options=options,
                    format_func=lambda c: labels[c],
                    key=choice_key,
                    on_change=_on_generic_dim_preset_change,
                    args=(line.item_id, line.recipe_code, choice_key, w_key, h_key, presets_by_key),
                )
                if choice == "custom":
                    default_w = float(presets[0]["width"]) if presets else 1.0
                    col1, col2 = st.columns(2)
                    col1.number_input(
                        "Width (m)", min_value=0.1, value=default_w, step=0.5, key=w_key,
                    )
                    col2.number_input(
                        "Height / Depth (m)", min_value=0.1, value=1.0, step=0.5, key=h_key,
                    )
                    st.caption("Enter both values, then click Apply Dimensions below.")
                if st.button("Apply Dimensions", key=f"btn_{key_base}_dim"):
                    if choice == "custom":
                        w, h = st.session_state.get(w_key), st.session_state.get(h_key)
                        if w and h:
                            _apply_resolution(build_generic_dimension_resolution(
                                line.item_id, line.recipe_code, Decimal(str(w)), Decimal(str(h)),
                            ))
                            st.rerun()
                    else:
                        preset = presets_by_key.get(choice)
                        if preset:
                            _apply_resolution(build_generic_dimension_resolution(
                                line.item_id, line.recipe_code,
                                Decimal(str(preset["width"])), Decimal(str(preset["height"])),
                                note=preset["label"],
                            ))
                            st.rerun()

            elif ptype == "range":
                min_q, max_q = meta["min"], meta["max"]
                choice_key, custom_key = f"gqc_{key_base}", f"gqv_{key_base}"
                choice = st.radio(
                    "Quantity Option",
                    options=["use_min", "use_max", "custom"],
                    format_func=lambda c: {
                        "use_min": f"Use {min_q}",
                        "use_max": f"Use {max_q}",
                        "custom": "Custom quantity",
                    }[c],
                    key=choice_key,
                    on_change=_on_generic_qty_range_change,
                    args=(line.item_id, line.recipe_code, choice_key, custom_key, min_q, max_q),
                )
                if choice == "custom":
                    st.number_input(
                        "Quantity", min_value=1, value=int(max_q), step=1, key=custom_key,
                        on_change=_on_generic_qty_range_change,
                        args=(line.item_id, line.recipe_code, choice_key, custom_key, min_q, max_q),
                    )
                if st.button("Apply Quantity", key=f"btn_{key_base}_qtyrange"):
                    val = {"use_min": min_q, "use_max": max_q}.get(choice)
                    if choice == "custom":
                        c = st.session_state.get(custom_key)
                        val = Decimal(str(c)) if c else None
                    if val:
                        _apply_resolution(build_generic_quantity_resolution(line.item_id, line.recipe_code, val))
                        st.rerun()

            elif ptype == "approx":
                stated = meta["stated"]
                choice_key, custom_key = f"gac_{key_base}", f"gav_{key_base}"
                choice = st.radio(
                    "Quantity Option",
                    options=["confirm", "custom"],
                    format_func=lambda c: {
                        "confirm": f"Confirm {stated} as stated in brief",
                        "custom": "Custom quantity",
                    }[c],
                    key=choice_key,
                    on_change=_on_generic_qty_approx_change,
                    args=(line.item_id, line.recipe_code, choice_key, custom_key, stated),
                )
                if choice == "custom":
                    st.number_input(
                        "Quantity", min_value=1, value=int(stated), step=1, key=custom_key,
                        on_change=_on_generic_qty_approx_change,
                        args=(line.item_id, line.recipe_code, choice_key, custom_key, stated),
                    )
                if st.button("Apply Quantity", key=f"btn_{key_base}_qtyapprox"):
                    val = stated if choice == "confirm" else None
                    if choice == "custom":
                        c = st.session_state.get(custom_key)
                        val = Decimal(str(c)) if c else None
                    if val:
                        _apply_resolution(build_generic_quantity_resolution(line.item_id, line.recipe_code, val))
                        st.rerun()

            elif ptype == "pricing":
                cost_basis = meta.get("cost_basis")
                choice_key, custom_key = f"gpc_{key_base}", f"gpv_{key_base}"
                options = ["pass_through", "margin_30", "custom"]
                choice = st.radio(
                    "Pricing Option",
                    options=options,
                    format_func=lambda c: {
                        "pass_through": (
                            f"Pass-through at cost: SAR {cost_basis:,.2f} (0% margin)"
                            if cost_basis else "Pass-through at cost"
                        ),
                        "margin_30": (
                            f"Apply 30% margin (Sell: SAR {(cost_basis / Decimal('0.70')):,.2f})"
                            if cost_basis else "Apply 30% margin"
                        ),
                        "custom": "Custom selling price",
                    }[c],
                    key=choice_key,
                    on_change=_on_generic_price_change,
                    args=(line.item_id, line.recipe_code, choice_key, custom_key, cost_basis),
                )
                if choice == "custom":
                    default_price = float(cost_basis) if cost_basis else 1.0
                    st.number_input(
                        "Selling Price (SAR)", min_value=1.0, value=default_price, step=100.0, key=custom_key,
                        on_change=_on_generic_price_change,
                        args=(line.item_id, line.recipe_code, choice_key, custom_key, cost_basis),
                    )
                if st.button("Apply Pricing", key=f"btn_{key_base}_price"):
                    if choice == "pass_through" and cost_basis:
                        _apply_resolution(build_generic_pricing_resolution(
                            line.item_id, line.recipe_code, unit_price_sar=cost_basis, margin_pct=Decimal("0"),
                        ))
                        st.rerun()
                    elif choice == "margin_30" and cost_basis:
                        sell = cost_basis / Decimal("0.70")
                        _apply_resolution(build_generic_pricing_resolution(
                            line.item_id, line.recipe_code, unit_price_sar=sell, margin_pct=Decimal("30"),
                        ))
                        st.rerun()
                    elif choice == "custom":
                        p = st.session_state.get(custom_key)
                        if p:
                            _apply_resolution(build_generic_pricing_resolution(
                                line.item_id, line.recipe_code, unit_price_sar=Decimal(str(p)),
                            ))
                            st.rerun()

            elif ptype == "hard_unmatched":
                st.info(
                    "No catalog recipe exists for this item and it cannot be auto-priced. "
                    "It must be quoted manually outside this system, or excluded from this quote."
                )
                if st.button("Exclude from quote", key=f"btn_{key_base}_exclude"):
                    _apply_resolution(build_generic_exclusion(line.item_id, line.recipe_code))
                    st.rerun()

            else:
                st.info(
                    "This item needs manual scope decisions that this tool cannot resolve "
                    "automatically yet (see notes above). Exclude it here once you've decided "
                    "how to handle it, or leave it pending and adjust the final proposal manually."
                )
                if st.button("Exclude from quote", key=f"btn_{key_base}_exclude_fallback"):
                    _apply_resolution(build_generic_exclusion(line.item_id, line.recipe_code))
                    st.rerun()

    if resolutions.resolutions:
        if st.button("Reset all human resolutions to initial state", key="btn_reset_resolutions"):
            _clear_all_resolutions()
            st.session_state["_panel_meta_cache"] = {}
            st.info("Resolutions cleared. Quote recalculated from initial extraction.")
            st.rerun()


def _display_quote_lines(quote) -> None:
    st.subheader("Internal quote line breakdown")

    rows = []
    for line in quote.lines:
        if "golden falcon" in line.description.lower():
            continue

        rows.append(
            {
                "Type": _line_kind_label(line),
                "Item": line.description,
                "Recipe": line.recipe_code or "—",
                "Req Qty": _format_requested_quantity(line),
                "Billable Qty": _format_quantity(line),
                "Unit": line.unit,
                "Material": _format_money(line.material_cost_sar),
                "Labour": _format_money(line.labour_cost_sar),
                "Equipment": _format_money(line.equipment_cost_sar),
                "Margin %": (
                    f"{line.margin_pct:.1f}%" if line.margin_pct is not None else "—"
                ),
                "Margin SAR": _format_money(line.margin_amount_sar),
                "Unit sell": _format_money(line.unit_price_sar),
                "Line total": (
                    _format_money(line.line_total_sar)
                    if line.line_total_sar is not None
                    else (
                        f"{_format_money(line.min_line_total_sar)} – "
                        f"{_format_money(line.max_line_total_sar)}"
                        if line.min_line_total_sar is not None
                        else "— (Pending Review)"
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
    st.subheader("Executive KPI Summary")
    col1, col2, col3, col4 = st.columns(4)

    status_color = "🟠" if quote.status == QuoteStatus.REQUIRES_REVIEW else ("🟢" if quote.status == QuoteStatus.READY else "🔴")
    col1.metric("Quote Status", f"{status_color} {quote.status.value.upper()}")

    if quote.subtotal_sar is not None:
        col2.metric("Subtotal (SAR)", f"SAR {_format_money(quote.subtotal_sar)}")
    elif quote.min_subtotal_sar is not None and quote.max_subtotal_sar is not None:
        col2.metric(
            "Subtotal Range",
            f"SAR {_format_money(quote.min_subtotal_sar)} – {_format_money(quote.max_subtotal_sar)}",
        )
    else:
        col2.metric("Subtotal (SAR)", "Pending Review")

    col3.metric("Scope Line Items", len(quote.lines))
    col4.metric("Active Flags", len(quote.review_flags))

    if quote.notes:
        st.caption(quote.notes)


def _display_client_proposal_tab(quote) -> None:
    st.subheader("Official Client Proposal Generator")
    st.caption("Generate a formatted commercial proposal document ready to send to the client.")

    # Proposal Metadata Editor
    with st.expander("Proposal Client & Event Details", expanded=False):
        c1, c2 = st.columns(2)
        default_client = getattr(st.session_state, "client_name", None) or (st.session_state.get("client_name") if isinstance(st.session_state, dict) else "")
        default_event = getattr(st.session_state, "event_name", None) or (st.session_state.get("event_name") if isinstance(st.session_state, dict) else "")
        default_venue = getattr(st.session_state, "venue", None) or (st.session_state.get("venue") if isinstance(st.session_state, dict) else "")
        client_name = c1.text_input("Client Organization", value=default_client, placeholder="Enter client organization name", key="prop_client")
        event_name = c2.text_input("Event Project Name", value=default_event, placeholder="Enter event/project name", key="prop_event")
        venue = c1.text_input("Venue / Location", value=default_venue, placeholder="Enter venue or location", key="prop_venue")
        st.session_state["client_name"] = client_name
        st.session_state["event_name"] = event_name
        st.session_state["venue"] = venue

    if not (st.session_state["client_name"] and st.session_state["event_name"] and st.session_state["venue"]):
        st.warning(
            "⚠️ Client Organization, Event Project Name, and/or Venue are still blank. "
            "Fill them in above (expand 'Proposal Client & Event Details') before sending this "
            "proposal to a client."
        )


    catalog = RecipeCatalog.load(CATALOG_PATH)
    proposal_gen = ProposalGenerator(catalog)
    current_extraction = getattr(st.session_state, "extraction", None) or (st.session_state.get("extraction") if isinstance(st.session_state, dict) else None)
    proposal: OfficialProposal = proposal_gen.build_proposal(
        quote=quote,
        extraction=current_extraction,
        client_name=st.session_state["client_name"],
        event_name=st.session_state["event_name"],
        venue=st.session_state["venue"],
    )

    # Summary Cards for Proposal
    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("Net Scope Subtotal", f"SAR {_format_money(proposal.subtotal_sar)}")
    pc2.metric("VAT (15%)", f"SAR {_format_money(proposal.vat_amount_sar)}")
    pc3.metric("Total Investment (incl. VAT)", f"SAR {_format_money(proposal.total_with_vat_sar)}")

    # Category Quick Overview
    st.markdown("#### Scope of Work Categories")
    cat_cols = st.columns(len(proposal.groups) if proposal.groups else 1)
    for idx, group in enumerate(proposal.groups):
        with cat_cols[idx % len(cat_cols)]:
            st.metric(
                label=f"{group.category_icon} {group.category_name}",
                value=f"SAR {_format_money(group.subtotal_sar)}",
                delta=f"{len(group.items)} items",
            )

    html_content = proposal_gen.generate_html(proposal)

    st.markdown("---")
    st.markdown("#### Document Actions")
    st.download_button(
        label="📄 Download Official Proposal (HTML — open and use Ctrl+P → Save as PDF)",
        data=html_content,
        file_name=f"{proposal.proposal_id}_Official_Proposal.html",
        mime="text/html",
        use_container_width=True,
    )

    st.markdown("#### Document Live Preview")
    components.html(html_content, height=880, scrolling=True)


def main() -> None:
    st.set_page_config(
        page_title="AI Quoting Tool — Studio Production",
        page_icon=str(LOGO_WATERMARK_PATH) if LOGO_WATERMARK_PATH.is_file() else "📋",
        layout="wide",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    _init_session_state()

    header_col1, header_col2 = st.columns([1, 8])
    with header_col1:
        if LOGO_HEADER_PATH.is_file():
            st.image(str(LOGO_HEADER_PATH), width=140)
    with header_col2:
        st.markdown('<div class="main-header">AI Quoting Tool & Proposal Studio</div>', unsafe_allow_html=True)
        st.caption(
            "AI-assisted client brief extraction, deterministic quoting, interactive human review, and official proposal generation."
        )

    watermark_b64 = _image_to_base64(LOGO_WATERMARK_PATH)
    if watermark_b64:
        st.markdown(
            f'<img class="app-watermark" src="data:image/png;base64,{watermark_b64}" alt="Munginvest" />',
            unsafe_allow_html=True,
        )

    with st.sidebar:
        if LOGO_HEADER_PATH.is_file():
            st.image(str(LOGO_HEADER_PATH), use_container_width=True)
            st.markdown("---")
        st.header("LLM Configuration")
        selected_model = st.text_input("Gemini Model", value=DEFAULT_GEMINI_MODEL)
        env_key = os.getenv("GEMINI_API_KEY")
        selected_api_key: str | None = None
        if not env_key:
            st.warning("`GEMINI_API_KEY` is not set in environment.")
            selected_api_key = st.text_input("Enter Gemini API Key", type="password")
        else:
            st.caption("Using `GEMINI_API_KEY` from environment.")
        st.caption("Uses Google Gemini API (`google-genai` SDK) with structured JSON output.")

        st.header("Brief Ingestion")
        input_mode = st.radio(
            "Input Mode",
            options=["Upload PDF", "Paste Text", "Bundled File"],
            index=0,
            horizontal=True,
            key="ui_input_mode",
        )

        if input_mode == "Upload PDF":
            uploaded = st.file_uploader("Client brief PDF", type=["pdf"])
            if st.button("Generate quote from uploaded PDF", use_container_width=True):
                if uploaded is None:
                    st.warning("Upload a PDF first.")
                else:
                    with st.spinner("Extracting brief with Gemini and generating quote…"):
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                            tmp.write(uploaded.getvalue())
                            tmp_path = Path(tmp.name)
                        try:
                            success = _run_pipeline_on_pdf(
                                tmp_path,
                                uploaded.name,
                                provider="gemini",
                                model=selected_model,
                                api_key=selected_api_key,
                            )
                            if success:
                                st.success("Quote generated successfully.")
                        finally:
                            tmp_path.unlink(missing_ok=True)

        elif input_mode == "Paste Text":
            pasted_text = st.text_area(
                "Paste Client Brief (emails / notes / requirements)",
                height=180,
                placeholder="Paste the email thread or brief text here...",
                key="pasted_brief_text",
            )
            if st.button("Generate quote from pasted text", use_container_width=True):
                if not pasted_text.strip():
                    st.warning("Please paste brief text first.")
                else:
                    with st.spinner("Extracting brief with Gemini and generating quote…"):
                        success = _run_pipeline_on_text(
                            pasted_text,
                            source_label="Pasted Client Brief",
                            provider="gemini",
                            model=selected_model,
                            api_key=selected_api_key,
                        )
                        if success:
                            st.success("Quote generated from text.")

        else:
            if st.button("Use bundled sample brief (instant / no LLM)", use_container_width=True):
                _load_sample_brief()
                st.success("Sample brief loaded.")

            if DEFAULT_PDF.is_file():
                if st.button(f"Run {DEFAULT_PDF.name} with Gemini", use_container_width=True):
                    with st.spinner("Calling Gemini extraction on bundled PDF…"):
                        success = _run_pipeline_on_pdf(
                            DEFAULT_PDF,
                            str(DEFAULT_PDF),
                            provider="gemini",
                            model=selected_model,
                            api_key=selected_api_key,
                        )
                        if success:
                            st.success("Quote generated from bundled PDF.")

        if st.session_state.source_label:
            st.caption(f"Source: {st.session_state.source_label}")

    if st.session_state.quote is None:
        st.info("Upload a brief or load the sample brief to begin.")
        return

    quote = st.session_state.quote

    # Render Tabs
    tab1, tab2 = st.tabs(["⚙️ Internal Pricing & Review", "📄 Official Client Proposal"])

    with tab1:
        _display_totals(st.session_state.quote)
        _display_review_flags(st.session_state.quote)
        _display_resolution_panels()
        _display_quote_lines(st.session_state.quote)

        hidden_flags = [
            f for f in st.session_state.quote.review_flags
            if f.reason == ReviewReason.HIDDEN_INSTRUCTION_DETECTED
        ]
        if hidden_flags:
            st.error(
                "Critical Security Flag: adversarial instruction detected in brief "
                "(Golden Falcon Welcome Arch). It is excluded from quote lines and proposal."
            )

    with tab2:
        _display_client_proposal_tab(st.session_state.quote)


if __name__ == "__main__":
    main()
