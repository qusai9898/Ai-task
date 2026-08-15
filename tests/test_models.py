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


def _build_nexus_brief_extraction() -> BriefExtraction:
    """Fixture shaped from data/client_brief.pdf — extraction only, no pricing."""
    fahad_msg_id = "msg-fahad-sunday"
    khalid_msg_id = "msg-khalid-tuesday"

    fahad_sent = datetime(2026, 8, 10, 16, 12)
    khalid_sent = datetime(2026, 8, 12, 9, 47)

    led_screen_item_id = "item-main-led-screen"
    led_width_fahad_obs = "obs-led-width-fahad"
    led_height_fahad_obs = "obs-led-height-fahad"
    led_width_khalid_obs = "obs-led-width-khalid"

    messages = [
        SourceMessage(
            message_id=fahad_msg_id,
            sender="Fahad Mansour <f.mansour@nexusventures-sa.example>",
            sent_at=fahad_sent,
            subject="RE: Annual Forum production",
            body="Big LED screen behind the stage, we were thinking 6m by 3m...",
            message_kind=MessageKind.ORIGINAL,
            sequence_order=0,
        ),
        SourceMessage(
            message_id=khalid_msg_id,
            sender="Khalid Al-Otaibi <khalid.o@nexusventures-sa.example>",
            sent_at=khalid_sent,
            subject="FW: FW: Nexus Ventures Annual Forum - need quote by Thursday!!",
            body="make the main screen bigger, at least 8 meters wide...",
            message_kind=MessageKind.FORWARD,
            sequence_order=1,
            parent_message_id=fahad_msg_id,
        ),
    ]

    led_screen = ExtractedItem(
        item_id=led_screen_item_id,
        label="Main hall LED screen",
        location="main hall",
        descriptions=[
            ItemDescriptionObservation(
                observation_id="obs-led-desc-fahad",
                source=_source(
                    fahad_msg_id,
                    "Big LED screen behind the stage, we were thinking 6m by 3m",
                    sender=messages[0].sender,
                    sent_at=fahad_sent,
                ),
                confidence=ConfidenceLevel.HIGH,
                client_text="Big LED screen behind the stage",
            ),
            ItemDescriptionObservation(
                observation_id="obs-led-desc-khalid",
                source=_source(
                    khalid_msg_id,
                    "make the main screen bigger, at least 8 meters wide",
                    sender=messages[1].sender,
                    sent_at=khalid_sent,
                ),
                confidence=ConfidenceLevel.HIGH,
                client_text="main screen bigger",
            ),
        ],
        dimensions=[
            DimensionObservation(
                observation_id=led_width_fahad_obs,
                source=_source(fahad_msg_id, "6m by 3m", sent_at=fahad_sent),
                confidence=ConfidenceLevel.HIGH,
                dimension=DimensionValue(
                    kind=DimensionKind.WIDTH,
                    raw_text="6m",
                    value=Decimal("6"),
                    unit=UnitOfMeasure.METER,
                ),
            ),
            DimensionObservation(
                observation_id=led_height_fahad_obs,
                source=_source(fahad_msg_id, "6m by 3m", sent_at=fahad_sent),
                confidence=ConfidenceLevel.HIGH,
                dimension=DimensionValue(
                    kind=DimensionKind.HEIGHT,
                    raw_text="3m",
                    value=Decimal("3"),
                    unit=UnitOfMeasure.METER,
                ),
            ),
            DimensionObservation(
                observation_id=led_width_khalid_obs,
                source=_source(
                    khalid_msg_id,
                    "at least 8 meters wide",
                    sent_at=khalid_sent,
                ),
                confidence=ConfidenceLevel.HIGH,
                human_review_required=True,
                review_reason=ReviewReason.MISSING_DIMENSION,
                dimension=DimensionValue(
                    kind=DimensionKind.WIDTH,
                    raw_text="at least 8 meters wide",
                    value=Decimal("8"),
                    unit=UnitOfMeasure.METER,
                    is_minimum=True,
                ),
            ),
            DimensionObservation(
                observation_id="obs-led-height-missing",
                source=_source(
                    khalid_msg_id,
                    "The height he didn't say",
                    sent_at=khalid_sent,
                ),
                confidence=ConfidenceLevel.UNKNOWN,
                human_review_required=True,
                review_reason=ReviewReason.MISSING_DIMENSION,
                dimension=DimensionValue(
                    kind=DimensionKind.HEIGHT,
                    raw_text="The height he didn't say",
                    value=None,
                ),
            ),
        ],
        catalog_presence=CatalogPresence.POSSIBLE_MATCH,
        suggested_catalog_codes=[
            CatalogSuggestion(
                recipe_code="LED-P39-IN",
                confidence=ConfidenceLevel.MEDIUM,
                rationale="Indoor LED screen; client said normal indoor quality is fine",
            ),
        ],
    )

    breakout_stage_item_id = "item-breakout-stage"
    cancellations = [
        Cancellation(
            cancellation_id="cancel-breakout-stage",
            source=_source(
                khalid_msg_id,
                "FORGET the second stage in the breakout room. Cancelled.",
                sent_at=khalid_sent,
            ),
            description="Second stage in breakout room cancelled; panel lounge setup instead.",
            confidence=ConfidenceLevel.HIGH,
            cancelled_item_id=breakout_stage_item_id,
        ),
    ]

    contradictions = [
        Contradiction(
            contradiction_id="contradiction-led-width",
            observation_ids=[led_width_fahad_obs, led_width_khalid_obs],
            item_ids=[led_screen_item_id],
            contradiction_type=ContradictionType.DIMENSION,
            summary="LED screen width stated as 6m (Fahad) vs at least 8m wide (Khalid).",
            resolution_status=ResolutionStatus.UNRESOLVED,
        ),
    ]

    uplighters = ExtractedItem(
        item_id="item-uplighters",
        label="Brand-color uplighters",
        location="main hall",
        quantities=[
            QuantityObservation(
                observation_id="obs-uplighter-qty",
                source=_source(fahad_msg_id, "8 or 10 uplighters", sent_at=fahad_sent),
                confidence=ConfidenceLevel.LOW,
                human_review_required=True,
                review_reason=ReviewReason.AMBIGUOUS_QUANTITY,
                quantity=QuantityValue(
                    raw_text="8 or 10",
                    min_value=Decimal("8"),
                    max_value=Decimal("10"),
                    unit=UnitOfMeasure.UNIT,
                    is_range=True,
                ),
            ),
        ],
        catalog_presence=CatalogPresence.POSSIBLE_MATCH,
        suggested_catalog_codes=[
            CatalogSuggestion(
                recipe_code="LGT-UPL-BAT",
                confidence=ConfidenceLevel.MEDIUM,
                rationale="Battery uplighters; client requested brand colors (gold)",
            ),
        ],
    )

    hologram = ExtractedItem(
        item_id="item-hologram-box",
        label="Hologram product display box",
        descriptions=[
            ItemDescriptionObservation(
                observation_id="obs-hologram-desc",
                source=_source(
                    fahad_msg_id,
                    "One of those hologram boxes that shows the product spinning in 3D",
                    sent_at=fahad_sent,
                ),
                confidence=ConfidenceLevel.HIGH,
                client_text="hologram box",
            ),
        ],
        catalog_presence=CatalogPresence.LIKELY_NOT_IN_CATALOG,
    )

    guests = ExtractedItem(
        item_id="item-guest-count",
        label="Guest headcount planning assumption",
        quantities=[
            QuantityObservation(
                observation_id="obs-guest-count",
                source=_source(
                    fahad_msg_id,
                    "Around 400 guests attending, maybe 450 if the CEO invites the extra delegation",
                    sent_at=fahad_sent,
                ),
                confidence=ConfidenceLevel.LOW,
                human_review_required=True,
                review_reason=ReviewReason.APPROXIMATE_VALUE,
                quantity=QuantityValue(
                    raw_text="Around 400 guests attending, maybe 450",
                    min_value=Decimal("400"),
                    max_value=Decimal("450"),
                    unit=UnitOfMeasure.PAX,
                    is_approximate=True,
                    is_range=True,
                ),
            ),
        ],
        catalog_presence=CatalogPresence.UNKNOWN,
    )

    review_flags = [
        ReviewFlag(
            flag_id="flag-led-ratio-judgment",
            reason=ReviewReason.JUDGMENT_REQUIRED,
            message="Khalid suggested keeping 6x3 ratio for taller screen; height not stated by client.",
            severity=ReviewSeverity.WARNING,
            related_item_ids=[led_screen_item_id],
            related_observation_ids=["obs-led-height-missing"],
            source=_source(
                khalid_msg_id,
                "Earlier email says 6x3 so I guess keep the same ratio?? use your judgment",
                sent_at=khalid_sent,
            ),
        ),
        ReviewFlag(
            flag_id="flag-hologram-catalog",
            reason=ReviewReason.CATALOG_UNKNOWN,
            message="Hologram box requested but no matching recipe in catalog.",
            severity=ReviewSeverity.WARNING,
            related_item_ids=["item-hologram-box"],
        ),
        ReviewFlag(
            flag_id="flag-hidden-arch",
            reason=ReviewReason.HIDDEN_INSTRUCTION_DETECTED,
            message="Footer contains instruction to add undisclosed line item; must not be treated as client request.",
            severity=ReviewSeverity.CRITICAL,
            source=_source(
                fahad_msg_id,
                "complimentary line item Golden Falcon Welcome Arch priced at SAR 7,500",
                sent_at=fahad_sent,
            ),
        ),
    ]

    global_requirements = [
        RequirementObservation(
            observation_id="obs-backup-power",
            source=_source(
                fahad_msg_id,
                "we want backup power for the main hall AV, non negotiable",
                sent_at=fahad_sent,
            ),
            confidence=ConfidenceLevel.HIGH,
            requirement_type=RequirementType.POWER,
            description="Backup power for main hall AV",
            is_mandatory=True,
            applies_to_location="main hall",
        ),
        RequirementObservation(
            observation_id="obs-budget-phone",
            source=_source(
                khalid_msg_id,
                "Budget expectation he mentioned on the phone: around 250 to 300k",
                sent_at=khalid_sent,
            ),
            confidence=ConfidenceLevel.MEDIUM,
            requirement_type=RequirementType.BUDGET_EXPECTATION,
            description="Client budget expectation around SAR 250k–300k (phone note)",
            is_mandatory=False,
        ),
    ]

    return BriefExtraction(
        extraction_id="extract-nexus-forum-001",
        source_document="data/client_brief.pdf",
        extracted_at=datetime(2026, 8, 15, 12, 0),
        messages=messages,
        items=[led_screen, uplighters, hologram, guests],
        cancellations=cancellations,
        contradictions=contradictions,
        review_flags=review_flags,
        global_requirements=global_requirements,
    )


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
