"""End-to-end pipeline: PDF → extraction → quote."""

from __future__ import annotations

from pathlib import Path

from app.extractor import BriefExtractor
from app.models import BriefExtraction
from app.quote_generator import DEFAULT_CATALOG_PATH, QuoteGenerator
from app.quote_models import Quote
from app.resolution import ResolutionSet


class QuotingPipeline:
    """Orchestrate brief extraction and deterministic quote generation."""

    def __init__(
        self,
        catalog_path: Path | str = DEFAULT_CATALOG_PATH,
        extractor: BriefExtractor | None = None,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.extractor = extractor
        self.quote_generator = QuoteGenerator.from_catalog_path(self.catalog_path)

    def generate_quote_from_extraction(
        self,
        extraction: BriefExtraction,
        quote_id: str | None = None,
        resolutions: ResolutionSet | None = None,
    ) -> Quote:
        return self.quote_generator.generate(
            extraction,
            quote_id=quote_id,
            resolutions=resolutions,
        )

    def generate_quote_from_pdf(
        self,
        pdf_path: Path | str,
        extraction_id: str | None = None,
        quote_id: str | None = None,
    ) -> tuple[BriefExtraction, Quote]:
        if self.extractor is None:
            self.extractor = BriefExtractor(catalog_path=self.catalog_path)

        extraction = self.extractor.extract_from_pdf(
            pdf_path=pdf_path,
            extraction_id=extraction_id,
        )
        quote = self.quote_generator.generate(extraction, quote_id=quote_id)
        return extraction, quote

    def generate_quote_from_text(
        self,
        brief_text: str,
        source_document: str,
        extraction_id: str | None = None,
        quote_id: str | None = None,
    ) -> tuple[BriefExtraction, Quote]:
        if self.extractor is None:
            self.extractor = BriefExtractor(catalog_path=self.catalog_path)

        extraction = self.extractor.extract_from_text(
            brief_text=brief_text,
            source_document=source_document,
            extraction_id=extraction_id,
        )
        quote = self.quote_generator.generate(extraction, quote_id=quote_id)
        return extraction, quote
