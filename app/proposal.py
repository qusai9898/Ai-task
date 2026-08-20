"""Client-facing proposal generation engine and professional HTML template."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from app.catalog import RecipeCatalog
from app.models import BriefExtraction
from app.quote_models import Quote, QuoteLineKind, QuoteLineStatus, QuoteStatus

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _logo_data_uri(filename: str) -> str | None:
    path = ASSETS_DIR / filename
    if not path.is_file():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@dataclass
class ProposalLineItem:
    item_id: Optional[str]
    category: str
    item_title: str
    specification: str
    quantity: Decimal
    unit: str
    unit_price_sar: Decimal
    total_sar: Decimal
    is_custom: bool = False


@dataclass
class ProposalCategoryGroup:
    category_name: str
    category_icon: str
    items: list[ProposalLineItem] = field(default_factory=list)
    subtotal_sar: Decimal = Decimal("0")


@dataclass
class OfficialProposal:
    proposal_id: str
    quote_id: str
    client_name: str
    event_name: str
    venue: str
    issue_date: str
    valid_until: str
    prepared_by: str
    status: QuoteStatus
    groups: list[ProposalCategoryGroup]
    subtotal_sar: Decimal
    vat_pct: Decimal = Decimal("15")
    vat_amount_sar: Decimal = Decimal("0")
    total_with_vat_sar: Decimal = Decimal("0")
    currency: str = "SAR"
    notes_and_inclusions: list[str] = field(default_factory=list)
    scope_gaps: list[str] = field(default_factory=list)
    terms_and_conditions: list[str] = field(default_factory=list)


CATEGORY_ICONS = {
    "Staging": "🎭",
    "LED & AV": "🖥️",
    "Lighting": "💡",
    "Furniture": "🪑",
    "Scenic & Fabrication": "🏗️",
    "Print & Branding": "🎨",
    "Power": "⚡",
    "Crew": "👷",
    "Custom Innovations": "✨",
    "Other Services": "📦",
}


class ProposalGenerator:
    """Transform internal Quote and Extraction models into a client-ready official proposal."""

    def __init__(self, catalog: RecipeCatalog) -> None:
        self.catalog = catalog

    def build_proposal(
        self,
        quote: Quote,
        extraction: Optional[BriefExtraction] = None,
        client_name: str = "Nexus Ventures",
        event_name: str = "Nexus Ventures Annual Forum",
        venue: str = "Main Ballroom & Breakout Suites",
    ) -> OfficialProposal:
        now = datetime.now(timezone.utc)
        issue_date_str = now.strftime("%d %B %Y")
        proposal_id = f"PROP-{now.strftime('%Y%m')}-{quote.quote_id[-6:].upper()}"

        groups_map: dict[str, ProposalCategoryGroup] = {}

        for line in quote.lines:
            if line.status in (QuoteLineStatus.EXCLUDED, QuoteLineStatus.UNMATCHED):
                continue
            if line.line_total_sar is None or line.quantity is None:
                continue

            # Determine category
            category = "Custom Innovations" if line.line_kind == QuoteLineKind.CUSTOM_NOT_IN_CATALOG else "Other Services"
            if line.recipe_code:
                recipe = self.catalog.get(line.recipe_code)
                if recipe and recipe.category:
                    category = recipe.category

            icon = CATEGORY_ICONS.get(category, "📦")

            if category not in groups_map:
                groups_map[category] = ProposalCategoryGroup(category_name=category, category_icon=icon)

            unit_price = line.unit_price_sar or (line.line_total_sar / line.quantity if line.quantity else Decimal("0"))
            
            # Format specification description
            spec = line.notes or ""
            if line.recipe_code:
                recipe = self.catalog.get(line.recipe_code)
                if recipe and recipe.notes:
                    spec = recipe.notes

            item = ProposalLineItem(
                item_id=line.item_id,
                category=category,
                item_title=line.description,
                specification=spec,
                quantity=line.quantity,
                unit=line.unit,
                unit_price_sar=unit_price,
                total_sar=line.line_total_sar,
                is_custom=(line.line_kind == QuoteLineKind.CUSTOM_NOT_IN_CATALOG),
            )
            groups_map[category].items.append(item)
            groups_map[category].subtotal_sar += line.line_total_sar

        ordered_categories = [
            "Staging",
            "LED & AV",
            "Lighting",
            "Furniture",
            "Print & Branding",
            "Scenic & Fabrication",
            "Power",
            "Crew",
            "Custom Innovations",
            "Other Services",
        ]

        sorted_groups = []
        for cat in ordered_categories:
            if cat in groups_map:
                sorted_groups.append(groups_map[cat])
        for cat, group in groups_map.items():
            if cat not in ordered_categories:
                sorted_groups.append(group)

        subtotal = sum((g.subtotal_sar for g in sorted_groups), Decimal("0"))
        vat_amount = (subtotal * Decimal("0.15")).quantize(Decimal("0.01"))
        total_with_vat = subtotal + vat_amount

        priced_categories = {g.category_name for g in sorted_groups if g.items}
        priced_titles_blob = " ".join(
            item.item_title.lower() for g in sorted_groups for item in g.items
        )

        def _has_priced_line(*keywords: str) -> bool:
            return any(kw in priced_titles_blob for kw in keywords)

        inclusions: list[str] = [
            "Complete delivery, professional rigging, installation, and technical "
            "setup prior to event commencement.",
            "De-rigging and venue clearance immediately following the conclusion "
            "of the event.",
        ]

        if _has_priced_line("technician", "stagehand", "crew"):
            inclusions.append(
                "Dedicated on-site technical crew for rehearsal and full-day "
                "event operation."
            )

        if _has_priced_line("ramp", "wheelchair"):
            inclusions.append(
                "Accessibility compliance including wheelchair ramp integration."
            )

        if _has_priced_line("generator", "backup power"):
            inclusions.append(
                "Backup power provision for AV systems in the main hall."
            )

        # Safety-relevant scope gaps: these must never be silently omitted.
        # If the brief raised a safety-relevant requirement (accessibility,
        # backup power) but it never made it into a priced quote line, the
        # proposal must say so explicitly rather than staying silent about
        # scope that was never actually confirmed or costed.
        safety_gaps: list[str] = []
        if extraction is not None:
            requested_ramp = any(
                "ramp" in req.description.lower() or "wheelchair" in req.description.lower()
                for item in extraction.items
                for req in item.requirements
            ) or any(
                "ramp" in req.description.lower() or "wheelchair" in req.description.lower()
                for req in extraction.global_requirements
            )
            if requested_ramp and not _has_priced_line("ramp", "wheelchair"):
                safety_gaps.append(
                    "Wheelchair accessibility ramp was requested in the brief but is "
                    "NOT YET included in this priced scope -- pending internal review."
                )

            requested_backup_power = any(
                "backup" in req.description.lower() and "power" in req.description.lower()
                for item in extraction.items
                for req in item.requirements
            ) or any(
                "backup" in req.description.lower() and "power" in req.description.lower()
                for req in extraction.global_requirements
            )
            if requested_backup_power and not _has_priced_line("generator", "backup power"):
                safety_gaps.append(
                    "Backup power for main hall AV was requested in the brief but is "
                    "NOT YET included in this priced scope -- pending internal review."
                )

        terms = [
            "Validity: This commercial proposal is valid for 14 calendar days from the issue date.",
            "Payment Terms: 50% advance upon contract signing; 50% upon completion of setup.",
            "Venue Access & Power: Client to facilitate venue access for setup the evening before show day.",
            "Cancellation: Cancellations within 72 hours of event are subject to a 50% mobilization fee.",
        ]

        return OfficialProposal(
            proposal_id=proposal_id,
            quote_id=quote.quote_id,
            client_name=client_name,
            event_name=event_name,
            venue=venue,
            issue_date=issue_date_str,
            valid_until="14 days from issue date",
            prepared_by="Apex Events & Production Solutions",
            status=quote.status,
            groups=sorted_groups,
            subtotal_sar=subtotal,
            vat_pct=Decimal("15"),
            vat_amount_sar=vat_amount,
            total_with_vat_sar=total_with_vat,
            notes_and_inclusions=inclusions,
            scope_gaps=safety_gaps,
            terms_and_conditions=terms,
        )

    def generate_html(self, proposal: OfficialProposal) -> str:
        """Generate a client-ready, styled HTML proposal suitable for export or printing."""

        category_sections_html = ""
        for group in proposal.groups:
            rows_html = ""
            for item in group.items:
                badge = '<span class="custom-badge">Custom Specification</span>' if item.is_custom else ''
                rows_html += f"""
                <tr>
                    <td class="item-cell">
                        <div class="item-title">{item.item_title} {badge}</div>
                        <div class="item-spec">{item.specification}</div>
                    </td>
                    <td class="num-cell">{item.quantity:,.2f}</td>
                    <td class="center-cell">{item.unit}</td>
                    <td class="num-cell">SAR {item.unit_price_sar:,.2f}</td>
                    <td class="num-cell bold">SAR {item.total_sar:,.2f}</td>
                </tr>
                """

            category_sections_html += f"""
            <div class="category-block">
                <div class="category-header">
                    <span class="category-icon">{group.category_icon}</span>
                    <span class="category-title">{group.category_name}</span>
                    <span class="category-subtotal">SAR {group.subtotal_sar:,.2f}</span>
                </div>
                <div class="table-scroll">
                <table class="proposal-table">
                    <thead>
                        <tr>
                            <th style="width: 45%;">Item & Scope Description</th>
                            <th style="width: 12%; text-align: right;">Quantity</th>
                            <th style="width: 10%; text-align: center;">Unit</th>
                            <th style="width: 15%; text-align: right;">Rate (SAR)</th>
                            <th style="width: 18%; text-align: right;">Total (SAR)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
                </div>
            </div>
            """

        inclusions_html = "".join([f"<li>{inc}</li>" for inc in proposal.notes_and_inclusions])

        header_logo_uri = _logo_data_uri("logo_pro.png")
        header_logo_html = (
            f'<img class="header-logo" src="{header_logo_uri}" alt="Munginvest" />'
            if header_logo_uri else ""
        )
        watermark_uri = _logo_data_uri("logo_watermark.png")
        watermark_html = (
            f'<img class="page-watermark" src="{watermark_uri}" alt="" />'
            if watermark_uri else ""
        )

        scope_gaps_html = ""
        if proposal.scope_gaps:
            gap_items = "".join([f"<li>{gap}</li>" for gap in proposal.scope_gaps])
            scope_gaps_html = f"""
            <div class="scope-gap-warning">
                <div class="scope-gap-title">⚠ Unpriced Safety-Relevant Requirements</div>
                <ul>{gap_items}</ul>
            </div>
            """
        terms_html = "".join([f"<li>{term}</li>" for term in proposal.terms_and_conditions])

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Commercial Proposal — {proposal.event_name}</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        body {{
            background-color: #f8fafc;
            color: #1e293b;
            padding: 40px 20px;
        }}
        .proposal-card {{
            max-width: 900px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05), 0 8px 10px -6px rgba(0, 0, 0, 0.01);
            padding: 48px;
            border: 1px solid #e2e8f0;
        }}
        .header-container {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: flex-start;
            gap: 16px;
            border-bottom: 2px solid #6366f1;
            padding-bottom: 24px;
            margin-bottom: 32px;
        }}
        .header-container > div:first-child {{
            flex: 3 1 240px;
            min-width: 0;
        }}
        .proposal-meta {{
            flex: 1 1 160px;
            min-width: 0;
            text-align: right;
        }}
        .brand-title {{
            font-size: 24px;
            font-weight: 800;
            color: #0f172a;
            letter-spacing: -0.5px;
        }}
        .brand-subtitle {{
            font-size: 13px;
            color: #64748b;
            margin-top: 4px;
            font-weight: 500;
        }}
        .prop-number {{
            font-size: 16px;
            font-weight: 700;
            color: #4f46e5;
        }}
        .prop-date {{
            font-size: 13px;
            color: #64748b;
            margin-top: 4px;
        }}
        .scope-gap-warning {{
            background: #fef2f2;
            border: 1px solid #fecaca;
            border-left: 4px solid #dc2626;
            border-radius: 6px;
            padding: 14px 18px;
            margin: 20px 0;
        }}
        .scope-gap-title {{
            font-size: 12px;
            font-weight: 700;
            color: #b91c1c;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }}
        .scope-gap-warning ul {{
            padding-left: 18px;
            font-size: 12px;
            color: #991b1b;
            line-height: 1.6;
        }}
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
            background: #f8fafc;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 36px;
            border: 1px solid #e2e8f0;
        }}
        .info-label {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #64748b;
            font-weight: 600;
            margin-bottom: 4px;
        }}
        .info-value {{
            font-size: 15px;
            color: #0f172a;
            font-weight: 600;
        }}
        .category-block {{
            margin-bottom: 32px;
            break-inside: avoid;
            page-break-inside: avoid;
        }}
        .category-header {{
            display: flex;
            align-items: center;
            padding: 10px 14px;
            background: #f1f5f9;
            border-radius: 6px;
            margin-bottom: 8px;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
            color-adjust: exact;
        }}
        .category-icon {{
            font-size: 16px;
            margin-right: 8px;
        }}
        .category-title {{
            font-size: 14px;
            font-weight: 700;
            color: #1e293b;
            flex-grow: 1;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .category-subtotal {{
            font-size: 13px;
            font-weight: 700;
            color: #475569;
        }}
        .proposal-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        .proposal-table th {{
            padding: 10px 12px;
            background: #ffffff;
            color: #64748b;
            font-weight: 600;
            border-bottom: 1px solid #cbd5e1;
            font-size: 12px;
        }}
        .proposal-table td {{
            padding: 12px;
            border-bottom: 1px solid #f1f5f9;
            color: #334155;
        }}
        .item-title {{
            font-weight: 600;
            color: #0f172a;
        }}
        .item-spec {{
            font-size: 11px;
            color: #64748b;
            margin-top: 2px;
        }}
        .custom-badge {{
            display: inline-block;
            font-size: 10px;
            padding: 2px 6px;
            background: #fef3c7;
            color: #92400e;
            border-radius: 4px;
            font-weight: 600;
            margin-left: 6px;
        }}
        .num-cell {{
            text-align: right;
        }}
        .center-cell {{
            text-align: center;
            color: #64748b;
        }}
        .bold {{
            font-weight: 700;
            color: #0f172a;
        }}
        .summary-container {{
            display: flex;
            justify-content: flex-end;
            margin-top: 36px;
            margin-bottom: 36px;
        }}
        .summary-box {{
            width: 340px;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
        }}
        .summary-row {{
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            color: #475569;
            margin-bottom: 10px;
        }}
        .summary-row.total {{
            border-top: 2px solid #cbd5e1;
            padding-top: 12px;
            margin-top: 12px;
            font-size: 16px;
            font-weight: 800;
            color: #4f46e5;
        }}
        .terms-section {{
            border-top: 1px solid #e2e8f0;
            padding-top: 28px;
            margin-top: 28px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 28px;
            font-size: 12px;
            color: #64748b;
        }}
        .terms-block h4 {{
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #334155;
            font-weight: 700;
            margin-bottom: 8px;
        }}
        .terms-block ul {{
            padding-left: 18px;
            line-height: 1.6;
        }}
        .signature-section {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            gap: 24px;
            margin-top: 48px;
            padding-top: 28px;
            border-top: 1px solid #e2e8f0;
        }}
        .sig-box {{
            flex: 1 1 220px;
            min-width: 0;
        }}
        .sig-line {{
            border-bottom: 1px solid #94a3b8;
            margin-top: 36px;
            margin-bottom: 6px;
        }}
        .sig-title {{
            font-size: 12px;
            font-weight: 600;
            color: #334155;
        }}
        .sig-subtitle {{
            font-size: 11px;
            color: #94a3b8;
        }}
        @media print {{
            html, body {{
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
                color-adjust: exact;
            }}
            body {{
                padding: 0;
                background: white;
            }}
            .proposal-card {{
                box-shadow: none;
                border: none;
                padding: 20px 0;
            }}
            .category-block {{
                break-inside: avoid;
                page-break-inside: avoid;
            }}
            .proposal-table tr {{
                break-inside: avoid;
                page-break-inside: avoid;
            }}
            .summary-container, .terms-section, .signature-section {{
                break-inside: avoid;
                page-break-inside: avoid;
            }}
        }}
        .header-logo {{
            height: 44px;
            width: auto;
            margin-bottom: 8px;
        }}
        .page-watermark {{
            position: absolute;
            top: 24px;
            right: 32px;
            width: 34px;
            height: auto;
            opacity: 0.15;
            pointer-events: none;
        }}
        .proposal-card {{
            position: relative;
        }}
        .table-scroll {{
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        @media (max-width: 640px) {{
            body {{
                padding: 16px 8px;
            }}
            .proposal-card {{
                padding: 20px 16px;
                border-radius: 8px;
            }}
            .header-container {{
                flex-direction: column;
                align-items: flex-start;
                gap: 14px;
            }}
            .proposal-meta {{
                text-align: left;
            }}
            .brand-title {{
                font-size: 19px;
            }}
            .info-grid {{
                grid-template-columns: 1fr;
                padding: 14px;
            }}
            .proposal-table {{
                font-size: 12px;
                min-width: 560px;
            }}
            .proposal-table th, .proposal-table td {{
                padding: 8px 6px;
            }}
            .summary-container {{
                justify-content: stretch;
            }}
            .summary-box {{
                width: 100%;
            }}
            .terms-section {{
                grid-template-columns: 1fr;
                gap: 20px;
            }}
            .signature-section {{
                flex-direction: column;
                gap: 28px;
            }}
            .sig-box {{
                width: 100%;
            }}
            .page-watermark {{
                width: 24px;
                top: 14px;
                right: 14px;
            }}
        }}
    </style>
</head>
<body>
    <div class="proposal-card">
        {watermark_html}
        <div class="header-container">
            <div>
                {header_logo_html}
                <div class="brand-title">{proposal.prepared_by}</div>
                <div class="brand-subtitle">Turnkey Event Production, Audio-Visual & Technical Staging</div>
            </div>
            <div class="proposal-meta">
                <div class="prop-number">{proposal.proposal_id}</div>
                <div class="prop-date">Date: {proposal.issue_date}</div>
                <div class="prop-date">Status: <strong>{proposal.status.value.upper()}</strong></div>
            </div>
        </div>

        <div class="info-grid">
            <div>
                <div class="info-label">Client Organization</div>
                <div class="info-value">{proposal.client_name}</div>
            </div>
            <div>
                <div class="info-label">Event Project</div>
                <div class="info-value">{proposal.event_name}</div>
            </div>
            <div>
                <div class="info-label">Venue Location</div>
                <div class="info-value">{proposal.venue}</div>
            </div>
            <div>
                <div class="info-label">Commercial Validity</div>
                <div class="info-value">{proposal.valid_until}</div>
            </div>
        </div>

        {scope_gaps_html}

        {category_sections_html}

        <div class="summary-container">
            <div class="summary-box">
                <div class="summary-row">
                    <span>Net Scope Subtotal</span>
                    <span class="bold">SAR {proposal.subtotal_sar:,.2f}</span>
                </div>
                <div class="summary-row">
                    <span>Standard VAT ({proposal.vat_pct}%)</span>
                    <span>SAR {proposal.vat_amount_sar:,.2f}</span>
                </div>
                <div class="summary-row total">
                    <span>Total Investment</span>
                    <span>SAR {proposal.total_with_vat_sar:,.2f}</span>
                </div>
            </div>
        </div>

        <div class="terms-section">
            <div class="terms-block">
                <h4>Inclusions & Production Services</h4>
                <ul>
                    {inclusions_html}
                </ul>
            </div>
            <div class="terms-block">
                <h4>Commercial Terms & Conditions</h4>
                <ul>
                    {terms_html}
                </ul>
            </div>
        </div>

        <div class="signature-section">
            <div class="sig-box">
                <div class="sig-title">Prepared by:</div>
                <div class="sig-line"></div>
                <div class="sig-title">Authorized Production Director</div>
                <div class="sig-subtitle">{proposal.prepared_by}</div>
            </div>
            <div class="sig-box">
                <div class="sig-title">Accepted & Approved by:</div>
                <div class="sig-line"></div>
                <div class="sig-title">Client Authorized Representative</div>
                <div class="sig-subtitle">{proposal.client_name}</div>
            </div>
        </div>
    </div>
</body>
</html>
"""
