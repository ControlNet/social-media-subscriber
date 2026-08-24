from __future__ import annotations

__test__ = False

import pytest
from pydantic import SecretStr

from social_media_subscriber.adapters.instance import (
    AccountRejectionCategory,
    AdapterInstanceSpec,
    BatchCompleted,
    CollectedAccount,
    InvalidCredentialBatchFailure,
    QuotaBatchFailure,
    RejectedAccount,
    RetryableBatchFailure,
    SchemaBatchFailure,
)
from social_media_subscriber.adapters.registry import AdapterRegistry
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    AccountRouteSucceeded,
    InstanceHealthStatus,
    RouterDiagnosticCategory,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind
from tests.fakes.router import (
    CompleteBatch,
    FakeDriver,
    FallbackFakeDriver,
    ScriptedFactory,
    make_account,
)
from tests.unit.test_router_support import build_post_requests, build_router


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
    router, factory = build_router(((first_step,), (CompleteBatch(),)))

    # When
    result = await router.route(build_post_requests((account,)))

    # Then
    assert [call.ordinal for call in factory.calls] == [0, 1]
    assert result.health[0].status is health
    assert result.diagnostics[0].category is diagnostic
    assert result.aggregate.status is RouterRunStatus.SUCCESS


@pytest.mark.anyio
async def test_transient_pre_acceptance_failure_tries_each_instance_once() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(
        ((RetryableBatchFailure(),), (RetryableBatchFailure(),))
    )

    # When
    result = await router.route(build_post_requests((account,)))

    # Then
    assert [call.ordinal for call in factory.calls] == [0, 1]
    assert result.accounts == (
        AccountRouteFailed(account.id, AccountRouteFailureCategory.POOL_EXHAUSTED),
    )


@pytest.mark.anyio
async def test_retryable_failure_can_fall_back_to_another_provider_driver() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    primary = ScriptedFactory(((RetryableBatchFailure(),),), FakeDriver)
    fallback = ScriptedFactory(((CompleteBatch(),),), FallbackFakeDriver)
    router = Router(
        AdapterRegistry((FallbackFakeDriver, FakeDriver)),
        (
            AdapterInstanceSpec(FakeDriver, primary, SecretStr("shared-provider-key")),
            AdapterInstanceSpec(
                FallbackFakeDriver,
                fallback,
                SecretStr("shared-provider-key"),
            ),
        ),
    )

    # When
    result = await router.route(build_post_requests((account,)))

    # Then
    assert [call.ordinal for call in primary.calls] == [0]
    assert [call.ordinal for call in fallback.calls] == [1]
    assert primary.created_ordinals == [0]
    assert fallback.created_ordinals == [1]
    assert result.aggregate.status is RouterRunStatus.SUCCESS


@pytest.mark.anyio
async def test_invalid_account_result_never_rotates_credentials() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    rejected = BatchCompleted(
        (RejectedAccount(account.id, AccountRejectionCategory.INVALID),)
    )
    router, factory = build_router(((rejected,), (CompleteBatch(),)))

    # When
    result = await router.route(build_post_requests((account,)))

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
    router, factory = build_router(((completed,), (CompleteBatch(),)))

    # When
    result = await router.route(
        build_post_requests((missing, found)),
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
    router, factory = build_router(((SchemaBatchFailure(),), (CompleteBatch(),)))

    # When
    result = await router.route(build_post_requests((account,)))

    # Then
    assert [call.ordinal for call in factory.calls] == [0]
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.posts == ()
    assert result.diagnostics[-1].category is RouterDiagnosticCategory.SCHEMA_ABORT


@pytest.mark.anyio
async def test_inconsistent_batch_identity_aborts_as_schema_corruption() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(((BatchCompleted(()),), (CompleteBatch(),)))

    # When
    result = await router.route(build_post_requests((account,)))

    # Then
    assert [call.ordinal for call in factory.calls] == [0]
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.diagnostics[-1].category is RouterDiagnosticCategory.SCHEMA_ABORT


@pytest.mark.anyio
async def test_health_is_fresh_for_each_route_call() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(
        ((QuotaBatchFailure(), CompleteBatch()), (CompleteBatch(),))
    )

    # When
    first = await router.route(build_post_requests((account,)))
    second = await router.route(build_post_requests((account,)))

    # Then
    assert [call.ordinal for call in factory.calls] == [0, 1, 0]
    assert first.health[0].status is InstanceHealthStatus.QUOTA_EXHAUSTED
    assert second.health[0].status is InstanceHealthStatus.HEALTHY
