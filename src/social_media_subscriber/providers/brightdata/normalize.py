"""Pure Bright Data LinkedIn Post normalization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from social_media_subscriber.domain.ids import PlatformPostId
from social_media_subscriber.domain.post import Post
from social_media_subscriber.platforms.linkedin import (
    canonical_media_items,
    canonical_platform_post_id,
    canonical_post_timestamp,
    canonical_post_type,
    has_meaningful_value,
)
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
    _normalize_media(content)
    _normalize_engagement(content)
    _normalize_author(content)
    _normalize_document(content)
    return content


def _normalize_media(content: dict[str, JsonValue]) -> None:
    images = content.get("images")
    if images is not None:
        content["images"] = canonical_media_items(images)
    videos = content.get("videos")
    if videos is not None:
        content["videos"] = canonical_media_items(videos)


def _normalize_engagement(content: dict[str, JsonValue]) -> None:
    engagement = content.get("engagement")
    canonical_engagement = dict(engagement) if isinstance(engagement, dict) else {}
    for source, target in (
        ("num_likes", "likes"),
        ("num_comments", "comments"),
        ("num_shares", "shares"),
    ):
        value = content.pop(source, None)
        if value is not None:
            canonical_engagement[target] = value
    if canonical_engagement:
        content["engagement"] = canonical_engagement


def _normalize_author(content: dict[str, JsonValue]) -> None:
    author = content.get("author")
    canonical_author = dict(author) if isinstance(author, dict) else {}
    for source, target in (
        ("user_name", "name"),
        ("user_title", "headline"),
        ("user_profile_pic", "profile_image_url"),
        ("author_profile_pic", "profile_image_url"),
        ("account_type", "type"),
    ):
        value = content.pop(source, None)
        if value is not None:
            canonical_author[target] = value
    if canonical_author:
        content["author"] = canonical_author


def _normalize_document(content: dict[str, JsonValue]) -> None:
    cover_image = content.pop("document_cover_image", None)
    page_count = content.pop("document_page_count", None)
    document = content.get("document")
    canonical_document = dict(document) if isinstance(document, dict) else {}
    if cover_image is not None:
        canonical_document["cover_image"] = cover_image
    if page_count is not None:
        canonical_document["page_count"] = page_count
    if canonical_document:
        content["document"] = canonical_document


def _is_repost(source_post: BrightDataPost) -> bool:
    return has_meaningful_value(source_post.payload.get("repost"))


def _canonical_post(
    account: Account,
    source_post: BrightDataPost,
    first_seen_at: datetime,
) -> Post:
    return Post(
        platform_post_id=PlatformPostId(canonical_platform_post_id(source_post.id)),
        account_profile_url=account.id,
        canonical_url=canonical_post_url(
            source_post.url, platform_post_id=source_post.id
        ),
        published_at=canonical_post_timestamp(
            datetime.fromisoformat(source_post.date_posted).astimezone(UTC)
        ),
        type=canonical_post_type(
            source_post.post_type, is_repost=_is_repost(source_post)
        ),
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
        platform_post_id = canonical_platform_post_id(record.id)
        existing = by_id.get(platform_post_id)
        if existing is not None and existing.content_hash != candidate.content_hash:
            raise BrightDataNormalizationError(
                BrightDataNormalizationErrorCategory.DUPLICATE
            )
        if existing is None or canonical_json_bytes(candidate) < canonical_json_bytes(
            existing
        ):
            by_id[platform_post_id] = candidate
    return BrightDataNormalizationResult(
        posts=tuple(by_id[platform_post_id] for platform_post_id in sorted(by_id))
    )
