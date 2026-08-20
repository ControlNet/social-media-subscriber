"""Bright Data identity lookup joined to immutable Account reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from social_media_subscriber.accounts.identity import AccountIdentityService
from social_media_subscriber.domain.platform import AccountKind
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    AccountIdentityOutcome,
    ResolvedAccountIdentity,
)
from social_media_subscriber.providers.brightdata.normalize import (
    resolve_company_identity,
    resolve_person_identity,
)

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataClientContract,
    )


@dataclass(frozen=True, slots=True)
class BrightDataIdentityResolver:
    """Resolve only unknown locators and reconcile stable provider identities."""

    client: BrightDataClientContract
    first_seen_at: datetime

    async def resolve(
        self,
        locators: tuple[LinkedInLocator, ...],
        known_accounts: tuple[Account, ...],
    ) -> tuple[AccountIdentityOutcome, ...]:
        """Return outcomes in locator order without mutating known Accounts."""
        service = AccountIdentityService(known_accounts)
        results: dict[str, AccountIdentityOutcome] = {}
        unknown = tuple(
            locator for locator in locators if service.find(locator) is None
        )
        for locator in locators:
            known = service.find(locator)
            if known is not None:
                results[locator.canonical_url] = ResolvedAccountIdentity(known)
        for kind in (AccountKind.PERSON, AccountKind.COMPANY):
            selected = tuple(locator for locator in unknown if locator.kind is kind)
            if not selected:
                continue
            candidates = await self._candidates(kind, selected)
            if len(candidates) != len(selected):
                raise BrightDataNormalizationError(
                    BrightDataNormalizationErrorCategory.IDENTITY
                )
            for locator, candidate in zip(selected, candidates, strict=True):
                results[locator.canonical_url] = service.resolve(locator, candidate)
        return tuple(results[locator.canonical_url] for locator in locators)

    async def _candidates(
        self,
        kind: AccountKind,
        locators: tuple[LinkedInLocator, ...],
    ) -> tuple[AccountIdentityOutcome, ...]:
        urls = tuple(locator.canonical_url for locator in locators)
        match kind:
            case AccountKind.PERSON:
                identities = await self.client.resolve_person_identities(urls)
                return tuple(
                    resolve_person_identity(
                        item, locator.canonical_url, self.first_seen_at
                    )
                    for locator, item in zip(locators, identities, strict=False)
                )
            case AccountKind.COMPANY:
                identities = await self.client.resolve_company_identities(urls)
                return tuple(
                    resolve_company_identity(
                        item, locator.canonical_url, self.first_seen_at
                    )
                    for locator, item in zip(locators, identities, strict=False)
                )
        assert_never(kind)
