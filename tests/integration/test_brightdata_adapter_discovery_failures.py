from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AccountKind,
    AdapterInstanceOrdinal,
    AdapterPostLocatorBatch,
    AdapterPostLocatorRequest,
    BrightDataAdapterConfig,
    BrightDataError,
    BrightDataErrorCategory,
    BrightDataLinkedInAdapter,
    InvalidCredentialBatchFailure,
    LocatorPostsBatchCompleted,
    QuotaBatchFailure,
    RetryableBatchFailure,
    SchemaBatchFailure,
    SyntheticBrightDataClient,
    UnresolvedLocatorPosts,
    _account,
    _post,
    date,
    parse_linkedin_locator,
    pytest,
)


@pytest.mark.anyio
async def test_locator_discovery_mixed_ids_abort_atomically() -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/mixed")
    first = _account(AccountKind.PERSON, "601", "mixed")
    second = _account(AccountKind.PERSON, "602", "mixed")
    client = SyntheticBrightDataClient(
        person_posts=(_post(first, "mixed-first"), _post(second, "mixed-second"))
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
    assert isinstance(attempt, SchemaBatchFailure)
    assert not isinstance(attempt, LocatorPostsBatchCompleted)


@pytest.mark.anyio
async def test_locator_discovery_malformed_actor_aborts_without_provider_text() -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/malformed")
    account = _account(AccountKind.PERSON, "701", "malformed")
    record = _post(account, "malformed-original").model_copy(
        update={
            "user_url": "provider-prompt-canary",
            "provider_note": "credential-canary",
        }
    )
    client = SyntheticBrightDataClient(person_posts=(record,))
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (AdapterPostLocatorRequest(locator, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, SchemaBatchFailure)
    assert "canary" not in repr(attempt)
    assert "prompt" not in repr(attempt)


@pytest.mark.anyio
async def test_locator_discovery_conflict_cross_owner_aborts_atomically() -> None:
    # Given
    first_locator = parse_linkedin_locator("https://linkedin.com/in/owner-one")
    second_locator = parse_linkedin_locator("https://linkedin.com/in/owner-two")
    first = _account(AccountKind.PERSON, "751", "owner-one")
    record = _post(first, "cross-owner").model_copy(
        update={"profile_url": second_locator.canonical_url}
    )
    client = SyntheticBrightDataClient(person_posts=(record,))
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (
            AdapterPostLocatorRequest(
                first_locator, date(2026, 8, 13), date(2026, 8, 20)
            ),
            AdapterPostLocatorRequest(
                second_locator, date(2026, 8, 13), date(2026, 8, 20)
            ),
        )
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, SchemaBatchFailure)
    assert not isinstance(attempt, LocatorPostsBatchCompleted)


@pytest.mark.anyio
async def test_locator_discovery_conflict_new_account_id_aborts_whole_batch() -> None:
    # Given
    first_locator = parse_linkedin_locator("https://linkedin.com/in/conflict-one")
    second_locator = parse_linkedin_locator("https://linkedin.com/in/conflict-two")
    first = _account(AccountKind.PERSON, "801", "conflict-one")
    second = _account(AccountKind.PERSON, "801", "conflict-two")
    client = SyntheticBrightDataClient(
        person_posts=(_post(first, "conflict-first"), _post(second, "conflict-second"))
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (
            AdapterPostLocatorRequest(
                first_locator, date(2026, 8, 13), date(2026, 8, 20)
            ),
            AdapterPostLocatorRequest(
                second_locator, date(2026, 8, 13), date(2026, 8, 20)
            ),
        )
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, SchemaBatchFailure)
    assert not isinstance(attempt, LocatorPostsBatchCompleted)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("category", "expected_type"),
    [
        (BrightDataErrorCategory.AUTH, InvalidCredentialBatchFailure),
        (BrightDataErrorCategory.QUOTA, QuotaBatchFailure),
        (BrightDataErrorCategory.RETRYABLE, RetryableBatchFailure),
        (BrightDataErrorCategory.TIMEOUT, RetryableBatchFailure),
        (BrightDataErrorCategory.SCHEMA, SchemaBatchFailure),
        (BrightDataErrorCategory.SNAPSHOT_TIMEOUT, SchemaBatchFailure),
        (BrightDataErrorCategory.SNAPSHOT_TERMINAL, SchemaBatchFailure),
    ],
)
async def test_locator_discovery_provider_failure_category_mapping(
    category: BrightDataErrorCategory,
    expected_type: type[
        InvalidCredentialBatchFailure
        | QuotaBatchFailure
        | RetryableBatchFailure
        | SchemaBatchFailure
    ],
) -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/failure")
    client = SyntheticBrightDataClient(failure=BrightDataError(category))
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    batch = AdapterPostLocatorBatch(
        (AdapterPostLocatorRequest(locator, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    # When
    attempt = await adapter.discover_posts(batch)

    # Then
    assert isinstance(attempt, expected_type)
    assert [call.operation for call in client.calls] == ["posts"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category",
    [BrightDataErrorCategory.INPUT, BrightDataErrorCategory.NOT_FOUND],
)
async def test_locator_discovery_no_identity_provider_result_is_unresolved(
    category: BrightDataErrorCategory,
) -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/not-found")
    client = SyntheticBrightDataClient(failure=BrightDataError(category))
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
