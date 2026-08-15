"""Deterministic quantity calculation from extracted observations."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from app.catalog import Recipe, RecipeCatalog
from app.models import (
    BriefExtraction,
    DimensionKind,
    DimensionObservation,
    DimensionValue,
    ExtractedItem,
    ObservationStatus,
    QuantityObservation,
    ReviewReason,
    UnitOfMeasure,
)
from app.quote_models import CatalogMatchResult, MatchStatus, QuantityResult
from app.resolution import ResolutionSet


class QuantityCalculator:
    """Compute catalog quantities from extraction observations."""

    def __init__(self, catalog: RecipeCatalog) -> None:
        self.catalog = catalog

    def calculate_all(
        self,
        extraction: BriefExtraction,
        matches: list[CatalogMatchResult],
        resolutions: ResolutionSet | None = None,
    ) -> list[QuantityResult]:
        match_map = {m.item_id: m for m in matches}
        guest_count = self._resolve_guest_count(extraction)
        resolved_ids = resolutions.resolved_item_ids() if resolutions else set()
        results: list[QuantityResult] = []

        for item in extraction.items:
            match = match_map.get(item.item_id)
            if not match or match.match_status not in (MatchStatus.MATCHED,):
                continue
            if not match.recipe_code:
                continue
            recipe = self.catalog.get(match.recipe_code)
            if not recipe:
                continue

            resolution = resolutions.get(item.item_id) if resolutions else None
            if resolution and resolution.excluded:
                continue

            if resolution and resolution.width_m is not None and resolution.height_m is not None:
                results.append(
                    self._calculate_sqm_from_resolved_dimensions(
                        item,
                        recipe,
                        resolution.width_m,
                        resolution.height_m,
                        resolution.note,
                    )
                )
                continue

            contradictory_dims = self._has_unresolved_dimension_contradiction(
                item, extraction, resolved_ids
            )
            results.append(
                self._calculate_item_quantity(
                    item,
                    recipe,
                    extraction,
                    guest_count,
                    contradictory_dims,
                )
            )

        return results

    def _resolve_guest_count(self, extraction: BriefExtraction) -> Decimal | None:
        for item in extraction.items:
            if "guest" in item.label.lower() or item.item_id == "item-guest-count":
                for qty_obs in item.quantities:
                    q = qty_obs.quantity
                    if q.max_value is not None:
                        return q.max_value
                    if q.value is not None:
                        return q.value
        return None

    def _calculate_item_quantity(
        self,
        item: ExtractedItem,
        recipe: Recipe,
        extraction: BriefExtraction,
        guest_count: Decimal | None,
        contradictory_dims: bool,
    ) -> QuantityResult:
        unit = recipe.unit

        if unit == "sqm":
            return self._calculate_sqm(item, recipe, contradictory_dims)
        if unit == "unit":
            return self._calculate_unit_count(item, recipe, guest_count, contradictory_dims)
        if unit == "package":
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit=unit,
                calculated_quantity=Decimal("1"),
                calculation_notes="One package per scope area.",
            )
        if unit == "day":
            days = Decimal("1")
            if self._mentions_setup_day(extraction):
                days = Decimal("2")
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit=unit,
                calculated_quantity=days,
                calculation_notes="Event day plus setup day when brief mentions setup night before.",
            )
        if unit == "set":
            return self._calculate_set_count(item, recipe)

        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit=unit,
            requires_review=True,
            review_reason=ReviewReason.OTHER,
            calculation_notes=f"No quantity rule for catalog unit '{unit}'.",
        )

    def _calculate_sqm(
        self,
        item: ExtractedItem,
        recipe: Recipe,
        contradictory_dims: bool,
    ) -> QuantityResult:
        width_obs = self._active_dimensions(item, DimensionKind.WIDTH)
        height_obs = self._active_dimensions(item, DimensionKind.HEIGHT)
        length_obs = self._active_dimensions(item, DimensionKind.LENGTH)
        area_obs = self._active_dimensions(item, DimensionKind.AREA)

        if contradictory_dims:
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="sqm",
                requires_review=True,
                review_reason=ReviewReason.CONTRADICTORY_INSTRUCTIONS,
                calculation_notes="Unresolved dimension contradiction; sqm not calculated.",
                observation_ids_used=self._obs_ids(width_obs + height_obs + area_obs),
            )

        if area_obs:
            area_value = self._single_dimension_value(area_obs[-1].dimension)
            if area_value is not None:
                qty = self._apply_min_order(area_value, recipe)
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="sqm",
                    calculated_quantity=qty,
                    calculation_notes="Explicit area from brief.",
                    observation_ids_used=[area_obs[-1].observation_id],
                )

        width = self._resolve_dimension_value(width_obs)
        height = self._resolve_dimension_value(height_obs)
        length = self._resolve_dimension_value(length_obs)

        if width is not None and height is not None:
            area = width * height
            qty = self._apply_min_order(area, recipe)
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="sqm",
                calculated_quantity=qty,
                calculation_notes=f"Area from width {width}m × height {height}m.",
                observation_ids_used=self._obs_ids(width_obs + height_obs),
            )

        if length is not None and height is not None:
            area = length * height
            qty = self._apply_min_order(area, recipe)
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="sqm",
                calculated_quantity=qty,
                calculation_notes=f"Area from length {length}m × height {height}m.",
                observation_ids_used=self._obs_ids(length_obs + height_obs),
            )

        missing_height = any(
            d.dimension.kind == DimensionKind.HEIGHT and d.dimension.value is None
            for d in height_obs
        )
        if width is not None and missing_height:
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="sqm",
                requires_review=True,
                review_reason=ReviewReason.MISSING_DIMENSION,
                calculation_notes="Width stated but height missing; sqm not calculated.",
                observation_ids_used=self._obs_ids(width_obs + height_obs),
            )

        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit="sqm",
            requires_review=True,
            review_reason=ReviewReason.MISSING_DIMENSION,
            calculation_notes="Insufficient dimensions to calculate sqm.",
            observation_ids_used=self._obs_ids(width_obs + height_obs + length_obs),
        )

    def _calculate_unit_count(
        self,
        item: ExtractedItem,
        recipe: Recipe,
        guest_count: Decimal | None,
        contradictory_dims: bool,
    ) -> QuantityResult:
        qty_observations = [
            q for q in item.quantities if q.status != ObservationStatus.CANCELLED
        ]

        if qty_observations:
            obs = qty_observations[-1]
            q = obs.quantity
            if q.is_range or q.is_approximate:
                min_q = q.min_value
                max_q = q.max_value or q.value
                if min_q is not None and max_q is not None:
                    return QuantityResult(
                        item_id=item.item_id,
                        recipe_code=recipe.recipe_code,
                        catalog_unit="unit",
                        min_quantity=min_q,
                        max_quantity=max_q,
                        requires_review=True,
                        review_reason=ReviewReason.AMBIGUOUS_QUANTITY,
                        calculation_notes="Ambiguous quantity preserved as min/max range.",
                        observation_ids_used=[obs.observation_id],
                    )
            if q.value is not None:
                qty = self._apply_min_order(q.value, recipe)
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="unit",
                    calculated_quantity=qty,
                    calculation_notes="Direct unit count from brief.",
                    observation_ids_used=[obs.observation_id],
                )

        if recipe.recipe_code == "FRN-TBL-RND" and guest_count is not None:
            tables = guest_count / Decimal("10")
            tables = tables.to_integral_value(rounding=ROUND_CEILING)
            qty = self._apply_min_order(tables, recipe)
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="unit",
                calculated_quantity=qty,
                calculation_notes=f"Tables for {guest_count} guests at 10 per table.",
            )

        if recipe.recipe_code == "FRN-CHR-BQT" and guest_count is not None:
            qty = self._apply_min_order(guest_count, recipe)
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="unit",
                calculated_quantity=qty,
                calculation_notes=f"Chairs for {guest_count} guests.",
            )

        if recipe.recipe_code == "FRN-CTL-HIGH":
            for obs in qty_observations:
                if obs.quantity.value is not None:
                    qty = self._apply_min_order(obs.quantity.value, recipe)
                    return QuantityResult(
                        item_id=item.item_id,
                        recipe_code=recipe.recipe_code,
                        catalog_unit="unit",
                        calculated_quantity=qty,
                        calculation_notes="Cocktail tables from stated headcount.",
                        observation_ids_used=[obs.observation_id],
                    )

        if contradictory_dims:
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="unit",
                requires_review=True,
                review_reason=ReviewReason.CONTRADICTORY_INSTRUCTIONS,
                calculation_notes="Unresolved contradiction affects unit count.",
            )

        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit="unit",
            calculated_quantity=self._apply_min_order(Decimal("1"), recipe),
            calculation_notes="Default single unit; no explicit quantity in brief.",
        )

    def _calculate_set_count(self, item: ExtractedItem, recipe: Recipe) -> QuantityResult:
        qty_observations = [
            q for q in item.quantities if q.status != ObservationStatus.CANCELLED
        ]
        for obs in qty_observations:
            q = obs.quantity
            if q.is_range:
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="set",
                    min_quantity=q.min_value,
                    max_quantity=q.max_value,
                    requires_review=True,
                    review_reason=ReviewReason.AMBIGUOUS_QUANTITY,
                    calculation_notes="Ambiguous set count preserved as range.",
                    observation_ids_used=[obs.observation_id],
                )
            if q.value is not None:
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="set",
                    calculated_quantity=self._apply_min_order(q.value, recipe),
                    observation_ids_used=[obs.observation_id],
                )

        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit="set",
            calculated_quantity=self._apply_min_order(Decimal("1"), recipe),
            calculation_notes="Default single set.",
        )

    def _apply_min_order(self, quantity: Decimal, recipe: Recipe) -> Decimal:
        if quantity < recipe.min_order_qty:
            return recipe.min_order_qty
        return quantity

    def _active_dimensions(
        self, item: ExtractedItem, kind: DimensionKind
    ) -> list[DimensionObservation]:
        return [
            d
            for d in item.dimensions
            if d.dimension.kind == kind and d.status != ObservationStatus.CANCELLED
        ]

    def _resolve_dimension_value(
        self, observations: list[DimensionObservation]
    ) -> Decimal | None:
        if not observations:
            return None
        # Prefer latest observation (assumes extraction lists in chronological order)
        for obs in reversed(observations):
            value = self._single_dimension_value(obs.dimension)
            if value is not None:
                return value
        return None

    def _single_dimension_value(self, dimension: DimensionValue) -> Decimal | None:
        if dimension.value is None:
            return None
        if dimension.unit == UnitOfMeasure.METER or dimension.unit is None:
            return dimension.value
        return dimension.value

    def _calculate_sqm_from_resolved_dimensions(
        self,
        item: ExtractedItem,
        recipe: Recipe,
        width_m: Decimal,
        height_m: Decimal,
        note: str,
    ) -> QuantityResult:
        area = width_m * height_m
        qty = self._apply_min_order(area, recipe)
        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit="sqm",
            calculated_quantity=qty,
            requires_review=False,
            calculation_notes=(
                f"Human-resolved dimensions: {width_m}m × {height_m}m = {area} sqm. {note}"
            ).strip(),
        )

    def _has_unresolved_dimension_contradiction(
        self,
        item: ExtractedItem,
        extraction: BriefExtraction,
        resolved_item_ids: set[str] | None = None,
    ) -> bool:
        if resolved_item_ids and item.item_id in resolved_item_ids:
            return False
        item_contradictions = [
            c
            for c in extraction.contradictions
            if item.item_id in c.item_ids
            and c.contradiction_type.value == "dimension"
        ]
        return len(item_contradictions) > 0

    def _mentions_setup_day(self, extraction: BriefExtraction) -> bool:
        blob = " ".join(m.body.lower() for m in extraction.messages)
        return "setup night" in blob or "setup night before" in blob

    def _obs_ids(self, observations: list) -> list[str]:
        return [o.observation_id for o in observations]
