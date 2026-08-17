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
    # Rules that share a `family` are treated as MUTUALLY EXCLUSIVE
    # alternatives for the same physical need (e.g. two LED pixel-pitch
    # options — you buy one, not both). A rule with family=None is always
    # an independent component: if it matches, it becomes its own quote
    # line and never competes with any other rule for "the" answer. Most
    # bundled real-world requests (stage + ramp + stairs, sound + lighting
    # + crew) are independent components, not alternatives, and must not
    # be forced through the same tie-break logic.
    family: str | None = None


def _item_text(item: ExtractedItem) -> str:
    parts = [item.label, item.location or ""]
    for desc in item.descriptions:
        if desc.status != ObservationStatus.CANCELLED:
            parts.append(desc.client_text)
    return " ".join(parts).lower()


def _family_key(rule: KeywordRule) -> str:
    return rule.family or rule.recipe_code


def _is_headcount_item(item: ExtractedItem) -> bool:
    """Identify informational guest attendance / headcount items that must not become quote lines."""
    label = item.label.lower().strip()
    item_id = item.item_id.lower().strip()

    headcount_ids = ("item_guest_headcount", "item_guest_count", "item_headcount", "item_attendees", "item_pax")
    if item_id in headcount_ids:
        return True

    # Exact labels
    if label in ("guests", "guest", "attendees", "headcount", "head count", "total guests", "guest count", "pax"):
        return True

    headcount_phrases = (
        "guest count",
        "headcount",
        "attendance",
        "attendee count",
        "expected attendees",
        "guest headcount",
        "guest volume",
        "number of guests",
        "guest numbers",
        "guest attendance",
        "headcount planning",
    )
    if any(phrase in label for phrase in headcount_phrases):
        if not any(f in label for f in ("chair", "table", "seat", "gift", "bag", "pass", "badge", "dinner", "catering", "sofa", "lounge", "drink", "cocktail")):
            return True

    if "guest" in label and any(w in label for w in ("plan", "assumption", "count", "estimate", "total")):
        if not any(f in label for f in ("chair", "table", "seat", "sofa", "lounge")):
            return True

    return False


