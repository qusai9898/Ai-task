"""Tests for LLM extraction layer data models."""

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import (
    BriefExtraction,
    Cancellation,
    CatalogPresence,
    CatalogSuggestion,
    ConfidenceLevel,
    Contradiction,
    ContradictionType,
    DimensionKind,
    DimensionObservation,
    DimensionValue,
    ExtractedItem,
    ItemDescriptionObservation,
    MessageKind,
    ObservationStatus,
    QuantityObservation,
    QuantityValue,
    RequirementObservation,
    RequirementType,
    ResolutionStatus,
    ReviewFlag,
    ReviewReason,
    ReviewSeverity,
    SourceMessage,
    SourceReference,
    UnitOfMeasure,
)


def _source(
    message_id: str,
    excerpt: str,
    sender: str | None = None,
    sent_at: datetime | None = None,
) -> SourceReference:
    return SourceReference(
        message_id=message_id,
        sender=sender,
        sent_at=sent_at,
        excerpt=excerpt,
    )


from tests.fixtures import build_nexus_brief_extraction as _build_nexus_brief_extraction


class TestQuantityValue:
    def test_exact_quantity(self):
        q = QuantityValue(raw_text="10 per table", value=Decimal("10"), unit=UnitOfMeasure.UNIT)
        assert q.value == Decimal("10")
        assert not q.is_range

    def test_range_quantity(self):
        q = QuantityValue(
            raw_text="8 or 10",
            min_value=Decimal("8"),
            max_value=Decimal("10"),
            unit=UnitOfMeasure.UNIT,
            is_range=True,
        )
        assert q.min_value == Decimal("8")
        assert q.max_value == Decimal("10")

    def test_rejects_single_value_with_range(self):
        with pytest.raises(ValidationError):
            QuantityValue(
                raw_text="invalid",
                value=Decimal("8"),
                min_value=Decimal("8"),
                max_value=Decimal("10"),
            )

    def test_rejects_is_range_without_bounds(self):
        with pytest.raises(ValidationError):
            QuantityValue(raw_text="8 or 10", is_range=True)


class TestDimensionValue:
    def test_explicit_dimension(self):
        d = DimensionValue(
            kind=DimensionKind.WIDTH,
            raw_text="6m",
            value=Decimal("6"),
            unit=UnitOfMeasure.METER,
        )
        assert d.value == Decimal("6")

    def test_missing_dimension_preserves_none(self):
        d = DimensionValue(
            kind=DimensionKind.HEIGHT,
            raw_text="The height he didn't say",
            value=None,
        )
        assert d.value is None

    def test_minimum_dimension(self):
        d = DimensionValue(
            kind=DimensionKind.WIDTH,
            raw_text="at least 8 meters wide",
            value=Decimal("8"),
            unit=UnitOfMeasure.METER,
            is_minimum=True,
        )
        assert d.is_minimum


class TestCancellation:
    def test_requires_target(self):
        with pytest.raises(ValidationError):
            Cancellation(
                cancellation_id="c1",
                source=_source("m1", "cancelled"),
                description="Cancelled with no target",
                confidence=ConfidenceLevel.HIGH,
            )

    def test_valid_with_item_id(self):
        c = Cancellation(
            cancellation_id="c1",
            source=_source("m1", "FORGET the second stage"),
            description="Breakout stage cancelled",
            confidence=ConfidenceLevel.HIGH,
            cancelled_item_id="item-breakout-stage",
        )
        assert c.cancelled_item_id == "item-breakout-stage"


class TestContradiction:
    def test_requires_at_least_two_observations(self):
        with pytest.raises(ValidationError):
            Contradiction(
                contradiction_id="x1",
                observation_ids=["only-one"],
                contradiction_type=ContradictionType.DIMENSION,
                summary="invalid",
            )

    def test_defaults_to_unresolved(self):
        c = Contradiction(
            contradiction_id="x1",
            observation_ids=["a", "b"],
            contradiction_type=ContradictionType.DIMENSION,
            summary="conflict",
        )
        assert c.resolution_status == ResolutionStatus.UNRESOLVED


