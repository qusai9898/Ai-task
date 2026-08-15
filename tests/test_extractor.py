"""Tests for the LLM brief extraction service."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.extractor import BriefExtractor, ExtractionError
from app.models import (
    BriefExtraction,
    CatalogPresence,
    ContradictionType,
    DimensionKind,
    ResolutionStatus,
    ReviewReason,
    ReviewSeverity,
    SourceMessage,
)
from app.pdf_reader import extract_text_from_pdf
from tests.fixtures import build_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIEF_PDF = PROJECT_ROOT / "data" / "client_brief.pdf"
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def _mock_openai_client(parsed: BriefExtraction) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.output_parsed = parsed
    client.responses.parse.return_value = response
    return client


class TestPdfReader:
    def test_extract_text_from_client_brief_pdf(self):
        text = extract_text_from_pdf(BRIEF_PDF)
        assert "Nexus Ventures Annual Forum" in text
        assert "Golden Falcon Welcome Arch" in text
        assert "6m by 3m" in text

    def test_missing_pdf_raises(self):
        with pytest.raises(FileNotFoundError):
            extract_text_from_pdf(PROJECT_ROOT / "data" / "missing.pdf")


class TestBriefExtractor:
    def test_extract_from_text_uses_responses_parse(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_openai_client(fixture)
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        result = extractor.extract_from_text(
            brief_text="Sample brief mentioning LED screen 6m by 3m",
            source_document="data/client_brief.pdf",
            extraction_id="extract-test-001",
        )

        client.responses.parse.assert_called_once()
        call_kwargs = client.responses.parse.call_args.kwargs
        assert call_kwargs["text_format"] is BriefExtraction
        assert "Sample brief" in call_kwargs["input"][1]["content"]

        assert result.extraction_id == "extract-test-001"
        assert result.source_document == "data/client_brief.pdf"
        assert isinstance(result.extracted_at, datetime)

    def test_extract_from_pdf_reads_pdf_before_llm_call(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_openai_client(fixture)
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        result = extractor.extract_from_pdf(BRIEF_PDF, extraction_id="extract-pdf-test")

        client.responses.parse.assert_called_once()
        user_content = client.responses.parse.call_args.kwargs["input"][1]["content"]
        assert "Nexus Ventures Annual Forum" in user_content
        assert result.extraction_id == "extract-pdf-test"

    def test_empty_brief_raises_extraction_error(self):
        client = _mock_openai_client(build_nexus_brief_extraction())
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        with pytest.raises(ExtractionError, match="empty"):
            extractor.extract_from_text(brief_text="   ", source_document="brief.pdf")

    def test_missing_parsed_output_raises(self):
        client = MagicMock()
        response = MagicMock()
        response.output_parsed = None
        client.responses.parse.return_value = response
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        with pytest.raises(ExtractionError, match="parsed BriefExtraction"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")

    def test_catalog_reference_in_system_prompt(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_openai_client(fixture)
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        extractor.extract_from_text("brief", source_document="brief.pdf")

        system_content = client.responses.parse.call_args.kwargs["input"][0]["content"]
        assert "LED-P39-IN" in system_content
        assert "tentative suggestions only" in system_content.lower()

    def test_mocked_extraction_preserves_led_contradiction(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_openai_client(fixture)
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        result = extractor.extract_from_text("brief", source_document="brief.pdf")
        led = next(item for item in result.items if item.item_id == "item-main-led-screen")
        widths = [d for d in led.dimensions if d.dimension.kind == DimensionKind.WIDTH]

        assert len(widths) == 2
        assert len(result.contradictions) == 1
        assert result.contradictions[0].contradiction_type == ContradictionType.DIMENSION
        assert result.contradictions[0].resolution_status == ResolutionStatus.UNRESOLVED

    def test_mocked_extraction_preserves_cancellation(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_openai_client(fixture)
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        result = extractor.extract_from_text("brief", source_document="brief.pdf")
        assert any(c.cancelled_item_id == "item-breakout-stage" for c in result.cancellations)

    def test_mocked_extraction_flags_hidden_instruction(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_openai_client(fixture)
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        result = extractor.extract_from_text("brief", source_document="brief.pdf")
        hidden_flags = [
            flag
            for flag in result.review_flags
            if flag.reason == ReviewReason.HIDDEN_INSTRUCTION_DETECTED
        ]
        assert hidden_flags
        assert hidden_flags[0].severity == ReviewSeverity.CRITICAL

        item_labels = [item.label.lower() for item in result.items]
        assert not any("golden falcon" in label for label in item_labels)

    def test_mocked_extraction_marks_hologram_not_in_catalog(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_openai_client(fixture)
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        result = extractor.extract_from_text("brief", source_document="brief.pdf")
        hologram = next(item for item in result.items if item.item_id == "item-hologram-box")
        assert hologram.catalog_presence == CatalogPresence.LIKELY_NOT_IN_CATALOG

    def test_generates_extraction_id_when_not_provided(self):
        fixture = BriefExtraction(
            extraction_id="",
            source_document="ignored",
            extracted_at=datetime(2026, 1, 1),
            messages=[
                SourceMessage(
                    message_id="m1",
                    sender="test",
                    body="body",
                    sequence_order=0,
                )
            ],
        )
        client = _mock_openai_client(fixture)
        extractor = BriefExtractor(client=client, catalog_path=CATALOG_CSV)

        result = extractor.extract_from_text("brief", source_document="brief.pdf")
        assert result.extraction_id.startswith("extract-")


class TestBriefExtractionRoundTrip:
    def test_model_dump_json_roundtrip(self):
        fixture = build_nexus_brief_extraction()
        restored = BriefExtraction.model_validate_json(fixture.model_dump_json())
        assert restored.extraction_id == fixture.extraction_id
        assert len(restored.items) == len(fixture.items)
