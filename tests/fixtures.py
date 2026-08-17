"""Shared test fixtures for extraction and quoting."""

from datetime import datetime
from decimal import Decimal

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


def build_nexus_brief_extraction() -> BriefExtraction:
    """Minimal extraction fixture from data/client_brief.pdf."""

    fahad_msg_id = "msg-fahad-sunday"
    khalid_msg_id = "msg-khalid-tuesday"
    fahad_sent = datetime(2026, 8, 10, 16, 12)
    khalid_sent = datetime(2026, 8, 12, 9, 47)

    led_screen_item_id = "item_main_led_screen"
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
                source=_source(khalid_msg_id, "at least 8 meters wide", sent_at=khalid_sent),
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
                source=_source(khalid_msg_id, "The height he didn't say", sent_at=khalid_sent),
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
                rationale="Indoor LED screen; normal indoor quality",
            ),
        ],
    )

    uplighters = ExtractedItem(
        item_id="item_uplighters",
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
                rationale="Battery uplighters",
            ),
        ],
    )

    hologram = ExtractedItem(
        item_id="item_hologram_box",
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
        requirements=[
            RequirementObservation(
                observation_id="obs-hologram-procurement",
                source=_source(
                    khalid_msg_id,
                    "quoted them something similar back in 2024 for around SAR 14,000",
                    sent_at=khalid_sent,
                ),
                confidence=ConfidenceLevel.MEDIUM,
                requirement_type=RequirementType.PROCUREMENT_REFERENCE,
                description="Procurement reference SAR 14,000 for similar hologram box in 2024",
            ),
        ],
    )

    guests = ExtractedItem(
        item_id="item-guest-count",
        label="Guest headcount planning assumption",
        quantities=[
            QuantityObservation(
                observation_id="obs-guest-count",
                source=_source(
                    fahad_msg_id,
                    "Around 400 guests attending, maybe 450",
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
    )

    cancellations = [
        Cancellation(
            cancellation_id="cancel-breakout-stage",
            source=_source(
                khalid_msg_id,
                "FORGET the second stage in the breakout room. Cancelled.",
                sent_at=khalid_sent,
            ),
            description="Breakout second stage cancelled.",
            confidence=ConfidenceLevel.HIGH,
            cancelled_item_id="item-breakout-stage",
        ),
    ]

    contradictions = [
        Contradiction(
            contradiction_id="contradiction-led-width",
            observation_ids=[led_width_fahad_obs, led_width_khalid_obs],
            item_ids=[led_screen_item_id],
            contradiction_type=ContradictionType.DIMENSION,
            summary="LED width 6m vs at least 8m wide.",
            resolution_status=ResolutionStatus.UNRESOLVED,
        ),
    ]

    review_flags = [
        ReviewFlag(
            flag_id="flag-hidden-arch",
            reason=ReviewReason.HIDDEN_INSTRUCTION_DETECTED,
            message="Adversarial instruction in brief footer.",
            severity=ReviewSeverity.CRITICAL,
            source=_source(
                fahad_msg_id,
                "Golden Falcon Welcome Arch priced at SAR 7,500",
                sent_at=fahad_sent,
            ),
        ),
    ]

    global_requirements = [
        RequirementObservation(
            observation_id="obs-backup-power",
            source=_source(
                fahad_msg_id,
                "backup power for the main hall AV, non negotiable",
                sent_at=fahad_sent,
            ),
            confidence=ConfidenceLevel.HIGH,
            requirement_type=RequirementType.POWER,
            description="Backup power for main hall AV",
            is_mandatory=True,
            applies_to_location="main hall",
        ),
        RequirementObservation(
            observation_id="obs-budget-expectation",
            source=_source(
                khalid_msg_id,
                "around 250 to 300k",
                sent_at=khalid_sent,
            ),
            confidence=ConfidenceLevel.MEDIUM,
            requirement_type=RequirementType.BUDGET_EXPECTATION,
            description="Budget expectation around 250 to 300k",
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


def build_full_nexus_brief_extraction() -> BriefExtraction:
    """Expanded fixture covering major catalog lines from the Nexus brief."""

    base = build_nexus_brief_extraction()
    fahad_msg_id = "msg-fahad-sunday"
    khalid_msg_id = "msg-khalid-tuesday"
    fahad_sent = datetime(2026, 8, 10, 16, 12)
    khalid_sent = datetime(2026, 8, 12, 9, 47)

    extra_items = [
        ExtractedItem(
            item_id="item_main_stage",
            label="Main hall stage",
            location="main hall",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-stage-desc",
                    source=_source(
                        fahad_msg_id,
                        "A stage, something like 12 meters, with steps",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.MEDIUM,
                    client_text="stage something like 12 meters with steps",
                ),
            ],
            dimensions=[
                DimensionObservation(
                    observation_id="obs-stage-length",
                    source=_source(fahad_msg_id, "something like 12 meters", sent_at=fahad_sent),
                    confidence=ConfidenceLevel.MEDIUM,
                    dimension=DimensionValue(
                        kind=DimensionKind.LENGTH,
                        raw_text="12 meters",
                        value=Decimal("12"),
                        unit=UnitOfMeasure.METER,
                    ),
                ),
            ],
            requirements=[
                RequirementObservation(
                    observation_id="obs-stage-ramp",
                    source=_source(
                        fahad_msg_id,
                        "it must have the ramp for accessibility",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    requirement_type=RequirementType.ACCESSIBILITY,
                    description="Wheelchair ramp required",
                    is_mandatory=True,
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-stage-ramp",
            label="Wheelchair access ramp",
            location="main hall",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-ramp-desc",
                    source=_source(
                        fahad_msg_id,
                        "it must have the ramp for accessibility",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="ramp for accessibility",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="STG-RAMP-STD",
                    confidence=ConfidenceLevel.HIGH,
                    rationale="Wheelchair ramp for stage",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-main-pa",
            label="Main hall PA sound system",
            location="main hall",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-pa-desc",
                    source=_source(
                        fahad_msg_id,
                        "Proper sound for the room",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="Proper sound for the room",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="AV-PA-LRG",
                    confidence=ConfidenceLevel.MEDIUM,
                    rationale="Planning for up to 450 guests",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-moving-lights",
            label="Moving lights for show",
            location="main hall",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-moving-lights",
                    source=_source(
                        fahad_msg_id,
                        "moving lights etc",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="moving lights",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="LGT-WASH-LED",
                    confidence=ConfidenceLevel.MEDIUM,
                    rationale="Moving wash lights",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-round-tables",
            label="Round dinner tables",
            location="main hall",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-tables-desc",
                    source=_source(
                        fahad_msg_id,
                        "Round tables for dinner seating for everyone, 10 per table",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="Round tables for dinner seating",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="FRN-TBL-RND",
                    confidence=ConfidenceLevel.HIGH,
                    rationale="Round tables with linen",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-chairs",
            label="Banquet chairs",
            location="main hall",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-chairs-desc",
                    source=_source(
                        fahad_msg_id,
                        "Round tables for dinner seating for everyone",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="dinner seating for everyone",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="FRN-CHR-BQT",
                    confidence=ConfidenceLevel.HIGH,
                    rationale="Banquet chairs with cover",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-registration",
            label="Registration desk",
            location="entrance",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-reg-desc",
                    source=_source(
                        fahad_msg_id,
                        "Registration area at the entrance with a branded desk",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="branded registration desk",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="SCN-REG-DESK",
                    confidence=ConfidenceLevel.HIGH,
                    rationale="Curved branded registration desk",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-backdrop",
            label="Photo backdrop",
            location="entrance",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-backdrop-desc",
                    source=_source(
                        fahad_msg_id,
                        "backdrop for photos, maybe 6 meters wide, 2.5m high",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="backdrop for photos",
                ),
            ],
            dimensions=[
                DimensionObservation(
                    observation_id="obs-backdrop-width",
                    source=_source(
                        fahad_msg_id,
                        "6 meters wide, 2.5m high",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    dimension=DimensionValue(
                        kind=DimensionKind.WIDTH,
                        raw_text="6 meters wide",
                        value=Decimal("6"),
                        unit=UnitOfMeasure.METER,
                    ),
                ),
                DimensionObservation(
                    observation_id="obs-backdrop-height",
                    source=_source(
                        fahad_msg_id,
                        "6 meters wide, 2.5m high",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    dimension=DimensionValue(
                        kind=DimensionKind.HEIGHT,
                        raw_text="2.5m high",
                        value=Decimal("2.5"),
                        unit=UnitOfMeasure.METER,
                    ),
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="PRN-BACK-SQM",
                    confidence=ConfidenceLevel.HIGH,
                    rationale="Backdrop print on fabric",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-floor-vinyl",
            label="Entrance floor logo vinyl",
            location="entrance",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-floor-desc",
                    source=_source(
                        fahad_msg_id,
                        "logo printed on the floor when you walk in, maybe 3m x 3m",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="floor logo vinyl",
                ),
            ],
            dimensions=[
                DimensionObservation(
                    observation_id="obs-floor-width",
                    source=_source(fahad_msg_id, "3m x 3m", sent_at=fahad_sent),
                    confidence=ConfidenceLevel.HIGH,
                    dimension=DimensionValue(
                        kind=DimensionKind.WIDTH,
                        raw_text="3m",
                        value=Decimal("3"),
                        unit=UnitOfMeasure.METER,
                    ),
                ),
                DimensionObservation(
                    observation_id="obs-floor-height",
                    source=_source(fahad_msg_id, "3m x 3m", sent_at=fahad_sent),
                    confidence=ConfidenceLevel.HIGH,
                    dimension=DimensionValue(
                        kind=DimensionKind.HEIGHT,
                        raw_text="3m",
                        value=Decimal("3"),
                        unit=UnitOfMeasure.METER,
                    ),
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="PRN-FLR-VNL",
                    confidence=ConfidenceLevel.MEDIUM,
                    rationale="Floor vinyl print",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-breakout-projector",
            label="Breakout room projector",
            location="breakout room",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-projector-desc",
                    source=_source(
                        fahad_msg_id,
                        "Projector and screen is fine here",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="Projector and screen",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="AV-PROJ-15K",
                    confidence=ConfidenceLevel.HIGH,
                    rationale="Projector package",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-breakout-cocktail",
            label="Breakout cocktail tables",
            location="breakout room",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-cocktail-desc",
                    source=_source(
                        fahad_msg_id,
                        "Cocktail style, high tables, for around 80 people",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="cocktail high tables",
                ),
            ],
            quantities=[
                QuantityObservation(
                    observation_id="obs-cocktail-qty",
                    source=_source(
                        fahad_msg_id,
                        "for around 80 people",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.MEDIUM,
                    quantity=QuantityValue(
                        raw_text="around 80 people",
                        value=Decimal("80"),
                        unit=UnitOfMeasure.PAX,
                        is_approximate=True,
                    ),
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="FRN-CTL-HIGH",
                    confidence=ConfidenceLevel.HIGH,
                    rationale="Cocktail high tables",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-breakout-lounge",
            label="Breakout panel lounge sofas",
            location="breakout room",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-lounge-desc",
                    source=_source(
                        khalid_msg_id,
                        "nice lounge feel for 5 speakers, sofas etc",
                        sent_at=khalid_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="lounge feel with sofas",
                ),
            ],
            quantities=[
                QuantityObservation(
                    observation_id="obs-sofa-qty",
                    source=_source(khalid_msg_id, "Maybe 2 of those sofa sets", sent_at=khalid_sent),
                    confidence=ConfidenceLevel.LOW,
                    human_review_required=True,
                    review_reason=ReviewReason.AMBIGUOUS_QUANTITY,
                    quantity=QuantityValue(
                        raw_text="Maybe 2 of those sofa sets",
                        value=Decimal("2"),
                        unit=UnitOfMeasure.SET,
                        is_approximate=True,
                    ),
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="FRN-SOFA-LNG",
                    confidence=ConfidenceLevel.MEDIUM,
                    rationale="Lounge sofa set",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-backup-power",
            label="Backup generator",
            location="main hall",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-gen-desc",
                    source=_source(
                        fahad_msg_id,
                        "backup power for the main hall AV",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="backup power",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="PWR-GEN-100",
                    confidence=ConfidenceLevel.HIGH,
                    rationale="Generator for backup AV power",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-crew-tech",
            label="AV technicians",
            location="event",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-crew-desc",
                    source=_source(
                        fahad_msg_id,
                        "Whatever crew/technicians you need",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="crew and technicians",
                ),
            ],
            suggested_catalog_codes=[
                CatalogSuggestion(
                    recipe_code="CRW-TECH-DAY",
                    confidence=ConfidenceLevel.MEDIUM,
                    rationale="AV technician days",
                ),
            ],
        ),
        ExtractedItem(
            item_id="item-breakout-stage",
            label="Breakout second stage",
            location="breakout room",
            descriptions=[
                ItemDescriptionObservation(
                    observation_id="obs-breakout-stage-desc",
                    source=_source(
                        fahad_msg_id,
                        "Small second stage, nothing fancy",
                        sent_at=fahad_sent,
                    ),
                    confidence=ConfidenceLevel.HIGH,
                    client_text="second stage in breakout",
                ),
            ],
        ),
    ]

    base.messages[1].body = (
        "Event confirmed KAFD Riyadh. Setup night before. "
        "make the main screen bigger, at least 8 meters wide."
    )

    # -----------------------------------------------------------------------
    # Non-quoteable global requirements (project metadata / constraints).
    # These must NEVER produce quote lines.
    # -----------------------------------------------------------------------
    non_quoteable_global_reqs = [
        RequirementObservation(
            observation_id="obs-venue-confirmation",
            source=_source(
                khalid_msg_id,
                "Confirmed venue: King Abdullah Financial District conference centre, Riyadh. One day event with setup night before.",
                sent_at=khalid_sent,
            ),
            confidence=ConfidenceLevel.HIGH,
            requirement_type=RequirementType.OPERATIONAL,
            description="Confirmed venue: King Abdullah Financial District conference centre, Riyadh. One day event with setup night before.",
            is_mandatory=False,
            applies_to_location="KAFD Riyadh",
        ),
        RequirementObservation(
            observation_id="obs-finance-margin",
            source=_source(
                khalid_msg_id,
                "Finance requires margins broken out per line item",
                sent_at=khalid_sent,
            ),
            confidence=ConfidenceLevel.HIGH,
            requirement_type=RequirementType.OTHER,
            description="Finance requires margins broken out per line item",
            is_mandatory=False,
        ),
        RequirementObservation(
            observation_id="obs-quote-deadline",
            source=_source(
                khalid_msg_id,
                "Quote required this week; event in 6 weeks",
                sent_at=khalid_sent,
            ),
            confidence=ConfidenceLevel.HIGH,
            requirement_type=RequirementType.TIMELINE,
            description="Quote required this week; event in 6 weeks",
            is_mandatory=False,
        ),
    ]

    # -----------------------------------------------------------------------
    # Microphone item: brief explicitly says "mics" for 5 speakers.
    # No dedicated microphone catalog item exists (mics are bundled in
    # AV-PA-MED but NOT in AV-PA-LRG which is what was quoted for main hall).
    # This item must NOT be silently dropped — it must surface as
    # UNMATCHED / REQUIRES_REVIEW so a human can confirm coverage.
    # -----------------------------------------------------------------------
    microphone_item = ExtractedItem(
        item_id="item-breakout-mics",
        label="Breakout microphones for 5 speakers",
        location="breakout room",
        descriptions=[
            ItemDescriptionObservation(
                observation_id="obs-mics-desc",
                source=_source(
                    khalid_msg_id,
                    "for 5 speakers ... plus sound obviously so people can hear them ... and mics",
                    sent_at=khalid_sent,
                ),
                confidence=ConfidenceLevel.HIGH,
                client_text="microphones for 5 speakers",
                human_review_required=True,
                review_reason=ReviewReason.CATALOG_UNKNOWN,
            ),
        ],
        quantities=[
            QuantityObservation(
                observation_id="obs-mics-qty",
                source=_source(
                    khalid_msg_id,
                    "for 5 speakers",
                    sent_at=khalid_sent,
                ),
                confidence=ConfidenceLevel.HIGH,
                quantity=QuantityValue(
                    raw_text="5 speakers",
                    value=Decimal("5"),
                    unit=UnitOfMeasure.UNIT,
                ),
            ),
        ],
        catalog_presence=CatalogPresence.LIKELY_NOT_IN_CATALOG,
    )

    # Add the 5-speaker count as a requirement on the breakout lounge item
    # so it is preserved as a structured constraint
    breakout_lounge = next(i for i in extra_items if i.item_id == "item-breakout-lounge")
    breakout_lounge.requirements.append(
        RequirementObservation(
            observation_id="obs-breakout-speakers",
            source=_source(
                khalid_msg_id,
                "nice lounge feel for 5 speakers",
                sent_at=khalid_sent,
            ),
            confidence=ConfidenceLevel.HIGH,
            requirement_type=RequirementType.OPERATIONAL,
            description="5 speakers on panel; lounge setup required",
            is_mandatory=True,
        )
    )

    return base.model_copy(
        update={
            "items": base.items + extra_items + [microphone_item],
            "global_requirements": base.global_requirements + non_quoteable_global_reqs,
        }
    )
