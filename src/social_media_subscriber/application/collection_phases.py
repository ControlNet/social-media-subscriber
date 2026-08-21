"""Run known-Account and locator-discovery collection phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from social_media_subscriber.accounts.identity import (
    AccountIdentityConflictError,
    AccountIdentityService,
)
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.router_discovery_state import (
    DiscoveryFailureCategory,
    DiscoveryLocatorFailed,
    DiscoveryLocatorResolved,
    DiscoveryLocatorUnresolved,
)
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    AccountRouteSucceeded,
    RouterRunStatus,
)
from social_media_subscriber.application.results import (
    CollectionExitCode,
    CollectionResult,
    aborted_result,
)
from social_media_subscriber.application.windows import (
    ExplicitWindow,
    WindowContext,
    build_locator_post_requests,
    build_post_requests,
)
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.accounts.input import AccountInput
    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.router_discovery_state import (
        DiscoveryRouterResult,
    )
    from social_media_subscriber.adapters.router_outcomes import RouterResult
    from social_media_subscriber.bootstrap import SubscriberRuntime
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId


@dataclass(frozen=True, slots=True)
class PreparedCollection:
    """Validated inputs shared by both collection phases."""

    account_input: AccountInput
    previous: SnapshotState | None
    run_started_at: datetime
    override: ExplicitWindow


@dataclass(frozen=True, slots=True)
class CollectedPosts:
    """Combined canonical output and terminal counts from both phases."""

    current: SnapshotState
    succeeded_count: int
    failed_count: int
    failed_ids: tuple[AccountId, ...]
    pool_exhausted_count: int


@dataclass(frozen=True, slots=True)
class _CollectionPartition:
    known_accounts: tuple[Account, ...]
    unknown_locators: tuple[LinkedInLocator, ...]


def _post_outcomes(
    post_result: RouterResult,
) -> tuple[set[AccountId], tuple[AccountId, ...], int]:
    succeeded: set[AccountId] = set()
    failed: list[AccountId] = []
    pool_exhausted = 0
    for outcome in post_result.accounts:
        match outcome:
            case AccountRouteSucceeded(account_id=account_id):
                succeeded.add(account_id)
            case AccountRouteFailed(
                account_id=account_id,
                category=AccountRouteFailureCategory.POOL_EXHAUSTED,
            ):
                failed.append(account_id)
                pool_exhausted += 1
            case AccountRouteFailed(account_id=account_id):
                failed.append(account_id)
    return succeeded, tuple(sorted(failed, key=str)), pool_exhausted


def _discovery_pool_exhausted(result: DiscoveryRouterResult) -> int:
    count = 0
    for outcome in result.outcomes:
        match outcome:
            case DiscoveryLocatorResolved() | DiscoveryLocatorUnresolved():
                pass
            case DiscoveryLocatorFailed(
                category=DiscoveryFailureCategory.POOL_EXHAUSTED
            ):
                count += 1
            case DiscoveryLocatorFailed():
                pass
    return count


def _partition_locators(
    prepared: PreparedCollection,
) -> _CollectionPartition | CollectionResult:
    previous_accounts = () if prepared.previous is None else prepared.previous.accounts
    try:
        identity_service = AccountIdentityService(previous_accounts)
    except AccountIdentityConflictError:
        return aborted_result(CollectionExitCode.INTEGRITY)
    known_accounts: dict[AccountId, Account] = {}
    unknown_locators: list[LinkedInLocator] = []
    for locator in prepared.account_input.locators:
        account = identity_service.find(locator)
        if account is None:
            unknown_locators.append(locator)
        else:
            known_accounts[account.id] = account
    return _CollectionPartition(
        tuple(known_accounts[key] for key in sorted(known_accounts, key=str)),
        tuple(unknown_locators),
    )


async def _collect_known_posts(
    accounts: tuple[Account, ...],
    prepared: PreparedCollection,
    runtime: SubscriberRuntime,
) -> CollectedPosts | CollectionResult:
    requests = build_post_requests(
        accounts,
        prepared.previous,
        WindowContext(prepared.run_started_at, prepared.override),
    )
    if not requests:
        return CollectedPosts(SnapshotState((), (), ()), 0, 0, (), 0)
    result = await runtime.router.route(
        requests,
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )
    match result.aggregate.status:
        case RouterRunStatus.ABORTED:
            return aborted_result(CollectionExitCode.INTEGRITY)
        case RouterRunStatus.SUCCESS | RouterRunStatus.PARTIAL:
            succeeded, failed, pool_exhausted = _post_outcomes(result)
    successful_accounts = tuple(
        account for account in accounts if account.id in succeeded
    )
    return CollectedPosts(
        SnapshotState(successful_accounts, result.posts, result.source_records),
        len(succeeded),
        len(failed),
        failed,
        pool_exhausted,
    )


async def _discover_posts(
    locators: tuple[LinkedInLocator, ...],
    prepared: PreparedCollection,
    runtime: SubscriberRuntime,
) -> CollectedPosts | CollectionResult:
    requests = build_locator_post_requests(
        locators,
        WindowContext(prepared.run_started_at, prepared.override),
    )
    if not requests:
        return CollectedPosts(SnapshotState((), (), ()), 0, 0, (), 0)
    result = await runtime.router.discover_posts(requests)
    match result.aggregate.status:
        case RouterRunStatus.ABORTED:
            return aborted_result(CollectionExitCode.INTEGRITY)
        case RouterRunStatus.SUCCESS | RouterRunStatus.PARTIAL:
            pass
    failed_count = (
        result.aggregate.unresolved_locators + result.aggregate.failed_locators
    )
    return CollectedPosts(
        SnapshotState(result.accounts, result.posts, result.source_records),
        result.aggregate.resolved_locators,
        failed_count,
        (),
        _discovery_pool_exhausted(result),
    )


async def collect_posts(
    prepared: PreparedCollection,
    runtime: SubscriberRuntime,
) -> CollectedPosts | CollectionResult:
    """Collect known Accounts and unknown locators before candidate mutation."""
    partition = _partition_locators(prepared)
    match partition:
        case CollectionResult() as terminal:
            return terminal
        case _CollectionPartition():
            pass
    known = await _collect_known_posts(partition.known_accounts, prepared, runtime)
    match known:
        case CollectionResult() as terminal:
            return terminal
        case CollectedPosts():
            pass
    discovered = await _discover_posts(
        partition.unknown_locators,
        prepared,
        runtime,
    )
    match discovered:
        case CollectionResult() as terminal:
            return terminal
        case CollectedPosts():
            pass
    succeeded_count = known.succeeded_count + discovered.succeeded_count
    failed_count = known.failed_count + discovered.failed_count
    pool_exhausted_count = known.pool_exhausted_count + discovered.pool_exhausted_count
    if not succeeded_count and failed_count == pool_exhausted_count:
        return aborted_result(CollectionExitCode.PROVIDER)
    return CollectedPosts(
        SnapshotState(
            known.current.accounts + discovered.current.accounts,
            known.current.posts + discovered.current.posts,
            known.current.source_records + discovered.current.source_records,
        ),
        succeeded_count,
        failed_count,
        known.failed_ids,
        pool_exhausted_count,
    )
