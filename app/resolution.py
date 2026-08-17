"""Human resolution of ambiguous extractions — deterministic, no LLM."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

LED_SCREEN_ITEM_ID = "item_main_led_screen"
UPLIGHTERS_ITEM_ID = "item_uplighters"
MAIN_STAGE_ITEM_ID = "item_main_stage"
HOLOGRAM_ITEM_ID = "item_hologram_box"
# The breakout AV item bundles THREE separate catalog products (projector,
# sofa sets, sound system) from one client sentence. Resolutions targeting
# it MUST set recipe_code to avoid a quantity meant for one product
# leaking onto its siblings.
BREAKOUT_AV_ITEM_ID = "item_breakout_av"
SOFA_RECIPE_CODE = "FRN-SOFA-LNG"


class ResolutionType(str, Enum):
    DIMENSION = "dimension"
    QUANTITY = "quantity"
    CUSTOM_PRICING = "custom_pricing"
    EXCLUSION = "exclusion"


class LedScreenResolutionChoice(str, Enum):
    """Supported resolution options for the LED screen contradiction."""

    ORIGINAL_6X3 = "original_6x3"
    RATIO_8X4 = "ratio_8x4"
    EXCLUDE = "exclude"
    CUSTOM = "custom"


class UplightersResolutionChoice(str, Enum):
    """Supported resolution options for uplighter quantity."""

    QTY_8 = "qty_8"
    QTY_10 = "qty_10"
    CUSTOM = "custom"


class StageResolutionChoice(str, Enum):
    """Supported resolution options for stage dimensions."""

    DIM_12X4 = "dim_12x4"
    DIM_12X6 = "dim_12x6"
    CUSTOM = "custom"
    EXCLUDE = "exclude"


class HologramResolutionChoice(str, Enum):
    """Supported resolution options for custom hologram pricing."""

    PASS_THROUGH = "pass_through"
    MARGIN_30 = "margin_30"
    CUSTOM_PRICE = "custom_price"
    EXCLUDE = "exclude"


class SofaResolutionChoice(str, Enum):
    """Supported resolution options for the breakout lounge sofa quantity."""

    CONFIRM_2 = "confirm_2"
    CUSTOM = "custom"
    EXCLUDE = "exclude"


class ItemResolution(BaseModel):
    """Human decision for a single extracted item.

    recipe_code optionally scopes this resolution to ONE specific catalog
    product within the item. This matters for bundled/fan-out items where
    one ExtractedItem produces several sibling quote lines (e.g. the
    breakout AV item produces a projector line AND a sofa line AND a
    sound line). Without this, a quantity override meant only for the
    sofa could silently also overwrite the projector's quantity, since
    both share unit="unit"/"set" and the same item_id. Leave it None for
    items that only ever produce one relevant line (LED, stage,
    uplighters) -- existing behaviour there is unaffected.
    """

    item_id: str
    recipe_code: Optional[str] = None
    resolution_type: ResolutionType = ResolutionType.DIMENSION
    choice: str
    width_m: Optional[Decimal] = None
    height_m: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    unit_price_sar: Optional[Decimal] = None
    margin_pct: Optional[Decimal] = None
    excluded: bool = False
    note: str = Field(default="", description="Optional reviewer note.")

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> ItemResolution:
        if self.choice == "exclude":
            self.excluded = True
            return self
        if self.resolution_type == ResolutionType.DIMENSION:
            if not self.excluded:
                if self.width_m is None or self.height_m is None:
                    raise ValueError("Dimension resolutions require width_m and height_m.")
                if self.width_m <= 0 or self.height_m <= 0:
                    raise ValueError("Dimensions must be positive.")
        elif self.resolution_type == ResolutionType.QUANTITY:
            if not self.excluded:
                if self.quantity is None or self.quantity <= 0:
                    raise ValueError("Quantity resolution requires positive quantity.")
        return self


class ResolutionSet(BaseModel):
    """Collection of human resolutions applied before quote recalculation."""

    resolutions: list[ItemResolution] = Field(default_factory=list)

    def get(self, item_id: str) -> ItemResolution | None:
        for resolution in self.resolutions:
            if resolution.item_id == item_id:
                return resolution
        return None

    def get_for_recipe(self, item_id: str, recipe_code: str) -> ItemResolution | None:
        """Find the resolution scoped to one specific catalog product
        within a (possibly bundled/fan-out) item. Prefers an exact
        recipe_code match; falls back to a resolution with no
        recipe_code set (broad/legacy behaviour, e.g. LED/stage/
        uplighters) only when no more specific match exists."""
        specific: ItemResolution | None = None
        generic: ItemResolution | None = None
        for resolution in self.resolutions:
            if resolution.item_id != item_id:
                continue
            if resolution.recipe_code == recipe_code:
                specific = resolution
            elif resolution.recipe_code is None and generic is None:
                generic = resolution
        return specific or generic

    def resolved_item_ids(self) -> set[str]:
        return {r.item_id for r in self.resolutions}


def build_led_resolution(
    choice: LedScreenResolutionChoice | str,
    custom_width_m: Decimal | None = None,
    custom_height_m: Decimal | None = None,
    note: str = "",
) -> ItemResolution:
    """Build a validated LED screen resolution from a UI choice."""

    choice_str = choice.value if isinstance(choice, Enum) else str(choice)

    if choice_str == LedScreenResolutionChoice.EXCLUDE.value:
        return ItemResolution(
            item_id=LED_SCREEN_ITEM_ID,
            resolution_type=ResolutionType.EXCLUSION,
            choice=choice_str,
            excluded=True,
            note=note or "LED screen line excluded by reviewer.",
        )

    if choice_str == LedScreenResolutionChoice.ORIGINAL_6X3.value:
        return ItemResolution(
            item_id=LED_SCREEN_ITEM_ID,
            resolution_type=ResolutionType.DIMENSION,
            choice=choice_str,
            width_m=Decimal("6"),
            height_m=Decimal("3"),
            note=note or "Reviewer chose original 6m × 3m specification (18 sqm).",
        )

    if choice_str == LedScreenResolutionChoice.RATIO_8X4.value:
        return ItemResolution(
            item_id=LED_SCREEN_ITEM_ID,
            resolution_type=ResolutionType.DIMENSION,
            choice=choice_str,
            width_m=Decimal("8"),
            height_m=Decimal("4"),
            note=note or "Reviewer chose 8m × 4m using original 2:1 width:height ratio (32 sqm).",
        )

    if choice_str == LedScreenResolutionChoice.CUSTOM.value:
        if custom_width_m is None or custom_height_m is None:
            raise ValueError("Custom resolution requires width and height.")
        return ItemResolution(
            item_id=LED_SCREEN_ITEM_ID,
            resolution_type=ResolutionType.DIMENSION,
            choice=choice_str,
            width_m=custom_width_m,
            height_m=custom_height_m,
            note=note or f"Reviewer chose custom {custom_width_m}m × {custom_height_m}m.",
        )

    raise ValueError(f"Unsupported resolution choice: {choice}")


def build_uplighters_resolution(
    choice: UplightersResolutionChoice | str,
    custom_quantity: Decimal | None = None,
    note: str = "",
) -> ItemResolution:
    """Build a validated uplighter quantity resolution."""

    choice_str = choice.value if isinstance(choice, Enum) else str(choice)

    if choice_str == UplightersResolutionChoice.QTY_8.value:
        return ItemResolution(
            item_id=UPLIGHTERS_ITEM_ID,
            resolution_type=ResolutionType.QUANTITY,
            choice=choice_str,
            quantity=Decimal("8"),
            note=note or "Reviewer selected 8 uplighters (catalog MOQ 10 billable).",
        )

    if choice_str == UplightersResolutionChoice.QTY_10.value:
        return ItemResolution(
            item_id=UPLIGHTERS_ITEM_ID,
            resolution_type=ResolutionType.QUANTITY,
            choice=choice_str,
            quantity=Decimal("10"),
            note=note or "Reviewer selected 10 uplighters.",
        )

    if choice_str == UplightersResolutionChoice.CUSTOM.value:
        if custom_quantity is None:
            raise ValueError("Custom uplighter resolution requires quantity.")
        return ItemResolution(
            item_id=UPLIGHTERS_ITEM_ID,
            resolution_type=ResolutionType.QUANTITY,
            choice=choice_str,
            quantity=custom_quantity,
            note=note or f"Reviewer selected {custom_quantity} uplighters.",
        )

    raise ValueError(f"Unsupported uplighter choice: {choice}")


def build_stage_resolution(
    choice: StageResolutionChoice | str,
    custom_width_m: Decimal | None = None,
    custom_depth_m: Decimal | None = None,
    note: str = "",
) -> ItemResolution:
    """Build a validated stage dimension resolution."""

    choice_str = choice.value if isinstance(choice, Enum) else str(choice)

    if choice_str == StageResolutionChoice.EXCLUDE.value:
        return ItemResolution(
            item_id=MAIN_STAGE_ITEM_ID,
            resolution_type=ResolutionType.EXCLUSION,
            choice=choice_str,
            excluded=True,
            note=note or "Main stage excluded by reviewer.",
        )

    if choice_str == StageResolutionChoice.DIM_12X4.value:
        return ItemResolution(
            item_id=MAIN_STAGE_ITEM_ID,
            resolution_type=ResolutionType.DIMENSION,
            choice=choice_str,
            width_m=Decimal("12"),
            height_m=Decimal("4"),
            note=note or "Reviewer confirmed stage dimensions: 12m × 4m (48 sqm / 48 decks).",
        )

    if choice_str == StageResolutionChoice.DIM_12X6.value:
        return ItemResolution(
            item_id=MAIN_STAGE_ITEM_ID,
            resolution_type=ResolutionType.DIMENSION,
            choice=choice_str,
            width_m=Decimal("12"),
            height_m=Decimal("6"),
            note=note or "Reviewer confirmed stage dimensions: 12m × 6m (72 sqm / 72 decks).",
        )

    if choice_str == StageResolutionChoice.CUSTOM.value:
        if custom_width_m is None or custom_depth_m is None:
            raise ValueError("Custom stage resolution requires width and depth.")
        return ItemResolution(
            item_id=MAIN_STAGE_ITEM_ID,
            resolution_type=ResolutionType.DIMENSION,
            choice=choice_str,
            width_m=custom_width_m,
            height_m=custom_depth_m,
            note=note or f"Reviewer specified custom stage: {custom_width_m}m × {custom_depth_m}m.",
        )

    raise ValueError(f"Unsupported stage choice: {choice}")


def build_hologram_resolution(
    choice: HologramResolutionChoice | str,
    custom_price_sar: Decimal | None = None,
    custom_margin_pct: Decimal | None = None,
    note: str = "",
) -> ItemResolution:
    """Build a validated custom pricing resolution for the hologram box."""

    choice_str = choice.value if isinstance(choice, Enum) else str(choice)

    if choice_str == HologramResolutionChoice.EXCLUDE.value:
        return ItemResolution(
            item_id=HOLOGRAM_ITEM_ID,
            resolution_type=ResolutionType.EXCLUSION,
            choice=choice_str,
            excluded=True,
            note=note or "Hologram box excluded by reviewer.",
        )

    if choice_str == HologramResolutionChoice.PASS_THROUGH.value:
        return ItemResolution(
            item_id=HOLOGRAM_ITEM_ID,
            resolution_type=ResolutionType.CUSTOM_PRICING,
            choice=choice_str,
            unit_price_sar=Decimal("14000"),
            margin_pct=Decimal("0"),
            note=note or "Reviewer approved pass-through pricing at cost: SAR 14,000 (0% margin).",
        )

    if choice_str == HologramResolutionChoice.MARGIN_30.value:
        cost = Decimal("14000")
        sell = cost / Decimal("0.70")  # ~20,000
        return ItemResolution(
            item_id=HOLOGRAM_ITEM_ID,
            resolution_type=ResolutionType.CUSTOM_PRICING,
            choice=choice_str,
            unit_price_sar=sell,
            margin_pct=Decimal("30"),
            note=note or "Reviewer confirmed 30% margin on SAR 14,000 cost basis (Sell: SAR 20,000).",
        )

    if choice_str == HologramResolutionChoice.CUSTOM_PRICE.value:
        if custom_price_sar is None:
            raise ValueError("Custom price resolution requires custom_price_sar.")
        cost = Decimal("14000")
        margin = ((custom_price_sar - cost) / custom_price_sar * Decimal("100")) if custom_price_sar > cost else Decimal("0")
        return ItemResolution(
            item_id=HOLOGRAM_ITEM_ID,
            resolution_type=ResolutionType.CUSTOM_PRICING,
            choice=choice_str,
            unit_price_sar=custom_price_sar,
            margin_pct=margin,
            note=note or f"Reviewer set custom selling price: SAR {custom_price_sar:,.2f}.",
        )

    raise ValueError(f"Unsupported hologram choice: {choice}")


def build_sofa_resolution(
    choice: SofaResolutionChoice | str,
    custom_quantity: Decimal | None = None,
    note: str = "",
) -> ItemResolution:
    """Build a validated sofa-set quantity resolution.

    recipe_code is always set to SOFA_RECIPE_CODE: item_breakout_av also
    produces a projector line and a sound-system line from the same
    client sentence, and this resolution must only ever touch the sofa
    line, never its siblings.
    """

    choice_str = choice.value if isinstance(choice, Enum) else str(choice)

    if choice_str == SofaResolutionChoice.EXCLUDE.value:
        return ItemResolution(
            item_id=BREAKOUT_AV_ITEM_ID,
            recipe_code=SOFA_RECIPE_CODE,
            resolution_type=ResolutionType.EXCLUSION,
            choice=choice_str,
            excluded=True,
            note=note or "Lounge sofa line excluded by reviewer.",
        )

    if choice_str == SofaResolutionChoice.CONFIRM_2.value:
        return ItemResolution(
            item_id=BREAKOUT_AV_ITEM_ID,
            recipe_code=SOFA_RECIPE_CODE,
            resolution_type=ResolutionType.QUANTITY,
            choice=choice_str,
            quantity=Decimal("2"),
            note=note or "Reviewer confirmed 2 sofa sets as stated in the brief.",
        )

    if choice_str == SofaResolutionChoice.CUSTOM.value:
        if custom_quantity is None:
            raise ValueError("Custom sofa resolution requires quantity.")
        return ItemResolution(
            item_id=BREAKOUT_AV_ITEM_ID,
            recipe_code=SOFA_RECIPE_CODE,
            resolution_type=ResolutionType.QUANTITY,
            choice=choice_str,
            quantity=custom_quantity,
            note=note or f"Reviewer set custom sofa quantity: {custom_quantity} sets.",
        )

    raise ValueError(f"Unsupported sofa choice: {choice}")


def build_generic_dimension_resolution(
    item_id: str,
    recipe_code: str | None,
    width_m: Decimal,
    height_m: Decimal,
    note: str = "",
) -> ItemResolution:
    """Generic dimension resolution for ANY sqm-unit line flagged with a
    missing or contradictory dimension -- works for any brief, not just
    the five items Nexus happened to raise."""
    return ItemResolution(
        item_id=item_id,
        recipe_code=recipe_code,
        resolution_type=ResolutionType.DIMENSION,
        choice="custom_dimension",
        width_m=width_m,
        height_m=height_m,
        note=note or f"Reviewer set dimensions: {width_m}m x {height_m}m.",
    )


def build_generic_quantity_resolution(
    item_id: str,
    recipe_code: str | None,
    quantity: Decimal,
    note: str = "",
) -> ItemResolution:
    """Generic quantity resolution for ANY unit/set-unit line flagged as
    an ambiguous range or an approximate value."""
    return ItemResolution(
        item_id=item_id,
        recipe_code=recipe_code,
        resolution_type=ResolutionType.QUANTITY,
        choice="custom_quantity",
        quantity=quantity,
        note=note or f"Reviewer confirmed quantity: {quantity}.",
    )


def build_generic_pricing_resolution(
    item_id: str,
    recipe_code: str | None,
    unit_price_sar: Decimal | None = None,
    margin_pct: Decimal | None = None,
    note: str = "",
) -> ItemResolution:
    """Generic custom-pricing resolution for ANY CUSTOM_ESTIMATE line
    (items not in the catalog, priced from a procurement reference)."""
    return ItemResolution(
        item_id=item_id,
        recipe_code=recipe_code,
        resolution_type=ResolutionType.CUSTOM_PRICING,
        choice="custom_pricing",
        unit_price_sar=unit_price_sar,
        margin_pct=margin_pct,
        note=note,
    )


def build_generic_exclusion(
    item_id: str,
    recipe_code: str | None = None,
    note: str = "",
) -> ItemResolution:
    """Generic exclusion for ANY line -- removes it from the quote
    without pricing it."""
    return ItemResolution(
        item_id=item_id,
        recipe_code=recipe_code,
        resolution_type=ResolutionType.EXCLUSION,
        choice="exclude",
        excluded=True,
        note=note or "Line excluded by reviewer.",
    )


def led_contradiction_pending(extraction) -> bool:
    """Return True when the LED width contradiction is still unresolved."""

    from app.models import ResolutionStatus

    for contradiction in extraction.contradictions:
        if contradiction.contradiction_id == "contradiction-led-width":
            return contradiction.resolution_status == ResolutionStatus.UNRESOLVED
        if LED_SCREEN_ITEM_ID in contradiction.item_ids:
            return contradiction.resolution_status == ResolutionStatus.UNRESOLVED

    return False
