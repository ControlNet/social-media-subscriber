"""Run-local state for deterministic identity routing."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from social_media_subscriber.accounts.identity import (
    AccountIdentityConflictError,
    AccountIdentityService,
)
from social_media_subscriber.adapters.router_outcomes import (
    IdentityRouterAggregate,
    IdentityRouterResult,
    InstanceHealth,
    InstanceHealthStatus,
    RouterDiagnostic,
    RouterDiagnosticCategory,
    RouterRunStatus,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    ResolvedAccountIdentity,
    UnresolvedAccountIdentity,
)

if TYPE_CHECKING:
    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.instance import (
        AdapterIdentityBatch,
        AdapterInstance,
        AdapterInstanceOrdinal,
    )
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.providers.brightdata.normalization_outcomes import (
        AccountIdentityOutcome,
    )


@final
class IdentityRouterState:
    """Accumulate one identity run without mutating persisted Accounts."""

    def __init__(
        self,
        instances: tuple[AdapterInstance, ...],
        known_accounts: tuple[Account, ...],
    ) -> None:
        """Seed fresh health and immutable known Account identity state."""
        self.health: dict[AdapterInstanceOrdinal, InstanceHealthStatus] = {
            instance.ordinal: InstanceHealthStatus.HEALTHY for instance in instances
        }
        self.known_accounts = known_accounts
        self.resolved_accounts: dict[AccountId, Account] = {}
        self.outcomes: dict[str, AccountIdentityOutcome] = {}
        self.diagnostics: list[RouterDiagnostic] = []
        self.aborted = False

    def seed_known(self, locators: tuple[LinkedInLocator, ...]) -> None:
        """Resolve existing aliases before any provider instance is called."""
        try:
            service = AccountIdentityService(self.known_accounts)
        except AccountIdentityConflictError:
            self.abort_schema()
            return
        for locator in locators:
            account = service.find(locator)
            if account is not None:
                self.outcomes[locator.canonical_url] = ResolvedAccountIdentity(account)

    def accept(
        self,
        batch: AdapterIdentityBatch,
        outcomes: tuple[AccountIdentityOutcome, ...],
    ) -> bool:
        """Reconcile a complete ordered identity response atomically."""
        if len(batch.locators) != len(outcomes):
            self.abort_schema()
            return False
        try:
            for locator, outcome in zip(batch.locators, outcomes, strict=True):
                service = AccountIdentityService(
                    (*self.known_accounts, *self.resolved_accounts.values())
                )
                reconciled = service.resolve(locator, outcome)
                self.outcomes[locator.canonical_url] = reconciled
                match reconciled:
                    case ResolvedAccountIdentity(account=account):
                        self.resolved_accounts[account.id] = account
                        self._replace_account(account)
                    case UnresolvedAccountIdentity():
                        pass
        except AccountIdentityConflictError:
            self.abort_schema()
            return False
        return True

    def fail(self, batch: AdapterIdentityBatch) -> None:
        """Mark one terminal locator batch unresolved."""
        for locator in batch.locators:
            self.outcomes[locator.canonical_url] = UnresolvedAccountIdentity()

    def is_healthy(self, instance: AdapterInstance) -> bool:
        """Return whether one instance remains eligible in this run."""
        return self.health[instance.ordinal] is InstanceHealthStatus.HEALTHY

    def disable_quota(self, instance: AdapterInstance) -> None:
        """Disable one quota-exhausted instance for this run."""
        self.health[instance.ordinal] = InstanceHealthStatus.QUOTA_EXHAUSTED
        self.diagnostics.append(
            RouterDiagnostic(RouterDiagnosticCategory.QUOTA_DISABLED, instance.ordinal)
        )

    def disable_credential(self, instance: AdapterInstance) -> None:
        """Disable one rejected credential for this run."""
        self.health[instance.ordinal] = InstanceHealthStatus.INVALID_CREDENTIAL
        self.diagnostics.append(
            RouterDiagnostic(
                RouterDiagnosticCategory.CREDENTIAL_DISABLED,
                instance.ordinal,
            )
        )

    def abort_schema(self) -> None:
        """Suppress all identity candidates after integrity corruption."""
        self.aborted = True
        self.diagnostics.append(RouterDiagnostic(RouterDiagnosticCategory.SCHEMA_ABORT))

    def result(self, locators: tuple[LinkedInLocator, ...]) -> IdentityRouterResult:
        """Freeze ordered outcomes, aggregate counts, health, and diagnostics."""
        outcomes = (
            ()
            if self.aborted
            else tuple(self.outcomes[locator.canonical_url] for locator in locators)
        )
        health = tuple(
            InstanceHealth(ordinal, self.health[ordinal])
            for ordinal in sorted(self.health)
        )
        resolved = sum(isinstance(item, ResolvedAccountIdentity) for item in outcomes)
        unresolved = sum(
            isinstance(item, UnresolvedAccountIdentity) for item in outcomes
        )
        status = (
            RouterRunStatus.ABORTED
            if self.aborted
            else RouterRunStatus.PARTIAL
            if unresolved
            else RouterRunStatus.SUCCESS
        )
        return IdentityRouterResult(
            aggregate=IdentityRouterAggregate(
                status,
                resolved,
                unresolved,
                sum(item.status is not InstanceHealthStatus.HEALTHY for item in health),
            ),
            outcomes=outcomes,
            health=health,
            diagnostics=tuple(self.diagnostics),
        )

    def _replace_account(self, account: Account) -> None:
        for locator_url, outcome in tuple(self.outcomes.items()):
            match outcome:
                case ResolvedAccountIdentity(account=previous) if (
                    previous.id == account.id
                ):
                    self.outcomes[locator_url] = ResolvedAccountIdentity(account)
                case ResolvedAccountIdentity() | UnresolvedAccountIdentity():
                    pass
