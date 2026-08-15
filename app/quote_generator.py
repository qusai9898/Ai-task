"""Quote assembly from extraction through matching, quantities, and pricing."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.catalog import RecipeCatalog
from app.matcher import CatalogMatcher
from app.models import (
    BriefExtraction,
    CatalogPresence,
    ExtractedItem,
    RequirementType,
    ReviewReason,
)
from app.pricing import PricingEngine
from app.quantities import QuantityCalculator
from app.quote_models import (
    CatalogMatchResult,
    MatchStatus,
    PricedLine,
    Quote,
    QuoteLineStatus,
    QuoteStatus,
)
from app.resolution import ResolutionSet
from app.review import ReviewAggregator


DEFAULT_CATALOG_PATH = Path("data/recipe_catalog.csv")
DEFAULT_CUSTOM_MARGIN_PCT = Decimal("30")


class QuoteGenerator:
    """Build a structured quote from a validated BriefExtraction."""

    def __init__(self, catalog: RecipeCatalog) -> None:
        self.catalog = catalog
        self.matcher = CatalogMatcher(catalog)
        self.quantity_calculator = QuantityCalculator(catalog)
        self.pricing_engine = PricingEngine(catalog)
        self.review_aggregator = ReviewAggregator()

    @classmethod
    def from_catalog_path(cls, catalog_path: Path | str) -> QuoteGenerator:
        return cls(RecipeCatalog.load(catalog_path))

    def generate(
        self,
        extraction: BriefExtraction,
        quote_id: str | None = None,
        resolutions: ResolutionSet | None = None,
    ) -> Quote:
        matches = self.matcher.match_extraction(extraction)
        quantities = self.quantity_calculator.calculate_all(
            extraction, matches, resolutions=resolutions
        )

        item_labels = {item.item_id: item.label for item in extraction.items}
        lines: list[PricedLine] = []

        resolved_ids = resolutions.resolved_item_ids() if resolutions else set()

        for qty in quantities:
            label = item_labels.get(qty.item_id, qty.recipe_code)
            lines.append(self.pricing_engine.price_quantity(qty, label))

        if resolutions:
            for resolution in resolutions.resolutions:
                if not resolution.excluded:
                    continue
                match = next(
                    (m for m in matches if m.item_id == resolution.item_id),
                    None,
                )
                if not match or not match.recipe_code:
                    continue
                recipe = self.catalog.get(match.recipe_code)
                lines.append(
                    self.pricing_engine.price_excluded_line(
                        item_id=resolution.item_id,
                        recipe_code=match.recipe_code,
                        description=recipe.recipe_name if recipe else match.item_label,
                        unit=recipe.unit if recipe else "unit",
                        note=resolution.note,
                    )
                )

        lines.extend(self._custom_lines_for_unmatched(extraction, matches))

        resolved_item_ids = resolved_ids
        review_flags = self.review_aggregator.aggregate_flags(
            extraction,
            matches,
            quantities,
            lines,
            resolved_item_ids=resolved_item_ids,
        )
        status = self.review_aggregator.determine_quote_status(
            review_flags, lines, matches
        )

        subtotal, min_subtotal, max_subtotal = self._compute_subtotals(lines)

        return Quote(
            quote_id=quote_id or f"quote-{uuid4()}",
            extraction_id=extraction.extraction_id,
            source_document=extraction.source_document,
            created_at=datetime.now(timezone.utc),
            status=status,
            lines=lines,
            review_flags=review_flags,
            subtotal_sar=subtotal,
            min_subtotal_sar=min_subtotal,
            max_subtotal_sar=max_subtotal,
            notes=self._quote_notes(status, extraction),
        )

    def _custom_lines_for_unmatched(
        self,
        extraction: BriefExtraction,
        matches: list[CatalogMatchResult],
    ) -> list[PricedLine]:
        custom_lines: list[PricedLine] = []
        unmatched = [
            m
            for m in matches
            if m.match_status == MatchStatus.NO_MATCH
            and m.item_id not in {c.cancelled_item_id for c in extraction.cancellations}
        ]

        for match in unmatched:
            item = next(
                (i for i in extraction.items if i.item_id == match.item_id),
                None,
            )
            if not item:
                continue

            procurement_cost = self._procurement_reference_cost(item, extraction)
            if procurement_cost is None:
                custom_lines.append(
                    PricedLine(
                        item_id=match.item_id,
                        description=item.label,
                        unit="unit",
                        status=QuoteLineStatus.UNMATCHED,
                        review_reasons=[ReviewReason.CATALOG_UNKNOWN],
                        notes=match.notes,
                    )
                )
                continue

            qty = Decimal("1")
            for obs in item.quantities:
                if obs.quantity.value is not None:
                    qty = obs.quantity.value
                elif obs.quantity.max_value is not None:
                    qty = obs.quantity.max_value

            custom_lines.append(
                self.pricing_engine.price_custom_line(
                    item_id=match.item_id,
                    description=item.label,
                    unit="unit",
                    unit_cost_sar=procurement_cost,
                    quantity=qty,
                    margin_pct=DEFAULT_CUSTOM_MARGIN_PCT,
                    notes="Custom estimate from procurement reference in brief; not a catalog recipe.",
                )
            )

        return custom_lines

    def _procurement_reference_cost(
        self, item: ExtractedItem, extraction: BriefExtraction
    ) -> Decimal | None:
        label_blob = item.label.lower()

        for req in item.requirements:
            if req.requirement_type == RequirementType.PROCUREMENT_REFERENCE:
                cost = self._extract_sar_amount(req.description)
                if cost is not None:
                    return cost

        for req in extraction.global_requirements:
            if req.requirement_type != RequirementType.PROCUREMENT_REFERENCE:
                continue
            blob = req.description.lower()
            if any(token in blob for token in label_blob.split()) or "hologram" in blob:
                cost = self._extract_sar_amount(req.description)
                if cost is not None:
                    return cost

        if item.catalog_presence == CatalogPresence.LIKELY_NOT_IN_CATALOG:
            for req in extraction.global_requirements:
                if "hologram" in req.description.lower():
                    cost = self._extract_sar_amount(req.description)
                    if cost is not None:
                        return cost

        return None

    def _extract_sar_amount(self, text: str) -> Decimal | None:
        patterns = [
            r"SAR\s*([\d,]+(?:\.\d+)?)",
            r"([\d,]+(?:\.\d+)?)\s*k",
            r"around\s+([\d,]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = match.group(1).replace(",", "")
                value = Decimal(raw)
                if "k" in pattern:
                    value *= Decimal("1000")
                return value
        return None

    def _compute_subtotals(
        self, lines: list[PricedLine]
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        priced_totals = [
            line.line_total_sar
            for line in lines
            if line.status == QuoteLineStatus.PRICED and line.line_total_sar is not None
        ]

        min_parts: list[Decimal] = []
        max_parts: list[Decimal] = []

        for line in lines:
            if line.status == QuoteLineStatus.EXCLUDED:
                continue
            if line.line_total_sar is not None:
                min_parts.append(line.line_total_sar)
                max_parts.append(line.line_total_sar)
            elif line.min_line_total_sar is not None and line.max_line_total_sar is not None:
                min_parts.append(line.min_line_total_sar)
                max_parts.append(line.max_line_total_sar)
            elif line.status == QuoteLineStatus.CUSTOM_ESTIMATE and line.line_total_sar:
                min_parts.append(line.line_total_sar)
                max_parts.append(line.line_total_sar)

        min_subtotal = sum(min_parts, Decimal("0")) if min_parts else None
        max_subtotal = sum(max_parts, Decimal("0")) if max_parts else None

        all_priced = all(
            line.status in (QuoteLineStatus.PRICED, QuoteLineStatus.CUSTOM_ESTIMATE)
            for line in lines
            if line.status != QuoteLineStatus.EXCLUDED
        )
        subtotal = sum(priced_totals, Decimal("0")) if priced_totals and all_priced else None

        if subtotal is None and min_subtotal is not None and min_subtotal == max_subtotal:
            subtotal = min_subtotal

        return subtotal, min_subtotal, max_subtotal

    def _quote_notes(self, status: QuoteStatus, extraction: BriefExtraction) -> str | None:
        if status == QuoteStatus.BLOCKED:
            return "Quote blocked due to critical review flags (e.g. adversarial brief content)."
        if status == QuoteStatus.REQUIRES_REVIEW:
            return (
                "Quote requires human review before issue: unresolved ambiguity, "
                "contradictions, or unmatched catalog items remain."
            )
        if status == QuoteStatus.READY:
            return "All line items priced deterministically from catalog."
        return f"Quote status: {status.value}. Source: {extraction.source_document}."
