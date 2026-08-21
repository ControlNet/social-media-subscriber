"""Pure numeric Account derivation from complete Bright Data post batches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.identity import (
    AccountIdentityConflictError,
    AccountIdentityService,
)
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import PlatformAccountId, account_id_for
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    BrightDataNormalizationResult,
    ResolvedAccountIdentity,
    UnresolvedAccountIdentity,
)
from social_media_subscriber.providers.brightdata.normalize import normalize_posts

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.providers.brightdata.models import BrightDataPost

_NUMERIC_ID: Final = re.compile(r"[0-9]+", re.ASCII)


@dataclass(frozen=True, slots=True)
class ResolvedPostsAccountDiscovery:
    """One reconciled Account together with its complete normalized post batch."""

    account: Account
    normalization: BrightDataNormalizationResult


@dataclass(frozen=True, slots=True)
class UnresolvedPostsAccountDiscovery:
    """A successful post lookup without ordinary-post numeric identity evidence."""


type PostsAccountDiscoveryOutcome = (
    ResolvedPostsAccountDiscovery | UnresolvedPostsAccountDiscovery
)


def _identity_error() -> BrightDataNormalizationError:
    return BrightDataNormalizationError(BrightDataNormalizationErrorCategory.IDENTITY)


def _ownership_error() -> BrightDataNormalizationError:
    return BrightDataNormalizationError(BrightDataNormalizationErrorCategory.OWNERSHIP)


def _ordinary_platform_id(
    records: tuple[BrightDataPost, ...],
) -> PlatformAccountId | None:
    ordinary_records = tuple(
        record for record in records if record.post_type.casefold() == "post"
    )
    if not ordinary_records:
        return None

    platform_ids: set[str] = set()
    for record in ordinary_records:
        user_id = record.user_id
        if user_id is None or _NUMERIC_ID.fullmatch(user_id) is None:
            raise _identity_error()
        platform_ids.add(user_id)
    if len(platform_ids) != 1:
        raise _identity_error()
    return PlatformAccountId(next(iter(platform_ids)))


def _candidate_account(
    requested_locator: LinkedInLocator,
    platform_account_id: PlatformAccountId,
    first_seen_at: datetime,
) -> Account:
    return Account(
        id=account_id_for(requested_locator.kind, platform_account_id),
        platform=Platform.LINKEDIN,
        kind=requested_locator.kind,
        platform_account_id=platform_account_id,
        profile_url=requested_locator.canonical_url,
        url_aliases=(),
        first_seen_at=first_seen_at,
    )


def _validate_actor_ownership(
    records: tuple[BrightDataPost, ...],
    requested_locator: LinkedInLocator,
    account: Account | None,
) -> None:
    allowed_aliases = {requested_locator.canonical_url}
    if account is not None:
        allowed_aliases.update((account.profile_url, *account.url_aliases))
    for record in records:
        for actor_url in (
            record.use_url,
            record.user_url,
            record.profile_url,
            record.company_url,
        ):
            if actor_url is None:
                continue
            try:
                actor_locator = parse_linkedin_locator(actor_url)
            except AccountInputError:
                raise _ownership_error() from None
            if (
                actor_locator.kind is not requested_locator.kind
                or actor_locator.canonical_url not in allowed_aliases
            ):
                raise _ownership_error()


def derive_account_from_posts(
    requested_locator: LinkedInLocator,
    records: tuple[BrightDataPost, ...],
    identity_service: AccountIdentityService,
    first_seen_at: datetime,
) -> PostsAccountDiscoveryOutcome:
    """Resolve one locator from unanimous ordinary-post numeric identity evidence."""
    platform_account_id = _ordinary_platform_id(records)
    if platform_account_id is None:
        _validate_actor_ownership(records, requested_locator, None)
        return UnresolvedPostsAccountDiscovery()

    candidate = _candidate_account(
        requested_locator,
        platform_account_id,
        first_seen_at,
    )
    try:
        identity = identity_service.resolve(
            requested_locator,
            ResolvedAccountIdentity(candidate),
        )
    except AccountIdentityConflictError:
        raise _identity_error() from None

    match identity:
        case ResolvedAccountIdentity(account=account):
            _validate_actor_ownership(records, requested_locator, account)
            normalization = normalize_posts(account, records, first_seen_at)
            return ResolvedPostsAccountDiscovery(account, normalization)
        case UnresolvedAccountIdentity():
            raise _identity_error()
