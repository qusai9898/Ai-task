"""Tests for end-to-end quoting pipeline."""

from pathlib import Path
from unittest.mock import MagicMock

from app.extractor import BriefExtractor
from app.models import BriefExtraction
from app.pipeline import QuotingPipeline
from app.quote_models import QuoteStatus
from tests.fixtures import build_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def test_pipeline_from_extraction_without_openai():
    pipeline = QuotingPipeline(catalog_path=CATALOG_CSV)
    extraction = build_nexus_brief_extraction()
    quote = pipeline.generate_quote_from_extraction(extraction, quote_id="quote-test-001")

    assert quote.quote_id == "quote-test-001"
    assert quote.extraction_id == extraction.extraction_id
    assert quote.status == QuoteStatus.BLOCKED
    assert quote.lines


def test_pipeline_from_text_mocks_extractor():
    fixture = build_nexus_brief_extraction()
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_parsed = fixture
    mock_client.responses.parse.return_value = mock_response

    extractor = BriefExtractor(client=mock_client, catalog_path=CATALOG_CSV)
    pipeline = QuotingPipeline(catalog_path=CATALOG_CSV, extractor=extractor)

    extraction, quote = pipeline.generate_quote_from_text(
        brief_text="Nexus Ventures forum brief",
        source_document="data/client_brief.pdf",
        extraction_id="extract-pipeline",
        quote_id="quote-pipeline",
    )

    mock_client.responses.parse.assert_called_once()
    assert extraction.extraction_id == "extract-pipeline"
    assert quote.quote_id == "quote-pipeline"
    assert len(quote.lines) > 0
