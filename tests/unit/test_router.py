from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters import (
    AdapterOperation,
    AdapterRegistry,
    ResolvedAdapterDrivers,
    adapter,
)
from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AccountRejectionCategory,
    AdapterInstanceOrdinal,
    AdapterPostLocatorBatch,
    AdapterPostLocatorRequest,
    AdapterPostRequest,
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
    RouterOperationError,
    RouterRunStatus,
)
from social_media_subscriber.application import windows as window_contract
from social_media_subscriber.application.windows import ExplicitWindow, WindowContext
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.brightdata.adapter import (
    BrightDataLinkedInAdapter,
)
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataAdapterConfig,
)
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from tests.fakes.brightdata_adapter import SyntheticBrightDataClient
from tests.fakes.router import (
    CompleteBatch,
    DeclaredFakeDriver,
    FakeDriver,
    FakeStep,
    ScriptedFactory,
    make_account,
    make_post,
)

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account


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


def _requests(accounts: tuple[Account, ...]) -> tuple[AdapterPostRequest, ...]:
    return tuple(
        AdapterPostRequest(
            account,
            date(2026, 8, 13),
            date(2026, 8, 20),
        )
        for account in accounts
    )


def test_locator_operation_contract_is_closed() -> None:
    # Given / When
    values = tuple(operation.value for operation in AdapterOperation)

    # Then
    assert values == (
        "resolve_account_identity",
        "collect_account_posts",
        "discover_locator_posts",
    )


def test_locator_batch_deduplicates_canonical_requests_and_rejects_mixed_kinds() -> (
    None
):
    # Given
    assert hasattr(instance_contract, "AdapterPostLocatorRequest")
    assert hasattr(instance_contract, "AdapterPostLocatorBatch")
    person = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    duplicate = parse_linkedin_locator("https://linkedin.com/in/example-person")
    company = parse_linkedin_locator(
        "https://www.linkedin.com/company/example-company/"
    )
    request = instance_contract.AdapterPostLocatorRequest(
        person, date(2026, 8, 14), date(2026, 8, 21)
    )
    equivalent = instance_contract.AdapterPostLocatorRequest(
        duplicate, date(2026, 8, 14), date(2026, 8, 21)
    )

    # When
    batch = instance_contract.AdapterPostLocatorBatch((request, equivalent))

    # Then
    assert batch.requests == (request,)
    with pytest.raises(instance_contract.AdapterRequestError) as mixed:
        _ = instance_contract.AdapterPostLocatorBatch(
            (
                request,
                instance_contract.AdapterPostLocatorRequest(
                    company, date(2026, 8, 14), date(2026, 8, 21)
                ),
            )
        )
    assert (
        mixed.value.category
        is instance_contract.AdapterRequestErrorCategory.MIXED_LOCATOR_KIND
    )


def test_locator_batch_rejects_conflicting_duplicate_windows_before_fake_calls() -> (
    None
):
    # Given
    assert hasattr(instance_contract, "AdapterPostLocatorRequest")
    assert hasattr(instance_contract, "AdapterPostLocatorBatch")
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    first = instance_contract.AdapterPostLocatorRequest(
        locator, date(2026, 8, 14), date(2026, 8, 21)
    )
    conflicting = instance_contract.AdapterPostLocatorRequest(
        locator, date(2026, 8, 15), date(2026, 8, 21)
    )
    _, factory = _router(((),))

    # When / Then
    with pytest.raises(instance_contract.AdapterRequestError) as captured:
        _ = instance_contract.AdapterPostLocatorBatch((first, conflicting))
    assert (
        captured.value.category
        is instance_contract.AdapterRequestErrorCategory.CONFLICTING_WINDOW
    )
    assert factory.calls == []


def test_locator_request_rejects_inverted_window_before_fake_calls() -> None:
    # Given
    assert hasattr(instance_contract, "AdapterPostLocatorRequest")
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    _, factory = _router(((),))

    # When / Then
    with pytest.raises(instance_contract.AdapterRequestError) as captured:
        _ = instance_contract.AdapterPostLocatorRequest(
            locator, date(2026, 8, 22), date(2026, 8, 21)
        )
    assert (
        captured.value.category
        is instance_contract.AdapterRequestErrorCategory.INVERTED_WINDOW
    )
    assert factory.calls == []


def test_locator_window_uses_initial_policy_without_reading_prior_posts() -> None:
    # Given
    assert hasattr(window_contract, "build_locator_post_requests")
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    context = WindowContext(
        run_started_at=datetime(2026, 8, 21, tzinfo=UTC),
        override=ExplicitWindow.parse(None, None),
    )

    # When
    requests = window_contract.build_locator_post_requests((locator,), context)

    # Then
    assert requests == (
        instance_contract.AdapterPostLocatorRequest(
            locator, date(2026, 8, 14), date(2026, 8, 21)
        ),
    )


def test_locator_window_preserves_explicit_range() -> None:
    # Given
    assert hasattr(window_contract, "build_locator_post_requests")
    locator = parse_linkedin_locator(
        "https://www.linkedin.com/company/example-company/"
    )
    context = WindowContext(
        run_started_at=datetime(2026, 8, 21, tzinfo=UTC),
        override=ExplicitWindow.parse(date(2026, 8, 1), date(2026, 8, 3)),
    )

    # When
    requests = window_contract.build_locator_post_requests((locator,), context)

    # Then
    assert requests == (
        instance_contract.AdapterPostLocatorRequest(
            locator, date(2026, 8, 1), date(2026, 8, 3)
        ),
    )


