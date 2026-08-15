"""Helpers for loading catalog metadata into extraction prompts."""

from __future__ import annotations

import csv
from pathlib import Path


def format_catalog_for_prompt(catalog_path: Path | str) -> str:
    """
    Build a lightweight catalog reference for the LLM prompt.

    This is context for tentative suggestions only — not authoritative matching.
    """

    path = Path(catalog_path)
    if not path.is_file():
        return "Catalog file not found."

    lines: list[str] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            code = row.get("recipe_code", "").strip()
            name = row.get("recipe_name", "").strip()
            category = row.get("category", "").strip()
            unit = row.get("unit", "").strip()
            if code and name:
                lines.append(f"- {code}: {name} ({category}, unit={unit})")

    if not lines:
        return "Catalog file is empty."

    return "\n".join(lines)
