"""Deterministic identity dispatch through the shared Adapter instance pool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final

from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.registry import (
    ResolvedAdapterDrivers,
    UnsupportedAdapterCapability,
)
from social_media_subscriber.adapters.router_identity_state import IdentityRouterState
from social_media_subscriber.domain.platform import AccountKind, Platform

if TYPE_CHECKING:
    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.instance import AdapterInstance
    from social_media_subscriber.adapters.registry import AdapterRegistry
    from social_media_subscriber.adapters.router_outcomes import IdentityRouterResult
    from social_media_subscriber.domain.account import Account

_MAX_BATCH_SIZE: Final = 20
_KIND_ORDER: Final = (AccountKind.PERSON, AccountKind.COMPANY)


@final
@dataclass(frozen=True, slots=True)
class IdentityRouter:
    """Route identity batches without constructing another credential pool."""

    registry: AdapterRegistry
    instances: tuple[AdapterInstance, ...]

    async def resolve(
        self,
        locators: tuple[LinkedInLocator, ...],
        known_accounts: tuple[Account, ...],
    ) -> IdentityRouterResult:
        """Resolve known aliases locally and route only unknown locators."""
        state = IdentityRouterState(self.instances, known_accounts)
        state.seed_known(locators)
        if state.aborted:
            return state.result(locators)
        unknown = tuple(
            locator
            for locator in locators
            if locator.canonical_url not in state.outcomes
        )
        batch_number = 0
        for kind in _KIND_ORDER:
            selected = tuple(locator for locator in unknown if locator.kind is kind)
            for offset in range(0, len(selected), _MAX_BATCH_SIZE):
                batch = instance_contract.AdapterIdentityBatch(
                    selected[offset : offset + _MAX_BATCH_SIZE],
                    known_accounts,
                )
                if await self._route_batch(batch, batch_number, state):
                    return state.result(locators)
                batch_number += 1
        return state.result(locators)

    async def _route_batch(
        self,
        batch: instance_contract.AdapterIdentityBatch,
        start_index: int,
        state: IdentityRouterState,
    ) -> bool:
        compatible = self._compatible(batch.locators[0].kind)
        if compatible is None or not compatible:
            state.fail(batch)
            return False
        ordered = tuple(
            compatible[(start_index + index) % len(compatible)]
            for index in range(len(compatible))
        )
        for adapter_instance in ordered:
            if not state.is_healthy(adapter_instance):
                continue
            attempt = await adapter_instance.resolve_identity(batch)
            match attempt:
                case instance_contract.IdentityBatchCompleted(outcomes=outcomes):
                    return not state.accept(batch, outcomes)
                case instance_contract.RetryableBatchFailure():
                    continue
                case instance_contract.QuotaBatchFailure():
                    state.disable_quota(adapter_instance)
                case instance_contract.InvalidCredentialBatchFailure():
                    state.disable_credential(adapter_instance)
                case instance_contract.AcceptedSnapshotBatchFailure():
                    state.fail(batch)
                    return False
                case instance_contract.SchemaBatchFailure():
                    state.abort_schema()
                    return True
        state.fail(batch)
        return False

    def _compatible(
        self,
        kind: AccountKind,
    ) -> tuple[AdapterInstance, ...] | None:
        resolution = self.registry.resolve(
            platform=Platform.LINKEDIN,
            operation=AdapterOperation.RESOLVE_ACCOUNT_IDENTITY,
            account_kind=kind,
        )
        match resolution:
            case ResolvedAdapterDrivers(driver_classes=driver_classes):
                return tuple(
                    instance
                    for instance in self.instances
                    if instance.driver_class in driver_classes
                )
            case UnsupportedAdapterCapability():
                return None
