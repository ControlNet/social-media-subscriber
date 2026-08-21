"""Deterministic locator Posts dispatch through the shared Adapter pool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final

from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.registry import (
    ResolvedAdapterDrivers,
    UnsupportedAdapterCapability,
)
from social_media_subscriber.adapters.router_discovery_state import (
    DiscoveryFailureCategory,
    DiscoveryRouterResult,
    DiscoveryRouterState,
)
from social_media_subscriber.domain.platform import AccountKind, Platform

if TYPE_CHECKING:
    from social_media_subscriber.adapters.instance import (
        AdapterInstance,
        AdapterPostLocatorBatch,
        AdapterPostLocatorRequest,
    )
    from social_media_subscriber.adapters.registry import AdapterRegistry

_MAX_BATCH_SIZE: Final = 20
_KIND_ORDER: Final = (AccountKind.PERSON, AccountKind.COMPANY)


@final
@dataclass(frozen=True, slots=True)
class DiscoveryRouter:
    """Route unknown locators without constructing another credential pool."""

    registry: AdapterRegistry
    instances: tuple[AdapterInstance, ...]

    async def discover(
        self,
        requests: tuple[AdapterPostLocatorRequest, ...],
    ) -> DiscoveryRouterResult:
        """Discover Accounts and Posts with fresh run-local health."""
        ordered = _canonical_requests(requests)
        state = DiscoveryRouterState(self.instances)
        batch_number = 0
        for kind in _KIND_ORDER:
            selected = tuple(
                request for request in ordered if request.locator.kind is kind
            )
            for offset in range(0, len(selected), _MAX_BATCH_SIZE):
                batch = instance_contract.AdapterPostLocatorBatch(
                    selected[offset : offset + _MAX_BATCH_SIZE]
                )
                if await self._route_batch(batch, batch_number, state):
                    return state.result(ordered)
                batch_number += 1
        return state.result(ordered)

    async def _route_batch(
        self,
        batch: AdapterPostLocatorBatch,
        start_index: int,
        state: DiscoveryRouterState,
    ) -> bool:
        compatible = self._batch_instances(batch, state)
        if compatible is None:
            return False
        ordered = tuple(
            compatible[(start_index + index) % len(compatible)]
            for index in range(len(compatible))
        )
        for adapter_instance in ordered:
            if not state.is_healthy(adapter_instance):
                continue
            attempt = await adapter_instance.discover_posts(batch)
            match attempt:
                case instance_contract.LocatorPostsBatchCompleted(outcomes=outcomes):
                    return not state.accept(batch, outcomes)
                case instance_contract.RetryableBatchFailure():
                    continue
                case instance_contract.QuotaBatchFailure():
                    state.disable_quota(adapter_instance)
                case instance_contract.InvalidCredentialBatchFailure():
                    state.disable_credential(adapter_instance)
                case instance_contract.AcceptedSnapshotBatchFailure():
                    state.fail_batch(
                        batch,
                        DiscoveryFailureCategory.ACCEPTED_SNAPSHOT_FAILED,
                    )
                    return False
                case instance_contract.SchemaBatchFailure():
                    state.abort_schema()
                    return True
        state.fail_batch(batch, DiscoveryFailureCategory.POOL_EXHAUSTED)
        return False

    def _batch_instances(
        self,
        batch: AdapterPostLocatorBatch,
        state: DiscoveryRouterState,
    ) -> tuple[AdapterInstance, ...] | None:
        compatible = self._compatible(batch.requests[0].locator.kind)
        if compatible is None:
            state.fail_batch(batch, DiscoveryFailureCategory.UNSUPPORTED_CAPABILITY)
            return None
        if not compatible:
            state.fail_batch(batch, DiscoveryFailureCategory.POOL_EXHAUSTED)
            return None
        return compatible

    def _compatible(
        self,
        kind: AccountKind,
    ) -> tuple[AdapterInstance, ...] | None:
        resolution = self.registry.resolve(
            platform=Platform.LINKEDIN,
            operation=AdapterOperation.DISCOVER_LOCATOR_POSTS,
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


def _canonical_requests(
    requests: tuple[AdapterPostLocatorRequest, ...],
) -> tuple[AdapterPostLocatorRequest, ...]:
    unique: dict[str, AdapterPostLocatorRequest] = {}
    for request in requests:
        locator_url = request.locator.canonical_url
        existing = unique.get(locator_url)
        if existing is not None and (
            existing.start_date != request.start_date
            or existing.end_date != request.end_date
        ):
            raise instance_contract.AdapterRequestError(
                instance_contract.AdapterRequestErrorCategory.CONFLICTING_WINDOW
            )
        if existing is None:
            unique[locator_url] = request
    return tuple(unique[key] for key in sorted(unique))
