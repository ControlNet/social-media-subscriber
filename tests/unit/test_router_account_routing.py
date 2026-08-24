from __future__ import annotations

__test__ = False

import pytest
from pydantic import SecretStr

from social_media_subscriber.adapters.instance import (
    AdapterInstanceOrdinal,
    AdapterInstanceSpec,
)
from social_media_subscriber.adapters.registry import AdapterRegistry
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind
from tests.fakes.router import (
    FakeDriver,
    FallbackFakeDriver,
    NonBatchFakeDriver,
    ScriptedFactory,
    make_account,
)
from tests.unit.test_router_support import build_post_requests, build_router


@pytest.mark.anyio
async def test_empty_account_set_succeeds_without_provider_calls() -> None:
    # Given
    router, factory = build_router(((),))

    # When
    result = await router.route(())

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
    result = await router.route(build_post_requests((account,)))

    # Then
    assert result.accounts == (
        AccountRouteFailed(account.id, AccountRouteFailureCategory.POOL_EXHAUSTED),
    )
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    assert factory.created_ordinals == []


@pytest.mark.anyio
@pytest.mark.parametrize("size", [1, 20, 21])
async def test_batches_are_bounded_and_start_with_first_source(size: int) -> None:
    # Given
    accounts = tuple(
        make_account(AccountKind.PERSON, number) for number in range(size, 0, -1)
    )
    router, factory = build_router(((), (), ()), ("key-a", "key-b", "key-c"))

    # When
    result = await router.route(build_post_requests(accounts))

    # Then
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert [call.ordinal for call in factory.calls] == [
        AdapterInstanceOrdinal(0) for _ in range((size + 19) // 20)
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
    )

    # Then
    assert [
        (call.ordinal, call.kind, len(call.account_ids)) for call in factory.calls
    ] == [
        (AdapterInstanceOrdinal(0), AccountKind.PERSON, 20),
        (AdapterInstanceOrdinal(0), AccountKind.PERSON, 1),
        (AdapterInstanceOrdinal(0), AccountKind.COMPANY, 2),
    ]


@pytest.mark.anyio
async def test_non_batching_compatible_driver_forces_single_account_batches() -> None:
    # Given
    accounts = tuple(make_account(AccountKind.PERSON, number) for number in (3, 2, 1))
    batching = ScriptedFactory(((),), FakeDriver)
    non_batching = ScriptedFactory(((),), NonBatchFakeDriver)
    router = Router(
        AdapterRegistry((FakeDriver, NonBatchFakeDriver)),
        (
            AdapterInstanceSpec(
                FakeDriver, batching, SecretStr("batching-provider-key")
            ),
            AdapterInstanceSpec(
                NonBatchFakeDriver,
                non_batching,
                SecretStr("single-provider-key"),
            ),
        ),
    )

    # When
    result = await router.route(build_post_requests(accounts))

    # Then
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    calls = (*batching.calls, *non_batching.calls)
    assert len(calls) == len(accounts)
    assert all(len(call.account_ids) == 1 for call in calls)


def test_router_creates_one_instance_per_source_with_the_same_driver() -> None:
    # Given
    first = ScriptedFactory(((),))
    second = ScriptedFactory(((),))

    # When
    _ = Router(
        AdapterRegistry((FakeDriver,)),
        (
            AdapterInstanceSpec(FakeDriver, first, SecretStr("first-provider-key")),
            AdapterInstanceSpec(FakeDriver, second, SecretStr("second-provider-key")),
        ),
    )

    # Then
    assert first.created_ordinals == [0]
    assert second.created_ordinals == [1]


def test_router_rejects_unregistered_instance_spec() -> None:
    # Given / When / Then
    with pytest.raises(ValueError, match="invalid Adapter instance spec"):
        _ = Router(
            AdapterRegistry((FakeDriver,)),
            (
                AdapterInstanceSpec(
                    FallbackFakeDriver,
                    ScriptedFactory(((),), FallbackFakeDriver),
                    SecretStr("provider-key"),
                ),
            ),
        )


def test_router_rejects_factory_instance_for_a_different_driver() -> None:
    # Given
    mismatched_factory = ScriptedFactory(((),), FallbackFakeDriver)

    # When / Then
    with pytest.raises(
        ValueError,
        match="factory returned an instance for a different driver",
    ):
        _ = Router(
            AdapterRegistry((FakeDriver, FallbackFakeDriver)),
            (
                AdapterInstanceSpec(
                    FakeDriver,
                    mismatched_factory,
                    SecretStr("provider-key"),
                ),
            ),
        )
