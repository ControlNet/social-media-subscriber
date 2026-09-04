"""Historical X referenced-media snapshot enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from social_media_subscriber.providers.x_syndication import (
    XMediaEnricher,
    XMediaEnricherContract,
    XMediaSyndicationClient,
)
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityCategory,
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class XMediaBackfillCommand:
    """Validated source and separate complete candidate locations."""

    snapshot: Path
    output: Path


class XMediaBackfillInputCategory(StrEnum):
    """Closed categories for invalid backfill input."""

    SAME_PATH = "same_path"


@dataclass(frozen=True, slots=True)
class XMediaBackfillInputError(Exception):
    """Reject an unsafe backfill command without echoing paths."""

    category: XMediaBackfillInputCategory


@dataclass(frozen=True, slots=True)
class XMediaBackfillResult:
    """Safe machine report for a completed candidate write."""

    digest: str
    scanned_posts: int
    eligible_posts: int
    enriched_posts: int
    missed_posts: int
    media_items: int


async def backfill_x_media(
    command: XMediaBackfillCommand,
    *,
    enricher: XMediaEnricherContract | None = None,
) -> XMediaBackfillResult:
    """Validate, enrich, and atomically write a complete candidate snapshot."""
    if command.snapshot.resolve() == command.output.resolve():
        raise XMediaBackfillInputError(XMediaBackfillInputCategory.SAME_PATH)
    validated = SnapshotRepository(command.snapshot).read_optional()
    if validated is None:
        raise SnapshotIntegrityError(SnapshotIntegrityCategory.INVENTORY)
    selected = enricher or XMediaEnricher(XMediaSyndicationClient())
    try:
        enrichment = await selected.enrich(validated.state.posts)
    finally:
        await selected.aclose()
    summary = SnapshotRepository(command.output).write(
        SnapshotState(validated.state.accounts, enrichment.posts)
    )
    report = enrichment.report
    return XMediaBackfillResult(
        summary.digest,
        report.scanned_posts,
        report.eligible_posts,
        report.enriched_posts,
        report.missed_posts,
        report.media_items,
    )
