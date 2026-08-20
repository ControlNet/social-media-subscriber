from __future__ import annotations

import pytest
from pydantic import SecretStr

from social_media_subscriber.adapters import (
    AdapterOperation,
    AdapterRegistry,
    ResolvedAdapterDrivers,
    adapter,
)
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AccountRejectionCategory,
    AdapterInstanceOrdinal,
    BatchCompleted,
    CollectedAccount,
    InvalidCredentialBatchFailure,
    QuotaBatchFailure,
    RejectedAccount,
    RetryableBatchFailure,
    SchemaBatchFailure,
)
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    AccountRouteSucceeded,
    InstanceHealthStatus,
    RouterDiagnosticCategory,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from tests.fakes.router import (
    CompleteBatch,
    DeclaredFakeDriver,
    FakeDriver,
    FakeStep,
    ScriptedFactory,
    make_account,
    make_post,
)


def _router(
    scripts: tuple[tuple[FakeStep, ...], ...],
    keys: tuple[str, ...] = ("test-credential-a", "test-credential-b"),
) -> tuple[Router, ScriptedFactory]:
    factory = ScriptedFactory(scripts)
    router = Router(
        AdapterRegistry((FakeDriver,)),
        factory,
        tuple(SecretStr(key) for key in keys),
    )
    return router, factory


def test_registry_resolution_preserves_declared_candidate_order() -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=False,
    )
    class SecondFakeDriver(DeclaredFakeDriver):
        pass

    registry = AdapterRegistry((SecondFakeDriver, FakeDriver))

    # When
    result = registry.resolve(
        platform=Platform.LINKEDIN,
        operation=AdapterOperation.COLLECT_ACCOUNT_POSTS,
        account_kind=AccountKind.PERSON,
    )

    # Then
    assert isinstance(result, ResolvedAdapterDrivers)
    assert result.driver_classes == (SecondFakeDriver, FakeDriver)


