"""Tests for end-to-end quoting pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from app.extractor import BriefExtractor
from app.pipeline import QuotingPipeline
from app.quote_models import QuoteStatus
from tests.fixtures import build_nexus_brief_extraction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_CSV = PROJECT_ROOT / "data" / "recipe_catalog.csv"


def test_pipeline_from_extraction_without_llm():
    pipeline = QuotingPipeline(catalog_path=CATALOG_CSV)
    extraction = build_nexus_brief_extraction()
    quote = pipeline.generate_quote_from_extraction(extraction, quote_id="quote-test-001")

    assert quote.quote_id == "quote-test-001"
    assert quote.extraction_id == extraction.extraction_id
    assert quote.status == QuoteStatus.REQUIRES_REVIEW
    assert quote.lines


def test_pipeline_from_text_mocks_gemini_extractor():
    fixture = build_nexus_brief_extraction()
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = fixture.model_dump_json()
    mock_client.models.generate_content.return_value = mock_response

    extractor = BriefExtractor(
        provider="gemini",
        client=mock_client,
        catalog_path=CATALOG_CSV,
    )
    pipeline = QuotingPipeline(catalog_path=CATALOG_CSV, extractor=extractor)

    extraction, quote = pipeline.generate_quote_from_text(
        brief_text="Nexus Ventures forum brief",
        source_document="data/client_brief.pdf",
        extraction_id="extract-pipeline-gemini",
        quote_id="quote-pipeline-gemini",
    )

    mock_client.models.generate_content.assert_called_once()
    assert extraction.extraction_id == "extract-pipeline-gemini"
    assert quote.quote_id == "quote-pipeline-gemini"
    assert len(quote.lines) > 0


def test_pipeline_from_text_mocks_openai_extractor():
    fixture = build_nexus_brief_extraction()
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_parsed = fixture
    mock_client.responses.parse.return_value = mock_response

    extractor = BriefExtractor(
        provider="openai",
        client=mock_client,
        catalog_path=CATALOG_CSV,
    )
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


def test_pipeline_from_text_mocks_ollama_extractor():
    fixture = build_nexus_brief_extraction()
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "model": "qwen3:4b",
        "message": {
            "role": "assistant",
            "content": fixture.model_dump_json(),
        },
    }
    mock_client.post.return_value = mock_response

    extractor = BriefExtractor(
        provider="ollama",
        client=mock_client,
        catalog_path=CATALOG_CSV,
    )
    pipeline = QuotingPipeline(catalog_path=CATALOG_CSV, extractor=extractor)

    extraction, quote = pipeline.generate_quote_from_text(
        brief_text="Nexus Ventures forum brief",
        source_document="data/client_brief.pdf",
        extraction_id="extract-pipeline-ollama",
        quote_id="quote-pipeline-ollama",
    )

    mock_client.post.assert_called_once()
    assert extraction.extraction_id == "extract-pipeline-ollama"
    assert quote.quote_id == "quote-pipeline-ollama"
    assert len(quote.lines) > 0
