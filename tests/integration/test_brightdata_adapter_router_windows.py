from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AccountKind,
    AdapterPostRequest,
    AdapterRequestError,
    AdapterRequestErrorCategory,
    BrightDataAdapterConfig,
    RuntimeInput,
    SecretStr,
    SourceId,
    SourceInput,
    SyntheticBrightDataClient,
    _account,
    bootstrap_runtime,
    date,
    parse_linkedin_locator,
    pytest,
)


@pytest.mark.anyio
async def test_router_preserves_each_account_collection_window() -> None:
    # Given
    person = _account(AccountKind.PERSON, "person")
    company = _account(AccountKind.COMPANY, "company")
    client = SyntheticBrightDataClient()
    runtime = bootstrap_runtime(
        RuntimeInput(
            locators=(
                parse_linkedin_locator(person.profile_url),
                parse_linkedin_locator(company.profile_url),
            ),
            sources=(
                SourceInput(
                    source_id=SourceId.BRIGHTDATA,
                    credential=SecretStr("synthetic-one"),
                ),
            ),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )
    requests = (
        AdapterPostRequest(person, date(2026, 8, 1), date(2026, 8, 2)),
        AdapterPostRequest(company, date(2026, 8, 17), date(2026, 8, 19)),
    )

    # When
    _ = await runtime.router.route(requests)

    # Then
    assert [call.windows for call in client.calls] == [
        ((date(2026, 8, 1), date(2026, 8, 2), True),),
        ((date(2026, 8, 17), date(2026, 8, 19), True),),
    ]


@pytest.mark.anyio
async def test_conflicting_duplicate_collection_windows_fail_before_provider_io() -> (
    None
):
    # Given
    account = _account(AccountKind.PERSON, "person")
    client = SyntheticBrightDataClient()
    runtime = bootstrap_runtime(
        RuntimeInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            sources=(
                SourceInput(
                    source_id=SourceId.BRIGHTDATA,
                    credential=SecretStr("synthetic-one"),
                ),
            ),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )
    requests = (
        AdapterPostRequest(account, date(2026, 8, 1), date(2026, 8, 2)),
        AdapterPostRequest(account, date(2026, 8, 3), date(2026, 8, 4)),
    )

    # When / Then
    with pytest.raises(AdapterRequestError) as captured:
        _ = await runtime.router.route(
            requests,
        )
    assert client.calls == []
    assert captured.value.category is AdapterRequestErrorCategory.CONFLICTING_WINDOW


@pytest.mark.anyio
async def test_equivalent_duplicate_collection_windows_deduplicate() -> None:
    # Given
    account = _account(AccountKind.PERSON, "person")
    client = SyntheticBrightDataClient()
    runtime = bootstrap_runtime(
        RuntimeInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            sources=(
                SourceInput(
                    source_id=SourceId.BRIGHTDATA,
                    credential=SecretStr("synthetic-one"),
                ),
            ),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )
    request = AdapterPostRequest(
        account,
        date(2026, 8, 1),
        date(2026, 8, 2),
    )

    # When
    result = await runtime.router.route(
        (request, request),
    )

    # Then
    assert result.aggregate.succeeded_accounts == 1
    assert client.calls[0].urls == (account.profile_url,)


@pytest.mark.anyio
async def test_conflicting_initial_collection_modes_fail_before_provider_io() -> None:
    account = _account(AccountKind.PERSON, "person")
    client = SyntheticBrightDataClient()
    runtime = bootstrap_runtime(
        RuntimeInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            sources=(
                SourceInput(
                    source_id=SourceId.BRIGHTDATA,
                    credential=SecretStr("synthetic-one"),
                ),
            ),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )
    dates = (date(2026, 8, 1), date(2026, 8, 2))
    requests = (
        AdapterPostRequest(account, *dates, is_initial_collection=False),
        AdapterPostRequest(account, *dates, is_initial_collection=True),
    )

    with pytest.raises(AdapterRequestError) as captured:
        _ = await runtime.router.route(requests)

    assert client.calls == []
    assert captured.value.category is AdapterRequestErrorCategory.CONFLICTING_WINDOW


def test_inverted_collection_window_fails_typed_before_provider_io() -> None:
    # Given
    account = _account(AccountKind.PERSON, "person")

    # When / Then
    with pytest.raises(AdapterRequestError) as captured:
        _ = AdapterPostRequest(
            account,
            date(2026, 8, 2),
            date(2026, 8, 1),
        )
    assert captured.value.category is AdapterRequestErrorCategory.INVERTED_WINDOW
