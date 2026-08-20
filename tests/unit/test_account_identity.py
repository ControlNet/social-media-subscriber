from __future__ import annotations

from datetime import UTC, datetime

import pytest

from social_media_subscriber.accounts.identity import (
    AccountIdentityConflictCategory,
    AccountIdentityConflictError,
    AccountIdentityService,
)
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import PlatformAccountId, account_id_for
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    ResolvedAccountIdentity,
    UnresolvedAccountIdentity,
)

_NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _account(
    platform_id: str,
    slug: str,
    *,
    aliases: tuple[str, ...] = (),
) -> Account:
    stable_id = PlatformAccountId(platform_id)
    return Account(
        id=account_id_for(AccountKind.PERSON, stable_id),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        platform_account_id=stable_id,
        profile_url=f"https://www.linkedin.com/in/{slug}/",
        url_aliases=aliases,
        first_seen_at=_NOW,
    )


def test_known_alias_resolves_without_candidate() -> None:
    # Given
    account = _account(
        "10",
        "original",
        aliases=("https://www.linkedin.com/in/current/",),
    )
    service = AccountIdentityService((account,))
    locator = parse_linkedin_locator("https://linkedin.com/in/current")

    # When
    result = service.resolve(locator, UnresolvedAccountIdentity())

    # Then
    assert result == ResolvedAccountIdentity(account)


def test_new_slug_for_stable_id_adds_one_sorted_alias() -> None:
    # Given
    existing = _account("10", "original")
    candidate = _account("10", "renamed")
    service = AccountIdentityService((existing,))
    locator = parse_linkedin_locator("https://linkedin.com/in/renamed")

    # When
    result = service.resolve(locator, ResolvedAccountIdentity(candidate))

    # Then
    assert isinstance(result, ResolvedAccountIdentity)
    assert result.account.profile_url == existing.profile_url
    assert result.account.url_aliases == (
        "https://www.linkedin.com/in/original/",
        "https://www.linkedin.com/in/renamed/",
    )
    assert service.accounts == (existing,)


def test_unknown_without_stable_id_remains_unresolved() -> None:
    # Given
    service = AccountIdentityService(())
    locator = parse_linkedin_locator("https://linkedin.com/in/no-id")

    # When
    result = service.resolve(locator, UnresolvedAccountIdentity())

    # Then
    assert isinstance(result, UnresolvedAccountIdentity)
    assert service.accounts == ()


def test_alias_mapping_to_two_ids_is_rejected_atomically() -> None:
    # Given
    alias = "https://www.linkedin.com/in/shared/"
    first = _account("10", "first", aliases=(alias,))
    second = _account("20", "second", aliases=(alias,))

    # When / Then
    with pytest.raises(AccountIdentityConflictError) as captured:
        _ = AccountIdentityService((first, second))
    assert captured.value.category is AccountIdentityConflictCategory.ALIAS
    assert "shared" not in str(captured.value)


def test_known_alias_resolving_to_different_account_is_rejected() -> None:
    # Given
    existing = _account("10", "known")
    candidate = _account("20", "known")
    service = AccountIdentityService((existing,))
    locator = parse_linkedin_locator("https://linkedin.com/in/known")

    # When / Then
    with pytest.raises(AccountIdentityConflictError) as captured:
        _ = service.resolve(locator, ResolvedAccountIdentity(candidate))
    assert captured.value.category is AccountIdentityConflictCategory.ACCOUNT
    assert service.accounts == (existing,)


def test_duplicate_account_id_with_conflicting_record_is_rejected() -> None:
    # Given
    first = _account("10", "first")
    second = _account("10", "second")

    # When / Then
    with pytest.raises(AccountIdentityConflictError) as captured:
        _ = AccountIdentityService((first, second))
    assert captured.value.category is AccountIdentityConflictCategory.ACCOUNT
