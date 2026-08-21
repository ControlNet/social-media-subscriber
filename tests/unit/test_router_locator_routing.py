from __future__ import annotations

from datetime import date

import pytest

from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.instance import (
    AdapterInstanceOrdinal,
    AdapterPostLocatorRequest,
)
from social_media_subscriber.adapters.router_outcomes import RouterRunStatus
from social_media_subscriber.domain.platform import AccountKind
from tests.unit.test_router_support import build_locator_request, build_router


@pytest.mark.anyio
async def test_locator_discovery_empty_requests_succeed_without_provider_calls() -> (
    None
):
    # Given
    router, factory = build_router(((),))

    # When / Then
    assert hasattr(router, "discover_posts")
    result = await router.discover_posts(())
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert factory.locator_calls == []


@pytest.mark.anyio
async def test_locator_discovery_batch_21_is_20_plus_1_in_stable_order() -> None:
    # Given
    requests = tuple(
        build_locator_request(AccountKind.PERSON, number) for number in range(21, 0, -1)
    )
    router, factory = build_router(
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
        build_locator_request(AccountKind.PERSON, number) for number in range(21, 0, -1)
    )
    companies = tuple(
        build_locator_request(AccountKind.COMPANY, number) for number in (2, 1)
    )
    router, factory = build_router(
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
    first = build_locator_request(AccountKind.PERSON, 1)
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
    router, factory = build_router(((),))

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