@pytest.mark.anyio
async def test_empty_account_set_succeeds_without_provider_calls() -> None:
    # Given
    router, factory = _router(((),))

    # When
    result = await router.route((), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert result.accounts == ()
    assert factory.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("keys", "expected_ordinals"),
    [
        (("canary-alpha",), [AdapterInstanceOrdinal(0)]),
        (
            (
                "canary-alpha",
                "canary-alpha",
                "canary-beta",
                "canary-gamma",
                "canary-beta",
            ),
            [
                AdapterInstanceOrdinal(0),
                AdapterInstanceOrdinal(1),
                AdapterInstanceOrdinal(2),
            ],
        ),
    ],
)
async def test_unique_instance_is_created_per_first_seen_credential(
    keys: tuple[str, ...],
    expected_ordinals: list[AdapterInstanceOrdinal],
) -> None:
    # Given / When
    router, factory = _router(((), (), ()), keys)
    result = await router.route((), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert factory.created_ordinals == expected_ordinals
    assert "canary" not in repr(result)
    assert "alpha" not in repr(result)
    assert "beta" not in repr(result)


@pytest.mark.anyio
async def test_zero_instance_pool_returns_account_scoped_exhaustion() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router((), ())

    # When
    result = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

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
    router, factory = _router(((), (), ()), ("key-a", "key-b", "key-c"))

    # When
    result = await router.route(accounts, AdapterOperation.COLLECT_ACCOUNT_POSTS)

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
    router, factory = _router(((), (), ()), ("key-a", "key-b", "key-c"))

    # When
    _ = await router.route(
        (*companies, *people),
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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("first_step", "health", "diagnostic"),
    [
        (
            QuotaBatchFailure(),
            InstanceHealthStatus.QUOTA_EXHAUSTED,
            RouterDiagnosticCategory.QUOTA_DISABLED,
        ),
        (
            InvalidCredentialBatchFailure(),
            InstanceHealthStatus.INVALID_CREDENTIAL,
            RouterDiagnosticCategory.CREDENTIAL_DISABLED,
        ),
    ],
)
async def test_disabled_instance_fails_over_once_for_the_run(
    first_step: QuotaBatchFailure | InvalidCredentialBatchFailure,
    health: InstanceHealthStatus,
    diagnostic: RouterDiagnosticCategory,
) -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router(((first_step,), (CompleteBatch(),)))

    # When
    result = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert [call.ordinal for call in factory.calls] == [0, 1]
    assert result.health[0].status is health
    assert result.diagnostics[0].category is diagnostic
    assert result.aggregate.status is RouterRunStatus.SUCCESS


@pytest.mark.anyio
async def test_transient_pre_acceptance_failure_tries_each_instance_once() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router(((RetryableBatchFailure(),), (RetryableBatchFailure(),)))

    # When
    result = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert [call.ordinal for call in factory.calls] == [0, 1]
    assert result.accounts == (
        AccountRouteFailed(account.id, AccountRouteFailureCategory.POOL_EXHAUSTED),
    )


@pytest.mark.anyio
async def test_accepted_snapshot_failure_never_retriggers_another_instance() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router(((AcceptedSnapshotBatchFailure(),), (CompleteBatch(),)))

    # When
    result = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert [call.ordinal for call in factory.calls] == [0]
    assert result.accounts == (
        AccountRouteFailed(
            account.id,
            AccountRouteFailureCategory.ACCEPTED_SNAPSHOT_FAILED,
        ),
    )


@pytest.mark.anyio
async def test_invalid_account_result_never_rotates_credentials() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    rejected = BatchCompleted(
        (RejectedAccount(account.id, AccountRejectionCategory.INVALID),)
    )
    router, factory = _router(((rejected,), (CompleteBatch(),)))

    # When
    result = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert [call.ordinal for call in factory.calls] == [0]
    assert result.accounts == (
        AccountRouteFailed(account.id, AccountRouteFailureCategory.INVALID_ACCOUNT),
    )


@pytest.mark.anyio
async def test_not_found_account_is_partial_without_credential_rotation() -> None:
    # Given
    found = make_account(AccountKind.PERSON, 1)
    missing = make_account(AccountKind.PERSON, 2)
    completed = BatchCompleted(
        (
            CollectedAccount(found.id, ()),
            RejectedAccount(missing.id, AccountRejectionCategory.NOT_FOUND),
        )
    )
    router, factory = _router(((completed,), (CompleteBatch(),)))

    # When
    result = await router.route(
        (missing, found),
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )

    # Then
    assert [call.ordinal for call in factory.calls] == [0]
    assert result.aggregate.succeeded_accounts == 1
    assert result.aggregate.failed_accounts == 1
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    assert result.accounts == (
        AccountRouteSucceeded(found.id, ()),
        AccountRouteFailed(
            missing.id,
            AccountRouteFailureCategory.ACCOUNT_NOT_FOUND,
        ),
    )


@pytest.mark.anyio
async def test_schema_failure_aborts_without_failover_or_posts() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router(((SchemaBatchFailure(),), (CompleteBatch(),)))

    # When
    result = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert [call.ordinal for call in factory.calls] == [0]
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.posts == ()
    assert result.diagnostics[-1].category is RouterDiagnosticCategory.SCHEMA_ABORT


@pytest.mark.anyio
async def test_inconsistent_batch_identity_aborts_as_schema_corruption() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router(((BatchCompleted(()),), (CompleteBatch(),)))

    # When
    result = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert [call.ordinal for call in factory.calls] == [0]
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.diagnostics[-1].category is RouterDiagnosticCategory.SCHEMA_ABORT


@pytest.mark.anyio
async def test_duplicate_post_ids_are_idempotent_within_a_result() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    post = make_post(account.id, 7)
    router, _factory = _router(((CompleteBatch(((post, post),)),),))

    # When
    result = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert result.posts == (post,)
    assert result.accounts == (AccountRouteSucceeded(account.id, (post.id,)),)


@pytest.mark.anyio
async def test_health_is_fresh_for_each_route_call() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router(
        ((QuotaBatchFailure(), CompleteBatch()), (CompleteBatch(),))
    )

    # When
    first = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)
    second = await router.route((account,), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert [call.ordinal for call in factory.calls] == [0, 1, 0]
    assert first.health[0].status is InstanceHealthStatus.QUOTA_EXHAUSTED
    assert second.health[0].status is InstanceHealthStatus.HEALTHY
