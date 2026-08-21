from __future__ import annotations

__test__ = False

import pytest
from pydantic import SecretStr

from social_media_subscriber.adapters import AdapterOperation, AdapterRegistry, adapter
from social_media_subscriber.adapters.instance import (
    InvalidCredentialBatchFailure,
    LocatorPostsBatchCompleted,
    QuotaBatchFailure,
    RetryableBatchFailure,
)
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.adapters.router_discovery_state import (
    DiscoveryLocatorFailed,
)
from social_media_subscriber.adapters.router_outcomes import (
    InstanceHealthStatus,
    RouterDiagnosticCategory,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from tests.fakes.router import (
    DeclaredFakeDriver,
    FakeDriver,
    ScriptedFactory,
    make_account,
)
from tests.unit.test_router_support import (
    build_locator_request,
    build_resolved_locator,
    build_router,
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "health", "diagnostic"),
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
async def test_locator_discovery_failover_disables_instance(
    failure: QuotaBatchFailure | InvalidCredentialBatchFailure,
    health: InstanceHealthStatus,
    diagnostic: RouterDiagnosticCategory,
) -> None:
    # Given
    request = build_locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    completed = LocatorPostsBatchCompleted((build_resolved_locator(request, account),))
    router, factory = build_router(
        ((), ()),
        locator_scripts=((failure,), (completed,)),
    )

    # When
    result = await router.discover_posts((request,))

    # Then
    assert [call.ordinal for call in factory.locator_calls] == [0, 1]
    assert result.health[0].status is health
    assert result.diagnostics[0].category is diagnostic
    assert result.accounts == (account,)
    assert result.aggregate.status is RouterRunStatus.SUCCESS


@pytest.mark.anyio
async def test_locator_discovery_retryable_failure_rotates_without_disabling() -> None:
    # Given
    request = build_locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    completed = LocatorPostsBatchCompleted((build_resolved_locator(request, account),))
    router, factory = build_router(
        ((), ()),
        locator_scripts=((RetryableBatchFailure(),), (completed,)),
    )

    # When
    result = await router.discover_posts((request,))

    # Then
    assert [call.ordinal for call in factory.locator_calls] == [0, 1]
    assert tuple(item.status for item in result.health) == (
        InstanceHealthStatus.HEALTHY,
        InstanceHealthStatus.HEALTHY,
    )
    assert result.diagnostics == ()
    assert result.accounts == (account,)


@pytest.mark.anyio
async def test_locator_discovery_health_is_fresh_for_each_call() -> None:
    # Given
    request = build_locator_request(AccountKind.PERSON, 1)
    router, factory = build_router(
        ((), ()),
        locator_scripts=((QuotaBatchFailure(),), ()),
    )

    # When
    first = await router.discover_posts((request,))
    second = await router.discover_posts((request,))

    # Then
    assert [call.ordinal for call in factory.locator_calls] == [0, 1, 0]
    assert first.health[0].status is InstanceHealthStatus.QUOTA_EXHAUSTED
    assert second.health[0].status is InstanceHealthStatus.HEALTHY


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("registry_supports_discovery", "keys", "category"),
    [
        (False, ("key-a",), "unsupported_capability"),
        (True, (), "pool_exhausted"),
    ],
)
async def test_locator_discovery_fail_attribution_for_unsupported_or_empty_pool(
    registry_supports_discovery: bool,
    keys: tuple[str, ...],
    category: str,
) -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=True,
    )
    class CollectionOnlyDriver(DeclaredFakeDriver):
        pass

    factory = ScriptedFactory(((),))
    driver = FakeDriver if registry_supports_discovery else CollectionOnlyDriver
    router = Router(
        AdapterRegistry((driver,)),
        factory,
        tuple(SecretStr(key) for key in keys),
    )
    request = build_locator_request(AccountKind.PERSON, 1)

    # When
    result = await router.discover_posts((request,))

    # Then
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    outcome = result.outcomes[0]
    assert isinstance(outcome, DiscoveryLocatorFailed)
    assert outcome.category.value == category
    assert factory.locator_calls == []


failure_attribution = (
    test_locator_discovery_fail_attribution_for_unsupported_or_empty_pool
)
