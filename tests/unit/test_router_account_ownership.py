from __future__ import annotations

__test__ = False

import pytest

from social_media_subscriber.adapters import AdapterOperation
from social_media_subscriber.adapters.instance import (
    BatchCompleted,
    CollectedAccount,
)
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteSucceeded,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)
from tests.fakes.router import CompleteBatch, make_account, make_post
from tests.unit.test_router_support import (
    build_post_requests,
    build_router,
    build_source_record,
)


@pytest.mark.anyio
async def test_duplicate_post_ids_are_idempotent_within_a_result() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    post = make_post(account.id, 7)
    router, _factory = build_router(((CompleteBatch(((post, post),)),),))

    # When
    result = await router.route(
        build_post_requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert result.posts == (post,)
    assert result.accounts == (AccountRouteSucceeded(account.id, (post.id,)),)


@pytest.mark.anyio
async def test_equivalent_source_records_collapse_with_deterministic_skips() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    post = make_post(account.id, 7)
    source = build_source_record(account, "activity-7", "provider source")
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
    router, _factory = build_router(((completed,),))

    # When
    result = await router.route(
        build_post_requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert result.source_records == (source,)
    assert result.skipped == SkippedPostCounts(replies=1)
    assert result.posts == (post,)


@pytest.mark.anyio
async def test_differing_source_payload_aborts_and_suppresses_all_output() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    first = build_source_record(account, "activity-7", "first payload")
    second = build_source_record(account, "activity-7", "second payload")
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
    router, _factory = build_router(((completed,),))

    # When
    result = await router.route(
        build_post_requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
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
    wrong_source = build_source_record(other, "activity-7", "wrong owner")
    completed = BatchCompleted(
        (CollectedAccount(account.id, (), (wrong_source,), SkippedPostCounts()),)
    )
    router, _factory = build_router(((completed,),))

    # When
    result = await router.route(
        build_post_requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.source_records == ()
    assert result.posts == ()
