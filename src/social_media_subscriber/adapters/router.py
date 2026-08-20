"""Deterministic batching and run-local classified Adapter failover."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, final

from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.registry import (
    ResolvedAdapterDrivers,
    UnsupportedAdapterCapability,
)
from social_media_subscriber.adapters.router_identity import IdentityRouter
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    RouterDiagnostic,
    RouterDiagnosticCategory,
    RouterOperationError,
    RouterResult,
    RouterRunStatus,
)
from social_media_subscriber.adapters.router_state import RouterRunState
from social_media_subscriber.domain.platform import AccountKind, Platform

if TYPE_CHECKING:
    from pydantic import SecretStr

    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.instance import (
        AdapterInstance,
        AdapterInstanceFactory,
    )
    from social_media_subscriber.adapters.registry import AdapterRegistry
    from social_media_subscriber.adapters.router_outcomes import IdentityRouterResult
    from social_media_subscriber.domain.account import Account

_MAX_BATCH_SIZE: Final = 20
_KIND_ORDER: Final = (AccountKind.PERSON, AccountKind.COMPANY)


@final
class Router:
    """Route bounded homogeneous batches through authorized Adapter instances."""

    _registry: AdapterRegistry
    _instances: tuple[AdapterInstance, ...]

    def __init__(
        self,
        registry: AdapterRegistry,
        factory: AdapterInstanceFactory,
        credentials: tuple[SecretStr, ...],
    ) -> None:
        """Create exactly one instance for each first-seen unique credential."""
        self._registry = registry
        unique_credentials: list[SecretStr] = []
        seen_values: set[str] = set()
        for credential in credentials:
            value = credential.get_secret_value()
            if value not in seen_values:
                seen_values.add(value)
                unique_credentials.append(credential)
        self._instances = tuple(
            factory.create(
                credential,
                instance_contract.AdapterInstanceOrdinal(index),
            )
            for index, credential in enumerate(unique_credentials)
        )

    async def route(
        self,
        accounts: tuple[Account, ...],
        operation: AdapterOperation,
    ) -> RouterResult:
        """Collect Accounts deterministically with health scoped to this call."""
        match operation:
            case AdapterOperation.COLLECT_ACCOUNT_POSTS:
                pass
            case AdapterOperation.RESOLVE_ACCOUNT_IDENTITY:
                raise RouterOperationError(operation)
        unique_accounts = {account.id: account for account in accounts}
        ordered_accounts = tuple(
            sorted(unique_accounts.values(), key=lambda account: str(account.id))
        )
        state = RouterRunState.for_instances(self._instances)
        batch_number = 0
        for kind in _KIND_ORDER:
            kind_accounts = tuple(
                account for account in ordered_accounts if account.kind is kind
            )
            for offset in range(0, len(kind_accounts), _MAX_BATCH_SIZE):
                batch = instance_contract.AdapterBatch(
                    accounts=kind_accounts[offset : offset + _MAX_BATCH_SIZE]
                )
                if await self._route_batch(batch, batch_number, state):
                    return state.result(RouterRunStatus.ABORTED, include_posts=False)
                batch_number += 1

        status = (
            RouterRunStatus.PARTIAL
            if any(
                isinstance(outcome, AccountRouteFailed)
                for outcome in state.routed.values()
            )
            else RouterRunStatus.SUCCESS
        )
        return state.result(status, include_posts=True)

    async def resolve_identities(
        self,
        locators: tuple[LinkedInLocator, ...],
        known_accounts: tuple[Account, ...],
    ) -> IdentityRouterResult:
        """Resolve locators through the same credential-bound instance pool."""
        return await IdentityRouter(
            self._registry,
            self._instances,
        ).resolve(locators, known_accounts)

    def _compatible_instances(
        self,
        operation: AdapterOperation,
        kind: AccountKind,
    ) -> tuple[AdapterInstance, ...] | None:
        resolution = self._registry.resolve(
            platform=Platform.LINKEDIN,
            operation=operation,
            account_kind=kind,
        )
        match resolution:
            case ResolvedAdapterDrivers(driver_classes=driver_classes):
                return tuple(
                    instance
                    for instance in self._instances
                    if instance.driver_class in driver_classes
                )
            case UnsupportedAdapterCapability():
                return None

    async def _route_batch(
        self,
        batch: instance_contract.AdapterBatch,
        start_index: int,
        state: RouterRunState,
    ) -> bool:
        compatible = self._collection_instances(batch, state)
        if compatible is None:
            return False
        ordered = tuple(
            compatible[(start_index + index) % len(compatible)]
            for index in range(len(compatible))
        )
        for adapter_instance in ordered:
            if not state.is_healthy(adapter_instance):
                continue
            attempt = await adapter_instance.collect(batch)
            match attempt:
                case instance_contract.BatchCompleted(outcomes=account_outcomes):
                    return not state.accept(batch, account_outcomes)
                case instance_contract.RetryableBatchFailure():
                    continue
                case instance_contract.QuotaBatchFailure():
                    state.disable_quota(adapter_instance)
                case instance_contract.InvalidCredentialBatchFailure():
                    state.disable_credential(adapter_instance)
                case instance_contract.AcceptedSnapshotBatchFailure():
                    state.fail_batch(
                        batch,
                        AccountRouteFailureCategory.ACCEPTED_SNAPSHOT_FAILED,
                    )
                    return False
                case instance_contract.SchemaBatchFailure():
                    state.diagnostics.append(
                        RouterDiagnostic(RouterDiagnosticCategory.SCHEMA_ABORT)
                    )
                    return True
        state.fail_batch(batch, AccountRouteFailureCategory.POOL_EXHAUSTED)
        return False

    def _collection_instances(
        self,
        batch: instance_contract.AdapterBatch,
        state: RouterRunState,
    ) -> tuple[AdapterInstance, ...] | None:
        compatible = self._compatible_instances(
            AdapterOperation.COLLECT_ACCOUNT_POSTS,
            batch.accounts[0].kind,
        )
        if compatible is None:
            state.fail_batch(
                batch,
                AccountRouteFailureCategory.UNSUPPORTED_CAPABILITY,
            )
            return None
        if not compatible:
            state.fail_batch(batch, AccountRouteFailureCategory.POOL_EXHAUSTED)
            return None
        return compatible
