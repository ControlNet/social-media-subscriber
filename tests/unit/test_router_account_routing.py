from __future__ import annotations

__test__ = False

import pytest

from social_media_subscriber.adapters import AdapterOperation
from social_media_subscriber.adapters.instance import AdapterInstanceOrdinal
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind
from tests.fakes.router import make_account
from tests.unit.test_router_support import build_post_requests, build_router


@pytest.mark.anyio
async def test_empty_account_set_succeeds_without_provider_calls() -> None:
    # Given
    router, factory = build_router(((),))

    # When
    result = await router.route((), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert result.accounts == ()
    assert factory.calls == []


@pytest.mark.anyio
async def test_zero_instance_pool_returns_account_scoped_exhaustion() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router((), ())

    # When
    result = await router.route(
        build_post_requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert result.accounts == (
        AccountRouteFailed(account.id, AccountRouteFailureCategory.POOL_EXHAUSTED),
    )
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    assert factory.created_ordinals == []


@pytest.mark.anyio
@pytest.mark.parametrize("size", [1, 20, 21])
async def test_batches_are_bounded_and_stably_distributed(size: int) -> None:
    # Given
    accounts = tuple(
        make_account(AccountKind.PERSON, number) for number in range(size, 0, -1)
    )
    router, factory = build_router(((), (), ()), ("key-a", "key-b", "key-c"))

    # When
    result = await router.route(
        build_post_requests(accounts), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert [call.ordinal for call in factory.calls] == [
        AdapterInstanceOrdinal(index) for index in range((size + 19) // 20)
    ]
    assert all(len(call.account_ids) <= 20 for call in factory.calls)
    assert tuple(
        account_id for call in factory.calls for account_id in call.account_ids
    ) == tuple(sorted((account.id for account in accounts), key=str))


@pytest.mark.anyio
async def test_person_and_company_batches_are_separate_and_stable() -> None:
    # Given
    people = tuple(
        make_account(AccountKind.PERSON, number) for number in range(21, 0, -1)
    )
    companies = tuple(make_account(AccountKind.COMPANY, number) for number in (2, 1))
    router, factory = build_router(((), (), ()), ("key-a", "key-b", "key-c"))

    # When
    _ = await router.route(
        build_post_requests((*companies, *people)),
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )

    # Then
    assert [
        (call.ordinal, call.kind, len(call.account_ids)) for call in factory.calls
    ] == [
        (AdapterInstanceOrdinal(0), AccountKind.PERSON, 20),
        (AdapterInstanceOrdinal(1), AccountKind.PERSON, 1),
        (AdapterInstanceOrdinal(2), AccountKind.COMPANY, 2),
    ]
