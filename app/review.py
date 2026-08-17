"""Human-review and ambiguity aggregation for quotes."""

from __future__ import annotations

from app.models import (
    BriefExtraction,
    ResolutionStatus,
    ReviewFlag,
    ReviewReason,
    ReviewSeverity,
)
from app.quote_models import (
    CatalogMatchResult,
    MatchStatus,
    PricedLine,
    QuoteLineStatus,
    QuoteStatus,
    QuantityResult,
)


class ReviewAggregator:
    """Collect review signals and determine quote readiness."""

    def aggregate_flags(
        self,
        extraction: BriefExtraction,
        matches: list[CatalogMatchResult],
        quantities: list[QuantityResult],
        lines: list[PricedLine],
        resolved_item_ids: set[str] | None = None,
    ) -> list[ReviewFlag]:
        resolved = resolved_item_ids or set()
        flags: list[ReviewFlag] = list(extraction.review_flags)

        for contradiction in extraction.contradictions:
            if contradiction.resolution_status == ResolutionStatus.UNRESOLVED:
                if resolved and all(
                    item_id in resolved for item_id in contradiction.item_ids
                ):
                    continue
                flags.append(
                    ReviewFlag(
                        flag_id=f"quote-{contradiction.contradiction_id}",
                        reason=ReviewReason.CONTRADICTORY_INSTRUCTIONS,
                        message=contradiction.summary,
                        severity=ReviewSeverity.WARNING,
                        related_observation_ids=contradiction.observation_ids,
                        related_item_ids=contradiction.item_ids,
                    )
                )

        for item in extraction.items:
            if item.item_id in resolved:
                continue
            for obs in (
                item.descriptions
                + item.quantities
                + item.dimensions
                + item.requirements
            ):
                if obs.human_review_required and obs.review_reason:
                    flags.append(
                        ReviewFlag(
                            flag_id=f"quote-obs-{obs.observation_id}",
                            reason=obs.review_reason,
                            message=f"Observation requires review on item '{item.label}'.",
                            severity=ReviewSeverity.WARNING,
                            related_observation_ids=[obs.observation_id],
                            related_item_ids=[item.item_id],
                            source=obs.source,
                        )
                    )

        for match in matches:
            if match.human_review_required and match.review_reason:
                flags.append(
                    ReviewFlag(
                        flag_id=f"quote-match-{match.item_id}",
                        reason=match.review_reason,
                        message=match.notes or f"Catalog match issue for '{match.item_label}'.",
                        severity=ReviewSeverity.WARNING,
                        related_item_ids=[match.item_id],
                    )
                )

        for qty in quantities:
            if qty.item_id in resolved:
                continue
            if qty.requires_review and qty.review_reason:
                flags.append(
                    ReviewFlag(
                        flag_id=f"quote-qty-{qty.item_id}-{qty.recipe_code}",
                        reason=qty.review_reason,
                        message=qty.calculation_notes,
                        severity=ReviewSeverity.WARNING,
                        related_item_ids=[qty.item_id],
                    )
                )

        for line in lines:
            if line.item_id and line.item_id in resolved:
                continue
            if line.status in (
                QuoteLineStatus.REQUIRES_REVIEW,
                QuoteLineStatus.UNMATCHED,
                QuoteLineStatus.CUSTOM_ESTIMATE,
            ):
                for reason in line.review_reasons:
                    flags.append(
                        ReviewFlag(
                            flag_id=f"quote-line-{line.item_id or line.recipe_code}-{reason.value}",
                            reason=reason,
                            message=line.notes or line.description,
                            severity=ReviewSeverity.WARNING,
                            related_item_ids=[line.item_id] if line.item_id else [],
                        )
                    )

        return self._deduplicate_flags(flags)

    def determine_quote_status(
        self,
        flags: list[ReviewFlag],
        lines: list[PricedLine],
        matches: list[CatalogMatchResult],
    ) -> QuoteStatus:
        if any(f.severity == ReviewSeverity.CRITICAL for f in flags):
            return QuoteStatus.REQUIRES_REVIEW

        unmatched_mandatory = [
            m
            for m in matches
            if m.match_status in (MatchStatus.NO_MATCH, MatchStatus.UNMATCHED)
            and m.item_id not in self._cancelled_ids(matches)
        ]
        if unmatched_mandatory:
            return QuoteStatus.REQUIRES_REVIEW

        if any(
            line.status in (
                QuoteLineStatus.REQUIRES_REVIEW,
                QuoteLineStatus.UNMATCHED,
                QuoteLineStatus.CUSTOM_ESTIMATE,
            )
            for line in lines
        ):
            return QuoteStatus.REQUIRES_REVIEW

        if any(f.severity == ReviewSeverity.WARNING for f in flags):
            return QuoteStatus.REQUIRES_REVIEW

        priced_lines = [line for line in lines if line.status == QuoteLineStatus.PRICED]
        if not priced_lines:
            return QuoteStatus.DRAFT

        return QuoteStatus.READY

    def _deduplicate_flags(self, flags: list[ReviewFlag]) -> list[ReviewFlag]:
        seen: set[str] = set()
        unique: list[ReviewFlag] = []
        for flag in flags:
            key = f"{flag.reason.value}:{flag.message}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(flag)
        return unique

    def _cancelled_ids(self, matches: list[CatalogMatchResult]) -> set[str]:
        return {m.item_id for m in matches if m.match_status == MatchStatus.CANCELLED}
