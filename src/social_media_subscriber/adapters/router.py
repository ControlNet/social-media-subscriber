"""Deterministic batching and run-local classified Adapter failover."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Final, final

from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    RouterDiagnostic,
    RouterDiagnosticCategory,
    RouterResult,
    RouterRunStatus,
)
from social_media_subscriber.adapters.router_state import RouterRunState
from social_media_subscriber.domain.platform import AccountKind, Platform

if TYPE_CHECKING:
    from social_media_subscriber.adapters.instance import (
        AdapterInstance,
        AdapterInstanceSpec,
        AdapterPostRequest,
    )
    from social_media_subscriber.adapters.registry import AdapterRegistry
    from social_media_subscriber.domain.ids import AccountId

_MAX_BATCH_SIZE: Final = 20


@final
class Router:
    """Route bounded homogeneous batches through authorized Adapter instances."""

    _registry: AdapterRegistry
    _instances: tuple[AdapterInstance, ...]
    _closed: bool

    def __init__(
        self,
        registry: AdapterRegistry,
        instance_specs: tuple[AdapterInstanceSpec, ...],
    ) -> None:
        """Create one ordered adapter instance per source specification."""
        self._registry = registry
        registered = frozenset(registry.driver_classes)
        instances: list[AdapterInstance] = []
        for spec in instance_specs:
            if spec.driver_class not in registered:
                message = f"invalid Adapter instance spec: {spec.driver_class.__name__}"
                raise ValueError(message)
            ordinal = instance_contract.AdapterInstanceOrdinal(len(instances))
            instance = spec.factory.create(spec.credential, ordinal)
            if instance.driver_class is not spec.driver_class:
                message = "Adapter factory returned an instance for a different driver"
                raise ValueError(message)
            instances.append(instance)
        self._instances = tuple(instances)
        self._closed = False

    async def aclose(self) -> None:
        """Close every credential instance at most once."""
        if self._closed:
            return
        self._closed = True
        async with AsyncExitStack() as stack:
            for instance in self._instances:
                _ = stack.push_async_callback(instance.aclose)

    async def route(
        self,
        requests: tuple[AdapterPostRequest, ...],
    ) -> RouterResult:
        """Collect Accounts deterministically with health scoped to this call."""
        unique_requests: dict[AccountId, AdapterPostRequest] = {}
        for request in requests:
            existing = unique_requests.get(request.account.id)
            if existing is not None and (
                existing.start_date != request.start_date
                or existing.end_date != request.end_date
            ):
                raise instance_contract.AdapterRequestError(
                    instance_contract.AdapterRequestErrorCategory.CONFLICTING_WINDOW
                )
            if existing is None:
                unique_requests[request.account.id] = request
        ordered_requests = tuple(
            unique_requests[key] for key in sorted(unique_requests, key=str)
        )
        state = RouterRunState.for_instances(self._instances)
        for platform in Platform:
            for kind in AccountKind:
                kind_requests = tuple(
                    request
                    for request in ordered_requests
                    if request.account.platform is platform
                    and request.account.kind is kind
                )
                batch_size = self._batch_size(platform, kind)
                for offset in range(0, len(kind_requests), batch_size):
                    batch = instance_contract.AdapterBatch(
                        requests=kind_requests[offset : offset + batch_size]
                    )
                    if await self._route_batch(batch, state):
                        return state.result(
                            RouterRunStatus.ABORTED, include_posts=False
                        )

        status = (
            RouterRunStatus.PARTIAL
            if any(
                isinstance(outcome, AccountRouteFailed)
                for outcome in state.routed.values()
            )
            else RouterRunStatus.SUCCESS
        )
        return state.result(status, include_posts=True)

    def _compatible_instances(
        self,
        platform: Platform,
        operation: AdapterOperation,
        kind: AccountKind,
    ) -> tuple[AdapterInstance, ...] | None:
        driver_classes = self._registry.resolve(
            platform=platform,
            operation=operation,
            account_kind=kind,
        )
        if not driver_classes:
            return None
        return tuple(
            instance
            for instance in self._instances
            if instance.driver_class in driver_classes
        )

    def _batch_size(self, platform: Platform, kind: AccountKind) -> int:
        compatible = self._compatible_instances(
            platform,
            AdapterOperation.COLLECT_ACCOUNT_POSTS,
            kind,
        )
        if compatible and any(
            not instance.driver_class.adapter_metadata.supports_batch
            for instance in compatible
        ):
            return 1
        return _MAX_BATCH_SIZE

    async def _route_batch(
        self,
        batch: instance_contract.AdapterBatch,
        state: RouterRunState,
    ) -> bool:
        compatible = self._collection_instances(batch, state)
        if compatible is None:
            return False
        for adapter_instance in compatible:
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
            batch.accounts[0].platform,
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
