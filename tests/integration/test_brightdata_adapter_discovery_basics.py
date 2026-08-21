from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AcceptedSnapshotBatchFailure,
    AccountKind,
    AdapterInstanceOrdinal,
    AdapterPostLocatorBatch,
    AdapterPostLocatorRequest,
    BrightDataAdapterConfig,
    BrightDataError,
    BrightDataErrorCategory,
    BrightDataLinkedInAdapter,
    LocatorPostsBatchCompleted,
    ResolvedLocatorPosts,
    SyntheticBrightDataClient,
    UnresolvedLocatorPosts,
    _account,
    _post,
    date,
    parse_linkedin_locator,
    pytest,
)


@pytest.mark.anyio
async def test_locator_discovery_numeric_collects_posts_without_identity_io() -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/discovery-person")
    discovered = _account(AccountKind.PERSON, "303", "discovery-person")
    client = SyntheticBrightDataClient(
        person_posts=(_post(discovered, "discovery-original"),)
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (AdapterPostLocatorRequest(locator, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, LocatorPostsBatchCompleted)
    assert len(attempt.outcomes) == 1
    outcome = attempt.outcomes[0]
    assert isinstance(outcome, ResolvedLocatorPosts)
    assert outcome.locator == locator
    assert outcome.account.id == "linkedin:person:303"
    assert len(outcome.collected.posts) == 1
    assert [
        (call.operation, call.kind, call.urls, call.windows) for call in client.calls
    ] == [
        (
            "posts",
            AccountKind.PERSON,
            (locator.canonical_url,),
            ((date(2026, 8, 13), date(2026, 8, 20), True),),
        )
    ]


@pytest.mark.anyio
async def test_locator_discovery_numeric_company_uses_company_posts_only() -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/company/discovery-org")
    discovered = _account(AccountKind.COMPANY, "404", "discovery-org")
    client = SyntheticBrightDataClient(
        company_posts=(_post(discovered, "company-original"),)
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (AdapterPostLocatorRequest(locator, date(2026, 8, 14), date(2026, 8, 20)),)
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, LocatorPostsBatchCompleted)
    assert isinstance(attempt.outcomes[0], ResolvedLocatorPosts)
    assert [(call.operation, call.kind) for call in client.calls] == [
        ("posts", AccountKind.COMPANY)
    ]


@pytest.mark.anyio
async def test_locator_discovery_alias_canonicalization_remains_stable() -> None:
    # Given
    locator = parse_linkedin_locator(
        "https://www.linkedin.com/in/discovery-person/?trk=synthetic"
    )
    discovered = _account(AccountKind.PERSON, "303", "discovery-person")
    client = SyntheticBrightDataClient(
        person_posts=(_post(discovered, "canonical-original"),)
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (AdapterPostLocatorRequest(locator, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, LocatorPostsBatchCompleted)
    outcome = attempt.outcomes[0]
    assert isinstance(outcome, ResolvedLocatorPosts)
    assert outcome.account.profile_url == locator.canonical_url
    assert client.calls[0].urls == (locator.canonical_url,)


@pytest.mark.anyio
@pytest.mark.parametrize("records", [(), ("repost",)])
async def test_locator_discovery_no_identity_returns_unresolved_without_records(
    records: tuple[str, ...],
) -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/no-identity")
    account = _account(AccountKind.PERSON, "505", "no-identity")
    provider_records = tuple(_post(account, kind, kind) for kind in records)
    client = SyntheticBrightDataClient(person_posts=provider_records)
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (AdapterPostLocatorRequest(locator, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, LocatorPostsBatchCompleted)
    assert attempt.outcomes == (UnresolvedLocatorPosts(locator),)
    assert [call.operation for call in client.calls] == ["posts"]


@pytest.mark.anyio
async def test_locator_discovery_accepted_snapshot_is_terminal_one_shot() -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/accepted")
    client = SyntheticBrightDataClient(
        failure=BrightDataError(
            BrightDataErrorCategory.RETRYABLE,
            snapshot_accepted=True,
        )
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (AdapterPostLocatorRequest(locator, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, AcceptedSnapshotBatchFailure)
    assert [call.operation for call in client.calls] == ["posts"]
