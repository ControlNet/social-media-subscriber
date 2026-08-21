from __future__ import annotations

__test__ = False

from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.adapters.instance import (
    LocatorPostsBatchCompleted,
    SchemaBatchFailure,
    UnresolvedLocatorPosts,
)
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
    from social_media_subscriber.adapters import instance as instance_contract


@pytest.mark.anyio
@pytest.mark.parametrize("shape", ["missing", "extra", "duplicate"])
async def test_locator_discovery_schema_requires_exact_complete_coverage(
    shape: str,
) -> None:
    # Given
    request = build_locator_request(AccountKind.PERSON, 1)
    extra = build_locator_request(AccountKind.PERSON, 2)
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
    router, _factory = build_router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((request,))

    # Then
    assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_schema_conflicting_duplicate_sources_abort() -> None:
    # Given
    request = build_locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    first = build_source_record(account, "activity-7", "first")
    second = build_source_record(account, "activity-7", "second")
    completed = LocatorPostsBatchCompleted(
        (
            build_resolved_locator(
                request,
                account,
                sources=(first, second),
                skipped=SkippedPostCounts(unknown=2),
            ),
        )
    )
    router, _factory = build_router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((request,))

    # Then
    assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_schema_conflicting_duplicate_posts_abort() -> None:
    # Given
    request = build_locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    first = make_post(account.id, 7)
    conflicting = make_post(account.id, 8).model_copy(update={"id": first.id})
    completed = LocatorPostsBatchCompleted(
        (build_resolved_locator(request, account, posts=(first, conflicting)),)
    )
    router, _factory = build_router(((),), locator_scripts=((completed,),))

    # When
    result = await router.discover_posts((request,))

    # Then
    assert_locator_schema_abort(result)


@pytest.mark.anyio
async def test_locator_discovery_late_schema_abort_suppresses_prior_batch() -> None:
    # Given
    requests = tuple(
        build_locator_request(AccountKind.PERSON, number) for number in range(1, 22)
    )
    ordered = tuple(sorted(requests, key=lambda request: request.locator.canonical_url))
    first_batch = LocatorPostsBatchCompleted(
        tuple(
            build_resolved_locator(request, make_account(AccountKind.PERSON, number))
            for number, request in enumerate(ordered[:20], start=1)
        )
    )
    router, factory = build_router(
        ((), ()),
        locator_scripts=((first_batch,), (SchemaBatchFailure(),)),
    )

    # When
    result = await router.discover_posts(requests)

    # Then
    assert [call.ordinal for call in factory.locator_calls] == [0, 1]
    assert_locator_schema_abort(result)
