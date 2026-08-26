"""Collect every requested URL Account through the normal Posts route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    build_post_requests,
)
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.adapters.router_outcomes import RouterResult
    from social_media_subscriber.bootstrap import SubscriberRuntime
    from social_media_subscriber.runtime_input import RuntimeInput


@dataclass(frozen=True, slots=True)
class PreparedCollection:
    """Validated inputs shared by both collection phases."""

    runtime_input: RuntimeInput
    previous: SnapshotState | None
    run_started_at: datetime
    override: ExplicitWindow


@dataclass(frozen=True, slots=True)
class CollectedPosts:
    """Canonical output and terminal counts from the Posts route."""

    current: SnapshotState
    succeeded_count: int
    failed_count: int
    failed_ids: tuple[AccountId, ...]
    pool_exhausted_count: int


def _post_outcomes(
    post_result: RouterResult,
) -> tuple[set[AccountId], tuple[AccountId, ...], int]:
    succeeded: set[AccountId] = set()
    failed: list[AccountId] = []
    pool_exhausted = 0
    for outcome in post_result.accounts:
        match outcome:  # The outcome union is exhaustively matched.
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
    return succeeded, tuple(sorted(set(failed), key=str)), pool_exhausted


def _requested_accounts(prepared: PreparedCollection) -> tuple[Account, ...]:
    previous_by_id = {
        account.id: account
        for account in (() if prepared.previous is None else prepared.previous.accounts)
    }
    requested: dict[AccountId, Account] = {}
    for locator in prepared.runtime_input.locators:
        account_id = AccountId(locator.canonical_url)
        requested[account_id] = previous_by_id.get(account_id) or Account(
            platform=locator.platform,
            kind=locator.kind,
            profile_url=locator.canonical_url,
            first_seen_at=prepared.run_started_at,
        )
    return tuple(requested[key] for key in sorted(requested, key=str))


def _provider_failure(failed_ids: tuple[AccountId, ...]) -> CollectionResult:
    return CollectionResult(
        CollectionExitCode.PROVIDER,
        CandidateChange.ABSENT,
        None,
        0,
        len(failed_ids),
        failed_ids,
    )


async def collect_posts(
    prepared: PreparedCollection,
    runtime: SubscriberRuntime,
) -> CollectedPosts | CollectionResult:
    """Collect requested canonical URL Accounts before candidate mutation."""
    accounts = _requested_accounts(prepared)
    requests = build_post_requests(
        accounts,
        prepared.previous,
        WindowContext(prepared.run_started_at, prepared.override),
    )
    if not requests:
        return CollectedPosts(SnapshotState((), ()), 0, 0, (), 0)
    result = await runtime.router.route(requests)
    match result.aggregate.status:  # The status enum is exhaustively grouped.
        case RouterRunStatus.ABORTED:
            return aborted_result(CollectionExitCode.INTEGRITY)
        case RouterRunStatus.SUCCESS | RouterRunStatus.PARTIAL:
            succeeded, failed, pool_exhausted = _post_outcomes(result)
    successful_accounts = tuple(
        account for account in accounts if account.id in succeeded
    )
    if not succeeded and failed and len(failed) == pool_exhausted:
        return _provider_failure(failed)
    return CollectedPosts(
        SnapshotState(successful_accounts, result.posts),
        len(succeeded),
        len(failed),
        failed,
        pool_exhausted,
    )
