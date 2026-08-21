from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.adapters.instance import (
    CollectedAccount,
    LocatorPostsBatchCompleted,
    ResolvedLocatorPosts,
    UnresolvedLocatorPosts,
)
from social_media_subscriber.adapters.router_outcomes import RouterRunStatus
from social_media_subscriber.domain.platform import AccountKind
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)
from tests.fakes.router import make_account, make_post
from tests.unit.test_router_support import (
    assert_locator_schema_abort,
    build_locator_request,
    build_resolved_locator,
    build_router,
    build_source_record,
)

if TYPE_CHECKING:
    from social_media_subscriber.adapters.router_discovery_state import (
        DiscoveryRouterResult,
    )


@pytest.mark.anyio
async def test_locator_discovery_cross_locator_account_owner_aborts() -> None:
    # Given
    first = build_locator_request(AccountKind.PERSON, 1)
    second = build_locator_request(AccountKind.PERSON, 2)
    account = make_account(AccountKind.PERSON, 1)
    completed = LocatorPostsBatchCompleted(
        (
            build_resolved_locator(first, account),
            build_resolved_locator(second, account),
        )
    )
    router, _factory = build_router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((first, second))

    # Then
    assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_cross_locator_failure_is_order_independent() -> None:
    # Given
    first = build_locator_request(AccountKind.PERSON, 1)
    second = build_locator_request(AccountKind.PERSON, 2)
    account = make_account(AccountKind.PERSON, 1)

    # When
    results: list[DiscoveryRouterResult] = []
    for requests in ((first, second), (second, first)):
        outcomes = tuple(
            build_resolved_locator(request, account) for request in requests
        )
        router, _factory = build_router(
            ((),), locator_scripts=((LocatorPostsBatchCompleted(outcomes),),)
        )
        results.append(await router.discover_posts(requests))

    # Then
    for result in results:
        assert_locator_schema_abort(result)
    assert results[0].diagnostics == results[1].diagnostics


@pytest.mark.anyio
@pytest.mark.parametrize("ownership", ["collected", "post", "source"])
async def test_locator_discovery_ownership_mismatch_aborts(ownership: str) -> None:
    # Given
    request = build_locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    other = make_account(AccountKind.PERSON, 2)
    post = make_post(other.id, 7) if ownership == "post" else None
    source = (
        build_source_record(other, "activity-7", "wrong owner")
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
    router, _factory = build_router(
        ((),), locator_scripts=((LocatorPostsBatchCompleted((outcome,)),),)
    )

    # When
    result = await router.discover_posts((request,))

    # Then
    assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_resolved_unresolved_and_posts_are_complete() -> None:
    # Given
    resolved_request = build_locator_request(AccountKind.PERSON, 1)
    unresolved_request = build_locator_request(AccountKind.PERSON, 2)
    account = make_account(AccountKind.PERSON, 1)
    post = make_post(account.id, 7)
    source = build_source_record(account, "activity-7", "source")
    completed = LocatorPostsBatchCompleted(
        (
            build_resolved_locator(
                resolved_request,
                account,
                posts=(post,),
                sources=(source,),
                skipped=SkippedPostCounts(replies=1),
            ),
            UnresolvedLocatorPosts(unresolved_request.locator),
        )
    )
    router, _factory = build_router(((),), locator_scripts=((completed,),))

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
