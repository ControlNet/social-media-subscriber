"""Stable Account identity reconciliation without persistence side effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, override

from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    AccountIdentityOutcome,
    ResolvedAccountIdentity,
    UnresolvedAccountIdentity,
)

if TYPE_CHECKING:
    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId


class AccountIdentityConflictCategory(StrEnum):
    """Machine-readable identity integrity failure."""

    ALIAS = "alias_conflict"
    ACCOUNT = "account_conflict"


@dataclass(frozen=True, slots=True)
class AccountIdentityConflictError(Exception):
    """Identity conflict containing no locator or provider response values."""

    category: AccountIdentityConflictCategory

    @override
    def __str__(self) -> str:
        return f"account identity conflict ({self.category.value})"


@dataclass(frozen=True, slots=True)
class AccountIdentityService:
    """Immutable index that reconciles provider identity candidates atomically."""

    accounts: tuple[Account, ...]

    def __post_init__(self) -> None:
        """Reject duplicate stable IDs and aliases before index use."""
        by_id: dict[AccountId, Account] = {}
        by_alias: dict[str, AccountId] = {}
        for account in self.accounts:
            existing = by_id.get(account.id)
            if existing is not None and existing != account:
                raise AccountIdentityConflictError(
                    AccountIdentityConflictCategory.ACCOUNT
                )
            by_id[account.id] = account
            for alias in (account.profile_url, *account.url_aliases):
                owner = by_alias.get(alias)
                if owner is not None and owner != account.id:
                    raise AccountIdentityConflictError(
                        AccountIdentityConflictCategory.ALIAS
                    )
                by_alias[alias] = account.id

    def find(self, locator: LinkedInLocator) -> Account | None:
        """Return the unique Account currently owning a canonical locator."""
        for account in self.accounts:
            if locator.canonical_url in (account.profile_url, *account.url_aliases):
                return account
        return None

    def resolve(
        self,
        locator: LinkedInLocator,
        candidate: AccountIdentityOutcome,
    ) -> AccountIdentityOutcome:
        """Reconcile one provider candidate without mutating the known snapshot."""
        known_alias = self.find(locator)
        match candidate:
            case UnresolvedAccountIdentity():
                if known_alias is None:
                    return candidate
                return ResolvedAccountIdentity(known_alias)
            case ResolvedAccountIdentity(account=candidate_account):
                return self._reconcile_resolved(
                    locator,
                    candidate_account,
                    known_alias,
                )

    def _reconcile_resolved(
        self,
        locator: LinkedInLocator,
        candidate: Account,
        known_alias: Account | None,
    ) -> ResolvedAccountIdentity:
        if candidate.kind is not locator.kind or locator.canonical_url not in (
            candidate.profile_url,
            *candidate.url_aliases,
        ):
            raise AccountIdentityConflictError(AccountIdentityConflictCategory.ACCOUNT)
        if known_alias is not None and known_alias.id != candidate.id:
            raise AccountIdentityConflictError(AccountIdentityConflictCategory.ACCOUNT)
        for alias in (candidate.profile_url, *candidate.url_aliases):
            owner = next(
                (
                    account
                    for account in self.accounts
                    if alias in (account.profile_url, *account.url_aliases)
                ),
                None,
            )
            if owner is not None and owner.id != candidate.id:
                raise AccountIdentityConflictError(
                    AccountIdentityConflictCategory.ALIAS
                )
        known_id = next(
            (account for account in self.accounts if account.id == candidate.id),
            None,
        )
        if known_id is None:
            return ResolvedAccountIdentity(candidate)
        aliases = tuple(
            sorted(
                {
                    known_id.profile_url,
                    *known_id.url_aliases,
                    candidate.profile_url,
                    *candidate.url_aliases,
                    locator.canonical_url,
                }
            )
        )
        merged = known_id.model_copy(update={"url_aliases": aliases})
        return ResolvedAccountIdentity(merged)
