"""Strict actor-URL ownership for Bright Data LinkedIn Posts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.platform import AccountKind
    from social_media_subscriber.providers.brightdata.models import BrightDataPost


def _ownership_failure() -> BrightDataNormalizationError:
    return BrightDataNormalizationError(BrightDataNormalizationErrorCategory.OWNERSHIP)


def actor_account_id(
    record: BrightDataPost,
    expected_kind: AccountKind,
) -> AccountId:
    """Return the one canonical actor URL after validating every supplied field."""
    actor_urls = tuple(
        value
        for value in (
            record.use_url,
            record.user_url,
            record.profile_url,
            record.company_url,
        )
        if value is not None
    )
    if not actor_urls:
        raise _ownership_failure()
    try:
        locators = tuple(parse_linkedin_locator(value) for value in actor_urls)
    except AccountInputError:
        raise _ownership_failure() from None
    canonical_urls = {locator.canonical_url for locator in locators}
    kinds = {locator.kind for locator in locators}
    if canonical_urls != {locators[0].canonical_url} or kinds != {expected_kind}:
        raise _ownership_failure()
    return AccountId(locators[0].canonical_url)


def validate_actor_ownership(account: Account, record: BrightDataPost) -> None:
    """Require the actor URL to equal the exact requested Account URL and kind."""
    if actor_account_id(record, account.kind) != account.id:
        raise _ownership_failure()
