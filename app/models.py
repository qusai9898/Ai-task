"""
Structured data models for the LLM extraction layer.

These models capture observations extracted from client briefs (emails, notes,
forwards). They deliberately do NOT calculate prices, resolve contradictions,
or confirm catalog matches.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class MessageKind(str, Enum):
    """How the message entered the brief thread."""

    ORIGINAL = "original"
    REPLY = "reply"
    FORWARD = "forward"
    INTERNAL_NOTE = "internal_note"
    PHONE_NOTE = "phone_note"


class UnitOfMeasure(str, Enum):
    """Units encountered in briefs and the recipe catalog."""

    SQM = "sqm"
    UNIT = "unit"
    PACKAGE = "package"
    DAY = "day"
    SET = "set"
    METER = "meter"
    PAX = "pax"
    HOURS = "hours"
    OTHER = "other"


class DimensionKind(str, Enum):
    WIDTH = "width"
    HEIGHT = "height"
    DEPTH = "depth"
    AREA = "area"
    LENGTH = "length"


class ConfidenceLevel(str, Enum):
    """Extractor confidence — not catalog or pricing certainty."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ObservationStatus(str, Enum):
    """Lifecycle of a single observation."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    CONTRADICTORY = "contradictory"


class CatalogPresence(str, Enum):
    """
    Whether the requested item is known to exist in the recipe catalog.

    Default is UNKNOWN — never assume a catalog match at extraction time.
    """

    UNKNOWN = "unknown"
    POSSIBLE_MATCH = "possible_match"
    LIKELY_NOT_IN_CATALOG = "likely_not_in_catalog"
    EXPLICITLY_CUSTOM = "explicitly_custom"


class RequirementType(str, Enum):
    ACCESSIBILITY = "accessibility"
    POWER = "power"
    OPERATIONAL = "operational"
    BRAND = "brand"
    TIMELINE = "timeline"
    BUDGET_EXPECTATION = "budget_expectation"
    PROCUREMENT_REFERENCE = "procurement_reference"
    SAFETY = "safety"
    OTHER = "other"


class ContradictionType(str, Enum):
    DIMENSION = "dimension"
    QUANTITY = "quantity"
    SPECIFICATION = "specification"
    SCOPE = "scope"
    TIMING = "timing"
    OTHER = "other"


class ResolutionStatus(str, Enum):
    """Extraction layer always leaves contradictions unresolved."""

    UNRESOLVED = "unresolved"


class ReviewReason(str, Enum):
    AMBIGUOUS_QUANTITY = "ambiguous_quantity"
    MISSING_DIMENSION = "missing_dimension"
    CONTRADICTORY_INSTRUCTIONS = "contradictory_instructions"
    JUDGMENT_REQUIRED = "judgment_required"
    CATALOG_UNKNOWN = "catalog_unknown"
    APPROXIMATE_VALUE = "approximate_value"
    HIDDEN_INSTRUCTION_DETECTED = "hidden_instruction_detected"
    CANCELLATION_AMBIGUITY = "cancellation_ambiguity"
    OTHER = "other"


class ReviewSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SourceReference(BaseModel):
    """Evidence tying an observation to a specific message and verbatim text."""

    message_id: str
    sender: Optional[str] = None
    sent_at: Optional[str] = None
    excerpt: str = Field(
        ...,
        min_length=1,
        description="Verbatim quote from the source message supporting the observation.",
    )
    excerpt_context: Optional[str] = Field(
        default=None,
        description="Optional surrounding text for disambiguation; not a substitute for excerpt.",
    )

    @field_validator("sent_at", mode="before")
    @classmethod
    def normalize_sent_at(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)


class SourceMessage(BaseModel):
    """A single email or note in the client brief thread."""

    message_id: str
    thread_id: Optional[str] = None
    sender: str
    recipients: list[str] = Field(default_factory=list)
    sent_at: Optional[str] = None
    subject: Optional[str] = None
    body: str
    message_kind: MessageKind = MessageKind.ORIGINAL
    sequence_order: int = Field(
        ...,
        ge=0,
        description="Chronological position in the thread; lower is earlier.",
    )
    parent_message_id: Optional[str] = None

    @field_validator("sent_at", mode="before")
    @classmethod
    def normalize_sent_at(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)


class QuantityValue(BaseModel):
    """
    A quantity as stated in the brief.

    Use value for exact quantities. Use min_value/max_value for ranges or
    alternatives (e.g. '8 or 10', '400 maybe 450'). Never infer a single
    value from a range at extraction time.
    """

    raw_text: str = Field(
        ...,
        min_length=1,
        description="Client language for the quantity, preserved verbatim.",
    )
    value: Optional[Decimal] = None
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    unit: Optional[UnitOfMeasure] = None
    is_approximate: bool = False
    is_range: bool = False

    @model_validator(mode="after")
    def validate_quantity_shape(self) -> QuantityValue:
        has_single = self.value is not None
        has_range = self.min_value is not None or self.max_value is not None
        if has_single and has_range:
            raise ValueError(
                "QuantityValue cannot combine a single value with min/max range fields."
            )
        if self.is_range and not has_range:
            raise ValueError("is_range=True requires min_value or max_value.")
        return self


class DimensionValue(BaseModel):
    """
    A single dimension as explicitly stated in the brief.

    value may be None when the dimension is mentioned but not quantified
    (e.g. 'the height he didn't say'). Never compute area from width × height.
    """

    kind: DimensionKind
    raw_text: str = Field(
        ...,
        min_length=1,
        description="Client language for this dimension, preserved verbatim.",
    )
    value: Optional[Decimal] = None
    unit: Optional[UnitOfMeasure] = None
    is_minimum: bool = Field(
        default=False,
        description="True when client stated a minimum (e.g. 'at least 8 meters wide').",
    )
    is_maximum: bool = Field(
        default=False,
        description="True when client stated a maximum bound.",
    )


class CatalogSuggestion(BaseModel):
    """
    A tentative catalog mapping suggested by the extractor.

    This is NOT a resolved match — downstream logic must validate against
    recipe_catalog.csv.
    """

    recipe_code: str
    confidence: ConfidenceLevel
    rationale: str = Field(
        ...,
        min_length=1,
        description="Why the extractor thinks this code might relate; not authoritative.",
    )


class BaseObservation(BaseModel):
    """Shared fields for every extracted observation."""

    observation_id: str
    source: SourceReference
    status: ObservationStatus = ObservationStatus.ACTIVE
    confidence: ConfidenceLevel
    human_review_required: bool = False
    review_reason: Optional[ReviewReason] = None
    notes: Optional[str] = None


class ItemDescriptionObservation(BaseModel):
    """
    Client language describing a requested item.

    Multiple descriptions for the same logical item are preserved when different
    emails phrase the request differently.
    """

    observation_id: str
    source: SourceReference
    status: ObservationStatus = ObservationStatus.ACTIVE
    confidence: ConfidenceLevel
    human_review_required: bool = False
    review_reason: Optional[ReviewReason] = None
    client_text: str = Field(
        ...,
        min_length=1,
        description="How the client described the item; not normalized to catalog names.",
    )
    notes: Optional[str] = None


class QuantityObservation(BaseObservation):
    quantity: QuantityValue


class DimensionObservation(BaseObservation):
    dimension: DimensionValue


class RequirementObservation(BaseObservation):
    requirement_type: RequirementType
    description: str = Field(..., min_length=1)
    is_mandatory: bool = False
    applies_to_location: Optional[str] = None


class ExtractedItem(BaseModel):
    """
    A logical item grouping observations that refer to the same requested thing.

    Observations from different messages remain separate rows even when they
    describe the same item — contradictions are surfaced explicitly, not merged.
    """

    item_id: str
    label: str = Field(
        ...,
        min_length=1,
        description="Human-readable grouping label (e.g. 'Main hall LED screen').",
    )
    location: Optional[str] = Field(
        default=None,
        description="Spatial context: main hall, breakout room, entrance, etc.",
    )
    descriptions: list[ItemDescriptionObservation] = Field(default_factory=list)
    quantities: list[QuantityObservation] = Field(default_factory=list)
    dimensions: list[DimensionObservation] = Field(default_factory=list)
    requirements: list[RequirementObservation] = Field(default_factory=list)
    catalog_presence: CatalogPresence = CatalogPresence.UNKNOWN
    suggested_catalog_codes: list[CatalogSuggestion] = Field(default_factory=list)


class Cancellation(BaseModel):
    """A request explicitly withdrawn or replaced in a later message."""

    cancellation_id: str
    source: SourceReference
    description: str = Field(..., min_length=1)
    confidence: ConfidenceLevel
    cancelled_item_id: Optional[str] = None
    cancelled_observation_ids: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    review_reason: Optional[ReviewReason] = None

    @model_validator(mode="after")
    def validate_cancellation_target(self) -> Cancellation:
        if not self.cancelled_item_id and not self.cancelled_observation_ids:
            raise ValueError(
                "Cancellation must reference cancelled_item_id or cancelled_observation_ids."
            )
        return self


class Contradiction(BaseModel):
    """
    An unresolved conflict between two or more observations.

    The extraction layer records the conflict and preserves all sides; it does
    not pick a winner.
    """

    contradiction_id: str
    observation_ids: Annotated[list[str], Field(min_length=2)]
    item_ids: list[str] = Field(default_factory=list)
    contradiction_type: ContradictionType
    summary: str = Field(..., min_length=1)
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED


class ReviewFlag(BaseModel):
    """Explicit signal that a human should review part of the extraction."""

    flag_id: str
    reason: ReviewReason
    message: str = Field(..., min_length=1)
    severity: ReviewSeverity = ReviewSeverity.WARNING
    related_observation_ids: list[str] = Field(default_factory=list)
    related_item_ids: list[str] = Field(default_factory=list)
    source: Optional[SourceReference] = None


class BriefExtraction(BaseModel):
    """
    Root model: structured output of the LLM extraction layer for one brief.

    Contains no prices, margins, or resolved catalog matches.
    """

    extraction_id: str
    source_document: str
    extracted_at: datetime
    client_organization: Optional[str] = Field(
        default=None,
        description=(
            "Name of the client organization/company requesting this event, if "
            "identifiable from the brief text (e.g. sender's company, letterhead, "
            "signature block). Null if not stated anywhere in the brief."
        ),
    )
    event_name: Optional[str] = Field(
        default=None,
        description=(
            "Name or title of the event itself, if stated in the brief "
            "(e.g. 'Annual Excellence Awards Gala'). Null if not stated."
        ),
    )
    venue: Optional[str] = Field(
        default=None,
        description="Venue name and/or location, if stated in the brief. Null if not stated.",
    )
    messages: list[SourceMessage] = Field(default_factory=list)
    items: list[ExtractedItem] = Field(default_factory=list)
    cancellations: list[Cancellation] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    global_requirements: list[RequirementObservation] = Field(default_factory=list)

    @field_validator("messages")
    @classmethod
    def validate_unique_message_ids(cls, messages: list[SourceMessage]) -> list[SourceMessage]:
        ids = [m.message_id for m in messages]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate message_id values in messages list.")
        return messages
