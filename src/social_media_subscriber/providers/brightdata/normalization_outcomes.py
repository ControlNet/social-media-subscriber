"""Frozen outcomes produced by pure Bright Data normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.source_record import (
        BrightDataLinkedInPostSourceRecord,
    )


@dataclass(frozen=True, slots=True)
class SkippedPostCounts:
    """Deterministic counters for source-preserved noncanonical post kinds."""

    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    unknown: int = 0

    @property
    def total(self) -> int:
        """Return the total number of source-preserved canonical skips."""
        return self.replies + self.reposts + self.quotes + self.unknown


@dataclass(frozen=True, slots=True)
class BrightDataNormalizationResult:
    """Pure candidate records, with persistence delegated to later layers."""

    source_records: tuple[BrightDataLinkedInPostSourceRecord, ...]
    posts: tuple[Post, ...]
    skipped: SkippedPostCounts
