from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AccountInput,
    AccountKind,
    AccountRouteFailed,
    AccountRouteFailureCategory,
    AdapterPostRequest,
    BrightDataAdapterConfig,
    SecretStr,
    SyntheticBrightDataClient,
    _account,
    _post,
    bootstrap_runtime,
    date,
    parse_linkedin_locator,
    pytest,
)


@pytest.mark.anyio
async def test_empty_bootstrap_pool_reports_exhaustion_without_client_creation() -> (
    None
):
    account = _account(AccountKind.PERSON, "person")
    account_input = AccountInput(
        locators=(parse_linkedin_locator(account.profile_url),),
        bright_data_api_keys=(),
    )
    clients: list[SyntheticBrightDataClient] = []

    def client_builder(_credential: str) -> SyntheticBrightDataClient:
        client = SyntheticBrightDataClient()
        clients.append(client)
        return client

    runtime = bootstrap_runtime(
        account_input,
        BrightDataAdapterConfig(_NOW),
        client_builder=client_builder,
    )

    result = await runtime.router.route(
        (AdapterPostRequest(account, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    assert result.accounts == (
        AccountRouteFailed(account.id, AccountRouteFailureCategory.POOL_EXHAUSTED),
    )
    assert clients == []


@pytest.mark.anyio
async def test_public_runtime_preserves_source_records_and_skips_through_router() -> (
    None
):
    account = _account(AccountKind.PERSON, "person")
    client = SyntheticBrightDataClient(
        person_posts=(
            _post(account, "original"),
            _post(account, "reply", "reply"),
        )
    )
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            bright_data_api_keys=(SecretStr("synthetic-one"),),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )

    result = await runtime.router.route(
        (AdapterPostRequest(account, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    assert len(result.posts) == 1
    assert [record.platform_post_id for record in result.source_records] == [
        "original",
    ]
    assert result.skipped.replies == 1