def test_locator_attempt_contract_has_complete_resolved_and_unresolved_outcomes() -> (
    None
):
    # Given
    assert hasattr(instance_contract, "ResolvedLocatorPosts")
    assert hasattr(instance_contract, "UnresolvedLocatorPosts")
    assert hasattr(instance_contract, "LocatorPostsBatchCompleted")
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    account = make_account(AccountKind.PERSON, 1)
    collected = CollectedAccount(account.id, (make_post(account.id, 1),))

    # When
    completed = instance_contract.LocatorPostsBatchCompleted(
        (
            instance_contract.ResolvedLocatorPosts(locator, account, collected),
            instance_contract.UnresolvedLocatorPosts(locator),
        )
    )

    # Then
    resolved, unresolved = completed.outcomes
    assert isinstance(resolved, instance_contract.ResolvedLocatorPosts)
    assert isinstance(unresolved, instance_contract.UnresolvedLocatorPosts)
    assert resolved.account is account
    assert resolved.collected is collected
    assert unresolved.locator is locator


@pytest.mark.anyio
async def test_post_route_rejects_discovery_before_provider_call() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router(((),))

    # When / Then
    with pytest.raises(RouterOperationError):
        _ = await router.route(
            _requests((account,)),
            AdapterOperation.DISCOVER_LOCATOR_POSTS,
        )
    assert factory.calls == []


@pytest.mark.anyio
async def test_bright_data_locator_discovery_is_safe_schema_failure_before_wiring() -> (
    None
):
    # Given
    client = SyntheticBrightDataClient()
    adapter_instance = BrightDataLinkedInAdapter(
        client,
        AdapterInstanceOrdinal(0),
        BrightDataAdapterConfig(datetime(2026, 8, 21, tzinfo=UTC)),
    )
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    batch = AdapterPostLocatorBatch(
        (AdapterPostLocatorRequest(locator, date(2026, 8, 14), date(2026, 8, 21)),)
    )

    # When
    attempt = await adapter_instance.discover_posts(batch)

    # Then
    assert isinstance(attempt, SchemaBatchFailure)
    assert client.calls == []


def _source_record(
    account: Account,
    platform_post_id: str,
    text: str,
) -> BrightDataLinkedInPostSourceRecord:
    post = BrightDataPost(
        id=platform_post_id,
        date_posted="2026-08-20T12:00:00+00:00",
        post_type="post",
        url=f"https://www.linkedin.com/posts/{platform_post_id}",
        user_id=account.platform_account_id,
        post_text=text,
    )
    return BrightDataLinkedInPostSourceRecord.from_post(account.id, post)


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
async def test_post_route_rejects_identity_operation_before_provider_call() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = _router(((),))

    # When / Then
    with pytest.raises(RouterOperationError):
        _ = await router.route(
            _requests((account,)),
            AdapterOperation.RESOLVE_ACCOUNT_IDENTITY,
        )
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
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
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
    router, factory = _router(((), (), ()), ("key-a", "key-b", "key-c"))

    # When
    result = await router.route(
        _requests(accounts), AdapterOperation.COLLECT_ACCOUNT_POSTS
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
    router, factory = _router(((), (), ()), ("key-a", "key-b", "key-c"))

    # When
    _ = await router.route(
        _requests((*companies, *people)),
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
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

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
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

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
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

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
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

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
        _requests((missing, found)),
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
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

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
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

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
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

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
    first = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )
    second = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert [call.ordinal for call in factory.calls] == [0, 1, 0]
    assert first.health[0].status is InstanceHealthStatus.QUOTA_EXHAUSTED
    assert second.health[0].status is InstanceHealthStatus.HEALTHY


@pytest.mark.anyio
async def test_equivalent_source_records_collapse_with_deterministic_skips() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    post = make_post(account.id, 7)
    source = _source_record(account, "activity-7", "provider source")
    completed = BatchCompleted(
        (
            CollectedAccount(
                account.id,
                (post,),
                (source, source),
                SkippedPostCounts(replies=1),
            ),
        )
    )
    router, _factory = _router(((completed,),))

    # When
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert result.source_records == (source,)
    assert result.skipped == SkippedPostCounts(replies=1)
    assert result.posts == (post,)


@pytest.mark.anyio
async def test_differing_source_payload_aborts_and_suppresses_all_output() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    first = _source_record(account, "activity-7", "first payload")
    second = _source_record(account, "activity-7", "second payload")
    completed = BatchCompleted(
        (
            CollectedAccount(
                account.id,
                (),
                (first, second),
                SkippedPostCounts(unknown=2),
            ),
        )
    )
    router, _factory = _router(((completed,),))

    # When
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.posts == ()
    assert result.source_records == ()
    assert result.skipped == SkippedPostCounts()


@pytest.mark.anyio
async def test_source_account_ownership_mismatch_aborts_without_output() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    other = make_account(AccountKind.PERSON, 2)
    wrong_source = _source_record(other, "activity-7", "wrong owner")
    completed = BatchCompleted(
        (CollectedAccount(account.id, (), (wrong_source,), SkippedPostCounts()),)
    )
    router, _factory = _router(((completed,),))

    # When
    result = await router.route(
        _requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.source_records == ()
    assert result.posts == ()