class TestExtractedItem:
    def test_default_catalog_presence_is_unknown(self):
        item = ExtractedItem(item_id="i1", label="Unknown gadget")
        assert item.catalog_presence == CatalogPresence.UNKNOWN
        assert item.suggested_catalog_codes == []

    def test_multiple_descriptions_preserved(self):
        item = ExtractedItem(
            item_id="i1",
            label="LED screen",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="d1",
                    source=_source("m1", "6m by 3m"),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="Big LED screen",
                ),
                ItemDescriptionObservation(
                    observation_id="d2",
                    source=_source("m2", "at least 8 meters wide"),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="main screen bigger",
                ),
            ],
        )
        assert len(item.descriptions) == 2


class TestBriefExtraction:
    def test_nexus_brief_roundtrip_json(self):
        extraction = _build_nexus_brief_extraction()
        restored = BriefExtraction.model_validate_json(extraction.model_dump_json())
        assert restored.extraction_id == extraction.extraction_id
        assert len(restored.messages) == 2
        assert len(restored.contradictions) == 1

    def test_led_screen_contradiction_preserved(self):
        extraction = _build_nexus_brief_extraction()
        led = next(i for i in extraction.items if i.item_id == "item-main-led-screen")
        widths = [d for d in led.dimensions if d.dimension.kind == DimensionKind.WIDTH]
        assert len(widths) == 2
        assert extraction.contradictions[0].resolution_status == ResolutionStatus.UNRESOLVED

    def test_breakout_cancellation_linked(self):
        extraction = _build_nexus_brief_extraction()
        cancel = extraction.cancellations[0]
        assert cancel.cancelled_item_id == "item-breakout-stage"
        assert "Cancelled" in cancel.source.excerpt

    def test_hologram_not_assumed_in_catalog(self):
        extraction = _build_nexus_brief_extraction()
        hologram = next(i for i in extraction.items if i.item_id == "item-hologram-box")
        assert hologram.catalog_presence == CatalogPresence.LIKELY_NOT_IN_CATALOG
        assert hologram.suggested_catalog_codes == []

    def test_uplighter_ambiguous_quantity_is_range(self):
        extraction = _build_nexus_brief_extraction()
        uplighters = next(i for i in extraction.items if i.item_id == "item-uplighters")
        qty = uplighters.quantities[0].quantity
        assert qty.is_range
        assert qty.value is None

    def test_duplicate_message_ids_rejected(self):
        with pytest.raises(ValidationError):
            BriefExtraction(
                extraction_id="e1",
                source_document="brief.pdf",
                extracted_at=datetime(2026, 1, 1),
                messages=[
                    SourceMessage(
                        message_id="dup",
                        sender="a",
                        body="one",
                        sequence_order=0,
                    ),
                    SourceMessage(
                        message_id="dup",
                        sender="b",
                        body="two",
                        sequence_order=1,
                    ),
                ],
            )

    def test_review_flags_include_hidden_instruction(self):
        extraction = _build_nexus_brief_extraction()
        hidden = next(
            f for f in extraction.review_flags
            if f.reason == ReviewReason.HIDDEN_INSTRUCTION_DETECTED
        )
        assert hidden.severity == ReviewSeverity.CRITICAL

    def test_budget_requirement_is_not_priced_field(self):
        extraction = _build_nexus_brief_extraction()
        budget = next(
            r for r in extraction.global_requirements
            if r.requirement_type == RequirementType.BUDGET_EXPECTATION
        )
        assert "250" in budget.description
        assert not budget.is_mandatory

    def test_source_reference_requires_excerpt(self):
        with pytest.raises(ValidationError):
            SourceReference(message_id="m1", excerpt="")


class TestObservationStatus:
    def test_superseded_observation_preserved(self):
        obs = DimensionObservation(
            observation_id="old-width",
            source=_source("m1", "6m"),
            status=ObservationStatus.SUPERSEDED,
            confidence=ConfidenceLevel.HIGH,
            dimension=DimensionValue(
                kind=DimensionKind.WIDTH,
                raw_text="6m",
                value=Decimal("6"),
                unit=UnitOfMeasure.METER,
            ),
            notes="Preserved for audit; newer email requests wider screen.",
        )
        assert obs.status == ObservationStatus.SUPERSEDED