class CatalogMatcher:
    """Match extracted items to recipe catalog entries using rules.

    An ExtractedItem is not guaranteed to map to a single catalog line.
    Clients routinely bundle several distinct physical needs into one
    sentence ("a stage... with steps and it must have the ramp"), and the
    extractor correctly captures that as one item with several
    suggested_catalog_codes. match_item() therefore returns a LIST of
    CatalogMatchResult — one per needed component — instead of picking a
    single "winner" per item. AMBIGUOUS is reserved for genuine ties: two
    or more catalog options competing for the *same* physical need.
    """

    KEYWORD_RULES: tuple[KeywordRule, ...] = (
        KeywordRule("STG-RAMP-STD", ("ramp", "wheelchair", "accessibility")),
        KeywordRule(
            "STG-STAIR-4T",
            ("stair", "steps", "tread"),
            predicate=lambda item, text: (
                ("stair" in item.label.lower() or "step" in item.label.lower() or "stage" not in item.label.lower())
                # Never fire on LED/screen/projector/backdrop items
                and not any(kw in item.label.lower() for kw in ("led", "screen", "projector", "backdrop", "vinyl", "floor"))
            ),
        ),
        KeywordRule(
            "LED-P26-IN",
            ("led", "screen"),
            ("projector",),
            lambda item, text: (
                ("p2.6" in text or "high-res" in text or "high resolution" in text)
                or (
                    "premium" in text
                    and not any(
                        neg in text
                        for neg in (
                            "doesn't need to be",
                            "does not need to be",
                            "don't need",
                            "not the super premium",
                            "not premium",
                            "no need for premium",
                            "normal indoor quality",
                            "normal quality",
                        )
                    )
                )
            ),
            family="led_screen",
        ),
        KeywordRule(
            "LED-P39-IN",
            ("led screen", "led"),
            ("projector", "hologram"),
            lambda item, text: "premium" not in text and "p2.6" not in text,
            family="led_screen",
        ),
        KeywordRule("AV-PROJ-15K", ("projector",)),
        KeywordRule(
            "AV-PA-LRG",
            ("pa sound", "sound system", "line array"),
            family="pa_sound",
        ),
        KeywordRule(
            "AV-PA-MED",
            ("pa sound", "sound system", "proper sound"),
            family="pa_sound",
        ),
        KeywordRule("LGT-WASH-LED", ("moving light", "wash light", "moving head")),
        KeywordRule("LGT-UPL-BAT", ("uplight", "uplighter")),
        KeywordRule(
            "LGT-FOH-CONS",
            ("lighting console", "lighting operator", "operate all of this", "cue programming"),
        ),
        # Round tables: only match items whose LABEL is clearly about tables, not chairs
        KeywordRule(
            "FRN-TBL-RND",
            ("round table", "dinner seating"),
            predicate=lambda item, text: not any(
                kw in item.label.lower() for kw in ("chair", "banquet chair", "seat cover")
            ),
        ),
        # Banquet chairs: only match items whose LABEL is clearly about chairs/seating covers,
        # not about tables
        KeywordRule(
            "FRN-CHR-BQT",
            ("banquet chair", "chair with cover", "dinner seating"),
            predicate=lambda item, text: not any(
                kw in item.label.lower() for kw in ("round table", "table", "dining table")
            ),
        ),
        KeywordRule("FRN-SOFA-LNG", ("sofa", "lounge sofa", "lounge feel", "lounge set")),
        KeywordRule("FRN-CTL-HIGH", ("cocktail", "high table")),
        KeywordRule("SCN-REG-DESK", ("registration desk", "registration area")),
        KeywordRule("PRN-BACK-SQM", ("backdrop",), exclude_keywords=("led",)),
        KeywordRule(
            "PRN-FLR-VNL",
            (
                "floor vinyl",
                "floor sticker",
                "logo printed on the floor",
                "floor logo",
                "floor print",
                "floor graphic",
                "floor branding",
                "entrance floor",
            ),
        ),
        KeywordRule("PWR-GEN-100", ("generator", "backup power")),
        KeywordRule("CRW-TECH-DAY", ("av technician", "technician", "sound engineer")),
        KeywordRule("CRW-STGH-DAY", ("stagehand", "general crew", "crew")),
        KeywordRule(
            "STG-DECK-1x1",
            ("stage deck", "modular stage", "stage", "main stage"),
            exclude_keywords=("second stage", "breakout stage"),
            predicate=lambda item, text: (
                "ramp" not in item.label.lower()
                and "stair" not in item.label.lower()
                # Never fire on LED/screen/projector/backdrop/floor items
                and not any(
                    kw in item.label.lower()
                    for kw in ("led", "screen", "projector", "backdrop", "vinyl", "floor")
                )
                # Bare "stage" is frequently just a contextual/spatial
                # reference ("to the side of the stage", "near the stage
                # for the Q&A", "for stage layout planning") rather than a
                # request for a physical stage deck. Only trust it when
                # the item's own label is genuinely about a stage, or a
                # strong unambiguous phrase appears anywhere in the text.
                and (
                    "stage" in item.label.lower()
                    or "stage deck" in text
                    or "modular stage" in text
                    or "main stage" in text
                )
            ),
        ),
    )

    NON_CATALOG_KEYWORDS: tuple[str, ...] = ("hologram", "golden falcon")

    def __init__(self, catalog: RecipeCatalog) -> None:
        self.catalog = catalog
        self._cancelled_item_ids = set()

    def match_extraction(self, extraction: BriefExtraction) -> list[CatalogMatchResult]:
        self._cancelled_item_ids = {
            c.cancelled_item_id for c in extraction.cancellations if c.cancelled_item_id
        }
        results: list[CatalogMatchResult] = []
        for item in extraction.items:
            results.extend(self.match_item(item))
        return results

    def match_text(self, text: str) -> str | None:
        """Best-effort deterministic match for freestanding requirement
        text that is not tied to any ExtractedItem -- e.g. a
        global_requirement description like "Backup power required for
        main hall AV (non-negotiable)". Global requirements have no
        item.label to evaluate a predicate against, so only keyword
        rules with no predicate are considered here."""
        lowered = text.lower()
        for rule in self.KEYWORD_RULES:
            if rule.predicate is not None:
                continue
            if rule.recipe_code not in self.catalog.codes():
                continue
            if any(ex in lowered for ex in rule.exclude_keywords):
                continue
            if any(kw in lowered for kw in rule.keywords):
                return rule.recipe_code
        return None

    def match_text_all(self, text: str) -> list[str]:
        """Like match_text, but returns EVERY matching predicate-less
        rule's recipe_code instead of stopping at the first hit. Needed
        because free text like "include all necessary crew and
        technicians" genuinely names two distinct catalog needs
        (CRW-STGH-DAY and CRW-TECH-DAY) -- match_text's first-match-wins
        behaviour was silently dropping whichever one didn't happen to
        come first in KEYWORD_RULES."""
        lowered = text.lower()
        codes: list[str] = []
        for rule in self.KEYWORD_RULES:
            if rule.predicate is not None:
                continue
            if rule.recipe_code not in self.catalog.codes():
                continue
            if any(ex in lowered for ex in rule.exclude_keywords):
                continue
            if any(kw in lowered for kw in rule.keywords):
                if rule.recipe_code not in codes:
                    codes.append(rule.recipe_code)
        return codes

    def match_item(self, item: ExtractedItem) -> list[CatalogMatchResult]:
        if item.item_id in self._cancelled_item_ids:
            return [
                CatalogMatchResult(
                    item_id=item.item_id,
                    item_label=item.label,
                    match_status=MatchStatus.CANCELLED,
                    notes="Item cancelled in brief thread.",
                )
            ]

        if _is_headcount_item(item):
            return [
                CatalogMatchResult(
                    item_id=item.item_id,
                    item_label=item.label,
                    match_status=MatchStatus.SKIPPED,
                    notes="Headcount / guest attendance is an informational planning variable, not a catalog or quote line.",
                )
            ]

        text = _item_text(item)

        if any(keyword in text for keyword in self.NON_CATALOG_KEYWORDS):
            return [
                CatalogMatchResult(
                    item_id=item.item_id,
                    item_label=item.label,
                    match_status=MatchStatus.NO_MATCH,
                    match_method=MatchMethod.MANUAL_REQUIRED,
                    human_review_required=True,
                    review_reason=ReviewReason.CATALOG_UNKNOWN,
                    notes="Requested item not represented in recipe catalog.",
                )
            ]

        if item.catalog_presence == CatalogPresence.LIKELY_NOT_IN_CATALOG:
            return [
                CatalogMatchResult(
                    item_id=item.item_id,
                    item_label=item.label,
                    match_status=MatchStatus.NO_MATCH,
                    match_method=MatchMethod.MANUAL_REQUIRED,
                    human_review_required=True,
                    review_reason=ReviewReason.CATALOG_UNKNOWN,
                    notes="Extractor flagged item as likely not in catalog.",
                )
            ]

        # DETERMINISTIC LAYER FIRST. The extractor's suggested_catalog_codes
        # are documented (see extractor.py SYSTEM_PROMPT_TEMPLATE) as
        # "tentative hints... never as confirmed matches" and must not
        # override our own keyword rules. But — unlike the previous
        # version of this matcher — finding several keyword-rule hits for
        # one item is NOT itself a sign of ambiguity. It's the normal,
        # expected shape of a bundled client request. Only hits that fall
        # in the SAME family (see KeywordRule.family) genuinely compete.
        validated_suggestions = [
            s.recipe_code
            for s in item.suggested_catalog_codes
            if s.recipe_code in self.catalog.codes()
        ]

        hits = self._keyword_rule_hits(item, text)
        results: list[CatalogMatchResult] = []
        processed_families: set[str] = set()

        if hits:
            families: dict[str, list[KeywordRule]] = {}
            for rule in hits:
                families.setdefault(_family_key(rule), []).append(rule)

            for family_key, family_hits in families.items():
                processed_families.add(family_key)

                if len(family_hits) == 1:
                    code = family_hits[0].recipe_code
                    recipe = self.catalog.get(code)
                    results.append(
                        CatalogMatchResult(
                            item_id=item.item_id,
                            item_label=item.label,
                            match_status=MatchStatus.MATCHED,
                            recipe_code=code,
                            recipe_name=recipe.recipe_name if recipe else None,
                            match_method=MatchMethod.KEYWORD_RULE,
                        )
                    )
                    continue

                # Multiple rules within the same family are true
                # alternatives for one physical need — let a validated LLM
                # suggestion break the tie if it narrows to exactly one.
                family_codes = [r.recipe_code for r in family_hits]
                narrowed = [c for c in validated_suggestions if c in family_codes]
                if len(narrowed) == 1:
                    code = narrowed[0]
                    recipe = self.catalog.get(code)
                    results.append(
                        CatalogMatchResult(
                            item_id=item.item_id,
                            item_label=item.label,
                            match_status=MatchStatus.MATCHED,
                            recipe_code=code,
                            recipe_name=recipe.recipe_name if recipe else None,
                            match_method=MatchMethod.KEYWORD_RULE,
                        )
                    )
                else:
                    results.append(
                        CatalogMatchResult(
                            item_id=item.item_id,
                            item_label=item.label,
                            match_status=MatchStatus.AMBIGUOUS,
                            candidate_codes=family_codes,
                            human_review_required=True,
                            review_reason=ReviewReason.CATALOG_UNKNOWN,
                            notes=f"Multiple '{family_key}' options matched and could not be resolved automatically.",
                        )
                    )
        else:
            explicit_code = self._explicit_mapping(item, text)
            if explicit_code:
                processed_families.add(explicit_code)
                recipe = self.catalog.get(explicit_code)
                results.append(
                    CatalogMatchResult(
                        item_id=item.item_id,
                        item_label=item.label,
                        match_status=MatchStatus.MATCHED,
                        recipe_code=explicit_code,
                        recipe_name=recipe.recipe_name if recipe else None,
                        match_method=MatchMethod.EXPLICIT_MAPPING,
                    )
                )

        # Any validated LLM suggestion that the deterministic layer never
        # even considered — i.e. it belongs to no family we already
        # processed above — represents a distinct bundled component the
        # keyword rules simply don't have a pattern for yet (e.g. "someone
        # to operate this on the day" -> a crew code). These are additional
        # lines, not competitors to what the keyword layer already found.
        code_family = {rule.recipe_code: _family_key(rule) for rule in self.KEYWORD_RULES}
        for code in validated_suggestions:
            family_key = code_family.get(code, code)
            if family_key in processed_families:
                continue
            processed_families.add(family_key)
            recipe = self.catalog.get(code)
            results.append(
                CatalogMatchResult(
                    item_id=item.item_id,
                    item_label=item.label,
                    match_status=MatchStatus.MATCHED,
                    recipe_code=code,
                    recipe_name=recipe.recipe_name if recipe else None,
                    match_method=MatchMethod.SUGGESTION_VALIDATED,
                )
            )

        if not results:
            results.append(
                CatalogMatchResult(
                    item_id=item.item_id,
                    item_label=item.label,
                    match_status=MatchStatus.NO_MATCH,
                    human_review_required=True,
                    review_reason=ReviewReason.CATALOG_UNKNOWN,
                    notes="No deterministic catalog match found.",
                )
            )

        return results

    def _keyword_rule_hits(self, item: ExtractedItem, text: str) -> list[KeywordRule]:
        hits: list[KeywordRule] = []
        seen_codes: set[str] = set()
        for rule in self.KEYWORD_RULES:
            if rule.recipe_code not in self.catalog.codes():
                continue
            if any(ex in text for ex in rule.exclude_keywords):
                continue
            if not any(kw in text for kw in rule.keywords):
                continue
            if rule.predicate and not rule.predicate(item, text):
                continue
            if rule.recipe_code in seen_codes:
                continue
            seen_codes.add(rule.recipe_code)
            hits.append(rule)
        return hits

    def _explicit_mapping(self, item: ExtractedItem, text: str) -> str | None:
        location = (item.location or "").lower()
        label = item.label.lower()

        strong_stage_phrase = (
            "stage deck" in text or "modular stage" in text or "main stage" in text
        )

        if (
            "main hall" in location
            and re.search(r"\bstage\b", text)
            and "second" not in text
            and "deck" not in text
            and "modular" not in text
            and ("stage" in label or strong_stage_phrase)
        ):
            return "STG-DECK-1x1"

        if item.item_id == "item_main_stage" or "stage" in label:
            if not any(ex in text for ex in ("second", "breakout", "ramp", "stair")):
                return "STG-DECK-1x1"

        return None
