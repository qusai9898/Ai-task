"""Deterministic pricing engine — no LLM involvement."""

from __future__ import annotations

from decimal import Decimal

from app.catalog import Recipe, RecipeCatalog
from app.models import ReviewReason
from app.quote_models import (
    PricedLine,
    QuantityResult,
    QuoteLineKind,
    QuoteLineStatus,
)


class PricingEngine:
    """Apply catalog costs and margins to calculated quantities."""

    def __init__(self, catalog: RecipeCatalog) -> None:
        self.catalog = catalog

    def price_quantity(
        self,
        quantity: QuantityResult,
        item_label: str,
    ) -> PricedLine:
        recipe = self.catalog.get(quantity.recipe_code)
        if not recipe:
            return PricedLine(
                item_id=quantity.item_id,
                recipe_code=quantity.recipe_code,
                description=item_label,
                unit=quantity.catalog_unit,
                status=QuoteLineStatus.UNMATCHED,
                line_kind=QuoteLineKind.UNRESOLVED,
                review_reasons=[ReviewReason.CATALOG_UNKNOWN],
                notes="Recipe code not found in catalog.",
            )

        if quantity.min_quantity is not None or quantity.max_quantity is not None:
            return self._price_range_quantity(quantity, recipe, item_label)

        if quantity.requires_review and quantity.calculated_quantity is None:
            return PricedLine(
                item_id=quantity.item_id,
                recipe_code=recipe.recipe_code,
                description=recipe.recipe_name,
                requested_quantity=quantity.requested_quantity,
                quantity=None,
                unit=recipe.unit,
                unit_cost_sar=recipe.unit_cost_sar,
                unit_price_sar=recipe.unit_price_sar,
                margin_pct=recipe.standard_margin_pct,
                status=QuoteLineStatus.REQUIRES_REVIEW,
                line_kind=QuoteLineKind.CATALOG,
                review_reasons=[quantity.review_reason or ReviewReason.OTHER],
                notes=quantity.calculation_notes,
            )

        if quantity.requires_review:
            return self._priced_line_with_review(quantity, recipe, item_label)

        if quantity.calculated_quantity is not None:
            return self._price_single_quantity(quantity, recipe, item_label)

        return PricedLine(
            item_id=quantity.item_id,
            recipe_code=recipe.recipe_code,
            description=recipe.recipe_name,
            unit=recipe.unit,
            status=QuoteLineStatus.REQUIRES_REVIEW,
            line_kind=QuoteLineKind.CATALOG,
            review_reasons=[quantity.review_reason or ReviewReason.OTHER],
            notes=quantity.calculation_notes,
        )

    def price_custom_line(
        self,
        item_id: str,
        description: str,
        unit: str,
        unit_cost_sar: Decimal,
        quantity: Decimal,
        margin_pct: Decimal | None = None,
        unit_price_sar: Decimal | None = None,
        notes: str | None = None,
    ) -> PricedLine:
        """Price a non-catalog line using a supplied cost basis (e.g. procurement reference)."""

        line_cost = unit_cost_sar * quantity

        if unit_price_sar is not None:
            unit_price = unit_price_sar
            line_total = unit_price * quantity
            margin_amount = line_total - line_cost
            calculated_margin_pct = (
                (margin_amount / line_total * Decimal("100")) if line_total > 0 else Decimal("0")
            )
            return PricedLine(
                item_id=item_id,
                description=description,
                requested_quantity=quantity,
                quantity=quantity,
                unit=unit,
                material_cost_sar=line_cost,
                labour_cost_sar=Decimal("0"),
                equipment_cost_sar=Decimal("0"),
                unit_cost_sar=unit_cost_sar,
                unit_price_sar=unit_price,
                margin_pct=calculated_margin_pct,
                margin_amount_sar=margin_amount,
                line_cost_sar=line_cost,
                line_total_sar=line_total,
                status=QuoteLineStatus.CUSTOM_ESTIMATE,
                line_kind=QuoteLineKind.CUSTOM_NOT_IN_CATALOG,
                notes=notes,
            )

        if margin_pct is not None:
            margin_fraction = margin_pct / Decimal("100")
            unit_price = unit_cost_sar / (Decimal("1") - margin_fraction)
            line_total = unit_price * quantity
            margin_amount = line_total - line_cost

            return PricedLine(
                item_id=item_id,
                description=description,
                requested_quantity=quantity,
                quantity=quantity,
                unit=unit,
                material_cost_sar=line_cost,
                labour_cost_sar=Decimal("0"),
                equipment_cost_sar=Decimal("0"),
                unit_cost_sar=unit_cost_sar,
                unit_price_sar=unit_price,
                margin_pct=margin_pct,
                margin_amount_sar=margin_amount,
                line_cost_sar=line_cost,
                line_total_sar=line_total,
                status=QuoteLineStatus.CUSTOM_ESTIMATE,
                line_kind=QuoteLineKind.CUSTOM_NOT_IN_CATALOG,
                notes=notes,
            )

        # Unconfirmed margin / price
        return PricedLine(
            item_id=item_id,
            description=description,
            requested_quantity=quantity,
            quantity=quantity,
            unit=unit,
            material_cost_sar=line_cost,
            labour_cost_sar=Decimal("0"),
            equipment_cost_sar=Decimal("0"),
            unit_cost_sar=unit_cost_sar,
            unit_price_sar=None,
            margin_pct=None,
            margin_amount_sar=None,
            line_cost_sar=line_cost,
            line_total_sar=None,
            status=QuoteLineStatus.CUSTOM_ESTIMATE,
            line_kind=QuoteLineKind.CUSTOM_NOT_IN_CATALOG,
            review_reasons=[ReviewReason.CATALOG_UNKNOWN, ReviewReason.JUDGMENT_REQUIRED],
            notes=notes or "CUSTOM / NOT IN CATALOG. Selling price and margin require human confirmation.",
        )

    def price_excluded_line(
        self,
        item_id: str,
        recipe_code: str,
        description: str,
        unit: str,
        note: str,
    ) -> PricedLine:
        return PricedLine(
            item_id=item_id,
            recipe_code=recipe_code,
            description=description,
            requested_quantity=Decimal("0"),
            quantity=Decimal("0"),
            unit=unit,
            status=QuoteLineStatus.EXCLUDED,
            line_kind=QuoteLineKind.CATALOG,
            notes=note,
        )

    def _cost_breakdown(self, recipe: Recipe, quantity: Decimal) -> dict[str, Decimal]:
        labour_unit = recipe.labour_hours_per_unit * recipe.labour_rate_sar_per_hr
        return {
            "material_cost_sar": recipe.material_cost_sar * quantity,
            "labour_cost_sar": labour_unit * quantity,
            "equipment_cost_sar": recipe.equipment_cost_sar * quantity,
        }

    def _price_single_quantity(
        self,
        quantity: QuantityResult,
        recipe: Recipe,
        item_label: str,
    ) -> PricedLine:
        qty = quantity.calculated_quantity or Decimal("0")
        unit_cost = recipe.unit_cost_sar
        unit_price = recipe.unit_price_sar
        line_cost = unit_cost * qty
        line_total = unit_price * qty
        margin_amount = line_total - line_cost
        breakdown = self._cost_breakdown(recipe, qty)

        return PricedLine(
            item_id=quantity.item_id,
            recipe_code=recipe.recipe_code,
            description=recipe.recipe_name,
            requested_quantity=quantity.requested_quantity,
            quantity=qty,
            unit=recipe.unit,
            material_cost_sar=breakdown["material_cost_sar"],
            labour_cost_sar=breakdown["labour_cost_sar"],
            equipment_cost_sar=breakdown["equipment_cost_sar"],
            unit_cost_sar=unit_cost,
            unit_price_sar=unit_price,
            margin_pct=recipe.standard_margin_pct,
            margin_amount_sar=margin_amount,
            line_cost_sar=line_cost,
            line_total_sar=line_total,
            status=QuoteLineStatus.PRICED,
            line_kind=QuoteLineKind.CATALOG,
            notes=quantity.calculation_notes,
        )

    def _price_range_quantity(
        self,
        quantity: QuantityResult,
        recipe: Recipe,
        item_label: str,
    ) -> PricedLine:
        unit_cost = recipe.unit_cost_sar
        unit_price = recipe.unit_price_sar
        min_qty = quantity.min_quantity or Decimal("0")
        max_qty = quantity.max_quantity or min_qty

        min_line_total = unit_price * min_qty
        max_line_total = unit_price * max_qty

        return PricedLine(
            item_id=quantity.item_id,
            recipe_code=recipe.recipe_code,
            description=recipe.recipe_name,
            requested_quantity=None,
            min_quantity=min_qty,
            max_quantity=max_qty,
            unit=recipe.unit,
            unit_cost_sar=unit_cost,
            unit_price_sar=unit_price,
            margin_pct=recipe.standard_margin_pct,
            line_cost_sar=None,
            min_line_total_sar=min_line_total,
            max_line_total_sar=max_line_total,
            status=QuoteLineStatus.REQUIRES_REVIEW,
            line_kind=QuoteLineKind.CATALOG,
            review_reasons=[quantity.review_reason or ReviewReason.AMBIGUOUS_QUANTITY],
            notes=quantity.calculation_notes,
        )

    def _priced_line_with_review(
        self,
        quantity: QuantityResult,
        recipe: Recipe,
        item_label: str,
    ) -> PricedLine:
        reasons = [quantity.review_reason or ReviewReason.OTHER]
        qty = quantity.calculated_quantity
        line_total = (recipe.unit_price_sar * qty) if qty is not None else None
        line_cost = (recipe.unit_cost_sar * qty) if qty is not None else None

        return PricedLine(
            item_id=quantity.item_id,
            recipe_code=recipe.recipe_code,
            description=recipe.recipe_name,
            requested_quantity=quantity.requested_quantity,
            quantity=qty,
            min_quantity=quantity.min_quantity,
            max_quantity=quantity.max_quantity,
            unit=recipe.unit,
            unit_cost_sar=recipe.unit_cost_sar,
            unit_price_sar=recipe.unit_price_sar,
            margin_pct=recipe.standard_margin_pct,
            line_cost_sar=line_cost,
            line_total_sar=line_total,
            status=QuoteLineStatus.REQUIRES_REVIEW,
            line_kind=QuoteLineKind.CATALOG,
            review_reasons=reasons,
            notes=quantity.calculation_notes,
        )
