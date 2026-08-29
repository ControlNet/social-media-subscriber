"""Pure Xquik Post normalization."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_x_locator
from social_media_subscriber.domain.ids import PlatformPostId
from social_media_subscriber.domain.post import Post
from social_media_subscriber.platforms.x import (
    XPostUrlError,
    canonical_platform_post_id,
    canonical_post_url,
)
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.serialization.json import JsonValue, canonical_json_bytes

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.providers.apify.x_models import ApifyXPost

_IDENTITY_FIELDS: Final = frozenset({"createdAt", "id", "text", "type", "url"})
_ENGAGEMENT_FIELDS: Final = (
    ("bookmarkCount", "bookmarks"),
    ("likeCount", "likes"),
    ("quoteCount", "quotes"),
    ("replyCount", "replies"),
    ("retweetCount", "reposts"),
    ("viewCount", "views"),
)


def _content(record: ApifyXPost) -> dict[str, JsonValue]:
    payload = record.payload
    content = {
        key: value for key, value in payload.items() if key not in _IDENTITY_FIELDS
    }
    content["text"] = record.text
    engagement: dict[str, JsonValue] = {}
    for source, target in _ENGAGEMENT_FIELDS:
        engagement[target] = content.pop(source)
    content["engagement"] = engagement
    return content


def _post_type(record: ApifyXPost) -> str:
    if (record.type == "reply") is not record.is_reply:
        raise ApifyError(ApifyErrorCategory.SCHEMA)
    if record.is_reply:
        return "reply"
    return "quote" if record.is_quote_status else "post"


def _canonical_post(
    account: Account,
    record: ApifyXPost,
    first_seen_at: datetime,
) -> Post:
    try:
        author = parse_x_locator(f"https://x.com/{record.author.username}/")
    except AccountInputError:
        raise ApifyError(ApifyErrorCategory.OWNERSHIP) from None
    if author.canonical_url != account.profile_url:
        raise ApifyError(ApifyErrorCategory.OWNERSHIP)
    try:
        platform_post_id = canonical_platform_post_id(record.id)
        post_url = canonical_post_url(record.url, platform_post_id=platform_post_id)
    except XPostUrlError:
        raise ApifyError(ApifyErrorCategory.POST_URL) from None
    if not post_url.startswith(account.profile_url):
        raise ApifyError(ApifyErrorCategory.OWNERSHIP)
    return Post(
        platform_post_id=PlatformPostId(platform_post_id),
        account_profile_url=account.id,
        canonical_url=post_url,
        published_at=record.timestamp,
        type=_post_type(record),
        content=_content(record),
        first_seen_at=first_seen_at,
    )


def normalize_posts(
    account: Account,
    records: tuple[ApifyXPost, ...],
    first_seen_at: datetime,
) -> tuple[Post, ...]:
    """Normalize complete owned Xquik tweets with deterministic duplicates."""
    by_id: dict[str, Post] = {}
    for record in records:
        candidate = _canonical_post(account, record, first_seen_at)
        existing = by_id.get(record.id)
        if existing is not None and existing.content_hash != candidate.content_hash:
            raise ApifyError(ApifyErrorCategory.DUPLICATE)
        if existing is None or canonical_json_bytes(candidate) < canonical_json_bytes(
            existing
        ):
            by_id[record.id] = candidate
    return tuple(by_id[key] for key in sorted(by_id))
