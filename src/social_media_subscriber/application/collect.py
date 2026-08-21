"""Incremental collection, merge, and candidate orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
from pydantic_core import PydanticCustomError

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.identity import (
    AccountIdentityConflictError,
    AccountIdentityService,
)
from social_media_subscriber.accounts.input import load_account_input
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
    CandidateChange,
    CollectionExitCode,
    CollectionResult,
    aborted_result,
)
from social_media_subscriber.application.windows import (
    ExplicitWindow,
    WindowContext,
    WindowInputError,
    build_locator_post_requests,
    build_post_requests,
)
from social_media_subscriber.bootstrap import bootstrap_runtime
from social_media_subscriber.domain.time import canonical_utc
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataAdapterConfig,
)
from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.storage.merge import SnapshotConflictError, merge_snapshot
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime
    from pathlib import Path

    from social_media_subscriber.accounts.input import AccountInput
    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.router_discovery_state import (
        DiscoveryRouterResult,
    )
    from social_media_subscriber.adapters.router_outcomes import (
        RouterResult,
    )
    from social_media_subscriber.bootstrap import SubscriberRuntime
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataClientContract,
    )
    from social_media_subscriber.settings import Settings

type ClientBuilder = Callable[[str], BrightDataClientContract]


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    """All deterministic boundary inputs for one collection attempt."""

    settings: Settings
    previous_snapshot_dir: Path
    candidate_snapshot_dir: Path
    run_started_at: datetime
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class _PreparedRun:
    account_input: AccountInput
    previous: SnapshotState | None
    run_started_at: datetime
    override: ExplicitWindow


@dataclass(frozen=True, slots=True)
class _CollectionPartition:
    known_accounts: tuple[Account, ...]
    unknown_locators: tuple[LinkedInLocator, ...]


@dataclass(frozen=True, slots=True)
class _PostPhase:
    current: SnapshotState
    succeeded_count: int
    failed_count: int
    failed_ids: tuple[AccountId, ...]
    pool_exhausted_count: int


def _build_client(credential: str) -> BrightDataClient:
    return BrightDataClient(credential)


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


def _prepare(request: CollectionRequest) -> _PreparedRun | CollectionResult:
    if (
        request.previous_snapshot_dir.resolve()
        == request.candidate_snapshot_dir.resolve()
    ):
        return aborted_result(CollectionExitCode.INPUT)
    try:
        account_input = load_account_input(request.settings)
        run_started_at = canonical_utc(request.run_started_at)
        override = ExplicitWindow.parse(request.start_date, request.end_date)
        previous = SnapshotRepository(request.previous_snapshot_dir).load_optional()
    except (AccountInputError, WindowInputError, PydanticCustomError):
        return aborted_result(CollectionExitCode.INPUT)
    except SnapshotIntegrityError:
        return aborted_result(CollectionExitCode.INTEGRITY)
    return _PreparedRun(account_input, previous, run_started_at, override)


def _partition_locators(
    prepared: _PreparedRun,
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


async def _collect_posts(
    accounts: tuple[Account, ...],
    prepared: _PreparedRun,
    runtime: SubscriberRuntime,
) -> _PostPhase | CollectionResult:
    requests = build_post_requests(
        accounts,
        prepared.previous,
        WindowContext(prepared.run_started_at, prepared.override),
    )
    if not requests:
        return _PostPhase(SnapshotState((), (), ()), 0, 0, (), 0)
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
    return _PostPhase(
        SnapshotState(successful_accounts, result.posts, result.source_records),
        len(succeeded),
        len(failed),
        failed,
        pool_exhausted,
    )


async def _discover_posts(
    locators: tuple[LinkedInLocator, ...],
    prepared: _PreparedRun,
    runtime: SubscriberRuntime,
) -> _PostPhase | CollectionResult:
    requests = build_locator_post_requests(
        locators,
        WindowContext(prepared.run_started_at, prepared.override),
    )
    if not requests:
        return _PostPhase(SnapshotState((), (), ()), 0, 0, (), 0)
    result = await runtime.router.discover_posts(requests)
    match result.aggregate.status:
        case RouterRunStatus.ABORTED:
            return aborted_result(CollectionExitCode.INTEGRITY)
        case RouterRunStatus.SUCCESS | RouterRunStatus.PARTIAL:
            pass
    failed_count = (
        result.aggregate.unresolved_locators + result.aggregate.failed_locators
    )
    return _PostPhase(
        SnapshotState(result.accounts, result.posts, result.source_records),
        result.aggregate.resolved_locators,
        failed_count,
        (),
        _discovery_pool_exhausted(result),
    )


async def _collect_with_runtime(
    request: CollectionRequest,
    prepared: _PreparedRun,
    runtime: SubscriberRuntime,
) -> CollectionResult:
    partition = _partition_locators(prepared)
    match partition:
        case CollectionResult() as terminal:
            return terminal
        case _CollectionPartition():
            pass
    known_phase = await _collect_posts(
        partition.known_accounts,
        prepared,
        runtime,
    )
    match known_phase:
        case CollectionResult() as terminal:
            return terminal
        case _PostPhase():
            pass
    discovery_phase = await _discover_posts(
        partition.unknown_locators,
        prepared,
        runtime,
    )
    match discovery_phase:
        case CollectionResult() as terminal:
            return terminal
        case _PostPhase():
            pass
    succeeded_count = known_phase.succeeded_count + discovery_phase.succeeded_count
    failed_count = known_phase.failed_count + discovery_phase.failed_count
    pool_exhausted_count = (
        known_phase.pool_exhausted_count + discovery_phase.pool_exhausted_count
    )
    if not succeeded_count and failed_count == pool_exhausted_count:
        return aborted_result(CollectionExitCode.PROVIDER)
    current = SnapshotState(
        known_phase.current.accounts + discovery_phase.current.accounts,
        known_phase.current.posts + discovery_phase.current.posts,
        known_phase.current.source_records + discovery_phase.current.source_records,
    )
    try:
        candidate = merge_snapshot(prepared.previous, current)
        manifest = SnapshotRepository(request.candidate_snapshot_dir).write(candidate)
    except (SnapshotConflictError, SnapshotIntegrityError):
        return aborted_result(CollectionExitCode.INTEGRITY)
    exit_code = (
        CollectionExitCode.PARTIAL if failed_count else CollectionExitCode.SUCCESS
    )
    changed = prepared.previous is None or candidate != prepared.previous
    return CollectionResult(
        exit_code,
        CandidateChange.CHANGED if changed else CandidateChange.UNCHANGED,
        manifest.digest,
        succeeded_count,
        failed_count,
        known_phase.failed_ids,
    )


async def collect_snapshot(
    request: CollectionRequest,
    client_builder: ClientBuilder = _build_client,
) -> CollectionResult:
    """Build one validated complete candidate without publishing it."""
    prepared = _prepare(request)
    match prepared:
        case CollectionResult() as terminal:
            return terminal
        case _PreparedRun():
            pass
    runtime = bootstrap_runtime(
        prepared.account_input,
        BrightDataAdapterConfig(prepared.run_started_at),
        client_builder=client_builder,
    )
    try:
        return await _collect_with_runtime(request, prepared, runtime)
    finally:
        with anyio.CancelScope(shield=True):
            await runtime.aclose()
