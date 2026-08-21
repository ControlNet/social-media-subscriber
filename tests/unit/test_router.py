from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

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
    AdapterPostLocatorRequest,
    AdapterPostRequest,
    BatchCompleted,
    CollectedAccount,
    InvalidCredentialBatchFailure,
    LocatorPostsBatchCompleted,
    QuotaBatchFailure,
    RejectedAccount,
    ResolvedLocatorPosts,
    RetryableBatchFailure,
    SchemaBatchFailure,
    UnresolvedLocatorPosts,
)
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.adapters.router_discovery_state import (
    DiscoveryFailureCategory,
    DiscoveryLocatorFailed,
)
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
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
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
    from social_media_subscriber.adapters.router_discovery_state import (
        DiscoveryRouterResult,
    )
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.post import Post

_DISCOVERY_START: Final = date(2026, 8, 14)
_NO_SKIPS: Final = SkippedPostCounts()


def _router(
    scripts: tuple[tuple[FakeStep, ...], ...],
    keys: tuple[str, ...] = ("test-credential-a", "test-credential-b"),
    locator_scripts: tuple[
        tuple[instance_contract.AdapterPostLocatorAttempt, ...], ...
    ] = (),
) -> tuple[Router, ScriptedFactory]:
    factory = ScriptedFactory(scripts, locator_scripts)
    router = Router(
        AdapterRegistry((FakeDriver,)),
        factory,
        tuple(SecretStr(key) for key in keys),
    )
    return router, factory


@pytest.mark.anyio
async def test_locator_discovery_empty_requests_succeed_without_provider_calls() -> (
    None
):
    # Given
    router, factory = _router(((),))

    # When / Then
    assert hasattr(router, "discover_posts")
    result = await router.discover_posts(())
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert factory.locator_calls == []


def _requests(accounts: tuple[Account, ...]) -> tuple[AdapterPostRequest, ...]:
    return tuple(
        AdapterPostRequest(
            account,
            date(2026, 8, 13),
            date(2026, 8, 20),
        )
        for account in accounts
    )


def _locator_request(
    kind: AccountKind,
    number: int,
    *,
    start_date: date = _DISCOVERY_START,
) -> AdapterPostLocatorRequest:
    path = "in" if kind is AccountKind.PERSON else "company"
    locator = parse_linkedin_locator(
        f"https://www.linkedin.com/{path}/router-test-{number}/"
    )
    return AdapterPostLocatorRequest(locator, start_date, date(2026, 8, 21))


