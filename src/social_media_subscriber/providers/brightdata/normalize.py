"""Pure Bright Data LinkedIn Post normalization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from social_media_subscriber.domain.ids import PlatformPostId
from social_media_subscriber.domain.post import Post
from social_media_subscriber.providers.brightdata.actor_ownership import (
    validate_actor_ownership,
)
from social_media_subscriber.providers.brightdata.models import (
    JsonValue,
    canonical_post_url,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    BrightDataNormalizationResult,
)
from social_media_subscriber.serialization.json import canonical_json_bytes

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.providers.brightdata.models import BrightDataPost

_IDENTITY_FIELDS: Final = frozenset(
    {
        "id",
        "date_posted",
        "post_type",
        "url",
        "user_id",
        "use_url",
        "user_url",
        "profile_url",
        "company_url",
        "post_text",
    }
)
_NON_POST_FIELDS: Final = frozenset(
    {
        "discovery_input",
        "input",
        "more_articles_by_user",
        "more_relevant_posts",
        "timestamp",
    }
)


def _canonical_content(source_post: BrightDataPost) -> dict[str, JsonValue]:
    """Keep every safe content field while normalizing common query fields."""
    payload = source_post.payload
    content: dict[str, JsonValue] = {
        key: value
        for key, value in payload.items()
        if key not in _IDENTITY_FIELDS and key not in _NON_POST_FIELDS
    }
    if "post_text" in payload:
        content["text"] = source_post.post_text
    if "embedded_links" in content:
        content["links"] = content.pop("embedded_links")
    return content


def _canonical_post(
    account: Account,
    source_post: BrightDataPost,
    first_seen_at: datetime,
) -> Post:
    return Post(
        platform_post_id=PlatformPostId(source_post.id),
        account_profile_url=account.id,
        canonical_url=canonical_post_url(source_post.url),
        published_at=datetime.fromisoformat(source_post.date_posted).astimezone(UTC),
        type=source_post.post_type.casefold(),
        content=_canonical_content(source_post),
        first_seen_at=first_seen_at,
    )


def normalize_posts(
    account: Account,
    records: tuple[BrightDataPost, ...],
    first_seen_at: datetime,
) -> BrightDataNormalizationResult:
    """Normalize every complete safe record without discarding post variants."""
    by_id: dict[str, Post] = {}
    for record in records:
        validate_actor_ownership(account, record)
        candidate = _canonical_post(account, record, first_seen_at)
        existing = by_id.get(record.id)
        if existing is not None and existing.content_hash != candidate.content_hash:
            raise BrightDataNormalizationError(
                BrightDataNormalizationErrorCategory.DUPLICATE
            )
        if existing is None or canonical_json_bytes(candidate) < canonical_json_bytes(
            existing
        ):
            by_id[record.id] = candidate
    return BrightDataNormalizationResult(
        posts=tuple(by_id[platform_post_id] for platform_post_id in sorted(by_id))
    )
