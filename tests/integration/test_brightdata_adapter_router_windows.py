from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AccountInput,
    AccountKind,
    AdapterOperation,
    AdapterPostRequest,
    AdapterRequestError,
    AdapterRequestErrorCategory,
    BrightDataAdapterConfig,
    SecretStr,
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
    person = _account(AccountKind.PERSON, "101", "person")
    company = _account(AccountKind.COMPANY, "202", "company")
    client = SyntheticBrightDataClient()
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(
                parse_linkedin_locator(person.profile_url),
                parse_linkedin_locator(company.profile_url),
            ),
            bright_data_api_keys=(SecretStr("synthetic-one"),),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )
    requests = (
        AdapterPostRequest(person, date(2026, 8, 1), date(2026, 8, 2)),
        AdapterPostRequest(company, date(2026, 8, 17), date(2026, 8, 19)),
    )

    # When
    _ = await runtime.router.route(requests, AdapterOperation.COLLECT_ACCOUNT_POSTS)

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
    account = _account(AccountKind.PERSON, "101", "person")
    client = SyntheticBrightDataClient()
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            bright_data_api_keys=(SecretStr("synthetic-one"),),
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
            AdapterOperation.COLLECT_ACCOUNT_POSTS,
        )
    assert client.calls == []
    assert captured.value.category is AdapterRequestErrorCategory.CONFLICTING_WINDOW


@pytest.mark.anyio
async def test_equivalent_duplicate_collection_windows_deduplicate() -> None:
    # Given
    account = _account(AccountKind.PERSON, "101", "person")
    client = SyntheticBrightDataClient()
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            bright_data_api_keys=(SecretStr("synthetic-one"),),
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
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )

    # Then
    assert result.aggregate.succeeded_accounts == 1
    assert client.calls[0].urls == (account.profile_url,)


def test_inverted_collection_window_fails_typed_before_provider_io() -> None:
    # Given
    account = _account(AccountKind.PERSON, "101", "person")

    # When / Then
    with pytest.raises(AdapterRequestError) as captured:
        _ = AdapterPostRequest(
            account,
            date(2026, 8, 2),
            date(2026, 8, 1),
        )
    assert captured.value.category is AdapterRequestErrorCategory.INVERTED_WINDOW
