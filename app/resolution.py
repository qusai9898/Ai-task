"""Human resolution of ambiguous extractions — deterministic, no LLM."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


LED_SCREEN_ITEM_ID = "item-main-led-screen"


class LedScreenResolutionChoice(str, Enum):
    """Supported resolution options for the LED screen contradiction."""

    ORIGINAL_6X3 = "original_6x3"
    RATIO_8X4 = "ratio_8x4"
    EXCLUDE = "exclude"
    CUSTOM = "custom"


class ItemResolution(BaseModel):
    """Human decision for a single extracted item."""

    item_id: str
    choice: LedScreenResolutionChoice
    width_m: Optional[Decimal] = None
    height_m: Optional[Decimal] = None
    excluded: bool = False
    note: str = Field(default="", description="Optional reviewer note.")

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> ItemResolution:
        if self.choice == LedScreenResolutionChoice.EXCLUDE:
            self.excluded = True
            return self
        if self.excluded:
            raise ValueError("excluded=True requires choice=exclude.")
        if self.width_m is None or self.height_m is None:
            raise ValueError("Non-excluded resolutions require width_m and height_m.")
        if self.width_m <= 0 or self.height_m <= 0:
            raise ValueError("Dimensions must be positive.")
        return self


class ResolutionSet(BaseModel):
    """Collection of human resolutions applied before quote recalculation."""

    resolutions: list[ItemResolution] = Field(default_factory=list)

    def get(self, item_id: str) -> ItemResolution | None:
        for resolution in self.resolutions:
            if resolution.item_id == item_id:
                return resolution
        return None

    def resolved_item_ids(self) -> set[str]:
        return {r.item_id for r in self.resolutions}


def build_led_resolution(
    choice: LedScreenResolutionChoice,
    custom_width_m: Decimal | None = None,
    custom_height_m: Decimal | None = None,
    note: str = "",
) -> ItemResolution:
    """Build a validated LED screen resolution from a UI choice."""

    if choice == LedScreenResolutionChoice.EXCLUDE:
        return ItemResolution(
            item_id=LED_SCREEN_ITEM_ID,
            choice=choice,
            excluded=True,
            note=note or "LED screen line excluded by reviewer.",
        )

    if choice == LedScreenResolutionChoice.ORIGINAL_6X3:
        return ItemResolution(
            item_id=LED_SCREEN_ITEM_ID,
            choice=choice,
            width_m=Decimal("6"),
            height_m=Decimal("3"),
            note=note or "Reviewer chose original 6m × 3m specification.",
        )

    if choice == LedScreenResolutionChoice.RATIO_8X4:
        return ItemResolution(
            item_id=LED_SCREEN_ITEM_ID,
            choice=choice,
            width_m=Decimal("8"),
            height_m=Decimal("4"),
            note=note or "Reviewer chose 8m × 4m using original 2:1 width:height ratio.",
        )

    if choice == LedScreenResolutionChoice.CUSTOM:
        if custom_width_m is None or custom_height_m is None:
            raise ValueError("Custom resolution requires width and height.")
        return ItemResolution(
            item_id=LED_SCREEN_ITEM_ID,
            choice=choice,
            width_m=custom_width_m,
            height_m=custom_height_m,
            note=note or f"Reviewer chose custom {custom_width_m}m × {custom_height_m}m.",
        )

    raise ValueError(f"Unsupported resolution choice: {choice}")


def led_contradiction_pending(extraction) -> bool:
    """Return True when the LED width contradiction is still unresolved."""

    from app.models import ResolutionStatus

    for contradiction in extraction.contradictions:
        if contradiction.contradiction_id == "contradiction-led-width":
            return contradiction.resolution_status == ResolutionStatus.UNRESOLVED
        if LED_SCREEN_ITEM_ID in contradiction.item_ids:
            return contradiction.resolution_status == ResolutionStatus.UNRESOLVED
    return False
