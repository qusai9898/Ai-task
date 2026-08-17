"""
Pydantic models for quote generation output.

These are separate from extraction models (app.models) so pricing and catalog
matching layers do not contaminate the LLM extraction schema.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models import ReviewFlag, ReviewReason


class MatchStatus(str, Enum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class MatchMethod(str, Enum):
    SUGGESTION_VALIDATED = "suggestion_validated"
    KEYWORD_RULE = "keyword_rule"
    EXPLICIT_MAPPING = "explicit_mapping"
    MANUAL_REQUIRED = "manual_required"


class CatalogMatchResult(BaseModel):
    """Deterministic catalog match for one extracted item."""

    item_id: str
    item_label: str
    match_status: MatchStatus
    recipe_code: Optional[str] = None
    recipe_name: Optional[str] = None
    match_method: Optional[MatchMethod] = None
    candidate_codes: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    review_reason: Optional[ReviewReason] = None
    notes: Optional[str] = None


class QuantityResult(BaseModel):
    """Deterministic quantity calculation for a matched catalog line."""

    item_id: str
    recipe_code: str
    catalog_unit: str
    requested_quantity: Optional[Decimal] = None
    calculated_quantity: Optional[Decimal] = None
    min_quantity: Optional[Decimal] = None
    max_quantity: Optional[Decimal] = None
    requires_review: bool = False
    review_reason: Optional[ReviewReason] = None
    calculation_notes: str = ""
    observation_ids_used: list[str] = Field(default_factory=list)


class QuoteLineStatus(str, Enum):
    PRICED = "priced"
    REQUIRES_REVIEW = "requires_review"
    EXCLUDED = "excluded"
    UNMATCHED = "unmatched"
    CUSTOM_ESTIMATE = "custom_estimate"


class QuoteLineKind(str, Enum):
    CATALOG = "catalog"
    CUSTOM_NOT_IN_CATALOG = "custom_not_in_catalog"
    UNRESOLVED = "unresolved"


class QuoteStatus(str, Enum):
    DRAFT = "draft"
    REQUIRES_REVIEW = "requires_review"
    READY = "ready"
    BLOCKED = "blocked"


class PricedLine(BaseModel):
    """Internal pricing breakdown for one quote line."""

    item_id: Optional[str] = None
    recipe_code: Optional[str] = None
    description: str
    requested_quantity: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    min_quantity: Optional[Decimal] = None
    max_quantity: Optional[Decimal] = None
    unit: str
    material_cost_sar: Optional[Decimal] = None
    labour_cost_sar: Optional[Decimal] = None
    equipment_cost_sar: Optional[Decimal] = None
    unit_cost_sar: Optional[Decimal] = None
    unit_price_sar: Optional[Decimal] = None
    margin_pct: Optional[Decimal] = None
    margin_amount_sar: Optional[Decimal] = None
    line_cost_sar: Optional[Decimal] = None
    line_total_sar: Optional[Decimal] = None
    min_line_total_sar: Optional[Decimal] = None
    max_line_total_sar: Optional[Decimal] = None
    status: QuoteLineStatus
    line_kind: QuoteLineKind = QuoteLineKind.CATALOG
    review_reasons: list[ReviewReason] = Field(default_factory=list)
    notes: Optional[str] = None


class Quote(BaseModel):
    """Final structured quote output."""

    quote_id: str
    extraction_id: str
    source_document: str
    created_at: datetime
    status: QuoteStatus
    currency: str = "SAR"
    lines: list[PricedLine] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    subtotal_sar: Optional[Decimal] = None
    min_subtotal_sar: Optional[Decimal] = None
    max_subtotal_sar: Optional[Decimal] = None
    notes: Optional[str] = None
