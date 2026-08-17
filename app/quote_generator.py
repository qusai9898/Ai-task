"""Quote assembly from extraction through matching, quantities, and pricing."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.catalog import RecipeCatalog
from app.matcher import CatalogMatcher, _is_headcount_item
from app.models import (
    BriefExtraction,
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
    QuantityResult,
    Quote,
    QuoteLineKind,
    QuoteLineStatus,
    QuoteStatus,
)
from app.resolution import ResolutionSet
from app.review import ReviewAggregator

DEFAULT_CATALOG_PATH = Path("data/recipe_catalog.csv")
DEFAULT_CUSTOM_MARGIN_PCT = Decimal("30")

# Only these requirement types represent PHYSICAL / OPERATIONAL SCOPE that can
# produce a catalog quote line or a mandatory-review line.
# All other types (TIMELINE, BUDGET_EXPECTATION, BRAND, PROCUREMENT_REFERENCE,
# OTHER) are project metadata, constraints, or administrative notes and must
# NEVER appear as quote line items.
QUOTEABLE_REQUIREMENT_TYPES: frozenset[RequirementType] = frozenset({
    RequirementType.POWER,
    RequirementType.ACCESSIBILITY,
    RequirementType.SAFETY,
    RequirementType.OPERATIONAL,
})


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
                # NOTE: an item_id can now map to several matches (e.g. the
                # main stage produces stage-deck + ramp + stairs lines). A
                # user excluding "the main stage" means excluding all of
                # its components, not just whichever one happened to be
                # first in the list -- so we iterate over every match for
                # this item_id, not just the first.
                item_matches = [
                    m for m in matches
                    if m.item_id == resolution.item_id
                    and (resolution.recipe_code is None or m.recipe_code == resolution.recipe_code)
                ]
                for match in item_matches:
                    if not match.recipe_code:
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

        lines.extend(self._custom_lines_for_unmatched(extraction, matches, resolutions=resolutions))
        lines.extend(self._lines_for_global_requirements(extraction, matches=matches))

        # Filter out any adversarial instructions from quote lines
        lines = [line for line in lines if not self._is_adversarial_line(line)]

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

    # Requirement descriptions that are administrative/informational rather
    # than a request for physical scope (venue confirmations, finance
    # formatting preferences, etc.) must not produce a "please price this"
    # line just because they happen to be tagged is_mandatory + OPERATIONAL.
    # POWER / ACCESSIBILITY / SAFETY requirements are inherently physical by
    # definition and always get the fallback line when unmatched; for the
    # broader OPERATIONAL type, only requirements that actually mention a
    # physical-need signal word get the fallback -- otherwise they're
    # silently (and correctly) treated as project metadata.
    _PHYSICAL_NEED_SIGNAL_WORDS: tuple[str, ...] = (
        "crew", "staff", "technician", "operator", "equipment",
        "install", "rig", "labour", "labor", "personnel", "hire",
        "generator", "power", "ramp", "access",
    )

    def _lines_for_global_requirements(self, extraction: BriefExtraction, matches: list[CatalogMatchResult] | None = None) -> list[PricedLine]:
        """
        Some client requirements are scope, not per-item detail -- e.g.
        "backup power for the main hall AV, non-negotiable" is captured
        by the extractor as a global_requirement, never as an
        ExtractedItem. That means it never reaches CatalogMatcher's
        item-based matching at all, so without this step a mandatory,
        client-flagged-critical requirement like backup power would
        never appear anywhere in the quote -- not priced, not even
        flagged. We run the same deterministic keyword rules used for
        items against each global requirement's free text; a match
        becomes a normal priced line, and a mandatory requirement with
        no catalog match still surfaces as REQUIRES_REVIEW rather than
        disappearing silently.

        Only requirements whose type is in QUOTEABLE_REQUIREMENT_TYPES
        (POWER, ACCESSIBILITY, SAFETY, OPERATIONAL) are even considered.
        Administrative/contextual types (TIMELINE, BUDGET_EXPECTATION,
        BRAND, PROCUREMENT_REFERENCE, OTHER) are silently skipped here --
        they are project metadata, not physical scope.

        If the same catalog code was already matched via a regular item,
        we skip the global-requirement line to avoid duplication.
        """
        # Build the set of recipe codes already covered by item-level matches
        already_covered_codes: set[str] = set()
        if matches:
            for m in matches:
                if m.match_status == MatchStatus.MATCHED and m.recipe_code:
                    already_covered_codes.add(m.recipe_code)

        setup_day_mentioned = self._mentions_setup_day(extraction)

        lines: list[PricedLine] = []
        for req in extraction.global_requirements:
            # Skip non-quoteable requirement types (venue info, timelines,
            # budget expectations, finance formatting notes, etc.)
            if req.requirement_type not in QUOTEABLE_REQUIREMENT_TYPES:
                continue

            synthetic_id = f"global-{req.observation_id}"
            # A single free-text requirement can genuinely name more than
            # one distinct catalog need (e.g. "crew and technicians" ->
            # both stagehands AND an AV technician). Use every match, not
            # just the first keyword rule that happens to fire.
            codes = self.matcher.match_text_all(req.description)
            new_codes = [c for c in codes if c not in already_covered_codes]

            if new_codes:
                for code in new_codes:
                    recipe = self.catalog.get(code)
                    if not recipe:
                        continue
                    already_covered_codes.add(code)
                    qty = Decimal("1")
                    notes = (
                        "Derived from a global requirement, not an itemized "
                        "brief line -- default single unit; confirm quantity."
                    )
                    if recipe.unit == "day" and setup_day_mentioned:
                        qty = Decimal("2")
                        notes = (
                            "Derived from a global requirement, not an itemized "
                            "brief line. Event day plus setup day when brief "
                            "mentions setup night before."
                        )
                    qty_result = QuantityResult(
                        item_id=synthetic_id,
                        recipe_code=code,
                        catalog_unit=recipe.unit,
                        requested_quantity=qty,
                        calculated_quantity=qty,
                        calculation_notes=notes,
                    )
                    lines.append(self.pricing_engine.price_quantity(qty_result, req.description))
                continue

            if not req.is_mandatory:
                continue

            is_inherently_physical = req.requirement_type in (
                RequirementType.POWER,
                RequirementType.ACCESSIBILITY,
                RequirementType.SAFETY,
            )
            mentions_physical_need = any(
                w in req.description.lower() for w in self._PHYSICAL_NEED_SIGNAL_WORDS
            )
            if not (is_inherently_physical or mentions_physical_need):
                # Administrative/informational note (e.g. venue confirmation,
                # "finance wants margins broken out per line") -- not a
                # request for physical scope, so it must not become a
                # "please price this" line.
                continue

            lines.append(
                PricedLine(
                    item_id=synthetic_id,
                    description=req.description,
                    unit="unit",
                    status=QuoteLineStatus.REQUIRES_REVIEW,
                    line_kind=QuoteLineKind.UNRESOLVED,
                    review_reasons=[ReviewReason.CATALOG_UNKNOWN],
                    notes=(
                        "Mandatory global requirement not represented in the "
                        "recipe catalog; human must add and price manually."
                    ),
                )
            )

        return lines


    def _mentions_setup_day(self, extraction: BriefExtraction) -> bool:
        blob = " ".join(m.body.lower() for m in extraction.messages)
        return "setup night" in blob or "setup night before" in blob

    def _is_adversarial_line(self, line: PricedLine) -> bool:
        desc = (line.description or "").lower()
        notes = (line.notes or "").lower()
        return "golden falcon" in desc or "golden falcon" in notes

    def _custom_lines_for_unmatched(
        self,
        extraction: BriefExtraction,
        matches: list[CatalogMatchResult],
        resolutions: ResolutionSet | None = None,
    ) -> list[PricedLine]:
        custom_lines: list[PricedLine] = []
        # IMPORTANT: AMBIGUOUS must be included here. A match with status
        # AMBIGUOUS (e.g. multiple validated LLM suggestions, or multiple
        # keyword rules matched) still gets a review flag from
        # ReviewAggregator, but unless it is also turned into a quote line
        # here it silently disappears from the final quote entirely --
        # the flag exists but no corresponding line ever renders. This was
        # the root cause of items such as the main stage vanishing between
        # the "flags" list and the priced line table.
        unmatched = [
            m
            for m in matches
            if m.match_status in (MatchStatus.NO_MATCH, MatchStatus.UNMATCHED, MatchStatus.AMBIGUOUS)
            and m.item_id not in {c.cancelled_item_id for c in extraction.cancellations}
        ]

        for match in unmatched:
            item = next(
                (i for i in extraction.items if i.item_id == match.item_id),
                None,
            )
            if not item:
                continue

            if _is_headcount_item(item):
                continue

            if "golden falcon" in item.label.lower():
                continue

            resolution = resolutions.get(item.item_id) if resolutions else None
            if resolution and resolution.excluded:
                continue

            if match.match_status == MatchStatus.AMBIGUOUS:
                # The deterministic matcher found more than one plausible
                # catalog code and correctly refused to guess. That is not
                # a reason to drop the line -- it must still show up so a
                # human can pick the right recipe code manually (there is
                # no generic "apply this recipe code" resolution mechanism
                # in resolution.py today; item-specific flows like
                # build_stage_resolution handle specific known items by
                # dimension/quantity instead, upstream of matching).
                candidates = match.candidate_codes or []
                custom_lines.append(
                    PricedLine(
                        item_id=match.item_id,
                        description=item.label,
                        unit="unit",
                        status=QuoteLineStatus.REQUIRES_REVIEW,
                        line_kind=QuoteLineKind.UNRESOLVED,
                        review_reasons=[ReviewReason.CATALOG_UNKNOWN],
                        notes=match.notes
                        or f"Multiple possible catalog matches ({', '.join(candidates)}); human must select the correct recipe code before this can be priced.",
                    )
                )
                continue

            procurement_cost = self._procurement_reference_cost(item, extraction)
            if procurement_cost is None:
                custom_lines.append(
                    PricedLine(
                        item_id=match.item_id,
                        description=item.label,
                        unit="unit",
                        status=QuoteLineStatus.UNMATCHED,
                        line_kind=QuoteLineKind.CUSTOM_NOT_IN_CATALOG,
                        review_reasons=[ReviewReason.CATALOG_UNKNOWN],
                        notes=match.notes or "CUSTOM / NOT IN CATALOG. No recipe code in catalog.",
                    )
                )
                continue

            qty = Decimal("1")
            for obs in item.quantities:
                if obs.quantity.value is not None:
                    qty = obs.quantity.value
                elif obs.quantity.max_value is not None:
                    qty = obs.quantity.max_value

            if resolution and (resolution.unit_price_sar is not None or resolution.margin_pct is not None):
                custom_lines.append(
                    self.pricing_engine.price_custom_line(
                        item_id=match.item_id,
                        description=item.label,
                        unit="unit",
                        unit_cost_sar=procurement_cost,
                        quantity=qty,
                        margin_pct=resolution.margin_pct,
                        unit_price_sar=resolution.unit_price_sar,
                        notes=resolution.note or f"CUSTOM / NOT IN CATALOG. Human-confirmed pricing (Cost: SAR {procurement_cost:,.2f}).",
                    )
                )
            else:
                # Do not automatically invent catalog margins for custom items
                custom_lines.append(
                    self.pricing_engine.price_custom_line(
                        item_id=match.item_id,
                        description=item.label,
                        unit="unit",
                        unit_cost_sar=procurement_cost,
                        quantity=qty,
                        margin_pct=None,
                        notes=f"CUSTOM / NOT IN CATALOG. Historical procurement reference: SAR {procurement_cost:,.2f}. Selling price and margin require human confirmation.",
                    )
                )

        return custom_lines

    def _procurement_reference_cost(
        self, item: ExtractedItem, extraction: BriefExtraction
    ) -> Decimal | None:
        label_blob = item.label.lower()

        # 1. Item-level requirements
        for req in item.requirements:
            if req.requirement_type == RequirementType.PROCUREMENT_REFERENCE:
                cost = self._extract_sar_amount(req.description)
                if cost is not None:
                    return cost

        # 2. Global requirements matching item-specific keywords
        item_words = [
            w for w in re.findall(r"[a-z0-9]+", label_blob)
            if len(w) > 3 and w not in ("room", "hall", "main", "custom", "item", "unit", "area", "line", "stage", "display")
        ]
        for req in extraction.global_requirements:
            if req.requirement_type != RequirementType.PROCUREMENT_REFERENCE:
                continue
            blob = req.description.lower()
            if any(w in blob for w in item_words):
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
            if line.status in (QuoteLineStatus.PRICED, QuoteLineStatus.CUSTOM_ESTIMATE)
            and line.line_total_sar is not None
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

        if subtotal is None and min_subtotal is not None and min_subtotal == max_subtotal and min_subtotal > Decimal("0"):
            subtotal = min_subtotal

        return subtotal, min_subtotal, max_subtotal

    def _quote_notes(self, status: QuoteStatus, extraction: BriefExtraction) -> str | None:
        if status == QuoteStatus.BLOCKED:
            return "Quote blocked due to critical error."
        if status == QuoteStatus.REQUIRES_REVIEW:
            return (
                "REVIEW REQUIRED: Resolvable lines calculated deterministically. "
                "Human review required for unresolved dimensions, ambiguous quantities, or custom pricing."
            )
        if status == QuoteStatus.READY:
            return "All line items priced deterministically."
        return f"Quote status: {status.value}. Source: {extraction.source_document}."
