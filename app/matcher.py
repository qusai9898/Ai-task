"""Deterministic catalog matching — no LLM involvement."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.catalog import RecipeCatalog
from app.models import (
    BriefExtraction,
    CatalogPresence,
    ExtractedItem,
    ObservationStatus,
    ReviewReason,
)
from app.quote_models import CatalogMatchResult, MatchMethod, MatchStatus


@dataclass(frozen=True)
class KeywordRule:
    recipe_code: str
    keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...] = ()
    predicate: Callable[[ExtractedItem, str], bool] | None = None


def _item_text(item: ExtractedItem) -> str:
    parts = [item.label, item.location or ""]
    for desc in item.descriptions:
        if desc.status != ObservationStatus.CANCELLED:
            parts.append(desc.client_text)
    return " ".join(parts).lower()


class CatalogMatcher:
    """Match extracted items to recipe catalog entries using rules."""

    KEYWORD_RULES: tuple[KeywordRule, ...] = (
        KeywordRule("STG-RAMP-STD", ("ramp", "wheelchair", "accessibility")),
        KeywordRule("STG-STAIR-4T", ("stair", "steps", "tread")),
        KeywordRule(
            "LED-P26-IN",
            ("led", "screen"),
            ("projector",),
            lambda item, text: "premium" in text or "p2.6" in text or "high-res" in text,
        ),
        KeywordRule(
            "LED-P39-IN",
            ("led screen", "led"),
            ("projector", "hologram"),
            lambda item, text: "premium" not in text and "p2.6" not in text,
        ),
        KeywordRule("AV-PROJ-15K", ("projector",)),
        KeywordRule(
            "AV-PA-LRG",
            ("pa sound", "sound system", "line array"),
        ),
        KeywordRule("AV-PA-MED", ("pa sound", "sound system", "proper sound")),
        KeywordRule("LGT-WASH-LED", ("moving light", "wash light", "moving head")),
        KeywordRule("LGT-UPL-BAT", ("uplight", "uplighter")),
        KeywordRule(
            "LGT-FOH-CONS",
            ("lighting console", "lighting operator", "operate all of this", "cue programming"),
        ),
        KeywordRule("FRN-TBL-RND", ("round table", "dinner seating")),
        KeywordRule("FRN-CHR-BQT", ("banquet chair", "chair with cover")),
        KeywordRule("FRN-SOFA-LNG", ("sofa", "lounge sofa", "lounge feel", "lounge set")),
        KeywordRule("FRN-CTL-HIGH", ("cocktail", "high table")),
        KeywordRule("SCN-REG-DESK", ("registration desk", "registration area")),
        KeywordRule("PRN-BACK-SQM", ("backdrop",)),
        KeywordRule("PRN-FLR-VNL", ("floor vinyl", "floor sticker", "logo printed on the floor")),
        KeywordRule("PWR-GEN-100", ("generator", "backup power")),
        KeywordRule("CRW-TECH-DAY", ("av technician", "technician", "sound engineer")),
        KeywordRule("CRW-STGH-DAY", ("stagehand", "general crew", "crew")),
        KeywordRule("STG-DECK-1x1", ("stage deck", "modular stage")),
        KeywordRule("STG-DECK-1x1", ("stage",), ("second stage", "breakout stage")),
    )

    NON_CATALOG_KEYWORDS: tuple[str, ...] = ("hologram", "golden falcon")

    def __init__(self, catalog: RecipeCatalog) -> None:
        self.catalog = catalog
        self._cancelled_item_ids = set()

    def match_extraction(self, extraction: BriefExtraction) -> list[CatalogMatchResult]:
        self._cancelled_item_ids = {
            c.cancelled_item_id for c in extraction.cancellations if c.cancelled_item_id
        }
        return [self.match_item(item) for item in extraction.items]

    def match_item(self, item: ExtractedItem) -> CatalogMatchResult:
        if item.item_id in self._cancelled_item_ids:
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.CANCELLED,
                notes="Item cancelled in brief thread.",
            )

        if item.item_id == "item-guest-count" or "guest" in item.label.lower() and not item.descriptions:
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.SKIPPED,
                notes="Headcount used for calculations, not a catalog line.",
            )

        text = _item_text(item)

        if any(keyword in text for keyword in self.NON_CATALOG_KEYWORDS):
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.NO_MATCH,
                match_method=MatchMethod.MANUAL_REQUIRED,
                human_review_required=True,
                review_reason=ReviewReason.CATALOG_UNKNOWN,
                notes="Requested item not represented in recipe catalog.",
            )

        if item.catalog_presence == CatalogPresence.LIKELY_NOT_IN_CATALOG:
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.NO_MATCH,
                match_method=MatchMethod.MANUAL_REQUIRED,
                human_review_required=True,
                review_reason=ReviewReason.CATALOG_UNKNOWN,
                notes="Extractor flagged item as likely not in catalog.",
            )

        validated_suggestions = [
            s.recipe_code
            for s in item.suggested_catalog_codes
            if s.recipe_code in self.catalog.codes()
        ]
        if len(validated_suggestions) == 1:
            code = validated_suggestions[0]
            recipe = self.catalog.get(code)
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.MATCHED,
                recipe_code=code,
                recipe_name=recipe.recipe_name if recipe else None,
                match_method=MatchMethod.SUGGESTION_VALIDATED,
            )
        if len(validated_suggestions) > 1:
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.AMBIGUOUS,
                candidate_codes=validated_suggestions,
                human_review_required=True,
                review_reason=ReviewReason.CATALOG_UNKNOWN,
                notes="Multiple validated LLM suggestions; deterministic matcher did not choose.",
            )

        keyword_matches = self._keyword_matches(item, text)
        if len(keyword_matches) == 1:
            code = keyword_matches[0]
            recipe = self.catalog.get(code)
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.MATCHED,
                recipe_code=code,
                recipe_name=recipe.recipe_name if recipe else None,
                match_method=MatchMethod.KEYWORD_RULE,
            )

        if len(keyword_matches) > 1:
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.AMBIGUOUS,
                candidate_codes=keyword_matches,
                human_review_required=True,
                review_reason=ReviewReason.CATALOG_UNKNOWN,
                notes="Multiple keyword rules matched.",
            )

        explicit_code = self._explicit_mapping(item, text)
        if explicit_code:
            recipe = self.catalog.get(explicit_code)
            return CatalogMatchResult(
                item_id=item.item_id,
                item_label=item.label,
                match_status=MatchStatus.MATCHED,
                recipe_code=explicit_code,
                recipe_name=recipe.recipe_name if recipe else None,
                match_method=MatchMethod.EXPLICIT_MAPPING,
            )

        return CatalogMatchResult(
            item_id=item.item_id,
            item_label=item.label,
            match_status=MatchStatus.NO_MATCH,
            human_review_required=True,
            review_reason=ReviewReason.CATALOG_UNKNOWN,
            notes="No deterministic catalog match found.",
        )

    def _keyword_matches(self, item: ExtractedItem, text: str) -> list[str]:
        matches: list[str] = []
        for rule in self.KEYWORD_RULES:
            if rule.recipe_code not in self.catalog.codes():
                continue
            if any(ex in text for ex in rule.exclude_keywords):
                continue
            if not any(kw in text for kw in rule.keywords):
                continue
            if rule.predicate and not rule.predicate(item, text):
                continue
            if rule.recipe_code not in matches:
                matches.append(rule.recipe_code)
        return matches

    def _explicit_mapping(self, item: ExtractedItem, text: str) -> str | None:
        location = (item.location or "").lower()

        if "main hall" in location and re.search(r"\bstage\b", text) and "second" not in text:
            if "deck" not in text and "modular" not in text:
                return "STG-DECK-1x1"

        if item.item_id == "item-main-stage":
            return "STG-DECK-1x1"

        return None
