"""Deterministic quantity calculation from extracted observations."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from app.catalog import Recipe, RecipeCatalog
from app.matcher import _is_headcount_item
from app.models import (
    BriefExtraction,
    DimensionKind,
    DimensionObservation,
    DimensionValue,
    ExtractedItem,
    ObservationStatus,
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
        # An item can now match to several catalog lines at once (bundled
        # components -- e.g. a stage produces stage-deck + ramp + stairs).
        # The old `{m.item_id: m for m in matches}` dict silently kept
        # only the LAST match seen for a given item_id and discarded the
        # rest, which is the reason bundled components (the ramp, the
        # sound system, dinner tables, the registration desk...) vanished
        # from priced quotes even after matcher.py stopped guessing.
        matches_by_item: dict[str, list[CatalogMatchResult]] = {}
        for m in matches:
            matches_by_item.setdefault(m.item_id, []).append(m)

        guest_count = self._resolve_guest_count(extraction)
        resolved_ids = resolutions.resolved_item_ids() if resolutions else set()
        results: list[QuantityResult] = []

        for item in extraction.items:
            item_matches = [
                m
                for m in matches_by_item.get(item.item_id, [])
                if m.match_status == MatchStatus.MATCHED and m.recipe_code
            ]
            if not item_matches:
                continue

            contradictory_dims = self._has_unresolved_dimension_contradiction(
                item, extraction, resolved_ids
            )

            for match in item_matches:
                recipe = self.catalog.get(match.recipe_code)
                if not recipe:
                    continue

                # Look up the resolution scoped to THIS specific catalog
                # product, not just the item as a whole. For bundled
                # items (e.g. item_breakout_av -> projector + sofa +
                # sound), a resolution set on one sibling (recipe_code
                # set) must never exclude, resize, or reprice another
                # sibling. Items that only ever produce one relevant line
                # (LED, stage, uplighters) keep working exactly as before
                # since their resolutions have recipe_code=None, which
                # get_for_recipe() treats as a broad/legacy match.
                resolution = (
                    resolutions.get_for_recipe(item.item_id, match.recipe_code)
                    if resolutions else None
                )
                if resolution and resolution.excluded:
                    continue

                # A human dimension/quantity resolution targets ONE
                # specific physical component of the item (e.g. the sqm
                # of the stage deck, or a unit count). It must only be
                # applied to the match whose catalog unit it actually
                # describes -- other bundled components of the same item
                # (the ramp, the stairs...) still need their own normal
                # calculation, not the resolution meant for a sibling.
                if (
                    resolution
                    and resolution.width_m is not None
                    and resolution.height_m is not None
                    and recipe.unit == "sqm"
                ):
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

                if (
                    resolution
                    and resolution.quantity is not None
                    and recipe.unit in ("unit", "set")
                ):
                    results.append(
                        self._calculate_unit_from_resolved_quantity(
                            item,
                            recipe,
                            resolution.quantity,
                            resolution.note,
                        )
                    )
                    continue

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
            if _is_headcount_item(item):
                for qty_obs in item.quantities:
                    q = qty_obs.quantity
                    if q.max_value is not None:
                        return q.max_value
                    if q.value is not None:
                        return q.value
        # Fallback search
        for item in extraction.items:
            if "guest" in item.label.lower() and not any(k in item.label.lower() for k in ("chair", "table", "seat")):
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
                requested_quantity=Decimal("1"),
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
                requested_quantity=days,
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
        depth_obs = self._active_dimensions(item, DimensionKind.DEPTH)
        area_obs = self._active_dimensions(item, DimensionKind.AREA)

        all_linear_obs = width_obs + height_obs + length_obs + depth_obs

        if contradictory_dims:
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="sqm",
                requires_review=True,
                review_reason=ReviewReason.CONTRADICTORY_INSTRUCTIONS,
                calculation_notes="Unresolved dimension contradiction; sqm not calculated.",
                observation_ids_used=self._obs_ids(all_linear_obs + area_obs),
            )

        if area_obs:
            area_value = self._single_dimension_value(area_obs[-1].dimension)
            if area_value is not None:
                billable_qty = self._apply_min_order(area_value, recipe)
                note = "Explicit area from brief."
                if area_value < recipe.min_order_qty:
                    note = (
                        f"Requested: {area_value} sqm. Billable: {billable_qty} sqm "
                        f"({recipe.min_order_qty} sqm minimum order quantity applied)."
                    )
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="sqm",
                    requested_quantity=area_value,
                    calculated_quantity=billable_qty,
                    requires_review=False,
                    calculation_notes=note,
                    observation_ids_used=[area_obs[-1].observation_id],
                )

        width = self._resolve_dimension_value(width_obs)
        height = self._resolve_dimension_value(height_obs)
        length = self._resolve_dimension_value(length_obs)
        depth = self._resolve_dimension_value(depth_obs)

        # Resolve 2 linear dimensions
        dim1 = width if width is not None else length
        dim2 = height if height is not None else depth

        if dim1 is not None and dim2 is None:
            if width is not None and length is not None:
                dim2 = length
            elif height is not None and depth is not None:
                dim1 = height
                dim2 = depth

        # Also check if there are 2 separate dimension observations with positive values
        if (dim1 is None or dim2 is None) and len(all_linear_obs) >= 2:
            vals = [
                self._single_dimension_value(d.dimension)
                for d in all_linear_obs
                if self._single_dimension_value(d.dimension) is not None
            ]
            if len(vals) >= 2:
                dim1 = vals[0]
                dim2 = vals[1]

        if dim1 is not None and dim2 is not None:
            area = dim1 * dim2
            billable_qty = self._apply_min_order(area, recipe)
            note = f"Area from {dim1}m × {dim2}m = {area} sqm."
            if area < recipe.min_order_qty:
                note = (
                    f"Requested: {area} sqm ({dim1}m × {dim2}m). Billable: {billable_qty} sqm "
                    f"({recipe.min_order_qty} sqm minimum order quantity applied)."
                )
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="sqm",
                requested_quantity=area,
                calculated_quantity=billable_qty,
                requires_review=False,
                calculation_notes=note,
                observation_ids_used=self._obs_ids(all_linear_obs),
            )

        missing_dimension = any(
            d.dimension.kind in (DimensionKind.HEIGHT, DimensionKind.DEPTH) and d.dimension.value is None
            for d in (height_obs + depth_obs)
        )
        if dim1 is not None and missing_dimension:
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="sqm",
                requires_review=True,
                review_reason=ReviewReason.MISSING_DIMENSION,
                calculation_notes=f"Dimension stated ({dim1}m) but depth/height is missing; cannot calculate sqm.",
                observation_ids_used=self._obs_ids(all_linear_obs),
            )

        if dim1 is not None and dim2 is None:
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="sqm",
                requires_review=True,
                review_reason=ReviewReason.MISSING_DIMENSION,
                calculation_notes=f"Dimension stated ({dim1}m) but depth/height is missing; cannot calculate sqm.",
                observation_ids_used=self._obs_ids(all_linear_obs),
            )

        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit="sqm",
            requires_review=True,
            review_reason=ReviewReason.MISSING_DIMENSION,
            calculation_notes="Insufficient dimensions to calculate sqm.",
            observation_ids_used=self._obs_ids(all_linear_obs),
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

        # FRN-CTL-HIGH early intercept: if the ONLY quantity observations are
        # pax/headcount specs, we cannot calculate table count (no capacity
        # figure in the catalog).  This check MUST precede the generic
        # qty_observations loop because an approximate pax value (e.g.
        # is_approximate=True, value=80, unit=PAX) would otherwise fall through
        # to "q.value is not None → price 80 tables", which is wrong.
        if recipe.recipe_code == "FRN-CTL-HIGH" and qty_observations:
            pax_obs = [
                o for o in qty_observations
                if o.quantity.unit and o.quantity.unit.value == "pax"
            ]
            non_pax_obs = [
                o for o in qty_observations
                if not (o.quantity.unit and o.quantity.unit.value == "pax")
            ]
            if pax_obs and not non_pax_obs:
                note = (
                    f"Capacity spec only: '{pax_obs[-1].quantity.raw_text}'. "
                    "Catalog FRN-CTL-HIGH carries no seats-per-table figure; "
                    "human must confirm table count for this headcount."
                )
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="unit",
                    requires_review=True,
                    review_reason=ReviewReason.JUDGMENT_REQUIRED,
                    calculation_notes=note,
                    observation_ids_used=[pax_obs[-1].observation_id],
                )

        # A quantity observation belongs to ONE specific physical
        # component of this item. Since match_item() can now fan an item
        # out into several sibling catalog lines (e.g. breakout AV ->
        # projector + sofa + sound), item.quantities is shared across all
        # of them -- so a "set"-typed observation meant only for the sofa
        # ("maybe 2 of those sofa sets") must not leak into this generic
        # "unit" count and get misapplied to a completely different
        # sibling like the projector. Only unit-agnostic or explicitly
        # UNIT-typed observations are eligible here; set/sqm/package/day/
        # pax/etc-typed ones are handled by their own dedicated
        # calculators (_calculate_set_count, _calculate_sqm, ...).
        generic_unit_observations = [
            q for q in qty_observations
            if q.quantity.unit is None or q.quantity.unit.value == "unit"
        ]

        if generic_unit_observations:
            obs = generic_unit_observations[-1]
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
                        calculation_notes=(
                            f"Ambiguous quantity: {min_q} or {max_q} requested "
                            f"(catalog minimum order is {recipe.min_order_qty} units)."
                        ),
                        observation_ids_used=[obs.observation_id],
                    )
            if q.value is not None:
                billable_qty = self._apply_min_order(q.value, recipe)
                note = "Direct unit count from brief."
                if q.value < recipe.min_order_qty:
                    note = (
                        f"Requested: {q.value} units. Billable: {billable_qty} units "
                        f"(catalog minimum order {recipe.min_order_qty} units applied)."
                    )
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="unit",
                    requested_quantity=q.value,
                    calculated_quantity=billable_qty,
                    calculation_notes=note,
                    observation_ids_used=[obs.observation_id],
                )


        if recipe.recipe_code == "FRN-TBL-RND" and guest_count is not None:
            seats_per_table = self._extract_seats_per_table(item, default=Decimal("10"))
            tables = guest_count / seats_per_table
            tables = tables.to_integral_value(rounding=ROUND_CEILING)
            billable_qty = self._apply_min_order(tables, recipe)
            note = f"Tables for {guest_count} guests at {seats_per_table} per table = {tables} tables."
            if tables < recipe.min_order_qty:
                note += f" Billable: {billable_qty} tables (minimum order {recipe.min_order_qty} applied)."
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="unit",
                requested_quantity=tables,
                calculated_quantity=billable_qty,
                calculation_notes=note,
            )

        if recipe.recipe_code == "FRN-CHR-BQT" and guest_count is not None:
            billable_qty = self._apply_min_order(guest_count, recipe)
            note = f"Chairs for {guest_count} guests."
            if guest_count < recipe.min_order_qty:
                note += f" Billable: {billable_qty} chairs (minimum order {recipe.min_order_qty} applied)."
            return QuantityResult(
                item_id=item.item_id,
                recipe_code=recipe.recipe_code,
                catalog_unit="unit",
                requested_quantity=guest_count,
                calculated_quantity=billable_qty,
                calculation_notes=note,
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
            requested_quantity=Decimal("1"),
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
            if q.is_approximate and q.value is not None:
                # "Maybe 2 sofa sets" — approximate quantities need human confirmation
                # before being treated as firm. Price at face value but flag for review.
                raw_q = q.value
                billable_q = self._apply_min_order(raw_q, recipe)
                note = (
                    f"Approximate quantity '{q.raw_text}'; {raw_q} sets requested "
                    f"(billable: {billable_q} sets). Human confirmation required."
                )
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="set",
                    requested_quantity=raw_q,
                    calculated_quantity=billable_q,
                    requires_review=True,
                    review_reason=ReviewReason.APPROXIMATE_VALUE,
                    calculation_notes=note,
                    observation_ids_used=[obs.observation_id],
                )
            if q.value is not None:
                raw_q = q.value
                billable_q = self._apply_min_order(raw_q, recipe)
                note = "Direct set count from brief."
                if raw_q < recipe.min_order_qty:
                    note = f"Requested: {raw_q} sets. Billable: {billable_q} sets (minimum order {recipe.min_order_qty} applied)."
                return QuantityResult(
                    item_id=item.item_id,
                    recipe_code=recipe.recipe_code,
                    catalog_unit="set",
                    requested_quantity=raw_q,
                    calculated_quantity=billable_q,
                    calculation_notes=note,
                    observation_ids_used=[obs.observation_id],
                )

        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit="set",
            requested_quantity=Decimal("1"),
            calculated_quantity=self._apply_min_order(Decimal("1"), recipe),
            calculation_notes="Default single set.",
        )

    def _extract_seats_per_table(self, item: ExtractedItem, default: Decimal) -> Decimal:
        """Parse an explicit 'N per table' / 'N guests per table' /
        'seats N' figure from the item's own client_text. This must never
        be assumed -- the previous hardcoded '/10' silently ignored any
        brief that specified a different seating ratio (e.g. '8 guests
        per table'), which only went unnoticed because the original test
        brief happened to also say 10."""
        import re

        pattern = re.compile(
            r"(\d+)\s*(?:guests?|people|pax|persons?)?\s*(?:per|/|each)\s*table"
            r"|table[s]?\s*(?:of|for|seat(?:s|ing)?)\s*(\d+)"
            r"|seat(?:s|ing)?\s*(\d+)\s*(?:per|/)\s*table"
        )
        for desc in item.descriptions:
            if desc.status == ObservationStatus.CANCELLED:
                continue
            match = pattern.search(desc.client_text.lower())
            if match:
                value = next(g for g in match.groups() if g is not None)
                return Decimal(value)
        return default

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
        billable_qty = self._apply_min_order(area, recipe)
        calc_note = f"Human-resolved dimensions: {width_m}m × {height_m}m = {area} sqm."
        if area < recipe.min_order_qty:
            calc_note += f" Billable: {billable_qty} sqm (minimum order {recipe.min_order_qty} applied)."
        if note:
            calc_note += f" {note}"
        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit="sqm",
            requested_quantity=area,
            calculated_quantity=billable_qty,
            requires_review=False,
            calculation_notes=calc_note.strip(),
        )

    def _calculate_unit_from_resolved_quantity(
        self,
        item: ExtractedItem,
        recipe: Recipe,
        quantity: Decimal,
        note: str,
    ) -> QuantityResult:
        billable_qty = self._apply_min_order(quantity, recipe)
        calc_note = f"Human-resolved quantity: {quantity} {recipe.unit}."
        if quantity < recipe.min_order_qty:
            calc_note += f" Billable: {billable_qty} {recipe.unit} (minimum order {recipe.min_order_qty} applied)."
        if note:
            calc_note += f" {note}"
        return QuantityResult(
            item_id=item.item_id,
            recipe_code=recipe.recipe_code,
            catalog_unit=recipe.unit,
            requested_quantity=quantity,
            calculated_quantity=billable_qty,
            requires_review=False,
            calculation_notes=calc_note.strip(),
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
