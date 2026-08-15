"""PDF text extraction utilities."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


class PdfTextExtractionError(Exception):
    """Raised when no usable text can be extracted from a PDF."""


def extract_text_from_pdf(path: Path | str) -> str:
    """Extract plain text from every page of a PDF file."""

    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    page_texts: list[str] = []

    for page in reader.pages:
        text = page.extract_text()
        if text:
            page_texts.append(text.strip())

    combined = "\n\n".join(page_texts).strip()
    if not combined:
        raise PdfTextExtractionError(f"No text extracted from PDF: {pdf_path}")

    return combined
