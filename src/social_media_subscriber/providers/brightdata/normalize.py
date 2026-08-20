"""Pure Bright Data identity and post normalization."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import (
    PlatformAccountId,
    PlatformPostId,
    account_id_for,
    post_id_for,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent
from social_media_subscriber.providers.brightdata.models import (
    canonical_links,
    canonical_post_url,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    AccountIdentityOutcome,
    BrightDataNormalizationResult,
    ResolvedAccountIdentity,
    SkippedPostCounts,
    UnresolvedAccountIdentity,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)

if TYPE_CHECKING:
    from social_media_subscriber.providers.brightdata.models import (
        BrightDataCompanyIdentity,
        BrightDataPersonIdentity,
        BrightDataPost,
    )

_NUMERIC_ID: Final = re.compile(r"[0-9]+", re.ASCII)


def _resolved_identity(
    kind: AccountKind,
    platform_id: str | None,
    response_url: str | None,
    requested_url: str,
    first_seen_at: datetime,
) -> AccountIdentityOutcome:
    if platform_id is None or _NUMERIC_ID.fullmatch(platform_id) is None:
        return UnresolvedAccountIdentity()
    try:
        requested = parse_linkedin_locator(requested_url)
        aliases = {requested.canonical_url}
        if response_url is not None:
            response = parse_linkedin_locator(response_url)
            if response.kind is not kind:
                raise BrightDataNormalizationError(
                    BrightDataNormalizationErrorCategory.IDENTITY
                )
            aliases.add(response.canonical_url)
    except AccountInputError:
        raise BrightDataNormalizationError(
            BrightDataNormalizationErrorCategory.IDENTITY
        ) from None
    if requested.kind is not kind:
        raise BrightDataNormalizationError(
            BrightDataNormalizationErrorCategory.IDENTITY
        )
    stable_id = PlatformAccountId(platform_id)
    account = Account(
        id=account_id_for(kind, stable_id),
        platform=Platform.LINKEDIN,
        kind=kind,
        platform_account_id=stable_id,
        profile_url=requested.canonical_url,
        url_aliases=tuple(aliases),
        first_seen_at=first_seen_at,
    )
    return ResolvedAccountIdentity(account=account)


def resolve_person_identity(
    identity: BrightDataPersonIdentity,
    requested_url: str,
    first_seen_at: datetime,
) -> AccountIdentityOutcome:
    """Resolve a personal lookup only from the plan-approved numeric field."""
    return _resolved_identity(
        AccountKind.PERSON,
        identity.linkedin_num_id,
        identity.url,
        requested_url,
        first_seen_at,
    )


def resolve_company_identity(
    identity: BrightDataCompanyIdentity,
    requested_url: str,
    first_seen_at: datetime,
) -> AccountIdentityOutcome:
    """Resolve a company lookup only from its non-empty numeric company ID."""
    return _resolved_identity(
        AccountKind.COMPANY,
        identity.company_id,
        identity.url,
        requested_url,
        first_seen_at,
    )


def _validate_post_ownership(account: Account, post: BrightDataPost) -> None:
    if post.user_id is not None and post.user_id != account.platform_account_id:
        raise BrightDataNormalizationError(
            BrightDataNormalizationErrorCategory.OWNERSHIP
        )
    known_aliases = {account.profile_url, *account.url_aliases}
    actor_urls = tuple(
        value
        for value in (post.use_url, post.user_url, post.profile_url, post.company_url)
        if value is not None
    )
    for actor_url in actor_urls:
        try:
            locator = parse_linkedin_locator(actor_url)
        except AccountInputError:
            raise BrightDataNormalizationError(
                BrightDataNormalizationErrorCategory.OWNERSHIP
            ) from None
        if (
            locator.kind is not account.kind
            or locator.canonical_url not in known_aliases
        ):
            raise BrightDataNormalizationError(
                BrightDataNormalizationErrorCategory.OWNERSHIP
            )


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
        schema_version=1,
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
    sources_by_id: dict[str, BrightDataLinkedInPostSourceRecord] = {}
    for record in records:
        _validate_post_ownership(account, record)
        source = BrightDataLinkedInPostSourceRecord.from_post(account.id, record)
        existing = sources_by_id.get(record.id)
        if existing is not None and existing.payload_sha256 != source.payload_sha256:
            raise BrightDataNormalizationError(
                BrightDataNormalizationErrorCategory.DUPLICATE
            )
        by_id[record.id] = record
        sources_by_id[record.id] = source

    posts: list[Post] = []
    skipped = SkippedPostCounts()
    for platform_post_id in sorted(by_id):
        record = by_id[platform_post_id]
        match record.post_type.casefold():
            case "post":
                posts.append(_canonical_post(account, record, first_seen_at))
            case _non_original:
                skipped = _increment_skip(skipped, record.post_type)
    sources = tuple(sources_by_id[key] for key in sorted(sources_by_id))
    return BrightDataNormalizationResult(
        source_records=sources,
        posts=tuple(posts),
        skipped=skipped,
    )
