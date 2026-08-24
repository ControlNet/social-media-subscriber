"""Pure Apify LinkedIn post normalization."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain.ids import AccountId, PlatformPostId
from social_media_subscriber.domain.post import Post
from social_media_subscriber.platforms.linkedin import (
    LinkedInPostUrlError,
    canonical_media_items,
    canonical_platform_post_id,
    canonical_post_timestamp,
    canonical_post_type,
    canonical_post_url,
    has_meaningful_value,
)
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.serialization.json import JsonValue, canonical_json_bytes

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.providers.apify.models import ApifyPost

_IDENTITY_FIELDS: Final = frozenset(
    {"id", "linkedinUrl", "type", "content", "postedAt"}
)
_NON_POST_FIELDS: Final = frozenset({"reactions", "comments"})
_REPOST_MARKERS: Final = ("repost", "repostId", "repostedAt", "repostedBy")


def actor_account_id(record: ApifyPost) -> AccountId:
    """Return the canonical requested account identity or a sanitized failure."""
    actor_url = (
        record.author.linkedin_url if record.query is None else record.query.target_url
    )
    try:
        locator = parse_linkedin_locator(actor_url)
    except AccountInputError:
        raise ApifyError(ApifyErrorCategory.OWNERSHIP) from None
    return AccountId(locator.canonical_url)


def _content(record: ApifyPost) -> dict[str, JsonValue]:
    payload = record.payload
    content = {
        key: value
        for key, value in payload.items()
        if key not in _IDENTITY_FIELDS and key not in _NON_POST_FIELDS
    }
    if "content" in payload:
        content["text"] = record.content
    images = content.pop("postImages", content.get("images"))
    if images is not None:
        content["images"] = canonical_media_items(images)
    video = content.pop("postVideo", content.get("videos"))
    if video is not None:
        content["videos"] = canonical_media_items(video)
    author = content.get("author")
    if isinstance(author, dict):
        canonical_author = dict(author)
        profile_url = canonical_author.pop("linkedinUrl", None)
        if profile_url is not None:
            canonical_author["profile_url"] = profile_url
        content["author"] = canonical_author
    document = content.get("document")
    if isinstance(document, dict) and "totalPageCount" in document:
        canonical_document = dict(document)
        canonical_document["page_count"] = canonical_document.pop("totalPageCount")
        content["document"] = canonical_document
    return content


def _is_repost(record: ApifyPost) -> bool:
    payload = record.payload
    return any(has_meaningful_value(payload.get(field)) for field in _REPOST_MARKERS)


def _canonical_post(
    account: Account, record: ApifyPost, first_seen_at: datetime
) -> Post:
    try:
        post_url = canonical_post_url(record.linkedin_url, platform_post_id=record.id)
    except LinkedInPostUrlError:
        raise ApifyError(ApifyErrorCategory.POST_URL) from None
    return Post(
        platform_post_id=PlatformPostId(canonical_platform_post_id(record.id)),
        account_profile_url=account.id,
        canonical_url=post_url,
        published_at=canonical_post_timestamp(record.posted_at.timestamp),
        type=canonical_post_type(record.type, is_repost=_is_repost(record)),
        content=_content(record),
        first_seen_at=first_seen_at,
    )


def normalize_posts(
    account: Account, records: tuple[ApifyPost, ...], first_seen_at: datetime
) -> tuple[Post, ...]:
    """Normalize complete actor records with strict ownership and duplicates."""
    by_id: dict[str, Post] = {}
    for record in records:
        if actor_account_id(record) != account.id:
            raise ApifyError(ApifyErrorCategory.OWNERSHIP)
        candidate = _canonical_post(account, record, first_seen_at)
        platform_post_id = canonical_platform_post_id(record.id)
        existing = by_id.get(platform_post_id)
        if existing is not None and existing.content_hash != candidate.content_hash:
            raise ApifyError(ApifyErrorCategory.DUPLICATE)
        if existing is None or canonical_json_bytes(candidate) < canonical_json_bytes(
            existing
        ):
            by_id[platform_post_id] = candidate
    return tuple(by_id[key] for key in sorted(by_id))
