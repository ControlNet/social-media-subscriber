"""Pure Bright Data LinkedIn Post normalization."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from social_media_subscriber.domain.ids import (
    PlatformPostId,
    post_id_for,
)
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent
from social_media_subscriber.providers.brightdata.actor_ownership import (
    validate_actor_ownership,
)
from social_media_subscriber.providers.brightdata.models import (
    canonical_links,
    canonical_post_url,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    BrightDataNormalizationResult,
    SkippedPostCounts,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.providers.brightdata.models import BrightDataPost


def _increment_skip(counts: SkippedPostCounts, post_type: str) -> SkippedPostCounts:
    match post_type.casefold():
        case "reply" | "comment":
            return replace(counts, replies=counts.replies + 1)
        case "repost" | "reshare":
            return replace(counts, reposts=counts.reposts + 1)
        case "quote" | "quote_post":
            return replace(counts, quotes=counts.quotes + 1)
        case _unknown:
            return replace(counts, unknown=counts.unknown + 1)


def _canonical_post(
    account: Account,
    source_post: BrightDataPost,
    first_seen_at: datetime,
) -> Post:
    platform_post_id = PlatformPostId(source_post.id)
    published_at = datetime.fromisoformat(source_post.date_posted).astimezone(UTC)
    stable = StablePostContent(
        schema_version=2,
        id=post_id_for(platform_post_id),
        platform_post_id=platform_post_id,
        account_id=account.id,
        canonical_url=canonical_post_url(source_post.url),
        published_at=published_at,
        text=source_post.post_text,
        kind=PostKind.ORIGINAL,
        hashtags=source_post.hashtags or (),
        links=canonical_links(source_post),
    )
    return Post.from_stable(stable, first_seen_at)


def normalize_posts(
    account: Account,
    records: tuple[BrightDataPost, ...],
    first_seen_at: datetime,
) -> BrightDataNormalizationResult:
    """Normalize a complete in-memory batch without performing I/O or hydration."""
    by_id: dict[str, BrightDataPost] = {}
    for record in records:
        validate_actor_ownership(account, record)
        existing = by_id.get(record.id)
        if existing is not None and existing.payload != record.payload:
            raise BrightDataNormalizationError(
                BrightDataNormalizationErrorCategory.DUPLICATE
            )
        by_id[record.id] = record

    posts: list[Post] = []
    sources: list[BrightDataLinkedInPostSourceRecord] = []
    skipped = SkippedPostCounts()
    for platform_post_id in sorted(by_id):
        record = by_id[platform_post_id]
        match record.post_type.casefold():
            case "post":
                posts.append(_canonical_post(account, record, first_seen_at))
                sources.append(
                    BrightDataLinkedInPostSourceRecord.from_post(account.id, record)
                )
            case _non_original:
                skipped = _increment_skip(skipped, record.post_type)
    return BrightDataNormalizationResult(
        source_records=tuple(sources),
        posts=tuple(posts),
        skipped=skipped,
    )