def _resolved_locator(
    request: AdapterPostLocatorRequest,
    account: Account,
    *,
    posts: tuple[Post, ...] = (),
    sources: tuple[BrightDataLinkedInPostSourceRecord, ...] = (),
    skipped: SkippedPostCounts = _NO_SKIPS,
) -> ResolvedLocatorPosts:
    return ResolvedLocatorPosts(
        request.locator,
        account,
        CollectedAccount(account.id, posts, sources, skipped),
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


@pytest.mark.anyio
async def test_locator_discovery_batch_21_is_20_plus_1_in_stable_order() -> None:
    # Given
    requests = tuple(
        _locator_request(AccountKind.PERSON, number) for number in range(21, 0, -1)
    )
    router, factory = _router(
        ((), (), ()),
        ("key-a", "key-b", "key-c"),
    )

    # When
    result = await router.discover_posts(requests)

    # Then
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    assert [
        (call.ordinal, len(call.locator_urls)) for call in factory.locator_calls
    ] == [
        (AdapterInstanceOrdinal(0), 20),
        (AdapterInstanceOrdinal(1), 1),
    ]
    assert tuple(
        locator_url
        for call in factory.locator_calls
        for locator_url in call.locator_urls
    ) == tuple(sorted(request.locator.canonical_url for request in requests))


@pytest.mark.anyio
async def test_locator_discovery_kind_partitions_people_before_companies() -> None:
    # Given
    people = tuple(
        _locator_request(AccountKind.PERSON, number) for number in range(21, 0, -1)
    )
    companies = tuple(
        _locator_request(AccountKind.COMPANY, number) for number in (2, 1)
    )
    router, factory = _router(
        ((), (), ()),
        ("key-a", "key-b", "key-c"),
    )

    # When
    _ = await router.discover_posts((*companies, *people))

    # Then
    assert [
        (call.ordinal, call.kind, len(call.locator_urls))
        for call in factory.locator_calls
    ] == [
        (AdapterInstanceOrdinal(0), AccountKind.PERSON, 20),
        (AdapterInstanceOrdinal(1), AccountKind.PERSON, 1),
        (AdapterInstanceOrdinal(2), AccountKind.COMPANY, 2),
    ]


@pytest.mark.anyio
async def test_locator_discovery_canonical_dedupe_and_conflicting_window() -> None:
    # Given
    first = _locator_request(AccountKind.PERSON, 1)
    equivalent = AdapterPostLocatorRequest(
        parse_linkedin_locator(
            first.locator.canonical_url.replace("www.linkedin.com", "linkedin.com")
        ),
        first.start_date,
        first.end_date,
    )
    conflicting = AdapterPostLocatorRequest(
        first.locator,
        date(2026, 8, 15),
        first.end_date,
    )
    router, factory = _router(((),))

    # When
    deduplicated = await router.discover_posts((first, equivalent))

    # Then
    assert deduplicated.aggregate.unresolved_locators == 1
    assert len(factory.locator_calls) == 1
    with pytest.raises(instance_contract.AdapterRequestError) as captured:
        _ = await router.discover_posts((first, conflicting))
    assert (
        captured.value.category
        is instance_contract.AdapterRequestErrorCategory.CONFLICTING_WINDOW
    )
    assert len(factory.locator_calls) == 1


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
    request = _locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    completed = LocatorPostsBatchCompleted((_resolved_locator(request, account),))
    router, factory = _router(
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
    request = _locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    completed = LocatorPostsBatchCompleted((_resolved_locator(request, account),))
    router, factory = _router(
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
    request = _locator_request(AccountKind.PERSON, 1)
    router, factory = _router(
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
    request = _locator_request(AccountKind.PERSON, 1)

    # When
    result = await router.discover_posts((request,))

    # Then
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    outcome = result.outcomes[0]
    assert isinstance(outcome, DiscoveryLocatorFailed)
    assert outcome.category.value == category
    assert factory.locator_calls == []


@pytest.mark.anyio
async def test_locator_discovery_accepted_snapshot_is_terminal_no_reroute() -> None:
    # Given
    request = _locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    completed = LocatorPostsBatchCompleted((_resolved_locator(request, account),))
    router, factory = _router(
        ((), ()),
        locator_scripts=((AcceptedSnapshotBatchFailure(),), (completed,)),
    )

    # When
    result = await router.discover_posts((request,))

    # Then
    assert [call.ordinal for call in factory.locator_calls] == [0]
    outcome = result.outcomes[0]
    assert isinstance(outcome, DiscoveryLocatorFailed)
    assert outcome.category is DiscoveryFailureCategory.ACCEPTED_SNAPSHOT_FAILED
    assert result.accounts == ()


@pytest.mark.anyio
@pytest.mark.parametrize("shape", ["missing", "extra", "duplicate"])
async def test_locator_discovery_schema_requires_exact_complete_coverage(
    shape: str,
) -> None:
    # Given
    request = _locator_request(AccountKind.PERSON, 1)
    extra = _locator_request(AccountKind.PERSON, 2)
    match shape:
        case "missing":
            outcomes: tuple[instance_contract.AdapterPostLocatorOutcome, ...] = ()
        case "extra":
            outcomes = (
                UnresolvedLocatorPosts(request.locator),
                UnresolvedLocatorPosts(extra.locator),
            )
        case "duplicate":
            outcomes = (
                UnresolvedLocatorPosts(request.locator),
                UnresolvedLocatorPosts(request.locator),
            )
        case unreachable:
            raise AssertionError(unreachable)
    completed = LocatorPostsBatchCompleted(outcomes)
    router, _factory = _router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((request,))

    # Then
    _assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_cross_locator_account_owner_aborts() -> None:
    # Given
    first = _locator_request(AccountKind.PERSON, 1)
    second = _locator_request(AccountKind.PERSON, 2)
    account = make_account(AccountKind.PERSON, 1)
    completed = LocatorPostsBatchCompleted(
        (_resolved_locator(first, account), _resolved_locator(second, account))
    )
    router, _factory = _router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((first, second))

    # Then
    _assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_cross_locator_failure_is_order_independent() -> None:
    # Given
    first = _locator_request(AccountKind.PERSON, 1)
    second = _locator_request(AccountKind.PERSON, 2)
    account = make_account(AccountKind.PERSON, 1)

    # When
    results: list[DiscoveryRouterResult] = []
    for requests in ((first, second), (second, first)):
        outcomes = tuple(_resolved_locator(request, account) for request in requests)
        router, _factory = _router(
            ((),), locator_scripts=((LocatorPostsBatchCompleted(outcomes),),)
        )
        results.append(await router.discover_posts(requests))

    # Then
    for result in results:
        _assert_locator_schema_abort(result)
    assert results[0].diagnostics == results[1].diagnostics


@pytest.mark.anyio
@pytest.mark.parametrize("ownership", ["collected", "post", "source"])
async def test_locator_discovery_ownership_mismatch_aborts(ownership: str) -> None:
    # Given
    request = _locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    other = make_account(AccountKind.PERSON, 2)
    post = make_post(other.id, 7) if ownership == "post" else None
    source = (
        _source_record(other, "activity-7", "wrong owner")
        if ownership == "source"
        else None
    )
    collected_id = other.id if ownership == "collected" else account.id
    outcome = ResolvedLocatorPosts(
        request.locator,
        account,
        CollectedAccount(
            collected_id,
            () if post is None else (post,),
            () if source is None else (source,),
        ),
    )
    router, _factory = _router(
        ((),), locator_scripts=((LocatorPostsBatchCompleted((outcome,)),),)
    )

    # When
    result = await router.discover_posts((request,))

    # Then
    _assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_schema_conflicting_duplicate_sources_abort() -> None:
    # Given
    request = _locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    first = _source_record(account, "activity-7", "first")
    second = _source_record(account, "activity-7", "second")
    completed = LocatorPostsBatchCompleted(
        (
            _resolved_locator(
                request,
                account,
                sources=(first, second),
                skipped=SkippedPostCounts(unknown=2),
            ),
        )
    )
    router, _factory = _router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((request,))

    # Then
    _assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_schema_conflicting_duplicate_posts_abort() -> None:
    # Given
    request = _locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    first = make_post(account.id, 7)
    conflicting = make_post(account.id, 8).model_copy(update={"id": first.id})
    completed = LocatorPostsBatchCompleted(
        (_resolved_locator(request, account, posts=(first, conflicting)),)
    )
    router, _factory = _router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((request,))

    # Then
    _assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_late_schema_abort_suppresses_prior_batch() -> None:
    # Given
    requests = tuple(
        _locator_request(AccountKind.PERSON, number) for number in range(1, 22)
    )
    ordered = tuple(sorted(requests, key=lambda request: request.locator.canonical_url))
    first_batch = LocatorPostsBatchCompleted(
        tuple(
            _resolved_locator(request, make_account(AccountKind.PERSON, number))
            for number, request in enumerate(ordered[:20], start=1)
        )
    )
    router, factory = _router(
        ((), ()),
        locator_scripts=((first_batch,), (SchemaBatchFailure(),)),
    )

    # When
    result = await router.discover_posts(requests)

    # Then
    assert [call.ordinal for call in factory.locator_calls] == [0, 1]
    _assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_resolved_unresolved_and_posts_are_complete() -> None:
    # Given
    resolved_request = _locator_request(AccountKind.PERSON, 1)
    unresolved_request = _locator_request(AccountKind.PERSON, 2)
    account = make_account(AccountKind.PERSON, 1)
    post = make_post(account.id, 7)
    source = _source_record(account, "activity-7", "source")
    completed = LocatorPostsBatchCompleted(
        (
            _resolved_locator(
                resolved_request,
                account,
                posts=(post,),
                sources=(source,),
                skipped=SkippedPostCounts(replies=1),
            ),
            UnresolvedLocatorPosts(unresolved_request.locator),
        )
    )
    router, _factory = _router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((resolved_request, unresolved_request))

    # Then
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    assert result.aggregate.resolved_locators == 1
    assert result.aggregate.unresolved_locators == 1
    assert result.accounts == (account,)
    assert result.posts == (post,)
    assert result.source_records == (source,)
    assert result.skipped == SkippedPostCounts(replies=1)


def _assert_locator_schema_abort(result: DiscoveryRouterResult) -> None:
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.accounts == ()
    assert result.outcomes == ()
    assert result.posts == ()
    assert result.source_records == ()
    assert result.skipped == SkippedPostCounts()
    assert tuple(item.category for item in result.diagnostics) == (
        RouterDiagnosticCategory.SCHEMA_ABORT,
    )
