"""Incremental collection, merge, and candidate orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_core import PydanticCustomError

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.input import load_account_input
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    AccountRouteSucceeded,
    InstanceHealthStatus,
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
    build_post_requests,
)
from social_media_subscriber.bootstrap import bootstrap_runtime
from social_media_subscriber.domain.time import canonical_utc
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataAdapterConfig,
)
from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    ResolvedAccountIdentity,
    UnresolvedAccountIdentity,
)
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
    from social_media_subscriber.adapters.router_outcomes import (
        IdentityRouterResult,
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
class _IdentityPhase:
    runtime: SubscriberRuntime
    accounts: tuple[Account, ...]
    unresolved_count: int


@dataclass(frozen=True, slots=True)
class _PostPhase:
    current: SnapshotState
    succeeded_count: int
    failed_ids: tuple[AccountId, ...]


def _build_client(credential: str) -> BrightDataClient:
    return BrightDataClient(credential)


def _resolved_accounts(
    identity_result: IdentityRouterResult,
) -> tuple[tuple[Account, ...], int]:
    accounts: dict[AccountId, Account] = {}
    unresolved = 0
    for outcome in identity_result.outcomes:
        match outcome:
            case ResolvedAccountIdentity(account=account):
                accounts[account.id] = account
            case UnresolvedAccountIdentity():
                unresolved += 1
    return tuple(accounts[key] for key in sorted(accounts, key=str)), unresolved


def _pool_is_unusable(identity_result: IdentityRouterResult) -> bool:
    return bool(identity_result.health) and all(
        item.status is not InstanceHealthStatus.HEALTHY
        for item in identity_result.health
    )


def _post_outcomes(
    post_result: RouterResult,
) -> tuple[set[AccountId], tuple[AccountId, ...]]:
    succeeded: set[AccountId] = set()
    failed: list[AccountId] = []
    for outcome in post_result.accounts:
        match outcome:
            case AccountRouteSucceeded(account_id=account_id):
                succeeded.add(account_id)
            case AccountRouteFailed(account_id=account_id):
                failed.append(account_id)
    return succeeded, tuple(sorted(failed, key=str))


def _total_pool_failure(post_result: RouterResult) -> bool:
    return bool(post_result.accounts) and all(
        isinstance(outcome, AccountRouteFailed)
        and outcome.category is AccountRouteFailureCategory.POOL_EXHAUSTED
        for outcome in post_result.accounts
    )


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


async def _resolve_identities(
    prepared: _PreparedRun,
    client_builder: ClientBuilder,
) -> _IdentityPhase | CollectionResult:
    runtime = bootstrap_runtime(
        prepared.account_input,
        BrightDataAdapterConfig(prepared.run_started_at),
        client_builder=client_builder,
    )
    known_accounts = () if prepared.previous is None else prepared.previous.accounts
    result = await runtime.router.resolve_identities(
        prepared.account_input.locators,
        known_accounts,
    )
    match result.aggregate.status:
        case RouterRunStatus.ABORTED:
            return aborted_result(CollectionExitCode.INTEGRITY)
        case RouterRunStatus.SUCCESS | RouterRunStatus.PARTIAL:
            accounts, unresolved = _resolved_accounts(result)
    if not accounts and _pool_is_unusable(result):
        return aborted_result(CollectionExitCode.PROVIDER)
    return _IdentityPhase(runtime, accounts, unresolved)


async def _collect_posts(
    phase: _IdentityPhase,
    prepared: _PreparedRun,
) -> _PostPhase | CollectionResult:
    requests = build_post_requests(
        phase.accounts,
        prepared.previous,
        WindowContext(prepared.run_started_at, prepared.override),
    )
    if not requests:
        return _PostPhase(SnapshotState((), (), ()), 0, ())
    result = await phase.runtime.router.route(
        requests,
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )
    match result.aggregate.status:
        case RouterRunStatus.ABORTED:
            return aborted_result(CollectionExitCode.INTEGRITY)
        case RouterRunStatus.SUCCESS | RouterRunStatus.PARTIAL:
            succeeded, failed = _post_outcomes(result)
    if _total_pool_failure(result):
        return aborted_result(CollectionExitCode.PROVIDER)
    successful_accounts = tuple(
        account for account in phase.accounts if account.id in succeeded
    )
    return _PostPhase(
        SnapshotState(successful_accounts, result.posts, result.source_records),
        len(succeeded),
        failed,
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
    identity = await _resolve_identities(prepared, client_builder)
    match identity:
        case CollectionResult() as terminal:
            return terminal
        case _IdentityPhase():
            pass
    post_phase = await _collect_posts(identity, prepared)
    match post_phase:
        case CollectionResult() as terminal:
            return terminal
        case _PostPhase():
            pass
    try:
        candidate = merge_snapshot(prepared.previous, post_phase.current)
        manifest = SnapshotRepository(request.candidate_snapshot_dir).write(candidate)
    except (SnapshotConflictError, SnapshotIntegrityError):
        return aborted_result(CollectionExitCode.INTEGRITY)
    failed_count = identity.unresolved_count + len(post_phase.failed_ids)
    exit_code = (
        CollectionExitCode.PARTIAL if failed_count else CollectionExitCode.SUCCESS
    )
    changed = prepared.previous is None or candidate != prepared.previous
    return CollectionResult(
        exit_code,
        CandidateChange.CHANGED if changed else CandidateChange.UNCHANGED,
        manifest.digest,
        post_phase.succeeded_count,
        failed_count,
        post_phase.failed_ids,
    )
