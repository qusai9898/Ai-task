"""Command-line entry points for extraction and quote generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.extractor import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_PROVIDER,
    BriefExtractor,
    ExtractionError,
)
from app.pipeline import QuotingPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AI quoting tool — extract briefs and generate structured quotes.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract structured observations from a client brief PDF",
    )
    extract_parser.add_argument(
        "pdf_path",
        nargs="?",
        type=Path,
        default=Path("data/client_brief.pdf"),
    )
    extract_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output/extraction.json"),
    )
    extract_parser.add_argument(
        "--provider",
        choices=["gemini", "ollama", "openai"],
        default=DEFAULT_PROVIDER,
        help="LLM provider (default: gemini)",
    )
    extract_parser.add_argument("--model", default=None, help="LLM model name")
    extract_parser.add_argument(
        "--base-url",
        default=DEFAULT_OLLAMA_BASE_URL,
        help="Ollama base URL (default: http://localhost:11434, used only for ollama)",
    )
    extract_parser.add_argument(
        "--api-key",
        default=None,
        help="API key for Gemini or OpenAI (optional, overrides environment variables)",
    )
    extract_parser.add_argument("--extraction-id", default=None)
    extract_parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/recipe_catalog.csv"),
    )

    quote_parser = subparsers.add_parser(
        "quote",
        help="Generate a structured quote from a client brief PDF (uses Gemini by default)",
    )
    quote_parser.add_argument(
        "pdf_path",
        nargs="?",
        type=Path,
        default=Path("data/client_brief.pdf"),
    )
    quote_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output/quote.json"),
    )
    quote_parser.add_argument(
        "--extraction-output",
        type=Path,
        default=Path("output/extraction.json"),
        help="Optional path to also save the intermediate extraction JSON",
    )
    quote_parser.add_argument(
        "--provider",
        choices=["gemini", "ollama", "openai"],
        default=DEFAULT_PROVIDER,
        help="LLM provider (default: gemini)",
    )
    quote_parser.add_argument("--model", default=None, help="LLM model name")
    quote_parser.add_argument(
        "--base-url",
        default=DEFAULT_OLLAMA_BASE_URL,
        help="Ollama base URL (default: http://localhost:11434, used only for ollama)",
    )
    quote_parser.add_argument(
        "--api-key",
        default=None,
        help="API key for Gemini or OpenAI (optional, overrides environment variables)",
    )
    quote_parser.add_argument("--extraction-id", default=None)
    quote_parser.add_argument("--quote-id", default=None)
    quote_parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/recipe_catalog.csv"),
    )

    quote_from_json = subparsers.add_parser(
        "quote-from-json",
        help="Generate a quote from an existing extraction JSON file (no LLM call)",
    )
    quote_from_json.add_argument(
        "extraction_path",
        type=Path,
        help="Path to BriefExtraction JSON",
    )
    quote_from_json.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output/quote.json"),
    )
    quote_from_json.add_argument("--quote-id", default=None)
    quote_from_json.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/recipe_catalog.csv"),
    )

    return parser


def cmd_extract(args: argparse.Namespace) -> int:
    extractor = BriefExtractor(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        catalog_path=args.catalog,
    )
    try:
        extraction = extractor.extract_from_pdf(
            pdf_path=args.pdf_path,
            extraction_id=args.extraction_id,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ExtractionError as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(extraction.model_dump_json(indent=2), encoding="utf-8")
    print(f"Extraction written to {args.output}")
    return 0


def cmd_quote(args: argparse.Namespace) -> int:
    extractor = BriefExtractor(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        catalog_path=args.catalog,
    )
    pipeline = QuotingPipeline(catalog_path=args.catalog, extractor=extractor)

    try:
        extraction, quote = pipeline.generate_quote_from_pdf(
            pdf_path=args.pdf_path,
            extraction_id=args.extraction_id,
            quote_id=args.quote_id,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ExtractionError as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(quote.model_dump_json(indent=2), encoding="utf-8")

    if args.extraction_output:
        args.extraction_output.parent.mkdir(parents=True, exist_ok=True)
        args.extraction_output.write_text(
            extraction.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(f"Extraction written to {args.extraction_output}")

    print(f"Quote written to {args.output} (status: {quote.status.value})")
    return 0


def cmd_quote_from_json(args: argparse.Namespace) -> int:
    from app.models import BriefExtraction

    if not args.extraction_path.is_file():
        print(f"Error: file not found: {args.extraction_path}", file=sys.stderr)
        return 1

    extraction = BriefExtraction.model_validate_json(
        args.extraction_path.read_text(encoding="utf-8")
    )
    pipeline = QuotingPipeline(catalog_path=args.catalog)
    quote = pipeline.generate_quote_from_extraction(
        extraction,
        quote_id=args.quote_id,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(quote.model_dump_json(indent=2), encoding="utf-8")
    print(f"Quote written to {args.output} (status: {quote.status.value})")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "extract":
        return cmd_extract(args)
    if args.command == "quote":
        return cmd_quote(args)
    if args.command == "quote-from-json":
        return cmd_quote_from_json(args)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
