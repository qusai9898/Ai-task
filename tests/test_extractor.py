"""Tests for the LLM brief extraction service supporting Gemini, Ollama, and OpenAI."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.extractor import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    BriefExtractor,
    ExtractionError,
)
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


def _mock_gemini_client(
    response_text: str | None = None,
    side_effect: Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    if side_effect:
        client.models.generate_content.side_effect = side_effect
    else:
        response = MagicMock()
        response.text = response_text if response_text is not None else ""
        client.models.generate_content.return_value = response
    return client


def _mock_openai_client(parsed: BriefExtraction) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.output_parsed = parsed
    client.responses.parse.return_value = response
    return client


def _mock_ollama_client(
    response_json: dict | None = None,
    status_code: int = 200,
    text: str = "",
    side_effect: Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    if side_effect:
        client.post.side_effect = side_effect
    else:
        response = MagicMock()
        response.status_code = status_code
        response.text = text or (json.dumps(response_json) if response_json else "")
        response.json.return_value = response_json or {}
        client.post.return_value = response
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


class TestBriefExtractorGemini:
    def test_default_configuration_is_gemini(self):
        extractor = BriefExtractor(catalog_path=CATALOG_CSV)
        assert extractor.provider == "gemini"
        assert extractor.model == "gemini-3.5-flash-lite"
        assert extractor.model == DEFAULT_GEMINI_MODEL

    def test_gemini_extract_from_text_success(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_gemini_client(response_text=fixture.model_dump_json())
        extractor = BriefExtractor(
            provider="gemini",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        result = extractor.extract_from_text(
            brief_text="Sample brief mentioning LED screen 6m by 3m",
            source_document="data/client_brief.pdf",
            extraction_id="extract-gemini-001",
        )

        client.models.generate_content.assert_called_once()
        call_kwargs = client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3.5-flash-lite"
        assert "Sample brief" in call_kwargs["contents"]

        assert result.extraction_id == "extract-gemini-001"
        assert result.source_document == "data/client_brief.pdf"
        assert len(result.items) == len(fixture.items)

    def test_gemini_extract_from_pdf_success(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_gemini_client(response_text=fixture.model_dump_json())
        extractor = BriefExtractor(
            provider="gemini",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        result = extractor.extract_from_pdf(BRIEF_PDF, extraction_id="extract-pdf-gemini")

        client.models.generate_content.assert_called_once()
        user_prompt = client.models.generate_content.call_args.kwargs["contents"]
        assert "Nexus Ventures Annual Forum" in user_prompt
        assert result.extraction_id == "extract-pdf-gemini"

    def test_gemini_config_and_schema_passed(self):
        fixture = build_nexus_brief_extraction()
        client = _mock_gemini_client(response_text=fixture.model_dump_json())
        extractor = BriefExtractor(
            provider="gemini",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        extractor.extract_from_text("brief", source_document="brief.pdf")

        config = client.models.generate_content.call_args.kwargs["config"]
        assert config.response_mime_type == "application/json"
        assert isinstance(config.response_schema, dict)
        assert config.response_schema.get("type") == "OBJECT"
        assert "messages" in config.response_schema.get("properties", {})
        assert "items" in config.response_schema.get("properties", {})
        assert config.temperature == 0.0
        assert config.seed == 42
        assert "LED-P39-IN" in config.system_instruction

    def test_gemini_schema_has_no_unsupported_openapi_constructs(self):
        from app.extractor import get_gemini_extraction_schema

        schema = get_gemini_extraction_schema()
        assert "$defs" not in schema
        assert "$ref" not in str(schema)
        assert "anyOf" not in str(schema)
        assert "pattern" not in str(schema)

        valid_types = {"STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY", "OBJECT"}

        def check_node(node, path="root"):
            if not isinstance(node, dict):
                return
            if "type" in node:
                assert node["type"] in valid_types, f"Invalid type at {path}: {node['type']}"
            if "properties" in node:
                for k, v in node["properties"].items():
                    check_node(v, f"{path}.{k}")
            if "items" in node:
                check_node(node["items"], f"{path}.items")

        check_node(schema)

    def test_gemini_strips_markdown_code_blocks(self):
        fixture = build_nexus_brief_extraction()
        wrapped = f"```json\n{fixture.model_dump_json()}\n```"
        client = _mock_gemini_client(response_text=wrapped)
        extractor = BriefExtractor(
            provider="gemini",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        result = extractor.extract_from_text("brief", source_document="brief.pdf")
        assert len(result.items) == len(fixture.items)

    def test_gemini_missing_api_key_raises_clear_error(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        extractor = BriefExtractor(
            provider="gemini",
            client=None,
            api_key=None,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError, match="GEMINI_API_KEY is not set"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")

    def test_gemini_api_error_raises_extraction_error(self):
        client = _mock_gemini_client(side_effect=Exception("API connection timeout"))
        extractor = BriefExtractor(
            provider="gemini",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError, match="Gemini API request failed"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")

    def test_gemini_empty_response_raises_extraction_error(self):
        client = _mock_gemini_client(response_text="")
        extractor = BriefExtractor(
            provider="gemini",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError, match="did not contain text content"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")

    def test_gemini_invalid_json_raises_extraction_error(self):
        client = _mock_gemini_client(response_text='{"invalid": true}')
        extractor = BriefExtractor(
            provider="gemini",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError, match="Failed to validate BriefExtraction schema"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")


class TestBriefExtractorOpenAI:
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
        led = next(item for item in result.items if item.item_id == "item_main_led_screen")
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
        hologram = next(item for item in result.items if item.item_id == "item_hologram_box")
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


class TestBriefExtractorOllama:
    def test_unsupported_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            BriefExtractor(provider="anthropic", catalog_path=CATALOG_CSV)

    def test_ollama_extract_from_text_success(self):
        fixture = build_nexus_brief_extraction()
        ollama_response = {
            "model": "qwen3:4b",
            "message": {
                "role": "assistant",
                "content": fixture.model_dump_json(),
            },
            "done": True,
        }
        client = _mock_ollama_client(ollama_response)
        extractor = BriefExtractor(
            provider="ollama",
            model="qwen3:4b",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        result = extractor.extract_from_text(
            brief_text="Sample brief text",
            source_document="data/client_brief.pdf",
            extraction_id="extract-ollama-001",
        )

        client.post.assert_called_once()
        call_args = client.post.call_args
        assert call_args[0][0] == f"{DEFAULT_OLLAMA_BASE_URL}/api/chat"
        payload = call_args[1]["json"]
        assert payload["model"] == "qwen3:4b"
        assert payload["stream"] is False
        assert payload["format"] == BriefExtraction.model_json_schema()
        assert "Sample brief text" in payload["messages"][1]["content"]

        assert result.extraction_id == "extract-ollama-001"
        assert result.source_document == "data/client_brief.pdf"
        assert len(result.items) == len(fixture.items)

    def test_ollama_extract_from_pdf_success(self):
        fixture = build_nexus_brief_extraction()
        ollama_response = {
            "model": "qwen3:4b",
            "message": {
                "role": "assistant",
                "content": fixture.model_dump_json(),
            },
            "done": True,
        }
        client = _mock_ollama_client(ollama_response)
        extractor = BriefExtractor(
            provider="ollama",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        result = extractor.extract_from_pdf(BRIEF_PDF, extraction_id="extract-pdf-ollama")

        client.post.assert_called_once()
        user_prompt = client.post.call_args[1]["json"]["messages"][1]["content"]
        assert "Nexus Ventures Annual Forum" in user_prompt
        assert result.extraction_id == "extract-pdf-ollama"

    def test_ollama_strips_markdown_code_blocks(self):
        fixture = build_nexus_brief_extraction()
        wrapped_content = f"```json\n{fixture.model_dump_json()}\n```"
        ollama_response = {
            "model": "qwen3:4b",
            "message": {
                "role": "assistant",
                "content": wrapped_content,
            },
            "done": True,
        }
        client = _mock_ollama_client(ollama_response)
        extractor = BriefExtractor(
            provider="ollama",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        result = extractor.extract_from_text("brief text", source_document="brief.pdf")
        assert len(result.items) == len(fixture.items)

    def test_ollama_connection_error_raises_clear_message(self):
        client = _mock_ollama_client(
            side_effect=httpx.ConnectError("Connection refused to port 11434")
        )
        extractor = BriefExtractor(
            provider="ollama",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError) as exc_info:
            extractor.extract_from_text("brief text", source_document="brief.pdf")

        error_message = str(exc_info.value)
        assert "Ollama server is not running or unreachable" in error_message
        assert "ollama serve" in error_message
        assert "qwen3:4b" in error_message

    def test_ollama_timeout_raises_clear_message(self):
        client = _mock_ollama_client(
            side_effect=httpx.TimeoutException("Read timeout after 120s")
        )
        extractor = BriefExtractor(
            provider="ollama",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError, match="timed out"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")

    def test_ollama_http_error_status_raises(self):
        client = _mock_ollama_client(status_code=404, text="model 'qwen3:4b' not found")
        extractor = BriefExtractor(
            provider="ollama",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError, match="HTTP error 404"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")

    def test_ollama_empty_response_content_raises(self):
        ollama_response = {
            "model": "qwen3:4b",
            "message": {"role": "assistant", "content": ""},
        }
        client = _mock_ollama_client(ollama_response)
        extractor = BriefExtractor(
            provider="ollama",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError, match="did not contain content"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")

    def test_ollama_invalid_json_raises(self):
        ollama_response = {
            "model": "qwen3:4b",
            "message": {
                "role": "assistant",
                "content": '{"invalid_schema": true}',
            },
        }
        client = _mock_ollama_client(ollama_response)
        extractor = BriefExtractor(
            provider="ollama",
            client=client,
            catalog_path=CATALOG_CSV,
        )

        with pytest.raises(ExtractionError, match="Failed to validate BriefExtraction schema"):
            extractor.extract_from_text("brief text", source_document="brief.pdf")


class TestBriefExtractionRoundTrip:
    def test_model_dump_json_roundtrip(self):
        fixture = build_nexus_brief_extraction()
        restored = BriefExtraction.model_validate_json(fixture.model_dump_json())
        assert restored.extraction_id == fixture.extraction_id
        assert len(restored.items) == len(fixture.items)
